import logging
from pathlib import Path
from typing import Any, Callable

LOGGER = logging.getLogger("clipper.visual-analysis")


def analyze_visuals(
    video: Path,
    duration: float,
    progress: Callable[[float, str], None],
) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        from scenedetect import ContentDetector, SceneManager, open_video

        video_stream = open_video(str(video))
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=27.0, min_scene_len=15))
        scene_manager.detect_scenes(video_stream, show_progress=False)
        for scene_start, _scene_end in scene_manager.get_scene_list():
            timestamp = float(scene_start.get_seconds())
            if timestamp > 0:
                events.append(
                    {
                        "start_time": max(0.0, timestamp - 1),
                        "end_time": min(duration, timestamp + 1),
                        "signal": "scene",
                        "score": 0.72,
                        "category": "scene_change",
                        "title": "Scene change",
                        "reason": "A visual scene transition creates a natural edit point.",
                    }
                )
    except ImportError as exc:
        warnings.append(f"Scene detection is unavailable: {exc}")
    except Exception as exc:
        LOGGER.warning("Scene detection failed for %s: %s", video, exc)
        warnings.append(f"Scene detection failed: {exc}")
    progress(45, f"Scene analysis complete ({sum(event['signal'] == 'scene' for event in events)} changes)")

    try:
        import cv2
        import numpy as np

        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            raise RuntimeError("OpenCV could not open the video.")
        sample_interval = 1.0
        samples: list[tuple[float, float]] = []
        previous_frame = None
        timestamp = 0.0
        try:
            while timestamp < duration:
                capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
                ok, frame = capture.read()
                if not ok:
                    break
                resized = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
                gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
                if previous_frame is not None:
                    intensity = float(np.mean(cv2.absdiff(gray, previous_frame))) / 255.0
                    samples.append((timestamp, intensity))
                previous_frame = gray
                if int(timestamp) % 10 == 0:
                    progress(
                        45 + min(54.0, timestamp / duration * 54),
                        f"Measured visual motion at {int(timestamp)} of {int(duration)} seconds",
                    )
                timestamp += sample_interval
        finally:
            capture.release()

        if samples:
            intensities = np.asarray([sample[1] for sample in samples])
            threshold = float(np.percentile(intensities, 80))
            scale = max(float(np.percentile(intensities, 95)), 1e-4)
            for index, (sample_time, intensity) in enumerate(samples):
                previous_value = samples[index - 1][1] if index else -1.0
                next_value = samples[index + 1][1] if index + 1 < len(samples) else -1.0
                if intensity >= threshold and intensity >= previous_value and intensity >= next_value:
                    events.append(
                        {
                            "start_time": max(0.0, sample_time - 1),
                            "end_time": min(duration, sample_time + 1),
                            "signal": "motion",
                            "score": min(1.0, intensity / scale),
                            "category": "motion_peak",
                            "title": "High-motion moment",
                            "reason": "Frame-to-frame visual motion is unusually intense.",
                        }
                    )
    except ImportError as exc:
        warnings.append(f"Motion analysis is unavailable: {exc}")
    except Exception as exc:
        LOGGER.warning("Motion analysis failed for %s: %s", video, exc)
        warnings.append(f"Motion analysis failed: {exc}")
    progress(100, f"Visual analysis complete ({sum(event['signal'] == 'motion' for event in events)} motion peaks)")
    return events, warnings
