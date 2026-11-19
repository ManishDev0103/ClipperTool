import {
  ChangeEvent,
  DragEvent,
  FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  apiRequest,
  apiUrl,
  Clip,
  ExportedClip,
  HighlightAnalysis,
  HighlightSuggestion,
  Job,
  ProjectDetail,
  ProjectSummary,
  Transcript,
  TranscriptWord,
  Video,
  videoUrl,
} from "./api";

const ACCEPTED_FILES = ".mp4,.mkv,.mov,.webm,.avi";
const CHUNK_SIZE = 8 * 1024 * 1024;
const MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024;
type CaptionPreset = "bold_yellow" | "minimal_white" | "boxed" | "glow";

function Header() {
  return (
    <header className="topbar">
      <Link to="/" className="brand">LOCAL CLIPPER</Link>
      <nav>
        <Link to="/">Projects</Link>
        <Link to="/settings">Settings</Link>
      </nav>
    </header>
  );
}

function HomePage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [projectName, setProjectName] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const loadProjects = useCallback(async () => {
    const result = await apiRequest<ProjectSummary[]>("/api/projects");
    setProjects(result);
    setSelectedProject((current) => current || result[0]?.id || "");
  }, []);

  useEffect(() => {
    loadProjects().catch((reason: unknown) =>
      setError(reason instanceof Error ? reason.message : "Could not load projects."),
    );
  }, [loadProjects]);

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      const project = await apiRequest<ProjectSummary>("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: projectName }),
      });
      setProjectName("");
      setProjects((current) => [project, ...current]);
      setSelectedProject(project.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create project.");
    }
  }

  function onStarted(jobId: string) {
    navigate(`/projects/${selectedProject}?job=${jobId}`);
  }

  return (
    <main className="page-shell">
      <section className="hero">
        <p className="eyebrow">YOUR MEDIA STAYS ON THIS MACHINE</p>
        <h1>Make a moment<br /><span>worth sharing.</span></h1>
        <p className="muted">Paste a video link or upload a file to start a local project.</p>
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">START HERE</p><h2>Bring in a video</h2></div>
          <label className="field compact">
            <span>Project</span>
            <select value={selectedProject} onChange={(event) => setSelectedProject(event.target.value)}>
              <option value="">Choose a project</option>
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </label>
        </div>
        {selectedProject ? (
          <IngestPanel projectId={selectedProject} onStarted={onStarted} onUploaded={() => navigate(`/projects/${selectedProject}`)} />
        ) : (
          <p className="notice">Create a project below before adding media.</p>
        )}
        {error && <p className="error">{error}</p>}
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">LIBRARY</p><h2>Your projects</h2></div>
          <span className="muted">{projects.length} total</span>
        </div>
        <form className="create-project" onSubmit={createProject}>
          <input
            aria-label="New project name"
            placeholder="Name a project"
            maxLength={120}
            value={projectName}
            onChange={(event) => setProjectName(event.target.value)}
          />
          <button type="submit" disabled={!projectName.trim()}>Create project</button>
        </form>
        {projects.length === 0 ? (
          <p className="muted empty-state">Your projects will appear here.</p>
        ) : (
          <div className="project-grid">
            {projects.map((project) => (
              <Link className="project-card" to={`/projects/${project.id}`} key={project.id}>
                <span className="project-icon">▶</span>
                <strong>{project.name}</strong>
                <span className="muted">{project.video_count} videos · {project.clip_count} clips</span>
              </Link>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

type IngestPanelProps = {
  projectId: string;
  onStarted: (jobId: string) => void;
  onUploaded: () => void;
};

function IngestPanel({ projectId, onStarted, onUploaded }: IngestPanelProps) {
  const [url, setUrl] = useState("");
  const [quality, setQuality] = useState("best");
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);

  async function submitLink(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await apiRequest<{ job_id: string }>(`/api/projects/${projectId}/downloads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, quality }),
      });
      onStarted(result.job_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not queue download.");
    } finally {
      setBusy(false);
    }
  }

  async function uploadFile(file?: File) {
    if (!file) return;
    setError("");
    if (file.size === 0 || file.size > MAX_FILE_SIZE) {
      setError("Choose a non-empty video file no larger than 10 GB.");
      return;
    }
    if (!/\.(mp4|mkv|mov|webm|avi)$/i.test(file.name)) {
      setError("Supported file types: mp4, mkv, mov, webm, and avi.");
      return;
    }

    setBusy(true);
    setUploadProgress(0);
    try {
      const session = await apiRequest<{ upload_id: string }>("/api/projects/" + projectId + "/uploads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, size: file.size }),
      });
      for (let offset = 0; offset < file.size; offset += CHUNK_SIZE) {
        const chunk = file.slice(offset, Math.min(offset + CHUNK_SIZE, file.size));
        await apiRequest<{ received_size: number }>(
          `/api/uploads/${session.upload_id}?offset=${offset}`,
          { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: chunk },
        );
        setUploadProgress(Math.round(((offset + chunk.size) / file.size) * 100));
      }
      await apiRequest(`/api/uploads/${session.upload_id}/complete`, { method: "POST" });
      onUploaded();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed.");
    } finally {
      setBusy(false);
      setUploadProgress(null);
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    void uploadFile(event.target.files?.[0]);
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    void uploadFile(event.dataTransfer.files[0]);
  }

  return (
    <div className="ingest-grid">
      <form className="link-form" onSubmit={submitLink}>
        <label className="field">
          <span>Video link</span>
          <input
            type="url"
            placeholder="https://…"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            required
          />
        </label>
        <label className="field">
          <span>Maximum quality</span>
          <select value={quality} onChange={(event) => setQuality(event.target.value)}>
            <option value="best">Best available</option>
            <option value="1080p">Up to 1080p</option>
            <option value="720p">Up to 720p</option>
            <option value="480p">Up to 480p</option>
          </select>
        </label>
        <button type="submit" disabled={busy || !url.trim()}>{busy ? "Working…" : "Add link"}</button>
        <p className="muted small">Uses yt-dlp for sites it supports. Downloads are queued locally.</p>
      </form>
      <label
        className={`drop-zone ${dragging ? "dragging" : ""}`}
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input type="file" accept={ACCEPTED_FILES} onChange={onFileChange} disabled={busy} />
        <span className="upload-icon">↑</span>
        <strong>{uploadProgress === null ? "Drop a video here" : `Uploading ${uploadProgress}%`}</strong>
        <span className="muted small">MP4, MKV, MOV, WEBM, AVI · up to 10 GB</span>
        {uploadProgress !== null && <span className="progress-track"><span style={{ width: `${uploadProgress}%` }} /></span>}
      </label>
      {error && <p className="error ingest-error">{error}</p>}
    </div>
  );
}

function useJobProgress(jobId: string | null, onFinished: () => void) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  useEffect(() => {
    setJob(null);
    setError("");
    if (!jobId) return;

    let active = true;
    let terminalHandled = false;
    let poll = 0;
    const update = (next: Job) => {
      if (!active) return;
      setJob(next);
      if (!terminalHandled && (next.status === "completed" || next.status === "failed")) {
        terminalHandled = true;
        window.clearInterval(poll);
        finishedRef.current();
      }
    };
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/ws/jobs/${jobId}`);
    socket.onmessage = (event) => {
      try {
        update(JSON.parse(event.data) as Job);
      } catch {
        setError("Received an invalid job update.");
      }
    };
    socket.onerror = () => setError("Live status connection failed; checking job status over HTTP.");
    poll = window.setInterval(() => {
      apiRequest<Job>(`/api/jobs/${jobId}`)
        .then(update)
        .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not read job status."));
    }, 2000);

    return () => {
      active = false;
      socket.close();
      window.clearInterval(poll);
    };
  }, [jobId]);
  return { job, error };
}

function ProjectPage() {
  const { projectId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const initialJobId = searchParams.get("job");
  const [jobId, setJobId] = useState<string | null>(initialJobId);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [selectedVideoId, setSelectedVideoId] = useState("");
  const [startTime, setStartTime] = useState("0");
  const [endTime, setEndTime] = useState("");
  const [mode, setMode] = useState<"accurate" | "copy">("accurate");
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [transcriptError, setTranscriptError] = useState("");
  const [transcriptMessage, setTranscriptMessage] = useState("");
  const [transcriptModel, setTranscriptModel] = useState("small");
  const [transcriptDevice, setTranscriptDevice] = useState<"auto" | "cpu">("auto");
  const [savingTranscript, setSavingTranscript] = useState(false);
  const [editingWord, setEditingWord] = useState<number | null>(null);
  const [playbackTime, setPlaybackTime] = useState(0);
  const [captionClipId, setCaptionClipId] = useState("");
  const [captionPreset, setCaptionPreset] = useState<CaptionPreset>("bold_yellow");
  const [captionColor, setCaptionColor] = useState("#FFD84D");
  const [captionOutlineColor, setCaptionOutlineColor] = useState("#111111");
  const [captionOutline, setCaptionOutline] = useState(3);
  const [captionSize, setCaptionSize] = useState(72);
  const [captionPosition, setCaptionPosition] = useState<"top" | "middle" | "bottom">("bottom");
  const [captionBitrate, setCaptionBitrate] = useState("8M");
  const [captionFps, setCaptionFps] = useState(30);
  const [captionResolution, setCaptionResolution] = useState("1080x1920");
  const [fonts, setFonts] = useState<{ filename: string; name: string }[]>([]);
  const [captionFont, setCaptionFont] = useState("");
  const [exportError, setExportError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [highlightAnalysis, setHighlightAnalysis] = useState<HighlightAnalysis | null>(null);
  const [highlightError, setHighlightError] = useState("");
  const [highlightMinDuration, setHighlightMinDuration] = useState(20);
  const [highlightMaxDuration, setHighlightMaxDuration] = useState(60);
  const [highlightCount, setHighlightCount] = useState(8);
  const [highlightOverlap, setHighlightOverlap] = useState(false);
  const [gamingMode, setGamingMode] = useState(false);
  const [ollamaModel, setOllamaModel] = useState("qwen2.5:7b");
  const [signalWeights, setSignalWeights] = useState({ transcript: 0.4, audio: 0.25, scene: 0.15, motion: 0.2 });
  const playerRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const { job, error: jobError } = useJobProgress(jobId, () => setRefresh((value) => value + 1));
  const video = detail?.videos.find((item) => item.id === selectedVideoId);
  const captionClip = detail?.clips.find((clip) => clip.id === captionClipId);
  const canExportCaptions = !!captionClip
    && transcript?.video_id === captionClip.video_id
    && !!transcript.words.length;

  const loadProject = useCallback(async () => {
    const project = await apiRequest<ProjectDetail>(`/api/projects/${projectId}`);
    setDetail(project);
    setSelectedVideoId((current) =>
      project.videos.some((item) => item.id === current) ? current : project.videos[0]?.id ?? "",
    );
  }, [projectId]);

  useEffect(() => {
    loadProject().catch((reason: unknown) =>
      setError(reason instanceof Error ? reason.message : "Could not load project."),
    );
  }, [loadProject, refresh]);

  useEffect(() => {
    if (video) setEndTime(String(Math.min(video.duration, 30)));
  }, [video?.id]);

  useEffect(() => {
    setTranscript(null);
    setTranscriptError("");
    setTranscriptMessage("");
    if (!video) return;
    apiRequest<Transcript>(`/api/videos/${video.id}/transcript`)
      .then(setTranscript)
      .catch((reason: unknown) =>
        setTranscriptError(reason instanceof Error ? reason.message : "Could not load transcript."),
      );
  }, [video?.id, refresh]);

  useEffect(() => {
    apiRequest<{ filename: string; name: string }[]>("/api/fonts")
      .then(setFonts)
      .catch((reason: unknown) =>
        setExportError(reason instanceof Error ? reason.message : "Could not load caption fonts."),
      );
  }, []);

  useEffect(() => {
    setHighlightAnalysis(null);
    setHighlightError("");
    if (!video) return;
    apiRequest<HighlightAnalysis>(`/api/videos/${video.id}/highlights`)
      .then(setHighlightAnalysis)
      .catch((reason: unknown) =>
        setHighlightError(reason instanceof Error ? reason.message : "Could not load highlight suggestions."),
      );
  }, [video?.id, refresh]);

  useEffect(() => {
    if (detail?.clips.length && !detail.clips.some((clip) => clip.id === captionClipId)) {
      const defaultClip = detail.clips.find((clip) => clip.video_id === selectedVideoId) ?? detail.clips[0];
      setCaptionClipId(defaultClip.id);
    }
  }, [detail?.clips, captionClipId, selectedVideoId]);

  const activeTranscriptIndex = transcript?.words.findIndex(
    (word) => playbackTime >= word.start_time && playbackTime <= word.end_time,
  ) ?? -1;

  async function generateTranscript() {
    if (!video) return;
    setTranscriptError("");
    setTranscriptMessage("");
    try {
      const result = await apiRequest<{ job_id: string }>(`/api/videos/${video.id}/transcriptions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: transcriptModel, device: transcriptDevice }),
      });
      setJobId(result.job_id);
    } catch (reason) {
      setTranscriptError(reason instanceof Error ? reason.message : "Could not queue transcription.");
    }
  }

  async function findHighlights() {
    if (!video) return;
    setHighlightError("");
    try {
      const result = await apiRequest<{ job_id: string }>(`/api/videos/${video.id}/highlights`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          min_duration: highlightMinDuration,
          max_duration: highlightMaxDuration,
          number_of_clips: highlightCount,
          allow_overlap: highlightOverlap,
          gaming_mode: gamingMode,
          ollama_model: ollamaModel,
          weights: signalWeights,
        }),
      });
      setJobId(result.job_id);
    } catch (reason) {
      setHighlightError(reason instanceof Error ? reason.message : "Could not queue highlight analysis.");
    }
  }

  async function updateHighlight(suggestionId: string, changes: {
    start_time?: number;
    end_time?: number;
    title?: string;
    status?: "accepted" | "rejected";
  }) {
    await apiRequest(`/api/highlights/${suggestionId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    });
    setRefresh((value) => value + 1);
  }

  async function createSuggestedClip(suggestionId: string) {
    setHighlightError("");
    try {
      const result = await apiRequest<{ job_id: string }>(`/api/highlights/${suggestionId}/create-clip`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "accurate" }),
      });
      setJobId(result.job_id);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setHighlightError(reason instanceof Error ? reason.message : "Could not create the suggested clip.");
    }
  }

  function previewHighlight(startTime: number) {
    if (!playerRef.current) return;
    playerRef.current.currentTime = startTime;
    playerRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function saveTranscript() {
    if (!video || !transcript) return;
    setSavingTranscript(true);
    setTranscriptError("");
    setTranscriptMessage("");
    try {
      await apiRequest(`/api/videos/${video.id}/transcript`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          words: transcript.words.map(({ start_time, end_time, text }) => ({ start_time, end_time, text })),
        }),
      });
      setEditingWord(null);
      setTranscriptMessage("Transcript edits saved.");
    } catch (reason) {
      setTranscriptError(reason instanceof Error ? reason.message : "Could not save transcript.");
    } finally {
      setSavingTranscript(false);
    }
  }

  function editTranscriptWord(wordIndex: number, text: string) {
    setTranscript((current) => current && ({
      ...current,
      words: current.words.map((word) =>
        word.word_index === wordIndex ? { ...word, text } : word,
      ),
    }));
  }

  async function uploadCaptionFont(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setExportError("");
    if (!file.name.toLowerCase().endsWith(".ttf") || file.size > 20 * 1024 * 1024) {
      setExportError("Choose a TrueType .ttf font file no larger than 20 MB.");
      return;
    }
    try {
      const uploaded = await apiRequest<{ filename: string; name: string }>(
        `/api/fonts?filename=${encodeURIComponent(file.name)}`,
        { method: "POST", headers: { "Content-Type": "font/ttf" }, body: file },
      );
      setFonts((current) => [...current.filter((font) => font.filename !== uploaded.filename), uploaded]);
      setCaptionFont(uploaded.filename);
    } catch (reason) {
      setExportError(reason instanceof Error ? reason.message : "Could not upload font.");
    }
  }

  async function exportCaptionedClip(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setExportError("");
    if (!captionClipId) {
      setExportError("Create a clip before exporting captions.");
      return;
    }
    setExporting(true);
    try {
      const [width, height] = captionResolution.split("x").map(Number);
      const result = await apiRequest<{ job_id: string }>(`/api/clips/${captionClipId}/exports`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preset: captionPreset,
          font_name: fonts.find((font) => font.filename === captionFont)?.name ?? "Arial",
          font_file: captionFont || null,
          font_size: captionSize,
          color: captionColor,
          outline_color: captionOutlineColor,
          outline: captionOutline,
          position: captionPosition,
          bitrate: captionBitrate,
          fps: captionFps,
          width,
          height,
        }),
      });
      setJobId(result.job_id);
    } catch (reason) {
      setExportError(reason instanceof Error ? reason.message : "Could not queue caption export.");
    } finally {
      setExporting(false);
    }
  }

  async function createClip(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!video) return;
    setError("");
    try {
      const result = await apiRequest<{ job_id: string }>(`/api/videos/${video.id}/clips`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_time: Number(startTime), end_time: Number(endTime), mode }),
      });
      setJobId(result.job_id);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not queue clip.");
    }
  }

  if (!detail) {
    return <main className="page-shell"><p className="muted">{error || "Loading project…"}</p></main>;
  }

  return (
    <main className="page-shell">
      <div className="page-title-row">
        <div><Link to="/" className="back-link">← All projects</Link><h1>{detail.project.name}</h1></div>
        {job && <JobCard job={job} />}
      </div>
      {(error || jobError) && <p className="error">{error || jobError}</p>}
      <section className="panel">
        <div className="section-heading"><div><p className="eyebrow">ADD MEDIA</p><h2>Link or upload</h2></div></div>
        <IngestPanel
          projectId={projectId}
          onStarted={(id) => setJobId(id)}
          onUploaded={() => { setRefresh((value) => value + 1); setError(""); }}
        />
      </section>
      <section className="editor-grid">
        <div className="panel">
          <div className="section-heading"><div><p className="eyebrow">SOURCE</p><h2>Video preview</h2></div></div>
          {detail.videos.length > 0 ? (
            <>
              <label className="field">
                <span>Choose a video</span>
                <select value={selectedVideoId} onChange={(event) => setSelectedVideoId(event.target.value)}>
                  {detail.videos.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
                </select>
              </label>
              {video && (
                <>
                  <video
                    ref={playerRef}
                    className="video-player"
                    controls
                    preload="metadata"
                    src={videoUrl(video.file_url)}
                    onTimeUpdate={(event) => setPlaybackTime(event.currentTarget.currentTime)}
                  />
                  <p className="muted small">
                    {formatTime(video.duration)}{video.width && video.height ? ` · ${video.width}×${video.height}` : ""}
                    {video.fps ? ` · ${video.fps.toFixed(2)} fps` : ""}
                  </p>
                </>
              )}
            </>
          ) : <p className="empty-state muted">Add a link or upload a video to preview it here.</p>}
        </div>

        <div className="panel">
          <div className="section-heading"><div><p className="eyebrow">EDIT</p><h2>Create a clip</h2></div></div>
          {video ? (
            <form className="clip-form" onSubmit={createClip}>
              <div className="time-inputs">
                <label className="field"><span>Start (seconds)</span><input type="number" min="0" step="0.1" value={startTime} onChange={(event) => setStartTime(event.target.value)} required /></label>
                <label className="field"><span>End (seconds)</span><input type="number" min="0.1" step="0.1" max={video.duration} value={endTime} onChange={(event) => setEndTime(event.target.value)} required /></label>
              </div>
              <label className="field">
                <span>Cut mode</span>
                <select value={mode} onChange={(event) => setMode(event.target.value as "accurate" | "copy")}>
                  <option value="accurate">Accurate re-encode (slower)</option>
                  <option value="copy">Fast stream copy (keyframe-limited)</option>
                </select>
              </label>
              <button type="submit" disabled={!!job && (job.status === "queued" || job.status === "running")}>Create clip</button>
              <p className="muted small">Times are seconds from the start of the selected video.</p>
            </form>
          ) : <p className="empty-state muted">Choose a video to enable clipping.</p>}
        </div>
      </section>
      <section className="panel transcript-panel">
        <div className="section-heading">
          <div><p className="eyebrow">WORDS & TIMING</p><h2>Transcript</h2></div>
          <div className="transcript-controls">
            <label className="field compact">
              <span>Whisper model</span>
              <select value={transcriptModel} onChange={(event) => setTranscriptModel(event.target.value)}>
                {["tiny", "base", "small", "medium", "large-v3"].map((model) => <option key={model} value={model}>{model}</option>)}
              </select>
            </label>
            <label className="field compact">
              <span>Device</span>
              <select value={transcriptDevice} onChange={(event) => setTranscriptDevice(event.target.value as "auto" | "cpu")}>
                <option value="auto">GPU with CPU fallback</option>
                <option value="cpu">CPU</option>
              </select>
            </label>
            <button type="button" disabled={!video || (!!job && ["queued", "running"].includes(job.status) && job.kind === "transcription")} onClick={() => void generateTranscript()}>
              {transcript?.words.length ? "Transcribe again" : "Generate transcript"}
            </button>
          </div>
        </div>
        {transcriptError && <p className="error">{transcriptError}</p>}
        {transcript?.language && <p className="muted small">Detected language: {transcript.language} · {transcript.model} model · {transcript.words.length} words</p>}
        {transcript && transcript.words.length > 0 ? (
          <>
            <div className="transcript-words">
              {transcript.words.map((word: TranscriptWord) => (
                <span className={`transcript-token ${activeTranscriptIndex === word.word_index ? "active-token" : ""}`} key={word.word_index}>
                  <button
                    type="button"
                    className="word-time"
                    title="Jump to this word"
                    onClick={() => {
                      if (playerRef.current) playerRef.current.currentTime = word.start_time;
                    }}
                  >{formatTime(word.start_time)}</button>
                  {editingWord === word.word_index ? (
                    <input
                      className="word-edit"
                      autoFocus
                      value={word.text}
                      onChange={(event) => editTranscriptWord(word.word_index, event.target.value)}
                      onBlur={() => setEditingWord(null)}
                      onKeyDown={(event) => { if (event.key === "Enter") setEditingWord(null); }}
                      aria-label={`Edit word at ${formatTime(word.start_time)}`}
                    />
                  ) : (
                    <button
                      type="button"
                      className="word-text"
                      title="Click to seek, double-click to edit"
                      onClick={() => { if (playerRef.current) playerRef.current.currentTime = word.start_time; }}
                      onDoubleClick={() => setEditingWord(word.word_index)}
                    >{word.text}</button>
                  )}
                </span>
              ))}
            </div>
            <div className="transcript-footer">
              <span className="muted small">Click a word to seek · double-click a word to edit it</span>
              <button type="button" className="secondary-button" disabled={savingTranscript} onClick={() => void saveTranscript()}>
                {savingTranscript ? "Saving…" : "Save transcript"}
              </button>
            </div>
          </>
        ) : <p className="empty-state muted">Generate a local word-timed transcript to edit and use as captions.</p>}
        {transcriptMessage && <p className="success">{transcriptMessage}</p>}
      </section>
      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">LOCAL MULTI-SIGNAL ANALYSIS</p><h2>Auto-find highlights</h2></div>
          <button
            type="button"
            disabled={!video || (!!job && ["queued", "running"].includes(job.status))}
            onClick={() => void findHighlights()}
          >Find highlights</button>
        </div>
        <div className="highlight-settings">
          <label className="field"><span>Minimum seconds</span><input type="number" min="15" max="180" value={highlightMinDuration} onChange={(event) => setHighlightMinDuration(Number(event.target.value))} /></label>
          <label className="field"><span>Maximum seconds</span><input type="number" min="15" max="180" value={highlightMaxDuration} onChange={(event) => setHighlightMaxDuration(Number(event.target.value))} /></label>
          <label className="field"><span>Number of clips</span><input type="number" min="1" max="30" value={highlightCount} onChange={(event) => setHighlightCount(Number(event.target.value))} /></label>
          <label className="field"><span>Local Ollama model (optional)</span><input value={ollamaModel} onChange={(event) => setOllamaModel(event.target.value)} placeholder="qwen2.5:7b" /></label>
          <label className="check-field"><input type="checkbox" checked={highlightOverlap} onChange={(event) => setHighlightOverlap(event.target.checked)} /><span>Allow overlapping suggestions</span></label>
          <label className="check-field"><input type="checkbox" checked={gamingMode} onChange={(event) => {
            const enabled = event.target.checked;
            setGamingMode(enabled);
            setSignalWeights(enabled
              ? { transcript: 0.15, audio: 0.35, scene: 0.15, motion: 0.35 }
              : { transcript: 0.4, audio: 0.25, scene: 0.15, motion: 0.2 });
          }} /><span>Gaming mode (emphasize audio + motion)</span></label>
        </div>
        <div className="signal-weights">
          {(["transcript", "audio", "scene", "motion"] as const).map((signal) => (
            <label className="weight-control" key={signal}>
              <span>{signal === "scene" ? "Scene changes" : signal[0].toUpperCase() + signal.slice(1)} <strong>{signalWeights[signal].toFixed(2)}</strong></span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={signalWeights[signal]}
                onChange={(event) => setSignalWeights((current) => ({ ...current, [signal]: Number(event.target.value) }))}
              />
            </label>
          ))}
        </div>
        {highlightError && <p className="error">{highlightError}</p>}
        {highlightAnalysis && (
          <>
            <p className="muted small">
              {highlightAnalysis.analysis_mode === "ollama" ? "Local Ollama + audio + visual signals" : "Local audio + visual + transcript heuristics"}
              {" · "}{highlightAnalysis.suggestions.length} ranked suggestions
            </p>
            {highlightAnalysis.warnings.map((warning, index) => <p className="notice" key={`${index}-${warning}`}>{warning}</p>)}
            {highlightAnalysis.suggestions.length === 0 ? (
              <p className="empty-state muted">No suggestions yet. Run analysis on a longer video or adjust the duration range.</p>
            ) : (
              <div className="suggestion-list">
                {highlightAnalysis.suggestions.map((suggestion) => (
                  <HighlightCard
                    key={suggestion.id}
                    suggestion={suggestion}
                    onSave={(changes) => updateHighlight(suggestion.id, changes)}
                    onCreate={() => void createSuggestedClip(suggestion.id)}
                    onPreview={() => previewHighlight(suggestion.start_time)}
                    busy={!!job && ["queued", "running"].includes(job.status)}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </section>
      <section className="panel">
        <div className="section-heading"><div><p className="eyebrow">CAPTION STYLE & DELIVERY</p><h2>Export captioned clip</h2></div></div>
        <form className="export-layout" onSubmit={(event) => void exportCaptionedClip(event)}>
          <div className="export-controls">
            <label className="field">
              <span>Clip</span>
              <select value={captionClipId} onChange={(event) => {
                const nextClip = detail.clips.find((clip) => clip.id === event.target.value);
                setCaptionClipId(event.target.value);
                if (nextClip) setSelectedVideoId(nextClip.video_id);
              }} required>
                <option value="">Choose a clip</option>
                {detail.clips.map((clip) => <option key={clip.id} value={clip.id}>{detail.videos.find((item) => item.id === clip.video_id)?.title ?? "Video"} · {formatTime(clip.start_time)}–{formatTime(clip.end_time)}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Caption preset</span>
              <select value={captionPreset} onChange={(event) => {
                const preset = event.target.value as CaptionPreset;
                setCaptionPreset(preset);
                setCaptionColor(preset === "bold_yellow" ? "#FFD84D" : "#FFFFFF");
                setCaptionOutlineColor(preset === "glow" ? "#FFD84D" : "#111111");
              }}>
                <option value="bold_yellow">Bold yellow</option>
                <option value="minimal_white">Minimal white</option>
                <option value="boxed">Boxed</option>
                <option value="glow">Glow</option>
              </select>
            </label>
            <div className="time-inputs">
              <label className="field"><span>Text color</span><input type="color" value={captionColor} onChange={(event) => setCaptionColor(event.target.value)} /></label>
              <label className="field"><span>Font size</span><input type="number" min="24" max="120" value={captionSize} onChange={(event) => setCaptionSize(Number(event.target.value))} /></label>
            </div>
            <div className="time-inputs">
              <label className="field"><span>Outline color</span><input type="color" value={captionOutlineColor} onChange={(event) => setCaptionOutlineColor(event.target.value)} /></label>
              <label className="field"><span>Outline width</span><input type="number" min="0" max="12" value={captionOutline} onChange={(event) => setCaptionOutline(Number(event.target.value))} /></label>
            </div>
            <div className="time-inputs">
              <label className="field"><span>Caption position</span>
                <select value={captionPosition} onChange={(event) => setCaptionPosition(event.target.value as "top" | "middle" | "bottom")}>
                  <option value="bottom">Bottom</option><option value="middle">Middle</option><option value="top">Top</option>
                </select>
              </label>
            </div>
            <label className="field"><span>Font</span>
              <select value={captionFont} onChange={(event) => setCaptionFont(event.target.value)}>
                <option value="">Arial (system default)</option>
                {fonts.map((font) => <option key={font.filename} value={font.filename}>{font.name} (uploaded)</option>)}
              </select>
            </label>
            <label className="font-upload">
              <span>Upload a TrueType font (.ttf)</span>
              <input type="file" accept=".ttf,font/ttf" onChange={(event) => void uploadCaptionFont(event)} />
            </label>
            <div className="time-inputs">
              <label className="field"><span>Resolution</span>
                <select value={captionResolution} onChange={(event) => setCaptionResolution(event.target.value)}>
                  <option value="1080x1920">1080×1920 vertical</option><option value="720x1280">720×1280 vertical</option><option value="1080x1080">1080×1080 square</option>
                </select>
              </label>
              <label className="field"><span>Bitrate</span>
                <select value={captionBitrate} onChange={(event) => setCaptionBitrate(event.target.value)}>
                  {["4M", "8M", "12M", "20M"].map((rate) => <option key={rate} value={rate}>{rate}</option>)}
                </select>
              </label>
            </div>
            <label className="field"><span>Frame rate</span>
              <select value={captionFps} onChange={(event) => setCaptionFps(Number(event.target.value))}>
                <option value={30}>30 fps</option><option value={60}>60 fps</option>
              </select>
            </label>
            <button type="submit" disabled={exporting || !canExportCaptions || (!!job && ["queued", "running"].includes(job.status))}>
              {exporting ? "Queueing…" : "Export captioned MP4"}
            </button>
            {captionClip && !canExportCaptions && <p className="muted small">Generate a transcript for this clip’s source video before exporting captions.</p>}
            {exportError && <p className="error">{exportError}</p>}
            <p className="muted small">H.264/AAC · audio normalized to -14 LUFS · NVENC when available, CPU fallback otherwise.</p>
          </div>
          <div className="caption-preview-wrap">
            <p className="eyebrow">LIVE PREVIEW</p>
            <div className={`caption-preview caption-${captionPosition}`} style={{ fontFamily: fonts.find((font) => font.filename === captionFont)?.name ?? "Arial", fontSize: `clamp(16px, ${captionSize / 3}px, 40px)`, color: captionColor, textShadow: `0 2px ${Math.max(1, captionOutline)}px ${captionOutlineColor}` }}>
              <span className="preview-inactive">Make every</span> <strong>moment</strong> <span className="preview-inactive">count ✨</span>
            </div>
            <p className="muted small">Preview updates with your selected color, size, and position. Emoji rendering depends on the selected font’s glyph support.</p>
          </div>
        </form>
        {detail.exports.length > 0 && (
          <div className="clip-list export-list">
            {detail.exports.map((exported: ExportedClip) => (
              <div className="clip-row" key={exported.id}>
                <div className="clip-mark">✦</div>
                <div className="clip-info"><strong>Captioned MP4</strong><span className="muted small">{exported.settings.preset} · {exported.settings.encoder ?? "H.264"} · {exported.settings.fps} fps · {exported.settings.bitrate}</span></div>
                <a className="button secondary" href={apiUrl(exported.file_url)} download>Download</a>
              </div>
            ))}
          </div>
        )}
      </section>
      <section className="panel">
        <div className="section-heading"><div><p className="eyebrow">OUTPUTS</p><h2>Clips</h2></div><span className="muted">{detail.clips.length} clips</span></div>
        {detail.clips.length === 0 ? <p className="empty-state muted">Finished clips will be listed here.</p> : (
          <div className="clip-list">
            {detail.clips.map((clip) => <ClipRow key={clip.id} clip={clip} video={detail.videos.find((item) => item.id === clip.video_id)} />)}
          </div>
        )}
      </section>
    </main>
  );
}

function ClipRow({ clip, video }: { clip: Clip; video?: Video }) {
  return (
    <div className="clip-row">
      <div className="clip-mark">▶</div>
      <div className="clip-info"><strong>{video?.title ?? "Video clip"}</strong><span className="muted small">{formatTime(clip.start_time)} – {formatTime(clip.end_time)} · {clip.mode === "copy" ? "Stream copy" : "Accurate"}</span></div>
      <div className="clip-actions">
        <a className="button secondary" href={apiUrl(`/api/clips/${clip.id}/subtitles.srt`)} download>SRT</a>
        <a className="button secondary" href={apiUrl(clip.file_url)} download>Download</a>
      </div>
    </div>
  );
}

type SuggestionChanges = {
  start_time?: number;
  end_time?: number;
  title?: string;
  status?: "accepted" | "rejected";
};

function HighlightCard({
  suggestion,
  onSave,
  onCreate,
  onPreview,
  busy,
}: {
  suggestion: HighlightSuggestion;
  onSave: (changes: SuggestionChanges) => Promise<void>;
  onCreate: () => void;
  onPreview: () => void;
  busy: boolean;
}) {
  const [title, setTitle] = useState(suggestion.title);
  const [startTime, setStartTime] = useState(String(suggestion.start_time));
  const [endTime, setEndTime] = useState(String(suggestion.end_time));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setTitle(suggestion.title);
    setStartTime(String(suggestion.start_time));
    setEndTime(String(suggestion.end_time));
  }, [suggestion.title, suggestion.start_time, suggestion.end_time]);

  async function save(changes: SuggestionChanges) {
    setSaving(true);
    setError("");
    try {
      await onSave(changes);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update this suggestion.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <article className={`suggestion-card suggestion-${suggestion.status}`}>
      {suggestion.thumbnail_url ? (
        <button type="button" className="thumbnail-button" onClick={onPreview} title="Preview this moment">
          <img src={apiUrl(suggestion.thumbnail_url)} alt={`Preview: ${suggestion.title}`} loading="lazy" />
          <span className="thumbnail-play">▶</span>
        </button>
      ) : <button type="button" className="thumbnail-placeholder" onClick={onPreview}>Preview</button>}
      <div className="suggestion-content">
        <div className="suggestion-heading">
          <strong>{suggestion.score.toFixed(1)}<small>/100</small></strong>
          <span className={`status-pill status-${suggestion.status}`}>{suggestion.status}</span>
        </div>
        <label className="field"><span>Suggested title</span><input value={title} maxLength={160} onChange={(event) => setTitle(event.target.value)} /></label>
        <div className="time-inputs">
          <label className="field"><span>Start (seconds)</span><input type="number" min="0" step="0.1" value={startTime} onChange={(event) => setStartTime(event.target.value)} /></label>
          <label className="field"><span>End (seconds)</span><input type="number" min="0.1" step="0.1" value={endTime} onChange={(event) => setEndTime(event.target.value)} /></label>
        </div>
        <p className="muted small">{suggestion.reason}</p>
        <div className="signal-badges">
          {Object.entries(suggestion.signals).map(([signal, value]) => (
            <span key={signal}>{signal}: {(value * 100).toFixed(0)}</span>
          ))}
        </div>
        {error && <p className="error">{error}</p>}
        <div className="suggestion-actions">
          <button type="button" className="secondary-button" disabled={saving} onClick={() => void save({
            title,
            start_time: Number(startTime),
            end_time: Number(endTime),
          })}>Save edits</button>
          <button type="button" className="secondary-button" disabled={saving || suggestion.status === "created"} onClick={() => void save({ status: "accepted" })}>Accept</button>
          <button type="button" className="secondary-button reject-button" disabled={saving || suggestion.status === "created"} onClick={() => void save({ status: "rejected" })}>Reject</button>
          <button type="button" disabled={busy || saving || suggestion.status === "rejected" || suggestion.status === "created"} onClick={onCreate}>Create clip</button>
        </div>
      </div>
    </article>
  );
}

function JobCard({ job }: { job: Job }) {
  const isFailed = job.status === "failed";
  const jobNames = {
    download: "Video download",
    clip: "Clip job",
    transcription: `Transcription${job.model ? ` · ${job.model}` : ""}`,
    export: "Captioned export",
    highlight_analysis: "Highlight analysis",
  };
  return (
    <div className={`job-card ${isFailed ? "job-failed" : ""}`}>
      <div className="job-copy"><strong>{jobNames[job.kind]}</strong><span>{job.error || job.message || job.status}</span></div>
      <div className="job-progress"><div className="progress-track"><span style={{ width: `${Math.min(100, job.progress)}%` }} /></div><small>{job.status === "completed" ? "Done" : `${Math.round(job.progress)}% · ${job.status}`}</small></div>
    </div>
  );
}

function SettingsPage() {
  const [dataDir, setDataDir] = useState("");
  const [cookiesFile, setCookiesFile] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    apiRequest<{ data_dir: string; cookies_file: string }>("/api/settings")
      .then((settings) => { setDataDir(settings.data_dir); setCookiesFile(settings.cookies_file); })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load settings."));
  }, []);

  async function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      const saved = await apiRequest<{ cookies_file: string }>("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cookies_file: cookiesFile }),
      });
      setCookiesFile(saved.cookies_file);
      setMessage("Settings saved.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save settings.");
    }
  }

  return (
    <main className="page-shell narrow">
      <div className="page-title-row"><div><p className="eyebrow">PREFERENCES</p><h1>Settings</h1></div></div>
      <form className="panel settings-form" onSubmit={saveSettings}>
        <div><p className="eyebrow">STORAGE</p><h2>Local paths</h2></div>
        <label className="field"><span>Project data directory</span><input value={dataDir} readOnly /></label>
        <p className="muted small">Change this path with DATA_DIR before starting the backend.</p>
        <label className="field"><span>yt-dlp cookies file (optional)</span><input value={cookiesFile} onChange={(event) => setCookiesFile(event.target.value)} placeholder="C:\\Users\\you\\cookies.txt" /></label>
        <p className="muted small">Use an exported cookies.txt file only for content you are authorized to access.</p>
        <div><button type="submit">Save settings</button></div>
        {message && <p className="success">{message}</p>}
        {error && <p className="error">{error}</p>}
      </form>
    </main>
  );
}

function formatTime(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const remainder = value % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

export default function App() {
  return (
    <div className="app min-h-screen">
      <Header />
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/projects/:projectId" element={<ProjectPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
      <footer className="footer">LOCAL-FIRST · NO CLOUD ACCOUNTS · YOUR FILES STAY YOURS</footer>
    </div>
  );
}
