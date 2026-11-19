export type ProjectSummary = {
  id: string;
  name: string;
  created_at: string;
  video_count: number;
  clip_count: number;
};

export type Video = {
  id: string;
  title: string;
  source_url: string | null;
  duration: number;
  width: number | null;
  height: number | null;
  fps: number | null;
  file_url: string;
};

export type Clip = {
  id: string;
  video_id: string;
  start_time: number;
  end_time: number;
  mode: "copy" | "accurate";
  file_url: string;
};

export type TranscriptWord = {
  word_index: number;
  start_time: number;
  end_time: number;
  text: string;
};

export type Transcript = {
  video_id: string;
  language: string | null;
  model: string | null;
  words: TranscriptWord[];
};

export type ExportedClip = {
  id: string;
  clip_id: string;
  file_url: string;
  created_at: string;
  settings: {
    encoder?: string;
    preset: string;
    bitrate: string;
    fps: number;
  };
};

export type HighlightSuggestion = {
  id: string;
  start_time: number;
  end_time: number;
  score: number;
  title: string;
  reason: string;
  status: "suggested" | "accepted" | "rejected" | "created";
  signals: Record<string, number>;
  thumbnail_url: string | null;
};

export type HighlightAnalysis = {
  analysis_mode: "ollama" | "local_heuristics" | null;
  warnings: string[];
  suggestions: HighlightSuggestion[];
};

export type VideoRegion = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type LayoutPreset = {
  id: string;
  name: string;
  aspect_ratio: "9:16" | "1:1" | "4:5" | "16:9";
  facecam_region: VideoRegion;
  gameplay_region: VideoRegion;
};

export type ProjectDetail = {
  project: { id: string; name: string };
  videos: Video[];
  clips: Clip[];
  exports: ExportedClip[];
  batch_exports: { id: string; file_url: string; created_at: string }[];
};

export type Job = {
  id: string;
  kind: "download" | "clip" | "transcription" | "export" | "batch_export" | "highlight_analysis" | "live_clip";
  model?: string;
  status: "queued" | "running" | "completed" | "failed";
  progress: number;
  message: string;
  error: string | null;
};

export type AppPreferences = {
  default_whisper_model: "tiny" | "base" | "small" | "medium" | "large-v3";
  ollama_model: string;
  gpu_enabled: boolean;
  default_caption_preset: "bold_yellow" | "minimal_white" | "boxed" | "glow";
  default_aspect_ratio: "9:16" | "1:1" | "4:5" | "16:9";
  default_clip_length: number;
  default_buffer_minutes: number;
  hotkey: "Ctrl+Alt+C" | "Ctrl+Alt+X" | "Ctrl+Shift+C";
};

export type LiveRecording = {
  id: string;
  project_id: string;
  source_url: string;
  status: "starting" | "recording" | "reconnecting" | "stopping" | "stopped" | "ended" | "failed";
  buffer_seconds: number;
  buffered_seconds: number;
  buffer_size_bytes: number;
  disk_cap_bytes: number;
  reconnect_count: number;
  last_error: string | null;
  audio_trigger_enabled: boolean;
  chat_trigger_enabled: boolean;
  started_at: string;
  ended_at: string | null;
};

export type LiveCandidate = {
  id: string;
  recording_id: string;
  trigger_at: string;
  reason: string;
  video_id: string | null;
  created_at: string;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init);
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      detail = response.statusText || detail;
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function videoUrl(path: string): string {
  return apiUrl(path);
}
