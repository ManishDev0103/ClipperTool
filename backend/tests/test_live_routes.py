import json
from pathlib import Path

import pytest

import app.config as config
import app.db as db
import app.routes.live as live_routes
import app.services.worker as worker
from app.schemas import LiveClipCreate, LiveRecordingCreate


@pytest.fixture
def live_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "clipper.sqlite3")
    monkeypatch.setattr(live_routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(worker, "DATA_DIR", tmp_path)
    db.initialize_db()
    with db.connect() as connection:
        connection.execute("INSERT INTO projects (id, name) VALUES ('project', 'Live')")
    return "project", tmp_path


def test_live_recording_creation_and_clip_request_queue_job(
    live_database: tuple[str, Path],
) -> None:
    project_id, _data_dir = live_database
    result = live_routes.start_live_recording(
        project_id,
        LiveRecordingCreate(
            url="https://www.twitch.tv/example",
            buffer_minutes=12,
            disk_cap_gb=3,
            audio_trigger_enabled=True,
        ),
    )
    queued = live_routes.clip_live_buffer(
        result["recording_id"],
        LiveClipCreate(seconds=30),
    )

    with db.connect() as connection:
        recording = connection.execute(
            "SELECT * FROM live_recordings WHERE id = ?",
            (result["recording_id"],),
        ).fetchone()
        job = connection.execute("SELECT kind, payload FROM jobs WHERE id = ?", (queued["job_id"],)).fetchone()

    assert recording["status"] == "starting"
    assert recording["buffer_seconds"] == 720
    assert recording["audio_trigger_enabled"] == 1
    assert job["kind"] == "live_clip"
    assert json.loads(job["payload"]) == {
        "recording_id": result["recording_id"],
        "seconds": 30,
        "candidate_id": None,
        "candidate_at": None,
    }


def test_live_clip_worker_creates_editable_video_and_links_candidate(
    live_database: tuple[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _data_dir = live_database
    recording_id = live_routes.start_live_recording(
        project_id,
        LiveRecordingCreate(url="https://www.youtube.com/watch?v=example"),
    )["recording_id"]
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO live_candidates (id, recording_id, project_id, reason) "
            "VALUES ('candidate', ?, ?, 'Audio peak')",
            (recording_id, project_id),
        )

    def fake_cut(_segments, _seconds: int, output: Path, **_kwargs: object) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"ready")

    monkeypatch.setattr(worker, "list_buffer_segments", lambda _path: [])
    monkeypatch.setattr(worker, "cut_last_n", fake_cut)
    monkeypatch.setattr(
        worker,
        "probe_media",
        lambda _path: {"duration": 30.0, "width": 1920, "height": 1080, "fps": 30.0},
    )

    worker._process_live_clip(
        {"id": "job", "project_id": project_id},
        {
            "recording_id": recording_id,
            "seconds": 30,
            "candidate_id": "candidate",
            "candidate_at": None,
        },
    )

    with db.connect() as connection:
        video = connection.execute("SELECT * FROM videos WHERE source_url IS NOT NULL").fetchone()
        candidate = connection.execute(
            "SELECT video_id FROM live_candidates WHERE id = 'candidate'"
        ).fetchone()
    assert video is not None
    assert video["duration"] == 30
    assert Path(video["file_path"]).read_bytes() == b"ready"
    assert candidate["video_id"] == video["id"]
