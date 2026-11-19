from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import MEDIA_PROXY_BASE_URL


MediaSource = Path | str


def is_remote_media_source(source: MediaSource) -> bool:
    return isinstance(source, str) and urlsplit(source).scheme in {"http", "https"}


def media_source_at(source: MediaSource, start_time: float) -> MediaSource:
    if not is_remote_media_source(source):
        return source
    parts = urlsplit(source)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["start_time"] = f"{start_time:.6f}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def video_source(video: Any, start_time: float | None = None) -> Path | str:
    file_path = str(video["file_path"] or "")
    if file_path and Path(file_path).is_file():
        return Path(file_path)
    if video["source_url"]:
        source = f"{MEDIA_PROXY_BASE_URL}/api/videos/{video['id']}/file"
        return media_source_at(source, start_time) if start_time is not None else source
    if file_path:
        return Path(file_path)
    raise FileNotFoundError("The source video is missing from local storage.")
