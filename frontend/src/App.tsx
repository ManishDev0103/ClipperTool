import {
  ChangeEvent,
  DragEvent,
  FormEvent,
  PointerEvent as ReactPointerEvent,
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
  LayoutPreset,
  LiveCandidate,
  LiveRecording,
  ProjectDetail,
  ProjectSummary,
  Transcript,
  TranscriptWord,
  Video,
  VideoRegion,
  videoUrl,
} from "./api";
import WaveformTimeline, { TimelineSegment } from "./WaveformTimeline";

const ACCEPTED_FILES = ".mp4,.mkv,.mov,.webm,.avi";
const CHUNK_SIZE = 8 * 1024 * 1024;
const MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024;
type CaptionPreset = "bold_yellow" | "minimal_white" | "boxed" | "glow";
type AspectRatio = "9:16" | "1:1" | "4:5" | "16:9";
type RegionKind = "facecam" | "gameplay";

function createSegmentId(): string {
  return crypto.randomUUID();
}

function defaultRegion(kind: RegionKind): VideoRegion {
  return kind === "facecam"
    ? { x: 0.68, y: 0.04, width: 0.28, height: 0.34 }
    : { x: 0.02, y: 0.28, width: 0.96, height: 0.68 };
}

function Header() {
  return (
    <header className="topbar">
      <Link to="/" className="brand">LOCAL CLIPPER</Link>
      <nav>
        <Link to="/">Projects</Link>
        <Link to="/live">Live</Link>
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

function LivePage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectId, setProjectId] = useState("");
  const [recordings, setRecordings] = useState<LiveRecording[]>([]);
  const [candidates, setCandidates] = useState<LiveCandidate[]>([]);
  const [url, setUrl] = useState("");
  const [bufferMinutes, setBufferMinutes] = useState(30);
  const [diskCapGb, setDiskCapGb] = useState(20);
  const [audioTrigger, setAudioTrigger] = useState(false);
  const [audioThreshold, setAudioThreshold] = useState(-10);
  const [chatTrigger, setChatTrigger] = useState(false);
  const [chatThreshold, setChatThreshold] = useState(30);
  const [clipSeconds, setClipSeconds] = useState(60);
  const [jobId, setJobId] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const { job, error: jobError } = useJobProgress(jobId, () => setRefresh((value) => value + 1));

  useEffect(() => {
    apiRequest<ProjectSummary[]>("/api/projects")
      .then((result) => {
        setProjects(result);
        setProjectId((current) => current || result[0]?.id || "");
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Could not load projects."),
      );
  }, []);

  useEffect(() => {
    if (!projectId) {
      setRecordings([]);
      setCandidates([]);
      return;
    }
    let active = true;
    const load = () => Promise.all([
      apiRequest<LiveRecording[]>(`/api/projects/${projectId}/live-recordings`),
      apiRequest<LiveCandidate[]>(`/api/projects/${projectId}/live-candidates`),
    ]).then(([nextRecordings, nextCandidates]) => {
      if (active) {
        setRecordings(nextRecordings);
        setCandidates(nextCandidates);
      }
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "Could not load live status.");
    });
    void load();
    const timer = window.setInterval(() => { void load(); }, 2500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [projectId, refresh]);

  async function startRecording(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!projectId) return;
    setError("");
    setMessage("");
    try {
      await apiRequest(`/api/projects/${projectId}/live-recordings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url,
          buffer_minutes: bufferMinutes,
          disk_cap_gb: diskCapGb,
          audio_trigger_enabled: audioTrigger,
          audio_peak_dbfs: audioThreshold,
          chat_trigger_enabled: chatTrigger,
          chat_messages_per_minute: chatThreshold,
        }),
      });
      setUrl("");
      setMessage("Recording requested. It will appear below as the worker connects.");
      setRefresh((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start recording.");
    }
  }

  async function stopRecording(recordingId: string) {
    setError("");
    try {
      await apiRequest(`/api/live-recordings/${recordingId}/stop`, { method: "POST" });
      setRefresh((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not stop recording.");
    }
  }

  async function queueClip(recordingId: string, candidateId?: string) {
    setError("");
    setMessage("");
    try {
      const path = candidateId
        ? `/api/live-candidates/${candidateId}/clip`
        : `/api/live-recordings/${recordingId}/clip`;
      const result = await apiRequest<{ job_id: string }>(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seconds: clipSeconds }),
      });
      setJobId(result.job_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not queue live clip.");
    }
  }

  const activeRecordings = recordings.filter((recording) =>
    ["starting", "recording", "reconnecting"].includes(recording.status),
  );

  return (
    <main className="page-shell">
      <div className="page-title-row">
        <div><p className="eyebrow">LIVE CAPTURE</p><h1>Record and clip in the moment</h1></div>
        {job && <JobCard job={job} />}
      </div>
      {(error || jobError) && <p className="error">{error || jobError}</p>}
      {message && <p className="success live-message">{message}</p>}
      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">NEW RECORDING</p><h2>Start a rolling buffer</h2></div>
          <label className="field compact">
            <span>Project</span>
            <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </label>
        </div>
        {projects.length === 0 ? (
          <p className="notice">Create a project before starting a live recording.</p>
        ) : (
          <form className="live-form" onSubmit={(event) => void startRecording(event)}>
            <label className="field live-url">
              <span>Live stream URL</span>
              <input type="url" required value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://…" />
            </label>
            <label className="field">
              <span>Rolling buffer (minutes)</span>
              <input type="number" min={1} max={240} value={bufferMinutes} onChange={(event) => setBufferMinutes(Number(event.target.value))} />
            </label>
            <label className="field">
              <span>Disk cap (GB)</span>
              <input type="number" min={0.25} max={500} step={0.25} value={diskCapGb} onChange={(event) => setDiskCapGb(Number(event.target.value))} />
            </label>
            <label className="check-field"><input type="checkbox" checked={audioTrigger} onChange={(event) => setAudioTrigger(event.target.checked)} />Create candidates on loudness spikes</label>
            <label className="field">
              <span>Audio peak threshold (dBFS)</span>
              <input type="number" min={-60} max={0} value={audioThreshold} onChange={(event) => setAudioThreshold(Number(event.target.value))} disabled={!audioTrigger} />
            </label>
            <label className="check-field"><input type="checkbox" checked={chatTrigger} onChange={(event) => setChatTrigger(event.target.checked)} />Create candidates on Twitch/YouTube chat spikes</label>
            <label className="field">
              <span>Chat messages per minute</span>
              <input type="number" min={1} max={10000} value={chatThreshold} onChange={(event) => setChatThreshold(Number(event.target.value))} disabled={!chatTrigger} />
            </label>
            <button type="submit" disabled={!projectId || !url.trim()}>Start recording</button>
            <p className="muted small live-note">Buffer cleanup removes the oldest completed segments to stay within the configured time and disk limits. Chat spikes use Twitch IRC or YouTube live chat; unsupported sites can still use loudness triggers.</p>
          </form>
        )}
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">RECORDINGS</p><h2>Live status</h2></div>
          <label className="field compact">
            <span>Clip length (seconds)</span>
            <input type="number" min={1} max={600} value={clipSeconds} onChange={(event) => setClipSeconds(Number(event.target.value))} />
          </label>
        </div>
        <div className="live-presets">
          {[30, 60, 90].map((seconds) => (
            <button key={seconds} type="button" className={`button secondary ${clipSeconds === seconds ? "selected" : ""}`} onClick={() => setClipSeconds(seconds)}>{seconds}s</button>
          ))}
          <span className="muted small">Or enter a custom duration above (1–600 seconds).</span>
        </div>
        {recordings.length === 0 ? <p className="muted empty-state">No live recordings in this project yet.</p> : (
          <div className="live-recording-list">
            {recordings.map((recording) => {
              const isActive = activeRecordings.includes(recording);
              return (
                <article className="live-recording-card" key={recording.id}>
                  <div className="live-recording-heading">
                    <span className={`status-pill status-${recording.status}`}>{recording.status}</span>
                    <span className="muted small">{recording.reconnect_count} reconnects</span>
                  </div>
                  <strong className="live-source">{recording.source_url}</strong>
                  <p className="muted small">
                    Buffer {recording.buffered_seconds.toFixed(0)}s / {recording.buffer_seconds}s
                    {" · "}{(recording.buffer_size_bytes / (1024 * 1024)).toFixed(1)} MB / {(recording.disk_cap_bytes / (1024 * 1024 * 1024)).toFixed(1)} GB
                  </p>
                  {recording.last_error && <p className="error live-error">{recording.last_error}</p>}
                  <div className="live-actions">
                    <button type="button" disabled={!isActive} onClick={() => void queueClip(recording.id)}>Clip last {clipSeconds}s</button>
                    <button type="button" className="button secondary" disabled={!isActive} onClick={() => void stopRecording(recording.id)}>Stop</button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="section-heading"><div><p className="eyebrow">AUTO TRIGGERS</p><h2>Clip candidates</h2></div></div>
        {candidates.length === 0 ? <p className="muted empty-state">Audio or chat spikes will appear here when enabled.</p> : (
          <div className="live-candidate-list">
            {candidates.map((candidate) => (
              <article className="live-candidate" key={candidate.id}>
                <div><strong>{candidate.reason}</strong><p className="muted small">{new Date(candidate.trigger_at).toLocaleString()}</p></div>
                {candidate.video_id ? (
                  <Link className="button secondary" to={`/projects/${projectId}`}>Open clip</Link>
                ) : (
                  <button type="button" onClick={() => void queueClip(candidate.recording_id, candidate.id)}>Clip last {clipSeconds}s</button>
                )}
              </article>
            ))}
          </div>
        )}
      </section>

      {job?.status === "completed" && (
        <p className="success live-message">Live clip is ready in the selected project's video list. <Link to={`/projects/${projectId}`}>Open the editor →</Link></p>
      )}
      {job?.status === "failed" && job.error && <p className="error">{job.error}</p>}
      <section className="panel hotkey-panel">
        <div><p className="eyebrow">WINDOWS HOTKEY</p><h2>Ctrl + Alt + C</h2></div>
        <p className="muted small">Run <code>run_live_hotkey.bat</code> in a separate window to clip the most recently started active recording while another app is focused. Use <code>run_live_hotkey.bat --seconds 90</code> to change its duration.</p>
      </section>
    </main>
  );
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
  const [segments, setSegments] = useState<TimelineSegment[]>([]);
  const [selectedSegmentId, setSelectedSegmentId] = useState("");
  const [wordPointMode, setWordPointMode] = useState<"preview" | "in" | "out">("preview");
  const [waveformError, setWaveformError] = useState("");
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
  const [aspectRatio, setAspectRatio] = useState<AspectRatio>("9:16");
  const [backgroundFill, setBackgroundFill] = useState<"blur" | "crop" | "pad">("blur");
  const [layoutMode, setLayoutMode] = useState<"single" | "gaming_split">("single");
  const [smartReframe, setSmartReframe] = useState(false);
  const [facecamRegion, setFacecamRegion] = useState<VideoRegion>(defaultRegion("facecam"));
  const [gameplayRegion, setGameplayRegion] = useState<VideoRegion>(defaultRegion("gameplay"));
  const [activeRegion, setActiveRegion] = useState<RegionKind>("gameplay");
  const [regionDrag, setRegionDrag] = useState<{
    kind: RegionKind;
    mode: "draw" | "move" | "resize";
    startX: number;
    startY: number;
    original: VideoRegion;
  } | null>(null);
  const [layoutPresets, setLayoutPresets] = useState<LayoutPreset[]>([]);
  const [presetName, setPresetName] = useState("");
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [reframeTrack, setReframeTrack] = useState<{ time: number; x: number; y: number }[]>([]);
  const [reframeError, setReframeError] = useState("");
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
  const playerRef = useRef<HTMLVideoElement | null>(null);
  const previewVideoRefs = useRef<(HTMLVideoElement | null)[]>([]);
  const regionOverlayRef = useRef<HTMLDivElement>(null);
  const pendingTimelineRef = useRef<{ videoId: string; start: number; end: number } | null>(null);
  const [sourceMedia, setSourceMedia] = useState<HTMLVideoElement | null>(null);
  const setVideoPlayerRef = useCallback((element: HTMLVideoElement | null) => {
    playerRef.current = element;
    setSourceMedia(element);
  }, []);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const { job, error: jobError } = useJobProgress(jobId, () => setRefresh((value) => value + 1));
  const video = detail?.videos.find((item) => item.id === selectedVideoId);
  const captionClip = detail?.clips.find((clip) => clip.id === captionClipId);
  const selectedSegment = segments.find((segment) => segment.id === selectedSegmentId);
  const outputDimensions: Record<AspectRatio, [number, number]> = {
    "9:16": [1080, 1920],
    "1:1": [1080, 1080],
    "4:5": [1080, 1350],
    "16:9": [1920, 1080],
  };
  const [previewWidth, previewHeight] = outputDimensions[aspectRatio];
  const previewFocus = getTrackFocus(
    reframeTrack,
    Math.max(0, playbackTime - (selectedSegment?.start_time ?? 0)),
  );
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
    if (!video) {
      setSegments([]);
      setSelectedSegmentId("");
      return;
    }
    const pending = pendingTimelineRef.current?.videoId === video.id ? pendingTimelineRef.current : null;
    pendingTimelineRef.current = null;
    const start = pending?.start ?? 0;
    const end = pending?.end ?? Math.min(video.duration, 30);
    const initialSegment = { id: createSegmentId(), start_time: start, end_time: end };
    setSegments([initialSegment]);
    setSelectedSegmentId(initialSegment.id);
    setStartTime(String(start));
    setEndTime(String(end));
    setReframeTrack([]);
    setReframeError("");
    setWaveformError("");
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
    apiRequest<LayoutPreset[]>(`/api/projects/${projectId}/layout-presets`)
      .then(setLayoutPresets)
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Could not load layout presets."),
      );
  }, [projectId]);

  useEffect(() => {
    setReframeTrack([]);
    setReframeError("");
    if (!video || !smartReframe || !selectedSegment) return;
    let active = true;
    const timer = window.setTimeout(() => {
      const query = new URLSearchParams({
        start_time: String(selectedSegment.start_time),
        end_time: String(selectedSegment.end_time),
      });
      apiRequest<{ track: { time: number; x: number; y: number }[] }>(
        `/api/videos/${video.id}/reframe-track?${query.toString()}`,
      )
        .then((result) => { if (active) setReframeTrack(result.track); })
        .catch((reason: unknown) => {
          if (active) setReframeError(reason instanceof Error ? reason.message : "Could not track the speaker.");
        });
    }, 450);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [video?.id, smartReframe, selectedSegment?.start_time, selectedSegment?.end_time]);

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
      const [width, height] = outputDimensions[aspectRatio];
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
          aspect_ratio: aspectRatio,
          smart_reframe: smartReframe,
          background_fill: backgroundFill,
          layout: layoutMode,
          facecam_region: facecamRegion,
          gameplay_region: gameplayRegion,
        }),
      });
      setJobId(result.job_id);
    } catch (reason) {
      setExportError(reason instanceof Error ? reason.message : "Could not queue caption export.");
    } finally {
      setExporting(false);
    }
  }

  const handleTimelineRangeChange = useCallback((id: string, start: number, end: number) => {
    setSegments((current) => current.map((segment) =>
      segment.id === id ? { ...segment, start_time: start, end_time: end } : segment,
    ));
    setSelectedSegmentId(id);
    setStartTime(String(start));
    setEndTime(String(end));
  }, []);

  const handleTimelineSelect = useCallback((id: string) => {
    const segment = segments.find((item) => item.id === id);
    if (!segment) return;
    setSelectedSegmentId(id);
    setStartTime(String(segment.start_time));
    setEndTime(String(segment.end_time));
  }, [segments]);

  function updateSegmentRange(id: string, start: number, end: number) {
    setSegments((current) => current.map((segment) =>
      segment.id === id ? { ...segment, start_time: start, end_time: end } : segment,
    ));
    setSelectedSegmentId(id);
    setStartTime(String(start));
    setEndTime(String(end));
  }

  function setRegion(kind: RegionKind, value: VideoRegion) {
    if (kind === "facecam") setFacecamRegion(value);
    else setGameplayRegion(value);
  }

  function getPointerPosition(event: ReactPointerEvent<HTMLElement>) {
    const bounds = regionOverlayRef.current?.getBoundingClientRect();
    if (!bounds || !bounds.width || !bounds.height) return null;
    return {
      x: Math.max(0, Math.min(0.9999, (event.clientX - bounds.left) / bounds.width)),
      y: Math.max(0, Math.min(0.9999, (event.clientY - bounds.top) / bounds.height)),
    };
  }

  function beginRegionDraw(event: ReactPointerEvent<HTMLElement>) {
    const point = getPointerPosition(event);
    if (!point || point.y >= 0.9) return;
    setActiveRegion(activeRegion);
    const initialRegion = {
      x: point.x,
      y: point.y,
      width: Math.min(0.03, 1 - point.x),
      height: Math.min(0.03, 1 - point.y),
    };
    setRegion(activeRegion, initialRegion);
    setRegionDrag({
      kind: activeRegion,
      mode: "draw",
      startX: point.x,
      startY: point.y,
      original: initialRegion,
    });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function beginRegionMove(event: ReactPointerEvent<HTMLElement>, kind: RegionKind) {
    event.stopPropagation();
    const point = getPointerPosition(event);
    if (!point) return;
    const original = kind === "facecam" ? facecamRegion : gameplayRegion;
    setActiveRegion(kind);
    setRegionDrag({ kind, mode: "move", startX: point.x, startY: point.y, original });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function beginRegionResize(event: ReactPointerEvent<HTMLSpanElement>, kind: RegionKind) {
    event.stopPropagation();
    const point = getPointerPosition(event);
    if (!point) return;
    const original = kind === "facecam" ? facecamRegion : gameplayRegion;
    setActiveRegion(kind);
    setRegionDrag({ kind, mode: "resize", startX: point.x, startY: point.y, original });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveRegion(event: ReactPointerEvent<HTMLElement>) {
    if (!regionDrag) return;
    const point = getPointerPosition(event);
    if (!point) return;
    const dx = point.x - regionDrag.startX;
    const dy = point.y - regionDrag.startY;
    if (regionDrag.mode === "draw") {
      const left = Math.min(regionDrag.startX, point.x);
      const top = Math.min(regionDrag.startY, point.y);
      setRegion(regionDrag.kind, {
        x: left,
        y: top,
        width: Math.min(1 - left, Math.max(0.001, Math.abs(dx))),
        height: Math.min(1 - top, Math.max(0.001, Math.abs(dy))),
      });
      return;
    }
    const { original } = regionDrag;
    if (regionDrag.mode === "resize") {
      setRegion(regionDrag.kind, {
        ...original,
        width: Math.max(Math.min(0.001, 1 - original.x), Math.min(1 - original.x, original.width + dx)),
        height: Math.max(Math.min(0.001, 1 - original.y), Math.min(1 - original.y, original.height + dy)),
      });
      return;
    }
    setRegion(regionDrag.kind, {
      ...original,
      x: Math.max(0, Math.min(1 - original.width, original.x + dx)),
      y: Math.max(0, Math.min(1 - original.height, original.y + dy)),
    });
  }

  function finishRegionDrag() {
    setRegionDrag(null);
  }

  function splitSelectedSegment() {
    if (!selectedSegment) return;
    if (segments.length >= 30) {
      setError("A timeline can contain at most 30 clip segments per batch.");
      return;
    }
    if (playbackTime <= selectedSegment.start_time + 0.05 || playbackTime >= selectedSegment.end_time - 0.05) {
      setError("Move the playhead inside the selected segment before splitting it.");
      return;
    }
    const left = { ...selectedSegment, end_time: playbackTime };
    const right = { id: createSegmentId(), start_time: playbackTime, end_time: selectedSegment.end_time };
    setSegments((current) => current.flatMap((segment) =>
      segment.id === selectedSegment.id ? [left, right] : [segment],
    ));
    setSelectedSegmentId(right.id);
    setStartTime(String(right.start_time));
    setEndTime(String(right.end_time));
    setError("");
  }

  function mergeSelectedSegment() {
    if (segments.length < 2 || !selectedSegment) return;
    const ordered = [...segments].sort((a, b) => a.start_time - b.start_time);
    const index = ordered.findIndex((segment) => segment.id === selectedSegment.id);
    const neighborIndex = index < ordered.length - 1 ? index + 1 : index - 1;
    const first = ordered[Math.min(index, neighborIndex)];
    const second = ordered[Math.max(index, neighborIndex)];
    if (Math.abs(first.end_time - second.start_time) > 0.05) {
      setError("Only adjacent segments can be merged.");
      return;
    }
    const merged = {
      id: createSegmentId(),
      start_time: first.start_time,
      end_time: second.end_time,
    };
    setSegments((current) => [
      ...current.filter((segment) => segment.id !== first.id && segment.id !== second.id),
      merged,
    ].sort((a, b) => a.start_time - b.start_time));
    setSelectedSegmentId(merged.id);
    setStartTime(String(merged.start_time));
    setEndTime(String(merged.end_time));
    setError("");
  }

  function setSelectedBoundary(kind: "in" | "out", time: number) {
    if (!selectedSegment) return;
    if ((kind === "in" && time >= selectedSegment.end_time)
      || (kind === "out" && time <= selectedSegment.start_time)) {
      setError("The in point must be before the out point.");
      return;
    }
    updateSegmentRange(
      selectedSegment.id,
      kind === "in" ? time : selectedSegment.start_time,
      kind === "out" ? time : selectedSegment.end_time,
    );
    setError("");
  }

  async function saveLayoutPreset() {
    if (!presetName.trim()) return;
    try {
      const saved = await apiRequest<LayoutPreset>(`/api/projects/${projectId}/layout-presets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: presetName,
          aspect_ratio: aspectRatio,
          facecam_region: facecamRegion,
          gameplay_region: gameplayRegion,
        }),
      });
      setLayoutPresets((current) => [...current.filter((item) => item.id !== saved.id), saved]);
      setSelectedPresetId(saved.id);
      setPresetName("");
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save layout preset.");
    }
  }

  async function deleteLayoutPreset() {
    if (!selectedPresetId) return;
    try {
      await apiRequest(`/api/layout-presets/${selectedPresetId}`, { method: "DELETE" });
      setLayoutPresets((current) => current.filter((preset) => preset.id !== selectedPresetId));
      setSelectedPresetId("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete layout preset.");
    }
  }

  function applyLayoutPreset(presetId: string) {
    setSelectedPresetId(presetId);
    const preset = layoutPresets.find((item) => item.id === presetId);
    if (!preset) return;
    setAspectRatio(preset.aspect_ratio);
    setFacecamRegion(preset.facecam_region);
    setGameplayRegion(preset.gameplay_region);
    setLayoutMode("gaming_split");
    setSmartReframe(false);
  }

  async function createClip(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!video || !segments.length) return;
    setError("");
    if (job && ["queued", "running"].includes(job.status)) {
      setError("Wait for the current job to finish before queueing more clips.");
      return;
    }
    const ranges = segments.map((segment) => (
      segment.id === selectedSegmentId
        ? { start_time: Number(startTime), end_time: Number(endTime) }
        : { start_time: segment.start_time, end_time: segment.end_time }
    )).sort((a, b) => a.start_time - b.start_time);
    if (ranges.some((range) =>
      !Number.isFinite(range.start_time)
      || !Number.isFinite(range.end_time)
      || range.start_time < 0
      || range.end_time <= range.start_time
      || range.end_time > video.duration,
    )) {
      setError("Every timeline segment must have valid in/out points inside the video.");
      return;
    }
    try {
      const result = await apiRequest<{ job_ids: string[] }>(`/api/videos/${video.id}/clips/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segments: ranges, mode }),
      });
      setJobId(result.job_ids[result.job_ids.length - 1] ?? null);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not queue timeline clips.");
    }
  }

  function editSelectedRange(field: "start" | "end", value: string) {
    if (field === "start") setStartTime(value);
    else setEndTime(value);
    const time = Number(value);
    if (!selectedSegment || !Number.isFinite(time)) return;
    const start = field === "start" ? time : selectedSegment.start_time;
    const end = field === "end" ? time : selectedSegment.end_time;
    if (start >= 0 && end > start && end <= (video?.duration ?? Number.MAX_SAFE_INTEGER)) {
      setSegments((current) => current.map((segment) =>
        segment.id === selectedSegment.id ? { ...segment, start_time: start, end_time: end } : segment,
      ));
    }
  }

  function syncPreviewTime(time: number) {
    for (const preview of previewVideoRefs.current) {
      if (preview && Math.abs(preview.currentTime - time) > 0.15) {
        preview.currentTime = time;
      }
    }
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, select, textarea, [contenteditable='true']")) return;
      if (event.code === "Space" && target?.matches("button, a")) return;
      if (event.code === "Space") {
        event.preventDefault();
        if (playerRef.current?.paused) void playerRef.current.play();
        else playerRef.current?.pause();
      } else if (event.key.toLowerCase() === "i") {
        setSelectedBoundary("in", playbackTime);
      } else if (event.key.toLowerCase() === "o") {
        setSelectedBoundary("out", playbackTime);
      } else if (event.key.toLowerCase() === "c") {
        void createClip();
      } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const frame = 1 / (video?.fps || 30);
        if (playerRef.current) {
          playerRef.current.currentTime = Math.max(
            0,
            Math.min(video?.duration ?? Number.MAX_SAFE_INTEGER,
              playerRef.current.currentTime + (event.key === "ArrowLeft" ? -frame : frame)),
          );
        }
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [playbackTime, selectedSegment, segments, selectedSegmentId, startTime, endTime, video, mode, job?.status]);

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
                  <div
                    className="source-frame"
                    style={{ aspectRatio: `${video.width ?? 16} / ${video.height ?? 9}` }}
                  >
                    <video
                      ref={setVideoPlayerRef}
                      controls
                      preload="metadata"
                      src={videoUrl(video.file_url)}
                      onTimeUpdate={(event) => {
                        const time = event.currentTarget.currentTime;
                        setPlaybackTime(time);
                        syncPreviewTime(time);
                      }}
                      onPlay={() => previewVideoRefs.current.forEach((preview) => {
                        if (preview) void preview.play().catch(() => undefined);
                      })}
                      onPause={() => previewVideoRefs.current.forEach((preview) => preview?.pause())}
                      onPointerDown={layoutMode === "gaming_split" ? beginRegionDraw : undefined}
                      onPointerMove={layoutMode === "gaming_split" ? moveRegion : undefined}
                      onPointerUp={layoutMode === "gaming_split" ? finishRegionDrag : undefined}
                      onPointerCancel={layoutMode === "gaming_split" ? finishRegionDrag : undefined}
                    />
                    {layoutMode === "gaming_split" && (
                      <div
                        className="region-overlay"
                        ref={regionOverlayRef}
                        onPointerMove={moveRegion}
                        onPointerUp={finishRegionDrag}
                        onPointerCancel={finishRegionDrag}
                      >
                        {([
                          ["gameplay", gameplayRegion],
                          ["facecam", facecamRegion],
                        ] as const).map(([kind, region]) => (
                          <div
                            key={kind}
                            className={`region-box region-${kind}`}
                            style={{
                              left: `${region.x * 100}%`,
                              top: `${region.y * 100}%`,
                              width: `${region.width * 100}%`,
                              height: `${region.height * 100}%`,
                              opacity: activeRegion === kind ? 1 : 0.7,
                            }}
                            onPointerDown={(event) => beginRegionMove(event, kind)}
                          >
                            {kind === "facecam" ? "FACECAM" : "GAMEPLAY"}
                            <span
                              className="region-resize-handle"
                              onPointerDown={(event) => beginRegionResize(event, kind)}
                              aria-label={`Resize ${kind} region`}
                            />
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
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
            <form className="clip-form" onSubmit={(event) => void createClip(event)}>
              <div className="time-inputs">
                <label className="field"><span>Start (seconds)</span><input type="number" min="0" step="0.1" value={startTime} onChange={(event) => editSelectedRange("start", event.target.value)} required /></label>
                <label className="field"><span>End (seconds)</span><input type="number" min="0.1" step="0.1" max={video.duration} value={endTime} onChange={(event) => editSelectedRange("end", event.target.value)} required /></label>
              </div>
              <label className="field">
                <span>Cut mode</span>
                <select value={mode} onChange={(event) => setMode(event.target.value as "accurate" | "copy")}>
                  <option value="accurate">Accurate re-encode (slower)</option>
                  <option value="copy">Fast stream copy (keyframe-limited)</option>
                </select>
              </label>
              <button type="submit" disabled={!segments.length || (!!job && (job.status === "queued" || job.status === "running"))}>
                Create {segments.length > 1 ? `${segments.length} clips` : "clip"}
              </button>
              <p className="muted small">Times are seconds from the selected video. Creating queues every timeline segment.</p>
            </form>
          ) : <p className="empty-state muted">Choose a video to enable clipping.</p>}
        </div>
      </section>
      <section className="panel timeline-panel">
        <div className="section-heading">
          <div><p className="eyebrow">TIMELINE</p><h2>Fine-tune clip segments</h2></div>
          {selectedSegment && <span className="muted small">{formatTime(selectedSegment.end_time - selectedSegment.start_time)} selected</span>}
        </div>
        {video && selectedSegment ? (
          <>
            <WaveformTimeline
              videoId={video.id}
              fileUrl={video.file_url}
              duration={video.duration}
              media={sourceMedia}
              segments={segments}
              selectedSegmentId={selectedSegmentId}
              onRangeChange={handleTimelineRangeChange}
              onSelect={handleTimelineSelect}
              onError={setWaveformError}
            />
            {waveformError && <p className="error">{waveformError}</p>}
            <div className="timeline-actions">
              <button type="button" className="secondary-button" onClick={() => setSelectedBoundary("in", playbackTime)}>Set in (I)</button>
              <button type="button" className="secondary-button" onClick={() => setSelectedBoundary("out", playbackTime)}>Set out (O)</button>
              <button type="button" className="secondary-button" onClick={splitSelectedSegment}>Split at playhead</button>
              <button type="button" className="secondary-button" disabled={segments.length < 2} onClick={mergeSelectedSegment}>Merge adjacent</button>
              <button type="button" disabled={!!job && ["queued", "running"].includes(job.status)} onClick={() => void createClip()}>
                Create {segments.length > 1 ? `${segments.length} clips` : "clip"} (C)
              </button>
            </div>
            <div className="timeline-segments">
              {[...segments].sort((a, b) => a.start_time - b.start_time).map((segment, index) => (
                <button
                  type="button"
                  className={`timeline-segment ${segment.id === selectedSegmentId ? "selected" : ""}`}
                  key={segment.id}
                  onClick={() => handleTimelineSelect(segment.id)}
                >
                  {index + 1} · {formatTime(segment.start_time)}–{formatTime(segment.end_time)}
                </button>
              ))}
            </div>
            <p className="muted small shortcut-help">Shortcuts: Space play/pause · I in · O out · C queue clips · ←/→ step one frame</p>
          </>
        ) : <p className="empty-state muted">Add a video to edit its timeline.</p>}
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
            <label className="field word-point-mode">
              <span>Word click</span>
              <select value={wordPointMode} onChange={(event) => setWordPointMode(event.target.value as "preview" | "in" | "out")}>
                <option value="preview">Preview</option>
                <option value="in">Set in point</option>
                <option value="out">Set out point</option>
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
                      title={`Click to ${wordPointMode === "preview" ? "seek" : `set the ${wordPointMode} point and seek`}; double-click to edit`}
                      onClick={() => {
                        if (playerRef.current) playerRef.current.currentTime = word.start_time;
                        if (wordPointMode === "in") setSelectedBoundary("in", word.start_time);
                        if (wordPointMode === "out") setSelectedBoundary("out", word.end_time);
                      }}
                      onDoubleClick={() => setEditingWord(word.word_index)}
                    >{word.text}</button>
                  )}
                </span>
              ))}
            </div>
            <div className="transcript-footer">
              <span className="muted small">Click words to seek or set the selected in/out point · double-click to edit</span>
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
                if (nextClip) {
                  pendingTimelineRef.current = {
                    videoId: nextClip.video_id,
                    start: nextClip.start_time,
                    end: nextClip.end_time,
                  };
                  setSelectedVideoId(nextClip.video_id);
                  if (nextClip.video_id === video?.id) {
                    const selected = {
                      id: createSegmentId(),
                      start_time: nextClip.start_time,
                      end_time: nextClip.end_time,
                    };
                    pendingTimelineRef.current = null;
                    setSegments([selected]);
                    setSelectedSegmentId(selected.id);
                    setStartTime(String(selected.start_time));
                    setEndTime(String(selected.end_time));
                  }
                }
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
              <label className="field"><span>Aspect ratio</span>
                <select value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value as AspectRatio)}>
                  <option value="9:16">9:16 · Shorts / Reels</option>
                  <option value="1:1">1:1 · Square</option>
                  <option value="4:5">4:5 · Portrait feed</option>
                  <option value="16:9">16:9 · Landscape</option>
                </select>
              </label>
              <label className="field"><span>Bitrate</span>
                <select value={captionBitrate} onChange={(event) => setCaptionBitrate(event.target.value)}>
                  {["4M", "8M", "12M", "20M"].map((rate) => <option key={rate} value={rate}>{rate}</option>)}
                </select>
              </label>
            </div>
            <div className="layout-controls">
              <label className="field"><span>Video layout</span>
                <select value={layoutMode} onChange={(event) => {
                  const nextLayout = event.target.value as "single" | "gaming_split";
                  setLayoutMode(nextLayout);
                  if (nextLayout === "gaming_split") setSmartReframe(false);
                }}>
                  <option value="single">Single reframed view</option>
                  <option value="gaming_split">Gaming split-screen</option>
                </select>
              </label>
              <label className="field"><span>Aspect fill</span>
                <select value={backgroundFill} disabled={layoutMode === "gaming_split" || smartReframe} onChange={(event) => setBackgroundFill(event.target.value as "blur" | "crop" | "pad")}>
                  <option value="blur">Blurred background</option>
                  <option value="crop">Fill by cropping</option>
                  <option value="pad">Fit with bars</option>
                </select>
              </label>
              <label className="check-field">
                <input type="checkbox" checked={smartReframe} disabled={layoutMode === "gaming_split"} onChange={(event) => setSmartReframe(event.target.checked)} />
                <span>Track and center speaker (MediaPipe)</span>
              </label>
            </div>
            {layoutMode === "gaming_split" && (
              <>
                <p className="muted small">Drag on the source preview to draw the active region; drag a box to move it. Gameplay appears above facecam.</p>
                <div className="region-mode-buttons">
                  <button type="button" className={`secondary-button ${activeRegion === "gameplay" ? "active" : ""}`} onClick={() => setActiveRegion("gameplay")}>Select gameplay region</button>
                  <button type="button" className={`secondary-button ${activeRegion === "facecam" ? "active" : ""}`} onClick={() => setActiveRegion("facecam")}>Select facecam region</button>
                </div>
                <div className="layout-controls">
                  <label className="field"><span>Saved layout</span>
                    <select value={selectedPresetId} onChange={(event) => applyLayoutPreset(event.target.value)}>
                      <option value="">Choose a preset</option>
                      {layoutPresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}
                    </select>
                  </label>
                  <label className="field"><span>Preset name</span><input value={presetName} maxLength={80} onChange={(event) => setPresetName(event.target.value)} placeholder="My gaming layout" /></label>
                  <button type="button" disabled={!presetName.trim()} onClick={() => void saveLayoutPreset()}>Save layout</button>
                  <button type="button" className="secondary-button" disabled={!selectedPresetId} onClick={() => void deleteLayoutPreset()}>Delete preset</button>
                </div>
              </>
            )}
            {reframeError && <p className="error">{reframeError}</p>}
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
            {video && layoutMode === "single" && (
              <div className="output-preview single-preview" style={{ aspectRatio: `${previewWidth} / ${previewHeight}` }}>
                <video
                  ref={(element) => { previewVideoRefs.current[0] = element; }}
                  src={videoUrl(video.file_url)}
                  muted
                  playsInline
                  preload="metadata"
                  style={{ objectPosition: `${previewFocus.x * 100}% ${previewFocus.y * 100}%` }}
                />
              </div>
            )}
            {video && layoutMode === "gaming_split" && (
              <div className="output-preview split-preview" style={{ aspectRatio: `${previewWidth} / ${previewHeight}` }}>
                <div className="split-preview-tile">
                  <video
                    ref={(element) => { previewVideoRefs.current[0] = element; }}
                    src={videoUrl(video.file_url)}
                    muted
                    playsInline
                    preload="metadata"
                    style={regionPreviewStyle(gameplayRegion)}
                  />
                </div>
                <div className="split-preview-tile">
                  <video
                    ref={(element) => { previewVideoRefs.current[1] = element; }}
                    src={videoUrl(video.file_url)}
                    muted
                    playsInline
                    preload="metadata"
                    style={regionPreviewStyle(facecamRegion)}
                  />
                </div>
              </div>
            )}
            <p className="muted small">Preview follows the source playhead and selected layout; the final export applies this crop, split-screen, or blurred fill.</p>
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
    live_clip: "Live buffer clip",
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

function getTrackFocus(
  track: { time: number; x: number; y: number }[],
  time: number,
): { x: number; y: number } {
  if (!track.length) return { x: 0.5, y: 0.5 };
  if (time <= track[0].time) return track[0];
  for (let index = 1; index < track.length; index += 1) {
    const right = track[index];
    if (time > right.time) continue;
    const left = track[index - 1];
    const span = right.time - left.time;
    const factor = span > 0 ? (time - left.time) / span : 0;
    return {
      x: left.x + (right.x - left.x) * factor,
      y: left.y + (right.y - left.y) * factor,
    };
  }
  return track[track.length - 1];
}

function regionPreviewStyle(region: VideoRegion) {
  return {
    left: `${-(region.x / region.width) * 100}%`,
    top: `${-(region.y / region.height) * 100}%`,
    width: `${100 / region.width}%`,
    height: `${100 / region.height}%`,
  };
}

export default function App() {
  return (
    <div className="app min-h-screen">
      <Header />
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/live" element={<LivePage />} />
        <Route path="/projects/:projectId" element={<ProjectPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
      <footer className="footer">LOCAL-FIRST · NO CLOUD ACCOUNTS · YOUR FILES STAY YOURS</footer>
    </div>
  );
}
