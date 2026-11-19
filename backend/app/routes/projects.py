import json
import logging
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.config import DATA_DIR
from app.db import connect
from app.schemas import ProjectCreate

router = APIRouter(prefix="/api/projects", tags=["projects"])
LOGGER = logging.getLogger("clipper.projects")


def _has_symlink_in_data_path(path: Path) -> bool:
    try:
        relative = path.absolute().relative_to(DATA_DIR.absolute())
    except ValueError:
        return True
    current = DATA_DIR
    for part in relative.parts:
        if part in {".", ".."}:
            return True
        current = current / part
        if current.is_symlink():
            return True
    return False


def _remove_project_files(project_id: str, paths: list[tuple[str, Path]]) -> list[str]:
    warnings: list[str] = []
    for raw_path, allowed_root in paths:
        candidate = Path(raw_path)
        try:
            root = allowed_root.resolve()
            resolved = candidate.resolve()
            if _has_symlink_in_data_path(candidate) or resolved == root or not resolved.is_relative_to(root):
                continue
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink(missing_ok=True)
        except (OSError, RuntimeError) as exc:
            LOGGER.warning("Could not remove project media file %s: %s", candidate, exc)
            warnings.append(candidate.name)
    for folder in ("videos", "clips", "thumbnails"):
        candidate = DATA_DIR / folder / project_id
        try:
            root = (DATA_DIR / folder).resolve()
            resolved = candidate.resolve()
            if not _has_symlink_in_data_path(candidate) and resolved != root and resolved.is_relative_to(root) and resolved.is_dir():
                shutil.rmtree(resolved)
        except (OSError, RuntimeError) as exc:
            LOGGER.warning("Could not remove project folder %s: %s", candidate, exc)
            warnings.append(candidate.name)
    return warnings


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
        batch_exports = connection.execute(
            "SELECT id, created_at FROM batch_exports "
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
    batch_export_items = [dict(batch) for batch in batch_exports]
    for batch in batch_export_items:
        batch["file_url"] = f"/api/batch-exports/{batch['id']}/file"
    return {
        "project": dict(project),
        "videos": video_items,
        "clips": clip_items,
        "exports": export_items,
        "batch_exports": batch_export_items,
    }


@router.delete("/{project_id}")
def delete_project(project_id: str) -> dict:
    paths: list[tuple[str, Path]] = []
    with connect() as connection:
        if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        if connection.execute(
            "SELECT 1 FROM jobs WHERE project_id = ? AND status IN ('queued', 'running') LIMIT 1",
            (project_id,),
        ).fetchone() is not None:
            raise HTTPException(status_code=409, detail="Wait for this project's active jobs to finish before deleting it.")
        for folder, table in (
            ("videos", "videos"),
            ("clips", "clips"),
            ("output", "exports"),
            ("output", "batch_exports"),
        ):
            rows = connection.execute(
                f"SELECT file_path FROM {table} WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            allowed_root = DATA_DIR / folder / project_id if folder != "output" else DATA_DIR / folder
            paths.extend((row["file_path"], allowed_root) for row in rows)
            if table in {"clips", "exports"}:
                paths.extend(
                    (str(Path(row["file_path"]).with_suffix(extension)), allowed_root)
                    for row in rows
                    for extension in (".json", ".txt")
                )
        rows = connection.execute(
            "SELECT thumbnail_path FROM highlight_suggestions WHERE project_id = ? AND thumbnail_path IS NOT NULL",
            (project_id,),
        ).fetchall()
        paths.extend((row["thumbnail_path"], DATA_DIR / "thumbnails") for row in rows)
        rows = connection.execute(
            "SELECT id, segment_dir FROM live_recordings WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        live_root = DATA_DIR / "live"
        for row in rows:
            expected_dir = live_root / row["id"]
            if Path(row["segment_dir"]).resolve() == expected_dir.resolve():
                paths.append((row["segment_dir"], live_root))
        rows = connection.execute(
            "SELECT id, temp_path FROM uploads WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        upload_root = DATA_DIR / "uploads"
        for row in rows:
            upload_path = Path(row["temp_path"])
            if (
                upload_path.name == f"{row['id']}.part"
                and upload_path.parent.resolve() == upload_root.resolve()
            ):
                paths.append((row["temp_path"], upload_root))
        connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    warnings = _remove_project_files(project_id, paths)
    return {"deleted": True, "cleanup_warnings": warnings}
