import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.db import connect

router = APIRouter(prefix="/api", tags=["jobs"])
TERMINAL_STATUSES = {"completed", "failed"}


def _job(job_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT id, project_id, kind, status, progress, message, error, payload, created_at, updated_at "
            "FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    job = dict(row)
    if job["kind"] == "transcription":
        job["model"] = json.loads(job.pop("payload")).get("model", "small")
    elif job["kind"] == "export":
        job.pop("payload")
    else:
        job.pop("payload")
    return job


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = _job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.websocket("/ws/jobs/{job_id}")
async def watch_job(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    try:
        while True:
            job = await asyncio.to_thread(_job, job_id)
            if job is None:
                await websocket.send_json({"error": "Job not found."})
                await websocket.close(code=4404)
                return
            await websocket.send_json(job)
            if job["status"] in TERMINAL_STATUSES:
                await websocket.close()
                return
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return
