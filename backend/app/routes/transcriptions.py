import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.db import connect
from app.schemas import TranscriptUpdate, TranscriptionCreate

router = APIRouter(prefix="/api/videos", tags=["transcriptions"])


@router.post("/{video_id}/transcriptions", status_code=202)
def queue_transcription(video_id: str, request: TranscriptionCreate) -> dict:
    job_id = str(uuid4())
    with connect() as connection:
        video = connection.execute(
            "SELECT project_id FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if video is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        existing = connection.execute(
            "SELECT t.model, COUNT(w.word_index) AS word_count "
            "FROM transcripts t LEFT JOIN transcript_words w ON w.video_id = t.video_id "
            "WHERE t.video_id = ? GROUP BY t.model",
            (video_id,),
        ).fetchone()
        if existing is not None and existing["model"] == request.model and existing["word_count"] > 0:
            return {
                "already_transcribed": True,
                "model": existing["model"],
                "message": "A transcript for this model is already available.",
            }
        active = connection.execute(
            "SELECT id FROM jobs WHERE kind = 'transcription' AND status IN ('queued', 'running') "
            "AND payload LIKE ? ORDER BY created_at DESC LIMIT 1",
            (f'%"video_id": "{video_id}"%',),
        ).fetchone()
        if active is not None:
            raise HTTPException(status_code=409, detail="A transcription job for this video is already queued.")
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, message, payload) "
            "VALUES (?, ?, 'transcription', 'queued', 'Waiting in transcription queue', ?)",
            (
                job_id,
                video["project_id"],
                json.dumps({"video_id": video_id, **request.model_dump()}),
            ),
        )
    return {"job_id": job_id, "status": "queued"}


@router.get("/{video_id}/transcript")
def get_transcript(video_id: str) -> dict:
    with connect() as connection:
        video = connection.execute("SELECT id FROM videos WHERE id = ?", (video_id,)).fetchone()
        if video is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        transcript = connection.execute(
            "SELECT language, model, updated_at FROM transcripts WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        if transcript is None:
            return {"video_id": video_id, "language": None, "model": None, "words": []}
        words = connection.execute(
            "SELECT word_index, start_time, end_time, text FROM transcript_words "
            "WHERE video_id = ? ORDER BY word_index",
            (video_id,),
        ).fetchall()
    return {
        "video_id": video_id,
        **dict(transcript),
        "words": [dict(word) for word in words],
    }


@router.put("/{video_id}/transcript")
def update_transcript(video_id: str, request: TranscriptUpdate) -> dict:
    with connect() as connection:
        video = connection.execute(
            "SELECT duration FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if video is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        transcript = connection.execute(
            "SELECT 1 FROM transcripts WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        if transcript is None:
            raise HTTPException(status_code=404, detail="Transcribe this video before editing its transcript.")

        previous_start = -1.0
        words = []
        for index, word in enumerate(request.words):
            if word.end_time <= word.start_time:
                raise HTTPException(status_code=422, detail=f"Word {index + 1} has an invalid time range.")
            if word.start_time < previous_start:
                raise HTTPException(status_code=422, detail="Transcript words must be ordered by start time.")
            if word.end_time > video["duration"]:
                raise HTTPException(
                    status_code=422,
                    detail=f"Word {index + 1} exceeds the video duration.",
                )
            previous_start = word.start_time
            words.append((video_id, index, word.start_time, word.end_time, word.text))

        connection.execute("DELETE FROM transcript_words WHERE video_id = ?", (video_id,))
        connection.executemany(
            "INSERT INTO transcript_words (video_id, word_index, start_time, end_time, text) "
            "VALUES (?, ?, ?, ?, ?)",
            words,
        )
        connection.execute(
            "UPDATE transcripts SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE video_id = ?",
            (video_id,),
        )
    return {"video_id": video_id, "words_saved": len(words)}
