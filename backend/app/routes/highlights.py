import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.db import connect
from app.schemas import HighlightAnalyzeCreate, SuggestionClipCreate, SuggestionUpdate

router = APIRouter(prefix="/api", tags=["highlights"])


@router.post("/videos/{video_id}/highlights", status_code=202)
def queue_highlight_analysis(video_id: str, request: HighlightAnalyzeCreate) -> dict:
    job_id = str(uuid4())
    with connect() as connection:
        video = connection.execute(
            "SELECT project_id FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if video is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        active = connection.execute(
            "SELECT id FROM jobs WHERE kind = 'highlight_analysis' AND status IN ('queued', 'running') "
            "AND payload LIKE ? ORDER BY created_at DESC LIMIT 1",
            (f'%"video_id": "{video_id}"%',),
        ).fetchone()
        if active is not None:
            raise HTTPException(status_code=409, detail="Highlight analysis for this video is already queued.")
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, message, payload) "
            "VALUES (?, ?, 'highlight_analysis', 'queued', 'Waiting in highlight queue', ?)",
            (job_id, video["project_id"], json.dumps({"video_id": video_id, "settings": request.model_dump()})),
        )
    return {"job_id": job_id, "status": "queued"}


@router.get("/videos/{video_id}/highlights")
def list_highlights(video_id: str) -> dict:
    with connect() as connection:
        if connection.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        analysis = connection.execute(
            "SELECT id, analysis_mode, warnings, settings, created_at FROM highlight_analyses "
            "WHERE video_id = ? ORDER BY created_at DESC LIMIT 1",
            (video_id,),
        ).fetchone()
        if analysis is None:
            return {"analysis_mode": None, "warnings": [], "suggestions": []}
        rows = connection.execute(
            "SELECT id, start_time, end_time, score, title, reason, status, signals, thumbnail_path "
            "FROM highlight_suggestions WHERE analysis_id = ? ORDER BY score DESC, start_time",
            (analysis["id"],),
        ).fetchall()
    suggestions = []
    for row in rows:
        suggestion = dict(row)
        suggestion["signals"] = json.loads(suggestion["signals"])
        suggestion["thumbnail_url"] = (
            f"/api/highlights/{suggestion['id']}/thumbnail" if suggestion["thumbnail_path"] else None
        )
        suggestion.pop("thumbnail_path")
        suggestions.append(suggestion)
    return {
        "analysis_mode": analysis["analysis_mode"],
        "warnings": json.loads(analysis["warnings"]),
        "settings": json.loads(analysis["settings"]),
        "created_at": analysis["created_at"],
        "suggestions": suggestions,
    }


@router.get("/highlights/{suggestion_id}/thumbnail")
def suggestion_thumbnail(suggestion_id: str) -> FileResponse:
    with connect() as connection:
        row = connection.execute(
            "SELECT thumbnail_path FROM highlight_suggestions WHERE id = ?",
            (suggestion_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Highlight suggestion not found.")
    if not row["thumbnail_path"]:
        raise HTTPException(status_code=404, detail="Preview thumbnail is unavailable.")
    path = Path(row["thumbnail_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Preview thumbnail is missing from local storage.")
    return FileResponse(path, media_type="image/jpeg")


@router.patch("/highlights/{suggestion_id}")
def update_suggestion(suggestion_id: str, request: SuggestionUpdate) -> dict:
    with connect() as connection:
        suggestion = connection.execute(
            "SELECT s.*, v.duration AS video_duration, a.settings AS analysis_settings "
            "FROM highlight_suggestions s JOIN videos v ON v.id = s.video_id "
            "JOIN highlight_analyses a ON a.id = s.analysis_id WHERE s.id = ?",
            (suggestion_id,),
        ).fetchone()
        if suggestion is None:
            raise HTTPException(status_code=404, detail="Highlight suggestion not found.")
        start = request.start_time if request.start_time is not None else suggestion["start_time"]
        end = request.end_time if request.end_time is not None else suggestion["end_time"]
        duration_settings = json.loads(suggestion["analysis_settings"])
        minimum = float(duration_settings["min_duration"])
        maximum = float(duration_settings["max_duration"])
        if end <= start or end - start < minimum or end - start > maximum:
            raise HTTPException(
                status_code=422,
                detail=f"Edited highlights must be between {minimum:g} and {maximum:g} seconds.",
            )
        if end > suggestion["video_duration"]:
            raise HTTPException(status_code=422, detail="Edited highlight exceeds the source video duration.")
        title = request.title if request.title is not None else suggestion["title"]
        status = request.status if request.status is not None else suggestion["status"]
        connection.execute(
            "UPDATE highlight_suggestions SET start_time = ?, end_time = ?, title = ?, status = ? WHERE id = ?",
            (start, end, title, status, suggestion_id),
        )
    return {
        "id": suggestion_id,
        "start_time": start,
        "end_time": end,
        "title": title,
        "status": status,
    }


@router.post("/highlights/{suggestion_id}/create-clip", status_code=202)
def create_clip_from_suggestion(suggestion_id: str, request: SuggestionClipCreate) -> dict:
    job_id = str(uuid4())
    with connect() as connection:
        suggestion = connection.execute(
            """
            SELECT s.*, a.settings AS analysis_settings
            FROM highlight_suggestions s
            JOIN highlight_analyses a ON a.id = s.analysis_id
            WHERE s.id = ?
            """,
            (suggestion_id,),
        ).fetchone()
        if suggestion is None:
            raise HTTPException(status_code=404, detail="Highlight suggestion not found.")
        if suggestion["status"] == "rejected":
            raise HTTPException(status_code=409, detail="Reject status must be changed before creating this clip.")
        if suggestion["status"] == "created":
            raise HTTPException(status_code=409, detail="A clip has already been created for this suggestion.")
        active = connection.execute(
            "SELECT id FROM jobs WHERE kind = 'clip' AND status IN ('queued', 'running') "
            "AND payload LIKE ? LIMIT 1",
            (f'%"suggestion_id": "{suggestion_id}"%',),
        ).fetchone()
        if active is not None:
            raise HTTPException(status_code=409, detail="A clip job for this suggestion is already queued.")
        analysis_settings = json.loads(suggestion["analysis_settings"])
        payload = {
            "video_id": suggestion["video_id"],
            "start_time": suggestion["start_time"],
            "end_time": suggestion["end_time"],
            "mode": request.mode,
            "suggestion_id": suggestion_id,
            "title": suggestion["title"],
            "ollama_model": analysis_settings.get("ollama_model", ""),
        }
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, message, payload) "
            "VALUES (?, ?, 'clip', 'queued', 'Creating suggested clip', ?)",
            (job_id, suggestion["project_id"], json.dumps(payload)),
        )
        connection.execute(
            "UPDATE highlight_suggestions SET status = 'accepted' WHERE id = ?",
            (suggestion_id,),
        )
    return {"job_id": job_id, "status": "queued"}
