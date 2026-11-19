import csv
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app.config import FFMPEG_BIN


class LiveBufferError(RuntimeError):
    """Raised when the rolling buffer cannot satisfy a clip request."""


@dataclass(frozen=True)
class BufferedSegment:
    path: Path
    duration: float
    size_bytes: int
    modified_at: float


def list_buffer_segments(root: Path) -> list[BufferedSegment]:
    segments: list[BufferedSegment] = []
    if not root.is_dir():
        return segments
    for listing in root.glob("*/segments.csv"):
        session_dir = listing.parent.resolve()
        try:
            with listing.open("r", encoding="utf-8", newline="") as stream:
                for row in csv.reader(stream):
                    if len(row) < 3:
                        continue
                    listed_path = Path(row[0])
                    segment_path = listed_path if listed_path.is_absolute() else session_dir / listed_path
                    segment_path = segment_path.resolve()
                    if not segment_path.is_relative_to(session_dir) or not segment_path.is_file():
                        continue
                    try:
                        duration = float(row[2]) - float(row[1])
                    except (TypeError, ValueError):
                        continue
                    if duration <= 0:
                        continue
                    stats = segment_path.stat()
                    segments.append(
                        BufferedSegment(segment_path, duration, stats.st_size, stats.st_mtime)
                    )
        except (OSError, UnicodeError, csv.Error):
            continue
    return sorted(segments, key=lambda item: item.modified_at)


def select_last_segments(
    segments: Sequence[BufferedSegment],
    seconds: float,
    end_before: float | None = None,
) -> tuple[list[BufferedSegment], float]:
    if seconds <= 0:
        raise LiveBufferError("Clip duration must be greater than zero.")
    selected: list[BufferedSegment] = []
    total = 0.0
    eligible = [
        segment
        for segment in segments
        if end_before is None or segment.modified_at <= end_before
    ]
    for segment in reversed(sorted(eligible, key=lambda item: item.modified_at)):
        selected.append(segment)
        total += segment.duration
        if total >= seconds:
            break
    if total < seconds:
        available = max(0, math.floor(total))
        raise LiveBufferError(
            f"Not enough buffered video yet. Available buffer: {available} seconds."
        )
    selected.reverse()
    return selected, total


def prune_segments(
    segments: Sequence[BufferedSegment],
    buffer_seconds: int,
    disk_cap_bytes: int,
) -> tuple[list[BufferedSegment], int, float]:
    retained: list[BufferedSegment] = []
    retained_bytes = 0
    retained_duration = 0.0
    for segment in reversed(sorted(segments, key=lambda item: item.modified_at)):
        exceeds_limit = retained_bytes + segment.size_bytes > disk_cap_bytes or (
            retained and retained_duration + segment.duration > buffer_seconds
        )
        if exceeds_limit:
            segment.path.unlink(missing_ok=True)
            continue
        retained.append(segment)
        retained_bytes += segment.size_bytes
        retained_duration += segment.duration
    retained.reverse()
    return retained, retained_bytes, retained_duration


def cut_last_n(
    segments: Sequence[BufferedSegment],
    seconds: int,
    output: Path,
    ffmpeg_bin: str = FFMPEG_BIN,
    end_before: float | None = None,
) -> None:
    selected, total_duration = select_last_segments(segments, seconds, end_before)
    output.parent.mkdir(parents=True, exist_ok=True)
    concat_path: Path | None = None
    succeeded = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            suffix=".txt",
            prefix="live-concat-",
            dir=output.parent,
            delete=False,
        ) as concat_file:
            concat_path = Path(concat_file.name)
            for segment in selected:
                escaped_path = str(segment.path.resolve()).replace("\\", "/").replace("'", "'\\''")
                concat_file.write(f"file '{escaped_path}'\n")
        command = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-ss",
            f"{max(0.0, total_duration - seconds):.3f}",
            "-t",
            str(seconds),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise LiveBufferError(f"Could not start ffmpeg: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or "unknown ffmpeg error"
            raise LiveBufferError(f"Could not cut the live buffer: {detail}")
        if not output.is_file() or output.stat().st_size == 0:
            raise LiveBufferError("ffmpeg finished without creating a non-empty live clip.")
        succeeded = True
    finally:
        if concat_path is not None:
            concat_path.unlink(missing_ok=True)
        if not succeeded:
            output.unlink(missing_ok=True)
