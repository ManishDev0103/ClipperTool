import json
import math
import subprocess
import sys
import tempfile
from array import array
from pathlib import Path
from uuid import uuid4

from app.config import FFMPEG_BIN
from app.services.media_source import MediaSource, is_remote_media_source

SAMPLE_RATE = 1000
MAX_PEAKS = 12000


class WaveformError(RuntimeError):
    """Raised when an audio waveform cannot be generated."""


def build_waveform_peaks(source: MediaSource, duration: float) -> list[float]:
    if not is_remote_media_source(source) and not Path(source).is_file():
        raise WaveformError("The source video is missing from local storage.")
    if duration <= 0:
        raise WaveformError("A positive video duration is required to build its waveform.")
    sample_count = max(1, round(duration * SAMPLE_RATE))
    peak_count = max(1, min(MAX_PEAKS, math.ceil(duration * 8)))
    maxima = [0] * peak_count
    sample_offset = 0
    command = [
        FFMPEG_BIN,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
            if process.stdout is None:
                raise WaveformError("Could not read audio samples from ffmpeg.")
            while True:
                chunk = process.stdout.read(128 * 1024)
                if not chunk:
                    break
                if len(chunk) % 2:
                    chunk = chunk[:-1]
                samples = array("h")
                samples.frombytes(chunk)
                if sys.byteorder == "big":
                    samples.byteswap()
                for index, sample in enumerate(samples):
                    peak_index = min(
                        ((sample_offset + index) * peak_count) // sample_count,
                        peak_count - 1,
                    )
                    amplitude = abs(sample)
                    if amplitude > maxima[peak_index]:
                        maxima[peak_index] = amplitude
                sample_offset += len(samples)
            return_code = process.wait()
            if return_code:
                errors.seek(0)
                detail = errors.read().decode("utf-8", errors="replace").strip()
                raise WaveformError(detail or f"ffmpeg exited with code {return_code}.")
    except OSError as exc:
        raise WaveformError(f"Could not start ffmpeg: {exc}") from exc
    if sample_offset == 0:
        raise WaveformError("The source video does not contain a readable audio track.")
    return [round(value / 32768, 5) for value in maxima]


def load_or_create_waveform(
    source: MediaSource,
    duration: float,
    cache_path: Path,
) -> list[float]:
    if isinstance(source, Path):
        stat = source.stat()
        fingerprint = {"size": stat.st_size, "modified": stat.st_mtime_ns}
    elif is_remote_media_source(source):
        fingerprint = {"url": source}
    else:
        raise WaveformError("The waveform source must be a local file or HTTP stream.")
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("source") == fingerprint and cached.get("duration") == duration:
            peaks = cached.get("peaks")
            if isinstance(peaks, list) and peaks:
                return [float(peak) for peak in peaks]
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        pass

    peaks = build_waveform_peaks(source, duration)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_name(f".{cache_path.name}.{uuid4().hex}.part")
    try:
        temporary_path.write_text(
            json.dumps({"source": fingerprint, "duration": duration, "peaks": peaks}),
            encoding="utf-8",
        )
        temporary_path.replace(cache_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return peaks
