import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.config as config
import app.db as db
import app.routes.maintenance as maintenance_routes
import app.routes.projects as project_routes
import app.routes.settings as settings_routes
import app.routes.transcriptions as transcription_routes
import app.routes.uploads as uploads_routes
import app.services.worker as worker
from app.schemas import SettingsUpdate, TranscriptionCreate


@pytest.fixture
def phase_six_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "clipper.sqlite3")
    monkeypatch.setattr(maintenance_routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(project_routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(uploads_routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(worker, "DATA_DIR", tmp_path)
    db.initialize_db()
    with db.connect() as connection:
        connection.execute("INSERT INTO projects (id, name) VALUES ('project', 'Phase Six')")
    return tmp_path


def _insert_video(video_id: str = "video") -> None:
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO videos (id, project_id, title, file_path, duration) "
            "VALUES (?, 'project', 'Video', 'video.mp4', 60)",
            (video_id,),
        )


def test_download_automatically_queues_highlight_analysis(
    phase_six_data: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = str(uuid4())
    source = phase_six_data / "source.webm"
    source.write_bytes(b"video")
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, payload) "
            "VALUES (?, 'project', 'download', 'running', ?)",
            (job_id, json.dumps({"url": "https://youtube.com/watch?v=test", "quality": "best"})),
        )
    monkeypatch.setattr(
        worker,
        "download_video",
        lambda *_args: (source, {"title": "Downloaded video"}),
    )
    monkeypatch.setattr(
        worker,
        "probe_media",
        lambda _path: {"duration": 120, "width": 1920, "height": 1080, "fps": 30},
    )

    worker._process_download(
        {"id": job_id, "project_id": "project"},
        {"url": "https://youtube.com/watch?v=test", "quality": "best"},
    )

    with db.connect() as connection:
        video = connection.execute("SELECT id FROM videos WHERE project_id = 'project'").fetchone()
        analysis_job = connection.execute(
            "SELECT payload FROM jobs WHERE kind = 'highlight_analysis' AND project_id = 'project'"
        ).fetchone()
    payload = json.loads(analysis_job["payload"])
    assert payload["video_id"] == video["id"]
    assert payload["auto_create_clips"] is True
    assert payload["settings"]["number_of_clips"] == 8


def test_completed_upload_automatically_queues_highlight_analysis(
    phase_six_data: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_id = str(uuid4())
    upload_path = phase_six_data / "uploads" / f"{upload_id}.part"
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(b"uploaded video")
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO uploads (id, project_id, filename, total_size, received_size, temp_path) "
            "VALUES (?, 'project', 'uploaded.mp4', 14, 14, ?)",
            (upload_id, str(upload_path)),
        )
    monkeypatch.setattr(
        uploads_routes,
        "probe_media",
        lambda _path: {"duration": 120, "width": 1920, "height": 1080, "fps": 30},
    )

    result = uploads_routes.complete_upload(upload_id)

    with db.connect() as connection:
        analysis_job = connection.execute(
            "SELECT payload FROM jobs WHERE kind = 'highlight_analysis' AND project_id = 'project'"
        ).fetchone()
        video = connection.execute(
            "SELECT file_path FROM videos WHERE id = ?",
            (result["video"]["id"],),
        ).fetchone()
    payload = json.loads(analysis_job["payload"])
    assert payload["video_id"] == result["video"]["id"]
    assert payload["auto_create_clips"] is True
    assert Path(video["file_path"]).is_file()
    assert not upload_path.exists()


def test_automatic_highlight_analysis_queues_clip_jobs(
    phase_six_data: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _insert_video()
    job_id = str(uuid4())
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, payload) "
            "VALUES (?, 'project', 'highlight_analysis', 'running', '{}')",
            (job_id,),
        )
    suggestion = {
        "start_time": 10.0,
        "end_time": 40.0,
        "score": 85.0,
        "title": "A highlight",
        "reason": "Strong signals.",
        "signals": {"audio": 0.9},
    }
    monkeypatch.setattr(
        worker,
        "analyze_video_highlights",
        lambda *_args: {
            "suggestions": [dict(suggestion)],
            "warnings": [],
            "analysis_mode": "local_heuristics",
            "settings": {"ollama_model": ""},
        },
    )

    def create_thumbnail(_video_path: Path, thumbnail_path: Path, _timestamp: float) -> None:
        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        thumbnail_path.write_bytes(b"jpeg")

    monkeypatch.setattr(worker, "create_thumbnail", create_thumbnail)

    worker._process_highlights(
        {"id": job_id, "project_id": "project"},
        {"video_id": "video", "settings": {}, "auto_create_clips": True},
    )

    with db.connect() as connection:
        clip_job = connection.execute(
            "SELECT payload FROM jobs WHERE kind = 'clip' AND project_id = 'project'"
        ).fetchone()
        stored_suggestion = connection.execute(
            "SELECT status FROM highlight_suggestions"
        ).fetchone()
        job_message = connection.execute("SELECT message FROM jobs WHERE id = ?", (job_id,)).fetchone()
    clip_payload = json.loads(clip_job["payload"])
    assert clip_payload["video_id"] == "video"
    assert clip_payload["start_time"] == suggestion["start_time"]
    assert clip_payload["suggestion_id"]
    assert stored_suggestion["status"] == "accepted"
    assert "queued 1 clips" in job_message["message"]


