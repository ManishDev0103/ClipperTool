import mimetypes
import re
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.db import connect

router = APIRouter(prefix="/api/videos", tags=["videos"])
RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)$")


def _stream_file(path: Path, start: int, length: int) -> Iterator[bytes]:
    with path.open("rb") as media_file:
        media_file.seek(start)
        remaining = length
        while remaining:
            chunk = media_file.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/{video_id}/file")
def video_file(video_id: str, request: Request) -> StreamingResponse:
    with connect() as connection:
        video = connection.execute("SELECT file_path FROM videos WHERE id = ?", (video_id,)).fetchone()
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    path = Path(video["file_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Video file is missing from local storage.")

    size = path.stat().st_size
    start, end = 0, size - 1
    status_code = 200
    headers = {"Accept-Ranges": "bytes"}
    range_header = request.headers.get("range")
    if range_header:
        match = RANGE_PATTERN.fullmatch(range_header.strip())
        if match is None or size == 0:
            raise HTTPException(
                status_code=416,
                detail="Requested byte range is not supported.",
                headers={"Content-Range": f"bytes */{size}"},
            )
        first, last = match.groups()
        if not first:
            suffix_length = int(last or 0)
            start = max(0, size - suffix_length)
        else:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        if start >= size or end < start:
            raise HTTPException(
                status_code=416,
                detail="Requested byte range is outside the video.",
                headers={"Content-Range": f"bytes */{size}"},
            )
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    length = max(0, end - start + 1)
    headers["Content-Length"] = str(length)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return StreamingResponse(
        _stream_file(path, start, length),
        status_code=status_code,
        media_type=content_type,
        headers=headers,
    )
