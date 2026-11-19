import json
import sqlite3
from uuid import uuid4


AUTO_HIGHLIGHT_SETTINGS = {
    "min_duration": 20,
    "max_duration": 60,
    "number_of_clips": 8,
    "allow_overlap": False,
    "gaming_mode": False,
    "ollama_model": "",
    "weights": {
        "transcript": 0.4,
        "audio": 0.25,
        "scene": 0.15,
        "motion": 0.2,
    },
}


def queue_auto_highlight_analysis(
    connection: sqlite3.Connection,
    project_id: str,
    video_id: str,
) -> str:
    connection.execute(
        "INSERT INTO jobs (id, project_id, kind, status, message, payload) "
        "VALUES (?, ?, 'transcription', 'queued', "
        "'Automatically transcribing video for highlight analysis', ?)",
        (
            str(uuid4()),
            project_id,
            json.dumps({"video_id": video_id, "model": "small", "device": "auto"}),
        ),
    )
    job_id = str(uuid4())
    connection.execute(
        "INSERT INTO jobs (id, project_id, kind, status, message, payload) "
        "VALUES (?, ?, 'highlight_analysis', 'queued', "
        "'Automatically finding highlights', ?)",
        (
            job_id,
            project_id,
            json.dumps(
                {
                    "video_id": video_id,
                    "settings": AUTO_HIGHLIGHT_SETTINGS,
                    "auto_create_clips": True,
                }
            ),
        ),
    )
    return job_id
