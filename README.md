# Local Clipper

A local-first video clipping app for Windows 10/11. It imports videos from URLs handled by yt-dlp or from local files, probes media with ffprobe, cuts clips with ffmpeg, and stores project data on your machine. It does not require cloud accounts or paid APIs.

Use it only with content you own or have permission to download and clip, and follow the source site's terms and applicable laws. Cookies are optional and should only be used for content you are authorized to access.

## Architecture

- **React + Vite + TypeScript + Tailwind** provides the project library, URL/file intake, player, waveform timeline, segment editing, job progress, and settings.
- **FastAPI** validates requests and exposes a local REST/WebSocket API.
- **SQLite** stores projects, videos, clips, word-level transcripts, ranked highlight suggestions, saved gaming layouts, analysis runs, queued jobs, upload sessions, settings, and export records. Jobs persist across restarts.
- **Worker** processes the SQLite job queue sequentially. Windows local mode starts it in-process; Docker Compose runs a separate worker container sharing the same data volume.
- **yt-dlp** downloads supported YouTube, Twitch VOD/clip, Kick, Facebook, X/Twitter, Instagram, TikTok, Reddit, and Vimeo URLs, plus other URLs handled by its extractors. Availability depends on the installed yt-dlp version and source-site access rules. Quality caps and an optional cookies file are configurable. The queue retries failed downloads up to three times.
- **Chunked upload** writes bounded chunks to disk; files up to 10 GB are never assembled in application memory.
- **ffprobe** extracts duration, frame size, and frame rate. **ffmpeg** creates accurate re-encoded MP4 clips or fast stream-copy MKV clips.
- **faster-whisper** runs locally, detects the spoken language, produces word timestamps, and processes long videos in ten-minute audio chunks. Model weights are cached locally in the data directory after first use.
- **ASS karaoke captions** group transcript words into readable lines and animate each word. Exports normalize audio to -14 LUFS and render H.264/AAC MP4s using NVENC when enabled and available, with CPU encoder fallback.
- **Export polish** supports local logo watermarks, optional intro/outro clips, a progress bar, background music with optional speech ducking, source/music volume controls, and punch-in zooms on emphasized transcript words. Multiple captioned clips can be queued together and downloaded as a ZIP.
- **Settings and maintenance** persist local defaults, show data/output paths, disk usage and recent logs, and remove only stale application-owned temporary files. Project deletion removes its database records and confined project files after active jobs finish.
- **Timeline and reframing** use streamed FFmpeg waveform peaks with wavesurfer.js regions, local MediaPipe face tracking with smoothed crop motion, optional blurred background fill, and draggable gaming-layout regions.
- **Highlight analysis** combines local Ollama transcript moments (when available), transcript keyword heuristics, streamed audio-energy/laughter-like cues, PySceneDetect scene changes, and sampled visual motion. Weighted scores are normalized to 0–100; missing/unavailable signals are reported and Ollama is never required.
- **Live capture** uses Streamlink with yt-dlp fallback and FFmpeg's segment muxer to keep a local rolling transport-stream buffer. The worker prunes completed segments against the time window and disk cap, reconnects after stream drops, measures optional audio peaks, and watches Twitch IRC or YouTube live chat activity when enabled.
- **Live clips and hotkey** cut recent buffered seconds into a new project video for the existing timeline, transcription, caption, and export flow. A small Windows `RegisterHotKey` helper sends the global Ctrl+Alt+C action to the local API; no browser focus is required.
- **Clip metadata** uses the local Ollama model when available and local heuristics otherwise. Each created clip receives `.json` and `.txt` title/description/hashtag sidecars; captioned exports copy those sidecars beside the MP4.
- **Local storage** defaults to `C:\1Clipping\data` and contains the SQLite database, source videos, clips, Whisper models, custom fonts, and finished exports.

## Folder structure

```text
clipper-pro-max/
├── backend/
│   ├── app/
│   │   ├── routes/                 # Projects, media, timeline, live, highlights, transcription, exports, jobs
│   │   ├── services/               # Ollama, live buffer, stream capture, Whisper, reframing, exports
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── schemas.py
│   │   └── main.py
│   ├── tests/                      # Cutting, captions, transcription, highlights, and crop math tests
│   ├── Dockerfile
│   └── requirements.txt
├── tools/
│   └── live_hotkey.py              # Optional Windows global hotkey helper
├── frontend/
│   ├── src/                        # App pages, waveform timeline, API client, and Tailwind styles
│   ├── Dockerfile
│   ├── nginx.conf
│   └── package.json
├── cookies/                        # Optional local yt-dlp cookies file
├── docker-compose.yml
├── .env.example
├── README.md
├── run_live_hotkey.bat
└── run_local.bat
```

