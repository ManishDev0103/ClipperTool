import logging
import json
from typing import Any

from app.config import OLLAMA_MODEL
from app.db import connect

LOGGER = logging.getLogger("clipper.settings")
SETTING_DEFAULTS: dict[str, Any] = {
    "default_whisper_model": "small",
    "ollama_model": OLLAMA_MODEL,
    "gpu_enabled": True,
    "default_caption_preset": "bold_yellow",
    "default_aspect_ratio": "9:16",
    "default_clip_length": 60,
    "default_buffer_minutes": 30,
    "hotkey": "Ctrl+Alt+C",
}


def get_app_settings() -> dict[str, Any]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT key, value FROM settings WHERE key IN (" +
            ",".join("?" for _ in SETTING_DEFAULTS) + ")",
            tuple(SETTING_DEFAULTS),
        ).fetchall()
    values = {row["key"]: row["value"] for row in rows}
    settings: dict[str, Any] = {}
    for key, default in SETTING_DEFAULTS.items():
        value = values.get(key)
        if value is None:
            settings[key] = default
            continue
        try:
            settings[key] = json.loads(value)
        except json.JSONDecodeError:
            LOGGER.warning("Ignoring invalid stored application setting: %s", key)
            settings[key] = default
    return settings
