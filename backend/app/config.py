import os
from pathlib import Path


DATA_DIR = Path(os.getenv("DATA_DIR", r"C:\1Clipping\data")).expanduser()
DATABASE_PATH = DATA_DIR / "clipper.sqlite3"
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
YT_DLP_COOKIES_FILE = os.getenv("YT_DLP_COOKIES_FILE", "").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip()
RUN_WORKER = os.getenv("RUN_WORKER", "true").lower() not in {"0", "false", "no"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024 * 1024
MAX_UPLOAD_CHUNK_BYTES = 16 * 1024 * 1024


def ensure_data_directories() -> None:
    for path in (
        DATA_DIR,
        DATA_DIR / "videos",
        DATA_DIR / "clips",
        DATA_DIR / "uploads",
        DATA_DIR / "models",
        DATA_DIR / "fonts",
        DATA_DIR / "output",
        DATA_DIR / "thumbnails",
        DATA_DIR / "waveforms",
        DATA_DIR / "live",
        DATA_DIR / "logos",
        DATA_DIR / "music",
        DATA_DIR / "logs",
    ):
        path.mkdir(parents=True, exist_ok=True)
