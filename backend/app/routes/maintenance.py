import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from app.config import DATA_DIR
from app.db import connect

router = APIRouter(prefix="/api", tags=["maintenance"])
LOGGER = logging.getLogger("clipper.maintenance")
TEMP_RETENTION_SECONDS = 24 * 60 * 60
_disk_cache: tuple[str, float, dict] | None = None


def _folder_size(root: Path) -> int:
    total = 0
    if root.is_symlink() or not root.is_dir():
        return total
    def log_walk_error(error: OSError) -> None:
        LOGGER.warning("Could not scan disk usage below %s: %s", root, error)

    for current, directories, filenames in os.walk(root, followlinks=False, onerror=log_walk_error):
        directories[:] = [
            name for name in directories
            if not (Path(current) / name).is_symlink()
        ]
        for filename in filenames:
            path = Path(current) / filename
            try:
                if not path.is_symlink():
                    total += path.stat().st_size
            except OSError as exc:
                LOGGER.warning("Could not read disk usage for %s: %s", path, exc)
    return total


@router.get("/maintenance/disk")
def disk_usage() -> dict:
    global _disk_cache
    cache_key = str(DATA_DIR.resolve())
    now = time.monotonic()
    if _disk_cache is not None and _disk_cache[0] == cache_key and _disk_cache[1] > now:
        return _disk_cache[2]
    categories = (
        "videos",
        "clips",
        "live",
        "output",
        "models",
        "uploads",
        "fonts",
        "logos",
        "music",
        "thumbnails",
        "waveforms",
        "logs",
    )
    usage = {name: _folder_size(DATA_DIR / name) for name in categories}
    result = {
        "data_dir": str(DATA_DIR),
        "total_bytes": sum(usage.values()),
        "categories": usage,
        "checked_at": time.time(),
    }
    _disk_cache = (cache_key, now + 15, result)
    return result


@router.post("/maintenance/cleanup")
def clean_temporary_files() -> dict:
    cutoff = time.time() - TEMP_RETENTION_SECONDS
    removed_files = 0
    removed_bytes = 0
    stale_upload_ids: list[str] = []
    with connect() as connection:
        stale = connection.execute(
            "SELECT id, temp_path FROM uploads "
            "WHERE created_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-24 hours')"
        ).fetchall()
        upload_root_path = DATA_DIR / "uploads"
        upload_root = upload_root_path.resolve()
        for row in stale:
            path = Path(row["temp_path"])
            try:
                resolved = path.resolve()
                if (
                    path.name == f"{row['id']}.part"
                    and not upload_root_path.is_symlink()
                    and path.parent.resolve() == upload_root
                    and not path.is_symlink()
                    and resolved.is_file()
                    and resolved.is_relative_to(upload_root)
                ):
                    removed_bytes += resolved.stat().st_size
                    resolved.unlink()
                    removed_files += 1
                elif path.name != f"{row['id']}.part":
                    LOGGER.warning("Skipped unexpected stale upload path: %s", path)
            except (OSError, RuntimeError) as exc:
                LOGGER.warning("Could not clean stale upload %s: %s", path, exc)
            stale_upload_ids.append(row["id"])
        if stale_upload_ids:
            connection.executemany("DELETE FROM uploads WHERE id = ?", [(upload_id,) for upload_id in stale_upload_ids])

    roots_and_patterns = (
        (DATA_DIR / "uploads", "*.chunk"),
        (DATA_DIR / "uploads", ".*.part"),
        (DATA_DIR / "fonts", ".*.part"),
        (DATA_DIR / "logos", ".*.part"),
        (DATA_DIR / "music", ".*.part"),
        (DATA_DIR / "output", "live-concat-*.txt"),
    )
    for root, pattern in roots_and_patterns:
        if root.is_symlink() or not root.is_dir():
            continue
        allowed_root = root.resolve()
        for path in root.glob(pattern):
            try:
                resolved = path.resolve()
                if (
                    not path.is_symlink()
                    and resolved.is_relative_to(allowed_root)
                    and resolved.is_file()
                    and resolved.stat().st_mtime < cutoff
                ):
                    removed_bytes += resolved.stat().st_size
                    resolved.unlink()
                    removed_files += 1
            except (OSError, RuntimeError) as exc:
                LOGGER.warning("Could not clean temporary file %s: %s", path, exc)
    return {
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "expired_upload_sessions": len(stale_upload_ids),
    }


@router.get("/logs", response_class=PlainTextResponse)
def read_logs(limit: int = Query(default=200, ge=1, le=500)) -> PlainTextResponse:
    path = DATA_DIR / "logs" / "clipper.log"
    if not path.is_file():
        return PlainTextResponse("No log entries yet.")
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 2 * 1024 * 1024))
            text = stream.read().decode("utf-8", errors="replace")
    except OSError as exc:
        LOGGER.exception("Could not read application log")
        return PlainTextResponse(f"Could not read application log: {exc}", status_code=500)
    return PlainTextResponse("\n".join(text.splitlines()[-limit:]))