## Windows setup (no Docker)

Install **Python 3.11**, **Node.js 20.19+ or 22.12+**, and **FFmpeg** (both `ffmpeg` and `ffprobe` must be on `PATH`). Then:

1. Open Command Prompt in the project folder.
2. Run:

   ```bat
   run_local.bat
   ```

3. Open [http://127.0.0.1:5173](http://127.0.0.1:5173). The backend listens on `127.0.0.1:8000`.

The launcher creates a project-local `.venv`, installs backend requirements, and installs frontend packages the first time. Media and the SQLite database are stored under `C:\1Clipping\data` by default. Override the location before launching with `set DATA_DIR=D:\YourData`.

## Docker Compose setup

Install Docker Desktop, then from the project folder run:

```bat
copy .env.example .env
docker compose up --build
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). The default host data path is `C:\1Clipping\data`; change `CLIPPER_DATA_DIR` in `.env` to use another local folder. The backend API is also available at `http://127.0.0.1:8000`.

The backend image build includes a smoke check for FFmpeg, Streamlink, pytchat, and MediaPipe's face detector; the build fails if a runtime dependency is unavailable.

To use cookies with Docker, place an authorized `cookies.txt` in the project `cookies` folder and set this in `.env`:

```dotenv
YT_DLP_COOKIES_FILE=/cookies/cookies.txt
```

The optional `redis` profile starts a local Redis service (the queue remains SQLite-backed). The `ollama` profile starts the local LLM service:

```bat
docker compose --profile redis --profile ollama up --build
```

Ollama is now used when available. Start its local Compose service and download a model once:

```bat
docker compose --profile ollama up --build -d
docker compose exec ollama ollama pull qwen2.5:7b
```

The model runs locally after download. Alternatively install Ollama for Windows and run `ollama pull qwen2.5:7b`; the local app connects to `http://127.0.0.1:11434`. If Ollama is stopped or has no model, analysis falls back to local transcript/audio/video heuristics and metadata uses a local fallback. Ollama URLs are restricted to loopback or the local Docker Ollama host.

The default worker is CPU-capable. For an NVIDIA GPU with Docker Desktop GPU passthrough configured, stop the normal stack and start the optional CUDA worker instead:

```bat
docker compose down
docker compose --profile gpu up --build --scale worker=0
```

The GPU worker uses CUDA/cuDNN when available. Transcription automatically retries on CPU if CUDA initialization or inference fails. NVENC exports also retry with libx264 if the encoder is unavailable. The GPU profile requires a compatible NVIDIA driver and Docker GPU support; without those, use the normal CPU worker.

## Use the app

1. Create a project on the home page and select it.
2. Paste a supported video URL and choose a quality cap, or drag an MP4/MKV/MOV/WEBM/AVI file into the upload area (up to 10 GB).
3. Open the project. Downloads show queued/running progress over WebSocket with HTTP polling as a fallback. Uploaded files are probed before appearing in the player.
4. Choose a video and use the waveform handles or numeric points to edit segments. Split at the playhead, merge adjacent segments, then queue one or more clips in **Accurate re-encode** or **Fast stream copy** mode. Stream-copy cuts are keyframe-limited and saved as MKV. Keyboard shortcuts: Space play/pause, I/O set in/out, C create clips, and left/right arrows step one frame.
5. In **Transcript**, choose a Whisper model and device, then generate a word-timed transcript. The first run downloads the selected model weights into the local data folder; later runs reuse the cache. GPU mode falls back to CPU when CUDA cannot run.
6. Click transcript words to seek the player; double-click a word to edit it, then save your changes. The active word follows playback.
7. Select a clip in **Export captioned clip**, choose the karaoke preset and 9:16, 1:1, 4:5, or 16:9 output. Blurred fill preserves the full frame by default. Enable local MediaPipe smart reframe to follow a detected face, or choose gaming split-screen and draw gameplay/facecam regions on the source preview. Save and reuse layout presets. The live preview follows the source playhead.
8. Download the finished captioned MP4 from the export list or the clip’s separate SRT from the Clips list. Exports are saved under `C:\1Clipping\data\output`.
9. In **Auto-find highlights**, choose a duration range, result count, overlap handling, optional gaming mode, and signal weights. Run analysis, review the ranked score/reason/thumbnail, edit times or title, accept/reject, and create a clip. New clips receive local title/description/hashtag `.json` and `.txt` files beside the clip.
10. Open **Live** to select a project, paste a live URL, and start recording. Configure the rolling window and disk cap; Streamlink is tried first and yt-dlp is used as fallback. The dashboard shows recording/reconnect status, buffer duration/size, and stop controls. Clip 30, 60, or 90 seconds (or enter a custom 1–600 seconds); the resulting MP4 appears as a video in the project editor.
11. To use the system-wide hotkey, keep the API running and launch `run_live_hotkey.bat` in another terminal. It registers **Ctrl+Alt+C** and queues a 60-second clip from the newest active recording, even when the browser is unfocused. For example, `run_live_hotkey.bat --seconds 90` changes its duration. Press Ctrl+C in the helper window to unregister the hotkey.
12. Optional live candidates can be enabled when starting a recording: audio peaks use FFmpeg's local loudness measurement; Twitch chat is read over anonymous IRC and YouTube chat through pytchat. Chat detection is not available for Kick. Candidate rows include the trigger reason and can queue a clip from the current buffer.
13. In **Export captioned clip**, upload a logo or audio bed, optionally choose intro/outro clips, adjust volumes/ducking, turn on the progress bar or emphasized-word zoom, and choose whether to use GPU encoding. These effects are rendered locally by FFmpeg.
14. Check multiple clip checkboxes and use **Batch export selected** to render them with the current export settings. Each output is added to the project’s export list and a ZIP link appears when the batch finishes.
15. Open **Settings** to configure persistent Whisper/Ollama/GPU/export/live defaults and the helper hotkey. The disk panel reports storage categories; stale temporary upload fragments are eligible for cleanup after 24 hours. Project deletion is available from the project page and is blocked while its jobs are active.
16. The same Whisper model transcript is reused on subsequent requests for that video. Selecting a different model replaces it with a new local transcript.

URL downloading depends on yt-dlp extractor support and source availability. Some sources require a cookies file. No login credentials are collected by this app. Audio laughter/excitement cues are local signal heuristics (loudness, burstiness, and zero-crossing features), not a dedicated trained emotion classifier.

## Settings, maintenance, and troubleshooting

- **Storage location:** local mode reads `DATA_DIR`; Docker Compose reads `CLIPPER_DATA_DIR` on Windows and mounts it at `/data`. Ensure the selected drive has enough free space for source videos, Whisper weights, live buffers, and exports.
- **FFmpeg unavailable:** install a Windows FFmpeg build and add its `bin` folder to `PATH`, then open a new terminal and confirm `ffmpeg -version` and `ffprobe -version`. Docker images include FFmpeg.
- **GPU unavailable:** keep GPU acceleration enabled to try NVENC; exports fall back to `libx264`. Whisper retries on CPU if CUDA fails. Docker GPU mode also requires NVIDIA drivers and Docker Desktop GPU passthrough.
- **Whisper first run:** the selected model downloads once into the local data directory. Larger models need more disk space and memory; choose `tiny` or `base` on lower-memory machines. Transcripts for the same video/model are reused.
- **Ollama not running:** highlight analysis and metadata generation continue with local heuristics. For Docker, start the `ollama` profile and pull a model as shown above.
- **Upload/download stalls:** check the project job card and `Settings → Application logs`; failed jobs display their error. Confirm there is space in the data directory and that the source URL remains accessible.
- **Cleanup and deletion:** cleanup removes only expired temporary upload fragments and stale concat lists, not media or exports. Project deletion is permanent; it is refused while the project's jobs are queued or running.
- **Data locality:** the app has no hosted service integrations. Whisper, FFmpeg, yt-dlp, Streamlink, SQLite and Ollama (if enabled) run locally. Download only media you own or have permission to use.

## Test

From the project root, run the backend unit tests and frontend production build:

```bat
.\.venv\Scripts\python.exe -m pytest backend\tests -q
npm --prefix frontend run build
```

If you have not run `run_local.bat`, create the environment first:

```bat
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm --prefix frontend install
```

To check the API after startup:

```bat
curl.exe http://127.0.0.1:8000/api/health
```

Caption tests verify word timing, ASS karaoke generation, preset validation, and SRT timestamps. Export tests inspect generated FFmpeg filter graphs, NVENC fallback, reframing, watermark/audio/progress effects, and do not require a GPU. Highlight tests cover weighted scoring, ranking, duration bounds, and overlap behavior. Reframing tests cover crop bounds, center tracking, smoothing, and time-varying crop expressions. Live-buffer tests cover selecting/pruning segments and cutting the latest requested duration; those tests mock FFmpeg and need no stream. Actual Streamlink/yt-dlp recording and FFmpeg live cuts require the installed local media dependencies and a permitted live stream.
