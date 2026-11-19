import logging
import math
import subprocess
from pathlib import Path
from typing import Any, Callable

from app.config import FFPROBE_BIN, FFMPEG_BIN

LOGGER = logging.getLogger("clipper.audio-analysis")
SAMPLE_RATE = 16000


def analyze_audio(
    video: Path,
    duration: float,
    progress: Callable[[float, str], None],
    gaming_mode: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        import numpy as np
    except ImportError as exc:
        return [], [f"Audio signal analysis needs NumPy: {exc}"]
    try:
        probe = subprocess.run(
            [
                FFPROBE_BIN,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        return [], [f"Audio stream probe failed: {getattr(exc, 'stderr', None) or exc}"]
    if not probe.stdout.strip():
        progress(100, "No audio stream found")
        return [], ["No audio stream was found; audio signals were skipped."]

    command = [
        FFMPEG_BIN,
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(video),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "f32le",
        "pipe:1",
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdout is not None
        windows: list[dict[str, float]] = []
        time_index = 0
        while raw := process.stdout.read(SAMPLE_RATE * 4):
            samples = np.frombuffer(raw[: len(raw) - len(raw) % 4], dtype="<f4")
            if not samples.size:
                continue
            rms = float(np.sqrt(np.mean(samples * samples)))
            peak = float(np.max(np.abs(samples)))
            crossings = float(np.mean(samples[1:] * samples[:-1] < 0)) if len(samples) > 1 else 0.0
            subframes = np.array_split(samples, 10)
            levels = np.asarray(
                [float(np.sqrt(np.mean(frame * frame))) if frame.size else 0.0 for frame in subframes]
            )
            median_level = float(np.median(levels))
            burst_count = int(np.count_nonzero(levels > max(0.012, median_level * 1.8)))
            windows.append(
                {
                    "time": float(time_index),
                    "db": 20 * math.log10(max(rms, 1e-8)),
                    "peak": peak,
                    "zcr": crossings,
                    "bursts": float(burst_count),
                }
            )
            time_index += 1
            if time_index % 10 == 0:
                progress(min(100.0, time_index / duration * 100), f"Analyzed audio through {time_index} seconds")
        stderr = process.stderr.read().decode("utf-8", errors="replace").strip() if process.stderr else ""
        exit_code = process.wait()
    except OSError as exc:
        return [], [f"Audio analysis could not start ffmpeg: {exc}"]
    if exit_code:
        return [], [f"Audio analysis failed: {stderr or 'ffmpeg could not decode the audio stream.'}"]
    if not windows:
        return [], ["Audio stream contained no decodable samples."]

    active_levels = [item["db"] for item in windows if item["db"] > -55]
    median_db = float(np.median(active_levels)) if active_levels else -55.0
    loudness_threshold = max(-24.0, median_db + 7.0)
    peak_threshold = 0.72 if gaming_mode else 0.82
    events: list[dict[str, Any]] = []
    for window in windows:
        loudness_delta = max(0.0, window["db"] - loudness_threshold)
        if window["db"] >= loudness_threshold or (window["peak"] >= peak_threshold and window["db"] > -38):
            score = min(1.0, 0.58 + loudness_delta / 24 + max(0.0, window["peak"] - peak_threshold) * 0.5)
            events.append(
                {
                    "start_time": window["time"],
                    "end_time": min(duration, window["time"] + 1),
                    "signal": "audio",
                    "score": score,
                    "category": "audio_peak",
                    "title": "Audio energy spike",
                    "reason": "A local loudness/peak spike suggests a high-energy moment.",
                }
            )

        burst_threshold = 0.10 if gaming_mode else 0.14
        if window["zcr"] >= burst_threshold and window["bursts"] >= 4 and window["db"] > median_db + 1:
            events.append(
                {
                    "start_time": window["time"],
                    "end_time": min(duration, window["time"] + 1),
                    "signal": "audio",
                    "score": min(0.9, 0.55 + window["bursts"] * 0.04),
                    "category": "laughter_or_excitement",
                    "title": "Laughter or excitement-like audio",
                    "reason": "Rapid energetic audio bursts can indicate laughter, shouting, or excitement.",
                }
            )
    progress(100, f"Audio analysis complete ({len(events)} candidate signals)")
    return events, []
