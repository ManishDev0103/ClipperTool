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

export type ProjectDetail = {
  project: { id: string; name: string };
  videos: Video[];
  clips: Clip[];
  exports: ExportedClip[];
};

export type Job = {
  id: string;
  kind: "download" | "clip" | "transcription" | "export" | "highlight_analysis";
  model?: string;
  status: "queued" | "running" | "completed" | "failed";
  progress: number;
  message: string;
  error: string | null;
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
  return (await response.json()) as T;
}

export function videoUrl(path: string): string {
  return apiUrl(path);
}
