import json
import logging
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import DATA_DIR, YT_DLP_COOKIES_FILE
from app.db import connect, initialize_db
from app.services.cutting import cut_clip
from app.services.captions import build_caption_style, font_family
from app.services.downloader import download_video
from app.services.exporting import export_captioned_clip
from app.services.highlight_analysis import analyze_video_highlights, create_thumbnail
from app.services.ollama import generate_clip_metadata
from app.services.media import probe_media
from app.services.reframing import detect_face_track
from app.services.transcription import transcribe_video
from app.services.live_buffer import cut_last_n, list_buffer_segments
from app.services.live_recording import reconcile_live_recordings

LOGGER = logging.getLogger("clipper.worker")
TERMINAL_STATUSES = {"completed", "failed"}


def update_job(job_id: str, **fields: Any) -> None:
    allowed = {"status", "progress", "message", "error"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    assignments = [f"{key} = ?" for key in values]
    assignments.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")
    parameters = list(values.values()) + [job_id]
    with connect() as connection:
        connection.execute(
            f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?",
            parameters,
        )


def _process_download(job: dict[str, Any], payload: dict[str, Any]) -> None:
    project_dir = DATA_DIR / "videos" / job["project_id"]

    def progress(percent: float, message: str) -> None:
        update_job(job["id"], progress=percent, message=message)

    media_path: Path | None = None
    info: dict[str, Any] = {}
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            if attempt > 1:
                update_job(job["id"], message=f"Retrying download ({attempt}/3)")
            media_path, info = download_video(
                payload["url"],
                project_dir,
                payload["quality"],
                _cookies_file(),
                progress,
                job["id"],
            )
            break
        except Exception as exc:
            last_error = exc
            LOGGER.warning("Download attempt %s for job %s failed: %s", attempt, job["id"], exc)
            if attempt < 3:
                time.sleep(attempt)
    if media_path is None:
        raise RuntimeError(f"Download failed after 3 attempts: {last_error}") from last_error

    metadata = probe_media(media_path)
    title = str(info.get("title") or media_path.stem)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO videos (id, project_id, title, source_url, file_path, duration, width, height, fps)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                job["project_id"],
                title,
                payload["url"],
                str(media_path),
                metadata["duration"],
                metadata["width"],
                metadata["height"],
                metadata["fps"],
            ),
        )


def _process_clip(job: dict[str, Any], payload: dict[str, Any]) -> None:
    with connect() as connection:
        video = connection.execute(
            "SELECT * FROM videos WHERE id = ? AND project_id = ?",
            (payload["video_id"], job["project_id"]),
        ).fetchone()
    if video is None:
        raise RuntimeError("The source video no longer exists in this project.")

    clip_id = str(uuid4())
    extension = ".mkv" if payload["mode"] == "copy" else ".mp4"
    output_path = DATA_DIR / "clips" / job["project_id"] / f"{clip_id}{extension}"

    def progress(percent: float) -> None:
        update_job(job["id"], progress=percent, message="Cutting clip")

    cut_clip(
        Path(video["file_path"]),
        payload["start_time"],
        payload["end_time"],
        output_path,
        payload["mode"],
        progress,
    )
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO clips (id, project_id, video_id, file_path, start_time, end_time, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip_id,
                job["project_id"],
                payload["video_id"],
                str(output_path),
                payload["start_time"],
                payload["end_time"],
                payload["mode"],
            ),
        )
        transcript_words = connection.execute(
            "SELECT text FROM transcript_words WHERE video_id = ? AND end_time > ? AND start_time < ? "
            "ORDER BY word_index",
            (payload["video_id"], payload["start_time"], payload["end_time"]),
        ).fetchall()
    transcript_text = " ".join(str(row["text"]).strip() for row in transcript_words).strip()
    fallback_title = str(payload.get("title") or f"Highlight {payload['start_time']:.0f}-{payload['end_time']:.0f}")
    if not transcript_text:
        transcript_text = f"{payload['end_time'] - payload['start_time']:.1f}-second highlight from {video['title']}."
    metadata, used_ollama = generate_clip_metadata(
        transcript_text,
        fallback_title,
        str(payload.get("ollama_model") or ""),
    )
    metadata["metadata_source"] = "ollama" if used_ollama else "local_heuristics"
    metadata["start_time"] = payload["start_time"]
    metadata["end_time"] = payload["end_time"]
    json_path = output_path.with_suffix(".json")
    text_path = output_path.with_suffix(".txt")
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    hashtags = " ".join(metadata["hashtags"])
    text_path.write_text(
        f"{metadata['title']}\n\n{metadata['description']}\n\n{hashtags}\n",
        encoding="utf-8",
    )
    if payload.get("suggestion_id"):
        with connect() as connection:
            connection.execute(
                "UPDATE highlight_suggestions SET status = 'created', metadata_path = ? WHERE id = ?",
                (str(json_path), payload["suggestion_id"]),
            )


