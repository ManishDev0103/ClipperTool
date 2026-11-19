import pytest

from app.services.reframing import build_crop_plan, compute_crop_rect, smooth_face_track


def test_portrait_crop_tracks_face_and_stays_inside_source() -> None:
    crop = compute_crop_rect(
        source_width=1920,
        source_height=1080,
        target_width=1080,
        target_height=1920,
        center_x=0.78,
        center_y=0.5,
    )

    assert crop["height"] == 1080
    assert crop["width"] < 1920
    assert 0 <= crop["x"] <= 1920 - crop["width"]
    assert crop["y"] == 0


def test_landscape_crop_reframes_vertically_without_exceeding_source() -> None:
    crop = compute_crop_rect(
        source_width=1080,
        source_height=1920,
        target_width=1920,
        target_height=1080,
        center_x=0.5,
        center_y=0.8,
    )

    assert crop["width"] == 1080
    assert crop["height"] < 1920
    assert 0 <= crop["y"] <= 1920 - crop["height"]


def test_face_track_smoothing_reduces_camera_jumps() -> None:
    track = [
        {"time": 0.0, "x": 0.5, "y": 0.5},
        {"time": 0.5, "x": 1.0, "y": 0.5},
    ]

    smoothed = smooth_face_track(track, smoothing=0.25)

    assert smoothed[1]["x"] == pytest.approx(0.625)
    assert all(0 <= point["x"] <= 1 and 0 <= point["y"] <= 1 for point in smoothed)


def test_crop_plan_builds_bounded_time_varying_crop_expressions() -> None:
    plan = build_crop_plan(
        source_width=1920,
        source_height=1080,
        target_width=1080,
        target_height=1920,
        duration=4.0,
        face_track=[
            {"time": 0.0, "x": 0.25, "y": 0.5},
            {"time": 2.0, "x": 0.75, "y": 0.5},
            {"time": 4.0, "x": 0.5, "y": 0.5},
        ],
    )

    assert plan["width"] < 1920
    assert "if(lt(t\\," in plan["x"]
    assert "if(gt(t\\,4.000)" in plan["x"]
    assert plan["y"]


def test_invalid_dimensions_are_rejected() -> None:
    with pytest.raises(ValueError, match="[Dd]imensions must be positive"):
        compute_crop_rect(0, 1080, 1080, 1920)
