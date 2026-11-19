from pathlib import Path
from typing import Any, Callable


QUALITY_FORMATS = {
    "best": "bestvideo+bestaudio/best",
    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
}


def download_video(
    url: str,
    output_dir: Path,
    quality: str,
    cookies_file: str,
    progress_callback: Callable[[float, str], None],
    output_stem: str,
) -> tuple[Path, dict[str, Any]]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed. Install backend/requirements.txt and retry.") from exc

    if quality not in QUALITY_FORMATS:
        raise ValueError(f"Unsupported download quality: {quality}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / f"{output_stem}.%(ext)s")

    def report_progress(status: dict[str, Any]) -> None:
        if status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
            downloaded = status.get("downloaded_bytes") or 0
            percent = min(99.0, downloaded / total * 100) if total else 0.0
            progress_callback(percent, status.get("_percent_str", "Downloading").strip())
        elif status.get("status") == "finished":
            progress_callback(99.0, "Download finished; processing file")

    options: dict[str, Any] = {
        "format": QUALITY_FORMATS[quality],
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "progress_hooks": [report_progress],
    }
    if cookies_file:
        options["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(options) as client:
        info = client.extract_info(url, download=True)
        candidates = [info.get("filepath"), info.get("_filename")]
        candidates.extend(
            item.get("filepath")
            for item in info.get("requested_downloads", [])
            if isinstance(item, dict)
        )
        candidates.append(client.prepare_filename(info))
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return Path(candidate), info
        raise RuntimeError("yt-dlp completed but the downloaded media file could not be located.")
