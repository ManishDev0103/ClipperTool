import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import DATA_DIR, OLLAMA_URL, YT_DLP_COOKIES_FILE
from app.db import connect
from app.schemas import SettingsUpdate
from app.services.application_settings import SETTING_DEFAULTS, get_app_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings() -> dict:
    with connect() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = 'cookies_file'").fetchone()
    return {
        "data_dir": str(DATA_DIR),
        "output_dir": str(DATA_DIR / "output"),
        "logs_file": str(DATA_DIR / "logs" / "clipper.log"),
        "ollama_url": OLLAMA_URL,
        "cookies_file": row["value"] if row is not None else YT_DLP_COOKIES_FILE,
        "preferences": get_app_settings(),
    }


@router.put("")
def update_settings(request: SettingsUpdate) -> dict:
    cookies_file = request.cookies_file.strip() if request.cookies_file is not None else None
    if cookies_file and not Path(cookies_file).expanduser().is_file():
        raise HTTPException(status_code=400, detail="Cookies file does not exist.")
    updates = {
        key: value
        for key, value in request.model_dump(exclude={"cookies_file"}).items()
        if value is not None
    }
    if "ollama_model" in updates:
        updates["ollama_model"] = updates["ollama_model"].strip()
        if not updates["ollama_model"]:
            raise HTTPException(status_code=422, detail="Ollama model cannot be blank.")
    with connect() as connection:
        if cookies_file is not None:
            connection.execute(
                "INSERT INTO settings (key, value) VALUES ('cookies_file', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (cookies_file,),
            )
        for key, value in updates.items():
            if key not in SETTING_DEFAULTS:
                raise HTTPException(status_code=422, detail=f"Unsupported setting: {key}")
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
    return get_settings()
