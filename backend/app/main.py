import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import DATA_DIR, RUN_WORKER, ensure_data_directories
from app.db import initialize_db
from app.routes import assets, clips, downloads, editor, exports, highlights, jobs, live, maintenance, projects, settings, transcriptions, uploads, videos
from app.services.worker import start_worker_thread

ensure_data_directories()
clipper_logger = logging.getLogger("clipper")
clipper_logger.setLevel(logging.INFO)
log_path = DATA_DIR / "logs" / "clipper.log"
if not any(getattr(handler, "baseFilename", None) == str(log_path.resolve()) for handler in clipper_logger.handlers):
    log_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    clipper_logger.addHandler(log_handler)
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_db()
    if RUN_WORKER:
        start_worker_thread()
    yield


app = FastAPI(title="Local Clipper", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for route_module in (
    projects,
    downloads,
    uploads,
    assets,
    videos,
    editor,
    clips,
    transcriptions,
    exports,
    highlights,
    live,
    jobs,
    settings,
    maintenance,
):
    app.include_router(route_module.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
