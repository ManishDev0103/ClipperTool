import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.db import connect
from app.schemas import DownloadCreate

router = APIRouter(prefix="/api/projects", tags=["downloads"])


@router.post("/{project_id}/downloads", status_code=202)
def queue_download(project_id: str, request: DownloadCreate) -> dict:
    job_id = str(uuid4())
    payload = {"url": str(request.url), "quality": request.quality}
    with connect() as connection:
        if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, message, payload) "
            "VALUES (?, ?, 'download', 'queued', 'Waiting in download queue', ?)",
            (job_id, project_id, json.dumps(payload)),
        )
    return {"job_id": job_id, "status": "queued"}
