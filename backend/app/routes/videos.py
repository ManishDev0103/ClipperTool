import logging
import logging
import mimetypes
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from http.client import HTTPResponse
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.config import FFMPEG_BIN, YT_DLP_COOKIES_FILE
from app.db import connect
from app.services.downloader import resolve_video_stream

router = APIRouter(prefix="/api/videos", tags=["videos"])
LOGGER = logging.getLogger("clipper.video-stream")
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


def _stream_remote(response: HTTPResponse) -> Iterator[bytes]:
    try:
        while chunk := response.read(1024 * 1024):
            yield chunk
    finally:
        response.close()


def _mux_command(info: dict[str, Any], start_time: float) -> tuple[list[str], str]:
    formats = info.get("_stream_formats") or [info]
    video_format = next(item for item in formats if item.get("vcodec") != "none")
    audio_format = next(
        (item for item in formats if item.get("acodec") != "none" and item is not video_format),
        None,
    )
    video_codec = str(video_format.get("vcodec") or "").lower()
    audio_codec = str((audio_format or video_format).get("acodec") or "").lower()
    is_webm = ("vp9" in video_codec or "vp09" in video_codec or "av01" in video_codec) and "opus" in audio_codec
    container = "webm" if is_webm else "mp4"
    command = [FFMPEG_BIN, "-hide_banner", "-nostdin", "-loglevel", "error"]
    for selected_format in formats:
        if start_time > 0:
            command.extend(["-ss", str(start_time)])
        headers = selected_format.get("http_headers") or info.get("http_headers") or {}
        header_value = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
        if header_value:
            command.extend(["-headers", header_value])
        command.extend(["-i", str(selected_format["url"])])
    command.extend(["-map", "0:v:0"])
    if audio_format is None:
        command.extend(["-map", "0:a:0?"])
    else:
        audio_index = formats.index(audio_format)
        command.extend(["-map", f"{audio_index}:a:0"])
    command.extend(["-c", "copy"])
    if container == "mp4":
        command.extend(["-movflags", "frag_keyframe+empty_moov+default_base_moof"])
    command.extend(["-f", container, "pipe:1"])
    return command, f"video/{container}"


def _stream_muxed(info: dict[str, Any], start_time: float) -> StreamingResponse:
    command, content_type = _mux_command(info, start_time)
    errors = tempfile.TemporaryFile()
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
    except OSError as exc:
        errors.close()
        raise HTTPException(status_code=502, detail=f"Could not start media stream: {exc}") from exc

    def chunks() -> Iterator[bytes]:
        try:
            if process.stdout is None:
                raise RuntimeError("Could not read the muxed media stream.")
            while chunk := process.stdout.read(1024 * 1024):
                yield chunk
            return_code = process.wait()
            if return_code:
                errors.seek(0)
                detail = errors.read().decode("utf-8", errors="replace").strip()
                safe_detail = re.sub(r"https?://[^\s'\"<>]+", "[remote stream]", detail)
                LOGGER.error(
                    "Remote media muxing failed: %s",
                    safe_detail or f"ffmpeg exited {return_code}.",
                )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdout is not None:
                process.stdout.close()
            errors.close()

    return StreamingResponse(
        chunks(),
        media_type=content_type,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{video_id}/file")
def video_file(
    video_id: str,
    request: Request,
    start_time: float = Query(default=0, ge=0),
) -> StreamingResponse:
    with connect() as connection:
        video = connection.execute(
            "SELECT file_path, source_url, stream_quality FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    path = Path(video["file_path"])
    if not path.is_file():
        if not video["source_url"]:
            raise HTTPException(status_code=404, detail="Video file is missing from local storage.")
        try:
            info = resolve_video_stream(
                video["source_url"],
                video["stream_quality"],
                YT_DLP_COOKIES_FILE,
            )
            formats = info.get("_stream_formats") or [info]
            if len(formats) > 1 or start_time > 0:
                return _stream_muxed(info, start_time)
            selected_format = formats[0]
            upstream_headers = {
                str(key): str(value)
                for key, value in (
                    selected_format.get("http_headers") or info.get("http_headers") or {}
                ).items()
            }
            range_header = request.headers.get("range")
            if range_header:
                if RANGE_PATTERN.fullmatch(range_header.strip()) is None:
                    raise HTTPException(
                        status_code=416,
                        detail="Requested byte range is not supported.",
                    )
                upstream_headers["Range"] = range_header
            upstream = urllib.request.urlopen(
                urllib.request.Request(str(selected_format["url"]), headers=upstream_headers),
                timeout=30,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 416:
                raise HTTPException(status_code=416, detail="Requested byte range is not supported.") from exc
            raise HTTPException(status_code=502, detail=f"Could not stream source video: {exc}") from exc
        except HTTPException:
            raise
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=f"Could not stream source video: {exc}") from exc

        headers = {"Accept-Ranges": "bytes"}
        for header in ("Content-Length", "Content-Range"):
            value = upstream.headers.get(header)
            if value:
                headers[header] = value
        return StreamingResponse(
            _stream_remote(upstream),
            status_code=getattr(upstream, "status", 200),
            media_type=upstream.headers.get_content_type(),
            headers=headers,
        )

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