def _process_transcription(job: dict[str, Any], payload: dict[str, Any]) -> None:
    with connect() as connection:
        video = connection.execute(
            "SELECT file_path, duration FROM videos WHERE id = ? AND project_id = ?",
            (payload["video_id"], job["project_id"]),
        ).fetchone()
    if video is None:
        raise RuntimeError("The source video no longer exists in this project.")

    def progress(percent: float, message: str) -> None:
        update_job(job["id"], progress=percent, message=message)

    words, language, _device = transcribe_video(
        Path(video["file_path"]),
        float(video["duration"]),
        payload["model"],
        payload["device"],
        progress,
    )
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO transcripts (video_id, language, model, updated_at)
            VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET
                language = excluded.language,
                model = excluded.model,
                updated_at = excluded.updated_at
            """,
            (payload["video_id"], language, payload["model"]),
        )
        connection.execute("DELETE FROM transcript_words WHERE video_id = ?", (payload["video_id"],))
        connection.executemany(
            "INSERT INTO transcript_words (video_id, word_index, start_time, end_time, text) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (payload["video_id"], index, word["start_time"], word["end_time"], word["text"])
                for index, word in enumerate(words)
            ],
        )
    update_job(job["id"], progress=100, message=f"Transcript ready ({len(words)} words)")


def _process_export(job: dict[str, Any], payload: dict[str, Any]) -> None:
    with connect() as connection:
        clip = connection.execute(
            """
            SELECT c.*, v.id AS source_video_id, v.width AS source_width, v.height AS source_height
            FROM clips c JOIN videos v ON v.id = c.video_id
            WHERE c.id = ? AND c.project_id = ?
            """,
            (payload["clip_id"], job["project_id"]),
        ).fetchone()
        transcript = connection.execute(
            "SELECT language FROM transcripts WHERE video_id = ?",
            (clip["source_video_id"],),
        ).fetchone() if clip is not None else None
        rows = connection.execute(
            """
            SELECT start_time, end_time, text FROM transcript_words
            WHERE video_id = ? AND end_time > ? AND start_time < ?
            ORDER BY word_index
            """,
            (clip["source_video_id"], clip["start_time"], clip["end_time"]),
        ).fetchall() if clip is not None else []
    if clip is None:
        raise RuntimeError("The source clip no longer exists in this project.")
    if transcript is None or not rows:
        raise RuntimeError("Transcribe this video's audio before exporting captions.")

    duration = float(clip["end_time"]) - float(clip["start_time"])
    words = [
        {
            "start_time": max(0.0, float(row["start_time"]) - float(clip["start_time"])),
            "end_time": min(duration, float(row["end_time"]) - float(clip["start_time"])),
            "text": str(row["text"]),
        }
        for row in rows
    ]
    settings = payload["settings"]
    font_path: Path | None = None
    font_file = settings.get("font_file")
    if font_file:
        safe_font = Path(font_file).name
        font_path = DATA_DIR / "fonts" / safe_font
        if not font_path.is_file():
            raise RuntimeError(f"Selected caption font is not available: {safe_font}")
        settings["font_name"] = font_family(font_path)
    style = build_caption_style(
        settings["preset"],
        settings["font_name"],
        settings["font_size"],
        settings.get("color"),
        settings.get("outline_color"),
        settings.get("outline"),
        settings["position"],
    )

    def progress(percent: float, message: str) -> None:
        update_job(job["id"], progress=percent, message=message)

    face_track: list[dict[str, float]] | None = None
    source_dimensions: tuple[int, int] | None = None
    if settings.get("smart_reframe") and settings.get("layout") != "gaming_split":
        face_track = detect_face_track(Path(clip["file_path"]), duration)
        source_width, source_height = clip["source_width"], clip["source_height"]
        if not source_width or not source_height:
            metadata = probe_media(Path(clip["file_path"]))
            source_width, source_height = metadata["width"], metadata["height"]
        if not source_width or not source_height:
            raise RuntimeError("Could not read the source dimensions needed for smart reframe.")
        source_dimensions = (int(source_width), int(source_height))

    output_path, used_nvenc = export_captioned_clip(
        Path(clip["file_path"]),
        words,
        style,
        settings,
        font_path,
        duration,
        progress,
        face_track,
        source_dimensions,
    )
    settings["encoder"] = "h264_nvenc" if used_nvenc else "libx264"
    with connect() as connection:
        connection.execute(
            "INSERT INTO exports (id, project_id, clip_id, file_path, settings) VALUES (?, ?, ?, ?, ?)",
            (str(uuid4()), job["project_id"], payload["clip_id"], str(output_path), json.dumps(settings)),
        )
    for extension in (".json", ".txt"):
        sidecar = Path(clip["file_path"]).with_suffix(extension)
        if sidecar.is_file():
            shutil.copyfile(sidecar, output_path.with_suffix(extension))


def _process_highlights(job: dict[str, Any], payload: dict[str, Any]) -> None:
    with connect() as connection:
        video = connection.execute(
            "SELECT * FROM videos WHERE id = ? AND project_id = ?",
            (payload["video_id"], job["project_id"]),
        ).fetchone()
        transcript_rows = connection.execute(
            "SELECT start_time, end_time, text FROM transcript_words "
            "WHERE video_id = ? ORDER BY word_index",
            (payload["video_id"],),
        ).fetchall()
    if video is None:
        raise RuntimeError("The source video no longer exists in this project.")

    def progress(percent: float, message: str) -> None:
        update_job(job["id"], progress=percent, message=message)

    result = analyze_video_highlights(
        Path(video["file_path"]),
        float(video["duration"]),
        [dict(row) for row in transcript_rows],
        payload["settings"],
        progress,
    )
    analysis_id = str(uuid4())
    suggestions: list[dict[str, Any]] = result["suggestions"]
    warnings: list[str] = result["warnings"]
    thumbnail_dir = DATA_DIR / "thumbnails" / job["project_id"]
    for index, suggestion in enumerate(suggestions):
        suggestion["id"] = str(uuid4())
        thumbnail_path = thumbnail_dir / f"{suggestion['id']}.jpg"
        try:
            create_thumbnail(
                Path(video["file_path"]),
                thumbnail_path,
                (suggestion["start_time"] + suggestion["end_time"]) / 2,
            )
            suggestion["thumbnail_path"] = str(thumbnail_path)
        except Exception as exc:
            LOGGER.warning("Could not create preview for suggestion %s: %s", suggestion["id"], exc)
            warnings.append(f"Preview thumbnail unavailable for “{suggestion['title']}”: {exc}")
            suggestion["thumbnail_path"] = None
        progress(
            95 + (index + 1) / max(1, len(suggestions)) * 4,
            f"Created preview {index + 1}/{len(suggestions)}",
        )

    with connect() as connection:
        connection.execute(
            """
            INSERT INTO highlight_analyses
                (id, project_id, video_id, job_id, analysis_mode, warnings, settings)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_id,
                job["project_id"],
                payload["video_id"],
                job["id"],
                result["analysis_mode"],
                json.dumps(list(dict.fromkeys(warnings)), ensure_ascii=False),
                json.dumps(result["settings"]),
            ),
        )
        connection.executemany(
            """
            INSERT INTO highlight_suggestions
                (id, project_id, video_id, analysis_id, start_time, end_time, score,
                 title, reason, signals, thumbnail_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    suggestion["id"],
                    job["project_id"],
                    payload["video_id"],
                    analysis_id,
                    suggestion["start_time"],
                    suggestion["end_time"],
                    suggestion["score"],
                    suggestion["title"],
                    suggestion["reason"],
                    json.dumps(suggestion["signals"]),
                    suggestion["thumbnail_path"],
                )
                for suggestion in suggestions
            ],
        )
    update_job(job["id"], progress=100, message=f"Found {len(suggestions)} highlight suggestions")


def _process_live_clip(job: dict[str, Any], payload: dict[str, Any]) -> None:
    with connect() as connection:
        recording = connection.execute(
            "SELECT * FROM live_recordings WHERE id = ? AND project_id = ?",
            (payload["recording_id"], job["project_id"]),
        ).fetchone()
    if recording is None:
        raise RuntimeError("The live recording no longer exists in this project.")

    video_id = str(uuid4())
    output_path = DATA_DIR / "videos" / job["project_id"] / "live-clips" / f"{video_id}.mp4"
    update_job(job["id"], progress=5, message="Selecting recent stream segments")
    candidate_at = payload.get("candidate_at")
    end_before = (
        datetime.fromisoformat(candidate_at.replace("Z", "+00:00")).timestamp()
        if candidate_at
        else None
    )
    cut_last_n(
        list_buffer_segments(Path(recording["segment_dir"])),
        int(payload["seconds"]),
        output_path,
        end_before=end_before,
    )
    update_job(job["id"], progress=85, message="Reading live clip metadata")
    metadata = probe_media(output_path)
    title = f"Live clip · {int(payload['seconds'])} seconds"
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO videos (id, project_id, title, source_url, file_path, duration, width, height, fps)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                job["project_id"],
                title,
                recording["source_url"],
                str(output_path),
                metadata["duration"],
                metadata["width"],
                metadata["height"],
                metadata["fps"],
            ),
        )
        if payload.get("candidate_id"):
            connection.execute(
                "UPDATE live_candidates SET video_id = ? WHERE id = ?",
                (video_id, payload["candidate_id"]),
            )
    update_job(job["id"], progress=100, message=f"Ready to edit: {title}")


def process_next_job() -> bool:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at, rowid LIMIT 1"
        ).fetchone()
        if row is None:
            connection.commit()
            return False
        job = dict(row)
        changed = connection.execute(
            """
            UPDATE jobs
            SET status = 'running', progress = 0,
                message = 'Starting', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND status = 'queued'
            """,
            (job["id"],),
        ).rowcount
        connection.commit()
    if not changed:
        return False

    try:
        payload = json.loads(job["payload"])
        if job["kind"] == "download":
            _process_download(job, payload)
        elif job["kind"] == "clip":
            _process_clip(job, payload)
        elif job["kind"] == "transcription":
            _process_transcription(job, payload)
        elif job["kind"] == "export":
            _process_export(job, payload)
        elif job["kind"] == "highlight_analysis":
            _process_highlights(job, payload)
        elif job["kind"] == "live_clip":
            _process_live_clip(job, payload)
        else:
            raise RuntimeError(f"Unsupported job type: {job['kind']}")
        update_job(job["id"], status="completed", progress=100, message="Complete", error=None)
    except Exception as exc:
        LOGGER.exception("Job %s failed", job["id"])
        update_job(job["id"], status="failed", message="Failed", error=str(exc))
    return True


def _cookies_file() -> str:
    with connect() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = 'cookies_file'").fetchone()
    return row["value"] if row is not None else YT_DLP_COOKIES_FILE


def run_worker(stop_event: threading.Event | None = None) -> None:
    initialize_db()
    with connect() as connection:
        connection.execute(
            "UPDATE jobs SET status = 'queued', progress = 0, message = 'Resuming after restart', "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE status = 'running'"
        )
    LOGGER.info("Clipper background worker started")
    while stop_event is None or not stop_event.is_set():
        try:
            reconcile_live_recordings()
            if not process_next_job():
                time.sleep(0.5)
        except sqlite3.Error:
            LOGGER.exception("Worker database error")
            time.sleep(1)


def start_worker_thread() -> threading.Thread:
    thread = threading.Thread(target=run_worker, name="clipper-worker", daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_worker()
