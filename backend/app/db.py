import sqlite3
from pathlib import Path
from typing import Any

from app.config import DATABASE_PATH, ensure_data_directories


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    ensure_data_directories()
    connection = sqlite3.connect(Path(DATABASE_PATH), timeout=30, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize_db() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                source_url TEXT,
                file_path TEXT NOT NULL,
                duration REAL NOT NULL,
                width INTEGER,
                height INTEGER,
                fps REAL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE INDEX IF NOT EXISTS videos_project_idx ON videos(project_id);
            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                file_path TEXT NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE INDEX IF NOT EXISTS clips_project_idx ON clips(project_id);
            CREATE TABLE IF NOT EXISTS transcripts (
                video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
                language TEXT,
                model TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE TABLE IF NOT EXISTS transcript_words (
                video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                word_index INTEGER NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (video_id, word_index)
            );
            CREATE INDEX IF NOT EXISTS transcript_words_timing_idx
                ON transcript_words(video_id, start_time, end_time);
            CREATE TABLE IF NOT EXISTS exports (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                clip_id TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
                file_path TEXT NOT NULL,
                settings TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE INDEX IF NOT EXISTS exports_project_idx ON exports(project_id);
            CREATE TABLE IF NOT EXISTS highlight_analyses (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
                analysis_mode TEXT NOT NULL,
                warnings TEXT NOT NULL DEFAULT '[]',
                settings TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE TABLE IF NOT EXISTS highlight_suggestions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                analysis_id TEXT NOT NULL REFERENCES highlight_analyses(id) ON DELETE CASCADE,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                score REAL NOT NULL,
                title TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'suggested',
                signals TEXT NOT NULL DEFAULT '{}',
                thumbnail_path TEXT,
                metadata_path TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE INDEX IF NOT EXISTS highlight_suggestions_video_idx
                ON highlight_suggestions(video_id, score DESC);
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE INDEX IF NOT EXISTS jobs_queue_idx ON jobs(status, created_at);
            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                total_size INTEGER NOT NULL,
                received_size INTEGER NOT NULL DEFAULT 0,
                temp_path TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS layout_presets (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                aspect_ratio TEXT NOT NULL,
                facecam_region TEXT NOT NULL,
                gameplay_region TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(project_id, name)
            );
            CREATE TABLE IF NOT EXISTS live_recordings (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                source_url TEXT NOT NULL,
                status TEXT NOT NULL,
                buffer_seconds INTEGER NOT NULL,
                disk_cap_bytes INTEGER NOT NULL,
                segment_dir TEXT NOT NULL,
                audio_trigger_enabled INTEGER NOT NULL DEFAULT 0,
                audio_peak_dbfs REAL NOT NULL DEFAULT -10,
                chat_trigger_enabled INTEGER NOT NULL DEFAULT 0,
                chat_messages_per_minute INTEGER NOT NULL DEFAULT 30,
                reconnect_count INTEGER NOT NULL DEFAULT 0,
                buffer_size_bytes INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                ended_at TEXT
            );
            CREATE INDEX IF NOT EXISTS live_recordings_status_idx
                ON live_recordings(status, started_at);
            CREATE TABLE IF NOT EXISTS live_candidates (
                id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL REFERENCES live_recordings(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                trigger_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                reason TEXT NOT NULL,
                video_id TEXT REFERENCES videos(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE INDEX IF NOT EXISTS live_candidates_project_idx
                ON live_candidates(project_id, created_at DESC);
            """
        )


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
