import json
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.config as config
import app.db as db
import app.services.waveform as waveform
from app.routes.clips import queue_clip_batch
from app.routes.editor import create_layout_preset, delete_layout_preset, list_layout_presets
from app.schemas import ClipBatchCreate, ExportCreate, LayoutPresetCreate


@pytest.fixture
def timeline_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "clipper.sqlite3")
    db.initialize_db()
    with db.connect() as connection:
        connection.execute("INSERT INTO projects (id, name) VALUES ('project', 'Timeline')")
        connection.execute(
            "INSERT INTO videos (id, project_id, title, file_path, duration) "
            "VALUES ('video', 'project', 'Source', 'source.mp4', 100)"
        )
    return "project", "video"


def test_clip_batch_queues_non_overlapping_segments_in_timeline_order(
    timeline_database: tuple[str, str],
) -> None:
    _project_id, video_id = timeline_database
    result = queue_clip_batch(
        video_id,
        ClipBatchCreate(
            segments=[
                {"start_time": 30, "end_time": 40},
                {"start_time": 5, "end_time": 15},
            ]
        ),
    )

    with db.connect() as connection:
        jobs = connection.execute("SELECT payload FROM jobs ORDER BY rowid").fetchall()
    payloads = [json.loads(row["payload"]) for row in jobs]
    assert len(result["job_ids"]) == len(payloads) == 2
    assert [(item["start_time"], item["end_time"]) for item in payloads] == [(5, 15), (30, 40)]


def test_batch_and_export_schemas_reject_invalid_ranges_and_support_all_ratios() -> None:
    with pytest.raises(ValueError, match="cannot overlap"):
        ClipBatchCreate(segments=[
            {"start_time": 0, "end_time": 10},
            {"start_time": 9, "end_time": 20},
        ])

    assert ExportCreate(aspect_ratio="1:1").width == 1080
    assert (ExportCreate(aspect_ratio="4:5").width, ExportCreate(aspect_ratio="4:5").height) == (1080, 1350)
    assert (ExportCreate(aspect_ratio="16:9").width, ExportCreate(aspect_ratio="16:9").height) == (1920, 1080)
    with pytest.raises(ValueError, match="both the facecam and gameplay regions"):
        ExportCreate(layout="gaming_split")


def test_layout_presets_are_saved_listed_and_deleted(
    timeline_database: tuple[str, str],
) -> None:
    project_id, _video_id = timeline_database
    request = LayoutPresetCreate(
        name="Streamer",
        aspect_ratio="9:16",
        facecam_region={"x": 0.6, "y": 0, "width": 0.4, "height": 0.3},
        gameplay_region={"x": 0, "y": 0.2, "width": 1, "height": 0.8},
    )

    created = create_layout_preset(project_id, request)
    assert list_layout_presets(project_id)[0]["facecam_region"] == request.facecam_region.model_dump()
    with pytest.raises(HTTPException) as duplicate:
        create_layout_preset(project_id, request)
    assert duplicate.value.status_code == 409

    delete_layout_preset(created["id"])
    assert list_layout_presets(project_id) == []


def test_waveform_peaks_are_streamed_and_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.touch()

    class Process:
        stdout = BytesIO(b"\x00\x80\xff\x7f\x00\x40")

        @staticmethod
        def wait() -> int:
            return 0

    calls = 0

    def fake_popen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return Process()

    monkeypatch.setattr(waveform.subprocess, "Popen", fake_popen)

    cache = tmp_path / "waveforms" / "video.json"
    peaks = waveform.load_or_create_waveform(source, duration=0.001, cache_path=cache)
    cached_peaks = waveform.load_or_create_waveform(source, duration=0.001, cache_path=cache)

    assert len(peaks) == 1
    assert peaks[0] == pytest.approx(1.0)
    assert cached_peaks == peaks
    assert calls == 1
