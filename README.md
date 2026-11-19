# Local Clipper

A local-first video clipping app for Windows 10/11. It imports videos from URLs handled by yt-dlp or from local files, probes media with ffprobe, cuts clips with ffmpeg, and stores project data on your machine. It does not require cloud accounts or paid APIs.

Use it only with content you own or have permission to download and clip, and follow the source site's terms and applicable laws. Cookies are optional and should only be used for content you are authorized to access.

## Architecture

- **React + Vite + TypeScript + Tailwind** provides the project library, URL/file intake, player, trim controls, job progress, and settings.
- **FastAPI** validates requests and exposes a local REST/WebSocket API.
- **SQLite** stores projects, videos, clips, word-level transcripts, ranked highlight suggestions, analysis runs, queued jobs, upload sessions, settings, and export records. Jobs persist across restarts.
- **Worker** processes the SQLite job queue sequentially. Windows local mode starts it in-process; Docker Compose runs a separate worker container sharing the same data volume.
- **yt-dlp** downloads supported YouTube, Twitch VOD/clip, Kick, Facebook, X/Twitter, Instagram, TikTok, Reddit, and Vimeo URLs, plus other URLs handled by its extractors. Availability depends on the installed yt-dlp version and source-site access rules. Quality caps and an optional cookies file are configurable. The queue retries failed downloads up to three times.
- **Chunked upload** writes bounded chunks to disk; files up to 10 GB are never assembled in application memory.
- **ffprobe** extracts duration, frame size, and frame rate. **ffmpeg** creates accurate re-encoded MP4 clips or fast stream-copy MKV clips.
- **faster-whisper** runs locally, detects the spoken language, produces word timestamps, and processes long videos in ten-minute audio chunks. Model weights are cached locally in the data directory after first use.
- **ASS karaoke captions** group transcript words into readable lines and animate each word. Exports normalize audio to -14 LUFS and render H.264/AAC MP4s using NVENC when available, with CPU encoder fallback.
- **Highlight analysis** combines local Ollama transcript moments (when available), transcript keyword heuristics, streamed audio-energy/laughter-like cues, PySceneDetect scene changes, and sampled visual motion. Weighted scores are normalized to 0–100; missing/unavailable signals are reported and Ollama is never required.
- **Clip metadata** uses the local Ollama model when available and local heuristics otherwise. Each created clip receives `.json` and `.txt` title/description/hashtag sidecars; captioned exports copy those sidecars beside the MP4.
- **Local storage** defaults to `C:\1Clipping\data` and contains the SQLite database, source videos, clips, Whisper models, custom fonts, and finished exports.

## Folder structure

```text
clipper-pro-max/
├── backend/
│   ├── app/
│   │   ├── routes/                 # Projects, media, highlights, transcription, exports, jobs
│   │   ├── services/               # Ollama, signal analysis, Whisper, caption rendering, worker
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── schemas.py
│   │   └── main.py
│   ├── tests/                      # Cutting, captions, transcription, and highlight ranking tests
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/                        # App pages, API client, and Tailwind styles
│   ├── Dockerfile
│   ├── nginx.conf
│   └── package.json
├── cookies/                        # Optional local yt-dlp cookies file
├── docker-compose.yml
├── .env.example
├── README.md
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
4. Choose a video, enter start/end times in seconds, and select **Accurate re-encode** or **Fast stream copy**. Stream-copy cuts are fast but start at nearby keyframes and are saved as MKV for broad codec compatibility.
5. In **Transcript**, choose a Whisper model and device, then generate a word-timed transcript. The first run downloads the selected model weights into the local data folder; later runs reuse the cache. GPU mode falls back to CPU when CUDA cannot run.
6. Click transcript words to seek the player; double-click a word to edit it, then save your changes. The active word follows playback.
7. Select a clip in **Export captioned clip**, choose the karaoke preset, font, color, size, outline, position, resolution, bitrate, and frame rate, then export. Upload optional TrueType `.ttf` fonts from the same panel.
8. Download the finished captioned MP4 from the export list or the clip’s separate SRT from the Clips list. Exports are saved under `C:\1Clipping\data\output`.
9. In **Auto-find highlights**, choose a duration range, result count, overlap handling, optional gaming mode, and signal weights. Run analysis, review the ranked score/reason/thumbnail, edit times or title, accept/reject, and create a clip. New clips receive local title/description/hashtag `.json` and `.txt` files beside the clip.

URL downloading depends on yt-dlp extractor support and source availability. Some sources require a cookies file. No login credentials are collected by this app. Audio laughter/excitement cues are local signal heuristics (loudness, burstiness, and zero-crossing features), not a dedicated trained emotion classifier.

## Test

From the project root, run the cutting and caption timing/ASS tests and frontend production build:

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

Caption tests verify word timing, ASS karaoke generation, preset validation, and SRT timestamps. Highlight tests cover weighted scoring, ranking, duration bounds, and overlap behavior. Cutting tests mock ffmpeg, so unit tests do not need media fixtures. To verify the complete flow end to end, upload a video with clear speech and visual changes, generate/edit its transcript, run highlight analysis, create a suggested clip, export captions, and download the MP4/SRT and metadata sidecars.
