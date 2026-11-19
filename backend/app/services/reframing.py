import math
from pathlib import Path
from typing import Any


class ReframeError(RuntimeError):
    """Raised when local face tracking cannot be completed."""


def compute_crop_rect(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    center_x: float = 0.5,
    center_y: float = 0.5,
) -> dict[str, int]:
    if min(source_width, source_height, target_width, target_height) <= 0:
        raise ValueError("Source and target dimensions must be positive.")
    target_ratio = target_width / target_height
    source_ratio = source_width / source_height
    if source_ratio > target_ratio:
        crop_height = source_height
        crop_width = max(1, min(source_width, round(crop_height * target_ratio)))
    else:
        crop_width = source_width
        crop_height = max(1, min(source_height, round(crop_width / target_ratio)))
    x = round(center_x * source_width - crop_width / 2)
    y = round(center_y * source_height - crop_height / 2)
    return {
        "width": crop_width,
        "height": crop_height,
        "x": max(0, min(source_width - crop_width, x)),
        "y": max(0, min(source_height - crop_height, y)),
    }


def smooth_face_track(
    points: list[dict[str, float]],
    smoothing: float = 0.35,
) -> list[dict[str, float]]:
    if not 0 < smoothing <= 1:
        raise ValueError("Smoothing must be greater than zero and at most one.")
    if not points:
        return []
    smoothed: list[dict[str, float]] = []
    last_x = min(1.0, max(0.0, points[0]["x"]))
    last_y = min(1.0, max(0.0, points[0]["y"]))
    for point in points:
        current_x = min(1.0, max(0.0, point["x"]))
        current_y = min(1.0, max(0.0, point["y"]))
        last_x += smoothing * (current_x - last_x)
        last_y += smoothing * (current_y - last_y)
        smoothed.append({"time": max(0.0, point["time"]), "x": last_x, "y": last_y})
    return smoothed


def _linear_expression(left: dict[str, float], right: dict[str, float], values: list[float]) -> str:
    start, end = left["time"], right["time"]
    first, last = values[0], values[-1]
    duration = end - start
    if duration <= 0:
        return f"{last:.3f}"
    return f"({first:.3f}+({last - first:.3f})*(t-{start:.3f})/{duration:.3f})"


def _piecewise_expression(points: list[dict[str, float]], values: list[float]) -> str:
    if len(points) == 2:
        return _linear_expression(points[0], points[1], values)
    middle = len(points) // 2
    left = _piecewise_expression(points[: middle + 1], values[: middle + 1])
    right = _piecewise_expression(points[middle:], values[middle:])
    return f"if(lt(t\\,{points[middle]['time']:.3f})\\,{left}\\,{right})"


def build_crop_plan(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    duration: float,
    face_track: list[dict[str, float]],
) -> dict[str, Any]:
    if duration <= 0:
        raise ValueError("Video duration must be positive.")
    track = smooth_face_track(face_track)
    if not track:
        track = [{"time": 0.0, "x": 0.5, "y": 0.5}]
    if len(track) == 1:
        track = [
            {"time": 0.0, "x": track[0]["x"], "y": track[0]["y"]},
            {"time": duration, "x": track[0]["x"], "y": track[0]["y"]},
        ]
    else:
        track[0] = {**track[0], "time": 0.0}
        track[-1] = {**track[-1], "time": duration}
    track = sorted(track, key=lambda point: point["time"])
    crop = compute_crop_rect(source_width, source_height, target_width, target_height)
    max_x = source_width - crop["width"]
    max_y = source_height - crop["height"]
    x_values = [
        max(0.0, min(float(max_x), point["x"] * source_width - crop["width"] / 2))
        for point in track
    ]
    y_values = [
        max(0.0, min(float(max_y), point["y"] * source_height - crop["height"] / 2))
        for point in track
    ]
    if len(track) == 2 and track[0]["time"] == track[1]["time"]:
        track[1] = {**track[1], "time": duration}
    x_expr = _piecewise_expression(track, x_values)
    y_expr = _piecewise_expression(track, y_values)
    return {
        "width": crop["width"],
        "height": crop["height"],
        "x": f"if(lt(t\\,0)\\,{x_values[0]:.3f}\\,if(gt(t\\,{duration:.3f})\\,{x_values[-1]:.3f}\\,{x_expr}))",
        "y": f"if(lt(t\\,0)\\,{y_values[0]:.3f}\\,if(gt(t\\,{duration:.3f})\\,{y_values[-1]:.3f}\\,{y_expr}))",
    }


def detect_face_track(
    source: Path,
    duration: float,
    start_offset: float = 0.0,
    sample_limit: int = 240,
) -> list[dict[str, float]]:
    if duration <= 0 or start_offset < 0:
        raise ValueError("Face tracking requires a positive duration and non-negative start time.")
    try:
        import cv2
        import mediapipe as mp
    except (ImportError, OSError) as exc:
        raise ReframeError(
            "Smart reframe requires the local MediaPipe and OpenCV dependencies. "
            "Install the backend requirements and restart the worker."
        ) from exc

    try:
        capture = cv2.VideoCapture(str(source))
    except cv2.error as exc:
        raise ReframeError(f"Could not open video for face tracking: {exc}") from exc
    if not capture.isOpened():
        raise ReframeError(f"Could not open video for face tracking: {source}")
    sample_count = min(max(2, math.ceil(duration * 2) + 1), max(2, sample_limit))
    last_center: tuple[float, float] | None = None
    points: list[dict[str, float]] = []
    try:
        with mp.solutions.face_detection.FaceDetection(
            model_selection=0,
            min_detection_confidence=0.5,
        ) as detector:
            for index in range(sample_count):
                time = duration * index / (sample_count - 1)
                capture.set(cv2.CAP_PROP_POS_MSEC, (start_offset + time) * 1000)
                success, frame = capture.read()
                center = last_center or (0.5, 0.5)
                if success:
                    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    result = detector.process(rgb_frame)
                    detections = result.detections or []
                    boxes: list[tuple[float, float, float, float]] = []
                    for detection in detections:
                        bounds = detection.location_data.relative_bounding_box
                        x = min(1.0, max(0.0, bounds.xmin + bounds.width / 2))
                        y = min(1.0, max(0.0, bounds.ymin + bounds.height / 2))
                        confidence = float(detection.score[0]) if detection.score else 0.0
                        proximity = math.dist(last_center, (x, y)) if last_center else 0.0
                        boxes.append((confidence - proximity * 0.25, x, y, bounds.width * bounds.height))
                    if boxes:
                        _, x, y, _area = max(boxes, key=lambda item: (item[0], item[3]))
                        center = (x, y)
                        last_center = center
                points.append({"time": time, "x": center[0], "y": center[1]})
    except (cv2.error, AttributeError, RuntimeError, ValueError) as exc:
        raise ReframeError(f"MediaPipe face tracking failed: {exc}") from exc
    finally:
        capture.release()
    return smooth_face_track(points)
