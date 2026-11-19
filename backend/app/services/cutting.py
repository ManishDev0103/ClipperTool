import subprocess
from pathlib import Path
from typing import Callable

from app.config import FFMPEG_BIN
from app.services.media_source import MediaSource, is_remote_media_source


class ClipCutError(RuntimeError):
    """Raised when clip arguments are invalid or ffmpeg fails."""


def cut_clip(
    source: MediaSource,
    start_time: float,
    end_time: float,
    output: Path,
    mode: str = "accurate",
    progress_callback: Callable[[float], None] | None = None,
    ffmpeg_bin: str = FFMPEG_BIN,
) -> None:
    if not is_remote_media_source(source) and not Path(source).is_file():
        raise ClipCutError(f"Video file does not exist: {source}")
    if start_time < 0:
        raise ClipCutError("Start time must be zero or greater.")
    if end_time <= start_time:
        raise ClipCutError("End time must be greater than start time.")
    if mode not in {"copy", "accurate"}:
        raise ClipCutError("Mode must be 'copy' or 'accurate'.")

    output.parent.mkdir(parents=True, exist_ok=True)
    duration = end_time - start_time
    command = [ffmpeg_bin, "-y", "-nostats", "-loglevel", "error"]
    if mode == "copy":
        command.extend(["-ss", str(start_time), "-i", str(source), "-t", str(duration), "-map", "0", "-c", "copy"])
    else:
        command.extend(
            [
                "-i",
                str(source),
                "-ss",
                str(start_time),
                "-t",
                str(duration),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
            ]
        )
    command.extend(["-progress", "pipe:1", str(output)])

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            key, separator, value = line.strip().partition("=")
            if separator and key == "out_time_ms" and progress_callback is not None:
                try:
                    elapsed = int(value) / 1_000_000
                    progress_callback(min(99.0, elapsed / duration * 100))
                except ValueError:
                    continue
        stderr = process.stderr.read().strip() if process.stderr is not None else ""
        return_code = process.wait()
    except OSError as exc:
        raise ClipCutError(f"Could not start ffmpeg: {exc}") from exc

    if return_code != 0:
        raise ClipCutError(f"ffmpeg could not create the clip: {stderr or 'unknown error'}")
    if not output.is_file() or output.stat().st_size == 0:
        raise ClipCutError("ffmpeg finished without creating a non-empty clip file.")
    if progress_callback is not None:
        progress_callback(100.0)
