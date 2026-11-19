import logging
import subprocess
from pathlib import Path
from typing import Any, Callable

from app.config import FFMPEG_BIN
from app.services.audio_analysis import analyze_audio
from app.services.highlights import (
    DEFAULT_WEIGHTS,
    GAMING_WEIGHTS,
    select_suggestions,
    transcript_events,
)
from app.services.ollama import OllamaError, analyze_transcript
from app.services.visual_analysis import analyze_visuals

LOGGER = logging.getLogger("clipper.highlights")


def analyze_video_highlights(
    video_path: Path,
    duration: float,
    words: list[dict[str, Any]],
    settings: dict[str, Any],
    progress: Callable[[float, str], None],
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    heuristic_events = transcript_events(words)
    events.extend(heuristic_events)
    model = str(settings.get("ollama_model") or "")
    analysis_mode = "local_heuristics"
    if words:
        progress(5, "Analyzing transcript signals")
        try:
            local_events = analyze_transcript(
                words,
                model,
                lambda value, message: progress(5 + value * 0.2, message),
            )
            if local_events:
                events.extend(local_events)
            analysis_mode = "ollama" if local_events else "local_heuristics"
        except OllamaError as exc:
            LOGGER.info("Local Ollama unavailable; using transcript heuristics: %s", exc)
            warnings.append(f"Ollama unavailable; transcript keyword heuristics were used: {exc}")
    else:
        warnings.append("No transcript is available; ranking uses audio and visual signals.")

    try:
        audio_events, audio_warnings = analyze_audio(
            video_path,
            duration,
            lambda value, message: progress(25 + value * 0.25, message),
            bool(settings.get("gaming_mode")),
        )
        events.extend(audio_events)
        warnings.extend(audio_warnings)
    except Exception as exc:
        LOGGER.exception("Audio analysis failed for %s", video_path)
        warnings.append(f"Audio analysis failed: {exc}")

    try:
        visual_events, visual_warnings = analyze_visuals(
            video_path,
            duration,
            lambda value, message: progress(50 + value * 0.35, message),
        )
        events.extend(visual_events)
        warnings.extend(visual_warnings)
    except Exception as exc:
        LOGGER.exception("Visual analysis failed for %s", video_path)
        warnings.append(f"Visual analysis failed: {exc}")

    if settings.get("weights") is not None:
        weights = settings["weights"]
    else:
        weights = GAMING_WEIGHTS if settings.get("gaming_mode") else DEFAULT_WEIGHTS
    progress(88, "Scoring and ranking clip candidates")
    suggestions = select_suggestions(
        events,
        duration,
        float(settings["min_duration"]),
        float(settings["max_duration"]),
        int(settings["number_of_clips"]),
        bool(settings["allow_overlap"]),
        weights,
        bool(settings.get("gaming_mode")),
    )
    if not suggestions:
        warnings.append("No segments met the requested duration and signal thresholds.")
    progress(95, f"Highlight ranking complete ({len(suggestions)} suggestions)")
    return {
        "suggestions": suggestions,
        "analysis_mode": analysis_mode,
        "warnings": list(dict.fromkeys(warnings)),
        "settings": {**settings, "weights": weights},
    }


def create_thumbnail(video_path: Path, output_path: Path, timestamp: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        FFMPEG_BIN,
        "-y",
        "-nostdin",
        "-v",
        "error",
        "-ss",
        str(max(0.0, timestamp)),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2",
        "-q:v",
        "6",
        str(output_path),
    ]
    try:
        subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        output_path.unlink(missing_ok=True)
        detail = getattr(exc, "stderr", None) or str(exc)
        raise RuntimeError(f"Could not create suggestion thumbnail: {detail.strip()}") from exc
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("ffmpeg did not create a non-empty suggestion thumbnail.")
