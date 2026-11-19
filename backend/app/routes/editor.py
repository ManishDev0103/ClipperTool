import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.config import DATA_DIR
from app.db import connect
from app.schemas import LayoutPresetCreate
from app.services.reframing import ReframeError, detect_face_track
from app.services.waveform import WaveformError, load_or_create_waveform
from app.services.media_source import is_remote_media_source, video_source

router = APIRouter(prefix="/api", tags=["timeline-editor"])


@router.get("/videos/{video_id}/waveform")
def get_waveform(video_id: str) -> dict:
    with connect() as connection:
        video = connection.execute(
            "SELECT id, file_path, source_url, stream_quality, duration FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    try:
        source = video_source(video)
        peaks = load_or_create_waveform(
            source,
            float(video["duration"]),
            DATA_DIR / "waveforms" / f"{video_id}.json",
        )
    except (OSError, WaveformError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not build waveform: {exc}") from exc
    return {"duration": float(video["duration"]), "peaks": peaks}


@router.get("/videos/{video_id}/reframe-track")
def get_reframe_track(
    video_id: str,
    start_time: float = Query(ge=0),
    end_time: float = Query(gt=0),
) -> dict:
    with connect() as connection:
        video = connection.execute(
            "SELECT id, file_path, source_url, stream_quality, duration FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    if end_time <= start_time or end_time > video["duration"]:
        raise HTTPException(
            status_code=422,
            detail=f"Choose a valid timeline range within the video ({video['duration']:.2f} seconds).",
        )
    try:
        source = video_source(video, start_time)
        track = detect_face_track(
            source,
            end_time - start_time,
            start_offset=0 if is_remote_media_source(source) else start_time,
        )
    except (OSError, ReframeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Smart reframe preview failed: {exc}") from exc
    return {"start_time": start_time, "end_time": end_time, "track": track}


@router.get("/projects/{project_id}/layout-presets")
def list_layout_presets(project_id: str) -> list[dict]:
    with connect() as connection:
        project = connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        rows = connection.execute(
            "SELECT * FROM layout_presets WHERE project_id = ? ORDER BY name COLLATE NOCASE",
            (project_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "aspect_ratio": row["aspect_ratio"],
            "facecam_region": json.loads(row["facecam_region"]),
            "gameplay_region": json.loads(row["gameplay_region"]),
        }
        for row in rows
    ]


@router.post("/projects/{project_id}/layout-presets", status_code=201)
def create_layout_preset(project_id: str, request: LayoutPresetCreate) -> dict:
    preset_id = str(uuid4())
    with connect() as connection:
        project = connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        try:
            connection.execute(
                """
                INSERT INTO layout_presets
                    (id, project_id, name, aspect_ratio, facecam_region, gameplay_region)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    preset_id,
                    project_id,
                    request.name,
                    request.aspect_ratio,
                    json.dumps(request.facecam_region.model_dump()),
                    json.dumps(request.gameplay_region.model_dump()),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="A layout preset with this name already exists in the project.",
            ) from exc
    return {
        "id": preset_id,
        "name": request.name,
        "aspect_ratio": request.aspect_ratio,
        "facecam_region": request.facecam_region.model_dump(),
        "gameplay_region": request.gameplay_region.model_dump(),
    }


@router.delete("/layout-presets/{preset_id}", status_code=204)
def delete_layout_preset(preset_id: str) -> None:
    with connect() as connection:
        changed = connection.execute("DELETE FROM layout_presets WHERE id = ?", (preset_id,)).rowcount
    if not changed:
        raise HTTPException(status_code=404, detail="Layout preset not found.")
