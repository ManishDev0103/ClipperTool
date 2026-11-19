import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.config import FFPROBE_BIN


class MediaProbeError(RuntimeError):
    """Raised when ffprobe cannot read a media file."""


def probe_media(path: Path) -> dict[str, Any]:
    command = [
        FFPROBE_BIN,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        metadata = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise MediaProbeError(f"Could not read media metadata: {detail.strip()}") from exc

    video_stream = next(
        (stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        raise MediaProbeError("The uploaded file does not contain a video stream.")

    rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/1"
    try:
        fps = float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        fps = None

    try:
        duration = float(metadata.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        raise MediaProbeError("Could not determine a valid video duration.")

    return {
        "duration": duration,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "fps": fps,
        "has_audio": any(
            stream.get("codec_type") == "audio"
            for stream in metadata.get("streams", [])
        ),
    }
