import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.config import DATA_DIR
from app.db import connect
from app.schemas import LiveClipCreate, LiveRecordingCreate
from app.services.live_buffer import list_buffer_segments

router = APIRouter(prefix="/api", tags=["live"])


def _recording_response(recording: dict) -> dict:
    segments = list_buffer_segments(Path(recording["segment_dir"]))
    recording["buffer_size_bytes"] = sum(segment.size_bytes for segment in segments)
    recording["buffered_seconds"] = round(sum(segment.duration for segment in segments), 1)
    recording["audio_trigger_enabled"] = bool(recording["audio_trigger_enabled"])
    recording["chat_trigger_enabled"] = bool(recording["chat_trigger_enabled"])
    return recording


def _queue_live_clip(recording_id: str, seconds: int, candidate_id: str | None = None) -> dict:
    job_id = str(uuid4())
    with connect() as connection:
        recording = connection.execute(
            "SELECT project_id FROM live_recordings WHERE id = ?",
            (recording_id,),
        ).fetchone()
        if recording is None:
            raise HTTPException(status_code=404, detail="Live recording not found.")
        if candidate_id is not None:
            candidate = connection.execute(
                "SELECT id, trigger_at FROM live_candidates WHERE id = ? AND recording_id = ?",
                (candidate_id, recording_id),
            ).fetchone()
            if candidate is None:
                raise HTTPException(status_code=404, detail="Live clip candidate not found.")
        payload = {
            "recording_id": recording_id,
            "seconds": seconds,
            "candidate_id": candidate_id,
            "candidate_at": candidate["trigger_at"] if candidate_id is not None else None,
        }
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, message, payload) "
            "VALUES (?, ?, 'live_clip', 'queued', 'Waiting to cut the rolling buffer', ?)",
            (job_id, recording["project_id"], json.dumps(payload)),
        )
    return {"job_id": job_id, "status": "queued"}


@router.get("/projects/{project_id}/live-recordings")
def list_live_recordings(project_id: str) -> list[dict]:
    with connect() as connection:
        if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        rows = connection.execute(
            "SELECT * FROM live_recordings WHERE project_id = ? ORDER BY started_at DESC LIMIT 50",
            (project_id,),
        ).fetchall()
    return [_recording_response(dict(row)) for row in rows]


@router.get("/projects/{project_id}/live-candidates")
def list_live_candidates(project_id: str) -> list[dict]:
    with connect() as connection:
        if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        rows = connection.execute(
            """
            SELECT c.*, r.status AS recording_status
            FROM live_candidates c
            JOIN live_recordings r ON r.id = c.recording_id
            WHERE c.project_id = ?
            ORDER BY c.created_at DESC LIMIT 100
            """,
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/projects/{project_id}/live-recordings", status_code=202)
def start_live_recording(project_id: str, request: LiveRecordingCreate) -> dict:
    recording_id = str(uuid4())
    segment_dir = DATA_DIR / "live" / recording_id
    with connect() as connection:
        if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        segment_dir.mkdir(parents=True, exist_ok=True)
        connection.execute(
            """
            INSERT INTO live_recordings
                (id, project_id, source_url, status, buffer_seconds, disk_cap_bytes,
                 segment_dir, audio_trigger_enabled, audio_peak_dbfs,
                 chat_trigger_enabled, chat_messages_per_minute)
            VALUES (?, ?, ?, 'starting', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recording_id,
                project_id,
                str(request.url),
                request.buffer_minutes * 60,
                int(request.disk_cap_gb * 1024 * 1024 * 1024),
                str(segment_dir),
                int(request.audio_trigger_enabled),
                request.audio_peak_dbfs,
                int(request.chat_trigger_enabled),
                request.chat_messages_per_minute,
            ),
        )
    return {"recording_id": recording_id, "status": "starting"}


@router.post("/live-recordings/{recording_id}/stop")
def stop_live_recording(recording_id: str) -> dict:
    with connect() as connection:
        row = connection.execute(
            "SELECT status FROM live_recordings WHERE id = ?",
            (recording_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Live recording not found.")
        status = row["status"]
        if status in {"starting", "recording", "reconnecting"}:
            connection.execute(
                "UPDATE live_recordings SET status = 'stopping' WHERE id = ?",
                (recording_id,),
            )
            status = "stopping"
    return {"recording_id": recording_id, "status": status}


@router.post("/live-recordings/{recording_id}/clip", status_code=202)
def clip_live_buffer(recording_id: str, request: LiveClipCreate) -> dict:
    return _queue_live_clip(recording_id, request.seconds)


@router.post("/live-candidates/{candidate_id}/clip", status_code=202)
def clip_live_candidate(
    candidate_id: str,
    request: LiveClipCreate,
) -> dict:
    with connect() as connection:
        row = connection.execute(
            "SELECT recording_id FROM live_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Live clip candidate not found.")
    return _queue_live_clip(row["recording_id"], request.seconds, candidate_id)


@router.post("/live/clip-last", status_code=202)
def clip_last_from_active_recording(
    request: LiveClipCreate,
    project_id: str | None = Query(default=None),
) -> dict:
    with connect() as connection:
        if project_id is None:
            row = connection.execute(
                "SELECT id FROM live_recordings "
                "WHERE status IN ('starting', 'recording', 'reconnecting') "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT id FROM live_recordings WHERE project_id = ? "
                "AND status IN ('starting', 'recording', 'reconnecting') "
                "ORDER BY started_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="There is no active live recording to clip.")
    return _queue_live_clip(row["id"], request.seconds)
