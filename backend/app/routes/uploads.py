import re
import shutil
import threading
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from app.config import DATA_DIR, MAX_UPLOAD_BYTES, MAX_UPLOAD_CHUNK_BYTES
from app.db import connect
from app.schemas import UploadCreate
from app.services.auto_clipping import queue_auto_highlight_analysis
from app.services.media import MediaProbeError, probe_media

router = APIRouter(prefix="/api", tags=["uploads"])
UPLOAD_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
_upload_lock = threading.Lock()


def _safe_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or Path(name).suffix.lower() not in UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Supported uploads: mp4, mkv, mov, webm, and avi.")
    return name


@router.post("/projects/{project_id}/uploads", status_code=201)
def begin_upload(project_id: str, request: UploadCreate) -> dict:
    if request.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload exceeds the 10 GB limit.")
    filename = _safe_filename(request.filename)
    with connect() as connection:
        if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        upload_id = str(uuid4())
        temp_path = DATA_DIR / "uploads" / f"{upload_id}.part"
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.touch()
        connection.execute(
            "INSERT INTO uploads (id, project_id, filename, total_size, temp_path) VALUES (?, ?, ?, ?, ?)",
            (upload_id, project_id, filename, request.size, str(temp_path)),
        )
    return {"upload_id": upload_id, "received_size": 0, "chunk_size": 8 * 1024 * 1024}


@router.put("/uploads/{upload_id}")
async def upload_chunk(
    upload_id: str,
    request: Request,
    offset: int = Query(ge=0),
) -> dict:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.") from exc
        if declared_length > MAX_UPLOAD_CHUNK_BYTES:
            raise HTTPException(status_code=413, detail="Upload chunks must not exceed 16 MB.")
    stage_path = DATA_DIR / "uploads" / f"{upload_id}.{uuid4().hex}.chunk"
    byte_count = 0
    try:
        with stage_path.open("wb") as staged:
            async for chunk in request.stream():
                byte_count += len(chunk)
                if byte_count > MAX_UPLOAD_CHUNK_BYTES:
                    raise HTTPException(status_code=413, detail="Upload chunks must not exceed 16 MB.")
                staged.write(chunk)
        if byte_count == 0:
            raise HTTPException(status_code=400, detail="Upload chunk is empty.")

        with _upload_lock:
            with connect() as connection:
                upload = connection.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
                if upload is None:
                    raise HTTPException(status_code=404, detail="Upload session not found.")
                if offset != upload["received_size"]:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Expected the next chunk at byte offset {upload['received_size']}.",
                    )
                if upload["received_size"] + byte_count > upload["total_size"]:
                    raise HTTPException(status_code=413, detail="Chunk exceeds the declared upload size.")
                target = Path(upload["temp_path"])
                with target.open("r+b") as output, stage_path.open("rb") as staged:
                    output.seek(offset)
                    shutil.copyfileobj(staged, output)
                received_size = upload["received_size"] + byte_count
                connection.execute(
                    "UPDATE uploads SET received_size = ? WHERE id = ?",
                    (received_size, upload_id),
                )
        return {"upload_id": upload_id, "received_size": received_size}
    finally:
        stage_path.unlink(missing_ok=True)


@router.post("/uploads/{upload_id}/complete", status_code=201)
def complete_upload(upload_id: str) -> dict:
    with connect() as connection:
        upload = connection.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if upload is None:
            raise HTTPException(status_code=404, detail="Upload session not found.")
        if upload["received_size"] != upload["total_size"]:
            raise HTTPException(
                status_code=409,
                detail=f"Upload is incomplete ({upload['received_size']} of {upload['total_size']} bytes).",
            )

    temp_path = Path(upload["temp_path"])
    try:
        metadata = probe_media(temp_path)
    except MediaProbeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    video_id = str(uuid4())
    project_dir = DATA_DIR / "videos" / upload["project_id"]
    project_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(upload["filename"]).stem).strip("._")
    final_path = project_dir / f"{video_id}_{safe_stem}{Path(upload['filename']).suffix.lower()}"
    temp_path.replace(final_path)

    with connect() as connection:
        connection.execute(
            """
            INSERT INTO videos (id, project_id, title, file_path, duration, width, height, fps)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                upload["project_id"],
                upload["filename"],
                str(final_path),
                metadata["duration"],
                metadata["width"],
                metadata["height"],
                metadata["fps"],
            ),
        )
        connection.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
        queue_auto_highlight_analysis(connection, upload["project_id"], video_id)
    return {
        "video": {
            "id": video_id,
            "title": upload["filename"],
            **metadata,
            "file_url": f"/api/videos/{video_id}/file",
        }
    }
