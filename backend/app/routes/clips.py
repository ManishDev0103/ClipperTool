import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.db import connect
from app.schemas import ClipCreate

router = APIRouter(prefix="/api", tags=["clips"])


@router.post("/videos/{video_id}/clips", status_code=202)
def queue_clip(video_id: str, request: ClipCreate) -> dict:
    if request.end_time <= request.start_time:
        raise HTTPException(status_code=422, detail="End time must be greater than start time.")
    job_id = str(uuid4())
    with connect() as connection:
        video = connection.execute(
            "SELECT project_id, duration FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if video is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        if request.end_time > video["duration"]:
            raise HTTPException(
                status_code=422,
                detail=f"End time cannot exceed the video duration ({video['duration']:.2f} seconds).",
            )
        payload = {
            "video_id": video_id,
            "start_time": request.start_time,
            "end_time": request.end_time,
            "mode": request.mode,
        }
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, message, payload) "
            "VALUES (?, ?, 'clip', 'queued', 'Waiting in clip queue', ?)",
            (job_id, video["project_id"], json.dumps(payload)),
        )
    return {"job_id": job_id, "status": "queued"}


@router.get("/clips/{clip_id}/file")
def clip_file(clip_id: str) -> FileResponse:
    with connect() as connection:
        clip = connection.execute("SELECT file_path FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    path = Path(clip["file_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Clip file is missing from local storage.")
    media_type = "video/x-matroska" if path.suffix.lower() == ".mkv" else "video/mp4"
    return FileResponse(path, filename=f"{clip_id}{path.suffix}", media_type=media_type)
