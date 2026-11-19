import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse

from app.config import DATA_DIR
from app.db import connect
from app.schemas import ExportCreate
from app.services.captions import build_caption_style, build_srt, font_family

router = APIRouter(prefix="/api", tags=["exports"])
MAX_FONT_BYTES = 20 * 1024 * 1024
FONT_SIGNATURES = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1")


@router.post("/clips/{clip_id}/exports", status_code=202)
def queue_export(clip_id: str, request: ExportCreate) -> dict:
    job_id = str(uuid4())
    settings = request.model_dump()
    with connect() as connection:
        clip = connection.execute(
            "SELECT c.project_id, c.video_id FROM clips c WHERE c.id = ?",
            (clip_id,),
        ).fetchone()
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        transcript = connection.execute(
            "SELECT 1 FROM transcripts t JOIN transcript_words w ON w.video_id = t.video_id "
            "WHERE t.video_id = ? LIMIT 1",
            (clip["video_id"],),
        ).fetchone()
        if transcript is None:
            raise HTTPException(status_code=409, detail="Transcribe the source video before exporting captions.")
        if settings["font_file"]:
            filename = Path(settings["font_file"]).name
            font_path = DATA_DIR / "fonts" / filename
            if filename != settings["font_file"] or not font_path.is_file():
                raise HTTPException(status_code=400, detail="Selected caption font was not uploaded.")
            settings["font_file"] = filename
            try:
                settings["font_name"] = font_family(font_path)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            build_caption_style(
                settings["preset"],
                settings["font_name"],
                settings["font_size"],
                settings["color"],
                settings["outline_color"],
                settings["outline"],
                settings["position"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        active = connection.execute(
            "SELECT id FROM jobs WHERE kind = 'export' AND status IN ('queued', 'running') "
            "AND payload LIKE ? LIMIT 1",
            (f'%"clip_id": "{clip_id}"%',),
        ).fetchone()
        if active is not None:
            raise HTTPException(status_code=409, detail="An export for this clip is already queued.")
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, message, payload) "
            "VALUES (?, ?, 'export', 'queued', 'Waiting in export queue', ?)",
            (job_id, clip["project_id"], json.dumps({"clip_id": clip_id, "settings": settings})),
        )
    return {"job_id": job_id, "status": "queued"}


@router.get("/clips/{clip_id}/subtitles.srt")
def download_srt(clip_id: str) -> PlainTextResponse:
    with connect() as connection:
        clip = connection.execute(
            "SELECT video_id, start_time, end_time FROM clips WHERE id = ?",
            (clip_id,),
        ).fetchone()
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found.")
        rows = connection.execute(
            """
            SELECT start_time, end_time, text FROM transcript_words
            WHERE video_id = ? AND end_time > ? AND start_time < ?
            ORDER BY word_index
            """,
            (clip["video_id"], clip["start_time"], clip["end_time"]),
        ).fetchall()
    if not rows:
        raise HTTPException(status_code=409, detail="Transcribe the source video before exporting subtitles.")
    duration = clip["end_time"] - clip["start_time"]
    words = [
        {
            "start_time": max(0.0, row["start_time"] - clip["start_time"]),
            "end_time": min(duration, row["end_time"] - clip["start_time"]),
            "text": row["text"],
        }
        for row in rows
    ]
    return PlainTextResponse(
        build_srt(words),
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{clip_id}.srt"'},
    )


@router.get("/exports/{export_id}/file")
def download_export(export_id: str) -> FileResponse:
    with connect() as connection:
        export = connection.execute(
            "SELECT file_path FROM exports WHERE id = ?",
            (export_id,),
        ).fetchone()
    if export is None:
        raise HTTPException(status_code=404, detail="Export not found.")
    path = Path(export["file_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Export file is missing from local storage.")
    return FileResponse(path, filename=path.name, media_type="video/mp4")


@router.get("/fonts")
def list_fonts() -> list[dict[str, str]]:
    font_dir = DATA_DIR / "fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    return [
        {"filename": path.name, "name": font_family(path)}
        for path in sorted(font_dir.glob("*.ttf"), key=lambda item: item.name.lower())
        if path.is_file()
    ]


@router.post("/fonts", status_code=201)
async def upload_font(request: Request, filename: str = Query(min_length=5, max_length=255)) -> dict:
    safe_name = Path(filename.replace("\\", "/")).name
    if safe_name != filename or Path(safe_name).suffix.lower() != ".ttf":
        raise HTTPException(status_code=415, detail="Upload a .ttf font file using a plain filename.")
    font_dir = DATA_DIR / "fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    final_path = font_dir / safe_name
    temp_path = font_dir / f".{uuid4().hex}.part"
    size = 0
    try:
        with temp_path.open("wb") as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_FONT_BYTES:
                    raise HTTPException(status_code=413, detail="Font files must not exceed 20 MB.")
                target.write(chunk)
        if size < 4:
            raise HTTPException(status_code=422, detail="The font file is empty or invalid.")
        with temp_path.open("rb") as source:
            signature = source.read(4)
        if signature not in FONT_SIGNATURES:
            raise HTTPException(status_code=422, detail="The uploaded file is not a supported TrueType font.")
        try:
            name = font_family(temp_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        temp_path.replace(final_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return {"filename": final_path.name, "name": name}
