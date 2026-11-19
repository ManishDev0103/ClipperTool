from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import DATA_DIR, YT_DLP_COOKIES_FILE
from app.db import connect
from app.schemas import SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings() -> dict:
    with connect() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = 'cookies_file'").fetchone()
    return {
        "data_dir": str(DATA_DIR),
        "cookies_file": row["value"] if row is not None else YT_DLP_COOKIES_FILE,
    }


@router.put("")
def update_settings(request: SettingsUpdate) -> dict:
    cookies_file = request.cookies_file.strip()
    if cookies_file and not Path(cookies_file).expanduser().is_file():
        raise HTTPException(status_code=400, detail="Cookies file does not exist.")
    with connect() as connection:
        connection.execute(
            "INSERT INTO settings (key, value) VALUES ('cookies_file', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (cookies_file,),
        )
    return {"data_dir": str(DATA_DIR), "cookies_file": cookies_file}
