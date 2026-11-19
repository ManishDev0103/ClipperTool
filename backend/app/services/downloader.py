import math
from typing import Any
from urllib.parse import urlsplit


QUALITY_FORMATS = {
    "best": "bestvideo+bestaudio/best",
    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
}


def resolve_video_stream(
    url: str,
    quality: str,
    cookies_file: str,
) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed. Install backend/requirements.txt and retry.") from exc

    if quality not in QUALITY_FORMATS:
        raise ValueError(f"Unsupported stream quality: {quality}")

    options: dict[str, Any] = {
        "format": QUALITY_FORMATS[quality],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "extractor_retries": 3,
        "skip_download": True,
    }
    if cookies_file:
        options["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(options) as client:
        info = client.extract_info(url, download=False)

    if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
        raise RuntimeError("Enter a link to one supported video, not a playlist.")
    selected_formats = info.get("requested_formats")
    if isinstance(selected_formats, list):
        video_formats = [
            item for item in selected_formats
            if isinstance(item, dict) and item.get("vcodec") not in {None, "none"}
        ]
        audio_formats = [
            item for item in selected_formats
            if isinstance(item, dict) and item.get("acodec") not in {None, "none"}
        ]
        if video_formats and audio_formats:
            video_format = video_formats[0]
            audio_format = audio_formats[0]
            streams = [video_format]
            if video_format is not audio_format:
                streams.append(audio_format)
            info["_stream_formats"] = streams

    if not info.get("_stream_formats"):
        if not info.get("url") or info.get("acodec") == "none" or info.get("vcodec") == "none":
            raise RuntimeError("The video site did not provide a usable video and audio stream.")
        info["_stream_formats"] = [info]

    for selected_format in info["_stream_formats"]:
        stream_url = selected_format.get("url")
        if not stream_url or urlsplit(str(stream_url)).scheme not in {"http", "https"}:
            raise RuntimeError("The video site did not provide a supported HTTP stream.")
    if not info.get("url") and len(info["_stream_formats"]) == 1:
        info["url"] = info["_stream_formats"][0]["url"]
    if not info.get("url") and len(info["_stream_formats"]) < 2:
        raise RuntimeError(
            "This link has no single audio-and-video stream available. "
            "The app will not download or combine the full source; upload the video instead."
        )
    try:
        duration = float(info.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("The video stream has no valid duration.")
    return info