def test_application_settings_are_persisted(phase_six_data: Path) -> None:
    result = settings_routes.update_settings(
        SettingsUpdate(
            cookies_file="",
            default_whisper_model="medium",
            default_clip_length=90,
            hotkey="Ctrl+Shift+C",
            gpu_enabled=False,
        )
    )

    assert result["preferences"]["default_whisper_model"] == "medium"
    assert result["preferences"]["default_clip_length"] == 90
    assert result["preferences"]["hotkey"] == "Ctrl+Shift+C"
    assert result["preferences"]["gpu_enabled"] is False
    assert result["cookies_file"] == ""


def test_transcription_queue_records_video_and_reuses_same_model(
    phase_six_data: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _insert_video()
    queued = transcription_routes.queue_transcription("video", TranscriptionCreate(model="tiny"))
    with db.connect() as connection:
        job = connection.execute("SELECT id, payload FROM jobs WHERE id = ?", (queued["job_id"],)).fetchone()
    assert json.loads(job["payload"])["video_id"] == "video"

    with db.connect() as connection:
        connection.execute(
            "INSERT INTO transcripts (video_id, model, language) VALUES ('video', 'tiny', 'en')"
        )
        connection.execute(
            "INSERT INTO transcript_words (video_id, word_index, start_time, end_time, text) "
            "VALUES ('video', 0, 0, 1, 'cached')"
        )
    cached = transcription_routes.queue_transcription("video", TranscriptionCreate(model="tiny"))
    assert cached["already_transcribed"] is True

    monkeypatch.setattr(worker, "transcribe_video", lambda *_args: pytest.fail("unexpected retranscription"))
    worker._process_transcription(
        {"id": queued["job_id"], "project_id": "project"},
        json.loads(job["payload"]),
    )
    with db.connect() as connection:
        status = connection.execute("SELECT message FROM jobs WHERE id = ?", (queued["job_id"],)).fetchone()
    assert status["message"] == "Reused the existing transcript"


def test_transcription_requests_for_a_video_cannot_be_queued_twice(
    phase_six_data: Path,
) -> None:
    _insert_video()
    transcription_routes.queue_transcription("video", TranscriptionCreate(model="tiny"))
    with pytest.raises(HTTPException) as error:
        transcription_routes.queue_transcription("video", TranscriptionCreate(model="small"))
    assert error.value.status_code == 409


def test_batch_export_worker_creates_zip_and_persists_batch_row(
    phase_six_data: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = str(uuid4())
    batch_id = str(uuid4())
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, payload) "
            "VALUES (?, 'project', 'batch_export', 'running', '{}')",
            (job_id,),
        )

    def fake_export(_job, payload, _progress_range, _label):
        output = phase_six_data / "output" / f"{payload['clip_id']}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"encoded")
        output.with_suffix(".json").write_text("{}", encoding="utf-8")
        return output

    monkeypatch.setattr(worker, "_process_export", fake_export)
    worker._process_batch_export(
        {"id": job_id, "project_id": "project"},
        {
            "batch_id": batch_id,
            "clip_ids": ["one", "two"],
            "settings": {},
        },
    )

    with db.connect() as connection:
        batch = connection.execute(
            "SELECT file_path FROM batch_exports WHERE id = ?",
            (batch_id,),
        ).fetchone()
    with zipfile.ZipFile(batch["file_path"]) as archive:
        assert sorted(archive.namelist()) == ["one.json", "one.mp4", "two.json", "two.mp4"]


def test_cleanup_removes_only_expired_application_temporaries(phase_six_data: Path) -> None:
    upload_id = "expired-upload"
    upload_path = phase_six_data / "uploads" / f"{upload_id}.part"
    chunk_path = phase_six_data / "uploads" / "expired-session.chunk"
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(b"partial")
    chunk_path.write_bytes(b"chunk")
    old_time = 1_000_000_000
    upload_path.touch()
    chunk_path.touch()
    import os

    os.utime(upload_path, (old_time, old_time))
    os.utime(chunk_path, (old_time, old_time))
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO uploads (id, project_id, filename, total_size, temp_path, created_at) "
            "VALUES (?, 'project', 'video.mp4', 20, ?, '2000-01-01T00:00:00Z')",
            (upload_id, str(upload_path)),
        )

    result = maintenance_routes.clean_temporary_files()
    assert result["removed_files"] == 2
    assert result["expired_upload_sessions"] == 1
    assert not upload_path.exists()
    assert not chunk_path.exists()
    with db.connect() as connection:
        assert connection.execute("SELECT 1 FROM uploads WHERE id = ?", (upload_id,)).fetchone() is None


def test_project_delete_is_blocked_while_jobs_are_active(phase_six_data: Path) -> None:
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, payload) "
            "VALUES ('active', 'project', 'export', 'running', '{}')"
        )
    with pytest.raises(HTTPException) as error:
        project_routes.delete_project("project")
    assert error.value.status_code == 409
