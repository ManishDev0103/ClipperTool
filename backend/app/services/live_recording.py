import logging
import re
import sqlite3
import socket
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from app.config import DATA_DIR, FFMPEG_BIN
from app.db import connect
from app.services.live_buffer import list_buffer_segments, prune_segments

LOGGER = logging.getLogger("clipper.live")
RECONNECT_DELAYS = (2, 4, 8, 15, 30)
MAX_RECONNECT_ATTEMPTS = 12
_recording_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


def _recording(recording_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM live_recordings WHERE id = ?",
            (recording_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def _set_recording(recording_id: str, **fields: Any) -> None:
    allowed = {
        "status",
        "reconnect_count",
        "buffer_size_bytes",
        "last_error",
        "ended_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    assignments = [f"{key} = ?" for key in values]
    with connect() as connection:
        connection.execute(
            f"UPDATE live_recordings SET {', '.join(assignments)} WHERE id = ?",
            (*values.values(), recording_id),
        )


def _stop_requested(recording_id: str) -> bool:
    current = _recording(recording_id)
    return current is None or current["status"] in {"stopping", "stopped"}


def _add_candidate(recording_id: str, reason: str) -> None:
    with connect() as connection:
        recording = connection.execute(
            "SELECT project_id FROM live_recordings WHERE id = ?",
            (recording_id,),
        ).fetchone()
        if recording is None:
            return
        connection.execute(
            "INSERT INTO live_candidates (id, recording_id, project_id, reason) VALUES (?, ?, ?, ?)",
            (str(uuid4()), recording_id, recording["project_id"], reason),
        )


def _twitch_channel(source_url: str) -> str | None:
    match = re.search(r"(?:^|//)(?:www\.)?twitch\.tv/([a-zA-Z0-9_]+)", source_url, re.IGNORECASE)
    return match.group(1).lower() if match else None


def _youtube_video_id(source_url: str) -> str | None:
    parsed = urlparse(source_url)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/").split("/", 1)[0] or None
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        return None
    query_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_id:
        return query_id
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) == 2 and path_parts[0] in {"live", "embed", "shorts"}:
        return path_parts[1]
    return None


def _watch_youtube_chat(recording_id: str, source_url: str, stop_event: threading.Event) -> None:
    video_id = _youtube_video_id(source_url)
    if video_id is None:
        LOGGER.info("YouTube live chat is unavailable for recording %s: video ID was not found", recording_id)
        return
    try:
        import pytchat
    except ImportError:
        LOGGER.error("YouTube chat trigger requires the pytchat dependency")
        return
    chat = pytchat.create(video_id=video_id)
    message_times: deque[float] = deque()
    last_candidate = 0.0
    try:
        while chat.is_alive() and not stop_event.is_set() and not _stop_requested(recording_id):
            data = chat.get()
            now = time.monotonic()
            message_times.extend(now for _item in data.sync_items())
            while message_times and now - message_times[0] > 60:
                message_times.popleft()
            current = _recording(recording_id)
            threshold = int(current["chat_messages_per_minute"]) if current else 10**9
            if len(message_times) >= threshold and now - last_candidate >= 60:
                _add_candidate(
                    recording_id,
                    f"YouTube live chat activity spike ({len(message_times)} messages in 60 seconds)",
                )
                last_candidate = now
            stop_event.wait(1)
    finally:
        chat.terminate()


def _watch_twitch_chat(recording_id: str, source_url: str, stop_event: threading.Event) -> None:
    current = _recording(recording_id)
    channel = _twitch_channel(source_url)
    if current is None or not current["chat_trigger_enabled"] or channel is None:
        return
    message_times: deque[float] = deque()
    last_candidate = 0.0
    while not stop_event.is_set() and not _stop_requested(recording_id):
        try:
            with socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=10) as chat:
                chat.settimeout(1)
                nickname = f"justinfan{int(time.time() * 1000) % 100000000}"
                chat.sendall(f"NICK {nickname}\r\nJOIN #{channel}\r\n".encode("ascii"))
                pending = b""
                while not stop_event.is_set() and not _stop_requested(recording_id):
                    try:
                        chunk = chat.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    pending += chunk
                    while b"\n" in pending:
                        raw_line, pending = pending.split(b"\n", 1)
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if line.startswith("PING "):
                            chat.sendall(f"PONG {line[5:]}\r\n".encode("ascii"))
                        elif " PRIVMSG #" in line:
                            now = time.monotonic()
                            message_times.append(now)
                            while message_times and now - message_times[0] > 60:
                                message_times.popleft()
                            latest = _recording(recording_id)
                            threshold = int(latest["chat_messages_per_minute"]) if latest else 10**9
                            if len(message_times) >= threshold and now - last_candidate >= 60:
                                _add_candidate(
                                    recording_id,
                                    f"Twitch chat activity spike ({len(message_times)} messages in 60 seconds)",
                                )
                                last_candidate = now
        except OSError as exc:
            LOGGER.info("Twitch chat monitor for %s will retry: %s", recording_id, exc)
            stop_event.wait(10)


def _watch_live_chat(recording_id: str, source_url: str, stop_event: threading.Event) -> None:
    recording = _recording(recording_id)
    if recording is None or not recording["chat_trigger_enabled"]:
        return
    host = urlparse(source_url).netloc.lower()
    if _twitch_channel(source_url):
        _watch_twitch_chat(recording_id, source_url, stop_event)
    elif _youtube_video_id(source_url):
        _watch_youtube_chat(recording_id, source_url, stop_event)
    elif host == "kick.com" or host.endswith(".kick.com"):
        LOGGER.info("Chat activity triggers are not available for Kick recording %s", recording_id)


def _measure_peak_dbfs(segment: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                FFMPEG_BIN,
                "-hide_banner",
                "-nostats",
                "-i",
                str(segment),
                "-vn",
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOGGER.warning("Could not measure live audio in %s: %s", segment.name, exc)
        return None
    match = re.search(r"max_volume:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dB", result.stderr)
    if result.returncode != 0 or match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _source_command(kind: str, source_url: str) -> list[str]:
    if kind == "streamlink":
        return ["streamlink", "--stdout", source_url, "best"]
    return ["yt-dlp", "--quiet", "--no-warnings", "-f", "best", "-o", "-", source_url]


def _terminate_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _capture_once(
    recording: dict[str, Any],
    source_kind: str,
    session_dir: Path,
    recording_id: str,
) -> tuple[int, int, str]:
    session_dir.mkdir(parents=True, exist_ok=True)
    segment_list = session_dir / "segments.csv"
    segment_pattern = session_dir / "segment-%09d.ts"
    ffmpeg_command = [
        FFMPEG_BIN,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        "pipe:0",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        "2",
        "-reset_timestamps",
        "1",
        "-segment_format",
        "mpegts",
        "-segment_list",
        str(segment_list),
        "-segment_list_type",
        "csv",
        "-segment_list_size",
        "0",
        "-segment_list_flags",
        "+live",
        str(segment_pattern),
    ]

    source: subprocess.Popen[bytes] | None = None
    ffmpeg: subprocess.Popen[bytes] | None = None
    error_log = tempfile.TemporaryFile(mode="w+b")
    try:
        source = subprocess.Popen(
            _source_command(source_kind, recording["source_url"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if source.stdout is None:
            raise RuntimeError(f"{source_kind} did not provide a media stream.")
        ffmpeg = subprocess.Popen(
            ffmpeg_command,
            stdin=source.stdout,
            stdout=subprocess.DEVNULL,
            stderr=error_log,
        )
        source.stdout.close()
        _set_recording(recording_id, status="recording", last_error=None)
        seen_audio: set[Path] = set()
        last_audio_candidate = 0.0
        last_scan = 0.0
        while ffmpeg.poll() is None and source.poll() is None:
            if _stop_requested(recording_id):
                break
            now = time.monotonic()
            if now - last_scan >= 2:
                latest_segments = list_buffer_segments(Path(recording["segment_dir"]))
                if recording["audio_trigger_enabled"]:
                    for segment in latest_segments:
                        if segment.path in seen_audio:
                            continue
                        seen_audio.add(segment.path)
                        peak = _measure_peak_dbfs(segment.path)
                        if (
                            peak is not None
                            and peak >= float(recording["audio_peak_dbfs"])
                            and now - last_audio_candidate >= 20
                        ):
                            _add_candidate(recording_id, f"Audio loudness spike ({peak:.1f} dBFS)")
                            last_audio_candidate = now
                _retained, size_bytes, _duration = prune_segments(
                    latest_segments,
                    int(recording["buffer_seconds"]),
                    int(recording["disk_cap_bytes"]),
                )
                _set_recording(recording_id, buffer_size_bytes=size_bytes)
                last_scan = now
            time.sleep(0.25)
        _terminate_process(ffmpeg)
        _terminate_process(source)
        ffmpeg_code = ffmpeg.returncode if ffmpeg.returncode is not None else -1
        source_code = source.returncode if source.returncode is not None else -1
        error_log.seek(0)
        detail = error_log.read().decode("utf-8", errors="replace").strip()[-2000:]
        return source_code, ffmpeg_code, detail
    finally:
        _terminate_process(ffmpeg)
        _terminate_process(source)
        error_log.close()


def _finish_recording(recording_id: str, status: str, error: str | None = None) -> None:
    _set_recording(recording_id, status=status, last_error=error)
    with connect() as connection:
        connection.execute(
            "UPDATE live_recordings SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (recording_id,),
        )


def _record_live(recording_id: str) -> None:
    chat_stop = threading.Event()
    try:
        recording = _recording(recording_id)
        if recording is None:
            return
        chat_thread = threading.Thread(
            target=_watch_live_chat,
            args=(recording_id, recording["source_url"], chat_stop),
            name=f"live-chat-{recording_id}",
            daemon=True,
        )
        chat_thread.start()
        failures = int(recording["reconnect_count"])
        preferred_source = "streamlink"
        while not _stop_requested(recording_id):
            recording = _recording(recording_id)
            if recording is None:
                break
            session_dir = Path(recording["segment_dir"]) / uuid4().hex
            try:
                source_code, ffmpeg_code, detail = _capture_once(
                    recording,
                    preferred_source,
                    session_dir,
                    recording_id,
                )
            except FileNotFoundError as exc:
                detail = f"Could not start {preferred_source}: {exc}"
                source_code, ffmpeg_code = -1, -1
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, sqlite3.Error) as exc:
                LOGGER.exception("Live recording %s failed", recording_id)
                detail = str(exc)
                source_code, ffmpeg_code = -1, -1
            if _stop_requested(recording_id):
                _finish_recording(recording_id, "stopped")
                return
            if source_code == 0 and ffmpeg_code == 0:
                _finish_recording(recording_id, "ended", detail or None)
                return
            failures += 1
            _set_recording(
                recording_id,
                status="reconnecting",
                reconnect_count=failures,
                last_error=detail or f"{preferred_source} exited ({source_code}); ffmpeg exited ({ffmpeg_code}).",
            )
            if preferred_source == "streamlink":
                preferred_source = "yt-dlp"
            else:
                preferred_source = "streamlink"
            if failures >= MAX_RECONNECT_ATTEMPTS:
                _finish_recording(
                    recording_id,
                    "ended",
                    detail or "The stream remained offline after repeated reconnect attempts.",
                )
                return
            delay = RECONNECT_DELAYS[min(failures - 1, len(RECONNECT_DELAYS) - 1)]
            for _ in range(delay):
                if _stop_requested(recording_id):
                    break
                time.sleep(1)
        _finish_recording(recording_id, "stopped")
    finally:
        chat_stop.set()


def reconcile_live_recordings() -> None:
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, status FROM live_recordings "
            "WHERE status IN ('starting', 'recording', 'reconnecting', 'stopping')"
        ).fetchall()
    with _threads_lock:
        for row in rows:
            recording_id = str(row["id"])
            thread = _recording_threads.get(recording_id)
            if thread is not None and thread.is_alive():
                continue
            if row["status"] == "stopping":
                _finish_recording(recording_id, "stopped")
                continue
            thread = threading.Thread(
                target=_record_live,
                args=(recording_id,),
                name=f"live-recording-{recording_id}",
                daemon=True,
            )
            _recording_threads[recording_id] = thread
            thread.start()
        dead_threads = [
            recording_id
            for recording_id, thread in _recording_threads.items()
            if not thread.is_alive()
        ]
        for recording_id in dead_threads:
            _recording_threads.pop(recording_id, None)
