import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from app.config import DATA_DIR, FFMPEG_BIN
from app.services.media_source import MediaSource, is_remote_media_source, media_source_at

LOGGER = logging.getLogger("clipper.transcription")
CHUNK_SECONDS = 600
MODEL_SIZES = {"tiny", "base", "small", "medium", "large-v3"}


class TranscriptionError(RuntimeError):
    """Raised when a video cannot be transcribed locally."""


def _gpu_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(
        token in message
        for token in ("cuda", "cudnn", "cublas", "gpu", "compute type", "out of memory", "failed to allocate")
    )


def _extract_chunk(video: MediaSource, start: float, duration: float, output: Path) -> None:
    if is_remote_media_source(video):
        command = [
            FFMPEG_BIN,
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(media_source_at(video, start)),
        ]
    else:
        command = [
            FFMPEG_BIN,
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            str(start),
            "-i",
            str(video),
        ]
    command.extend(
        [
            "-t",
            str(duration),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise TranscriptionError(f"Could not prepare audio chunk: {detail.strip()}") from exc


def _transcribe_chunks(
    video: MediaSource,
    duration: float,
    model_size: str,
    device: str,
    progress: Callable[[float, str], None],
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError(
            "faster-whisper is not installed. Install backend/requirements.txt and retry."
        ) from exc

    compute_type = "float16" if device == "cuda" else "int8"
    try:
        model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root=str(DATA_DIR / "models"),
        )
    except Exception as exc:
        raise TranscriptionError(f"Could not load the {device} transcription model: {exc}") from exc

    words: list[dict[str, Any]] = []
    language: str | None = None
    with tempfile.TemporaryDirectory(prefix="clipper-audio-") as temp_dir:
        chunk_path = Path(temp_dir) / "chunk.wav"
        for chunk_start in range(0, int(duration) + 1, CHUNK_SECONDS):
            if chunk_start >= duration:
                break
            chunk_duration = min(CHUNK_SECONDS, duration - chunk_start)
            _extract_chunk(video, chunk_start, chunk_duration, chunk_path)
            try:
                segments, info = model.transcribe(
                    str(chunk_path),
                    word_timestamps=True,
                    language=language,
                    vad_filter=True,
                )
                if language is None:
                    language = info.language
                last_reported_time = float(chunk_start)
                for segment in segments:
                    segment_end = min(duration, chunk_start + float(segment.end))
                    for word in segment.words or []:
                        if word.start is None or word.end is None:
                            continue
                        text = str(word.word)
                        if text.strip():
                            words.append(
                                {
                                    "start_time": max(0.0, chunk_start + float(word.start)),
                                    "end_time": min(duration, chunk_start + float(word.end)),
                                    "text": text,
                                }
                            )
                    if segment_end - last_reported_time >= 10:
                        progress(
                            min(99.0, segment_end / duration * 100),
                            f"Transcribed {int(segment_end)} of {int(duration)} seconds",
                        )
                        last_reported_time = segment_end
                percent = min(99.0, (chunk_start + chunk_duration) / duration * 100)
                progress(percent, f"Transcribed {int(chunk_start + chunk_duration)} of {int(duration)} seconds")
            except Exception:
                raise
            finally:
                chunk_path.unlink(missing_ok=True)
    return words, language


def transcribe_video(
    video: MediaSource,
    duration: float,
    model_size: str,
    device: str,
    progress: Callable[[float, str], None],
) -> tuple[list[dict[str, Any]], str | None, str]:
    if model_size not in MODEL_SIZES:
        raise TranscriptionError(f"Unsupported Whisper model: {model_size}")
    if device not in {"auto", "cpu"}:
        raise TranscriptionError("Transcription device must be 'auto' or 'cpu'.")
    if not is_remote_media_source(video) and not Path(video).is_file():
        raise TranscriptionError(f"Video file does not exist: {video}")
    if duration <= 0:
        raise TranscriptionError("Video duration must be greater than zero.")

    if device == "cpu":
        progress(0, f"Loading {model_size} model on CPU")
        words, language = _transcribe_chunks(video, duration, model_size, "cpu", progress)
        return words, language, "cpu"

    progress(0, f"Loading {model_size} model on GPU (CPU fallback enabled)")
    try:
        words, language = _transcribe_chunks(video, duration, model_size, "cuda", progress)
        return words, language, "cuda"
    except Exception as exc:
        if not _gpu_error(exc):
            raise
        LOGGER.warning("CUDA transcription failed; retrying from the beginning on CPU: %s", exc)
        progress(0, f"GPU unavailable; retrying {model_size} model on CPU")
        words, language = _transcribe_chunks(video, duration, model_size, "cpu", progress)
        return words, language, "cpu"
