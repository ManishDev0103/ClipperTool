import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.db import connect
from app.schemas import ProjectCreate

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_projects() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT p.*,
                (SELECT COUNT(*) FROM videos v WHERE v.project_id = p.id) AS video_count,
                (SELECT COUNT(*) FROM clips c WHERE c.project_id = p.id) AS clip_count
            FROM projects p ORDER BY p.created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("", status_code=201)
def create_project(request: ProjectCreate) -> dict:
    project_id = str(uuid4())
    with connect() as connection:
        connection.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)",
            (project_id, request.name),
        )
        row = connection.execute(
            """
            SELECT p.*,
                (SELECT COUNT(*) FROM videos v WHERE v.project_id = p.id) AS video_count,
                (SELECT COUNT(*) FROM clips c WHERE c.project_id = p.id) AS clip_count
            FROM projects p WHERE p.id = ?
            """,
            (project_id,),
        ).fetchone()
    return dict(row)


@router.get("/{project_id}")
def get_project(project_id: str) -> dict:
    with connect() as connection:
        project = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        videos = connection.execute(
            "SELECT id, project_id, title, source_url, duration, width, height, fps, created_at "
            "FROM videos WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        clips = connection.execute(
            "SELECT id, project_id, video_id, start_time, end_time, mode, created_at "
            "FROM clips WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        exports = connection.execute(
            "SELECT id, clip_id, file_path, settings, created_at FROM exports "
            "WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
    video_items = [dict(video) for video in videos]
    for video in video_items:
        video["file_url"] = f"/api/videos/{video['id']}/file"
    clip_items = [dict(clip) for clip in clips]
    for clip in clip_items:
        clip["file_url"] = f"/api/clips/{clip['id']}/file"
    export_items = [dict(export) for export in exports]
    for export in export_items:
        export["file_url"] = f"/api/exports/{export['id']}/file"
        export["settings"] = json.loads(export["settings"])
    return {"project": dict(project), "videos": video_items, "clips": clip_items, "exports": export_items}
