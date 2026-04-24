export type JobStatus = "queued" | "running" | "done" | "error";

export interface JobState {
  id: string;
  status: JobStatus;
  stage: string;
  progress: number;
  tempo: number | null;
  num_cuts: number | null;
  num_candidates: number | null;
  num_clips_scanned: number | null;
  final_duration: number | null;
  error: string | null;
}

export interface MedalClip {
  contentId: string;
  title: string;
  duration: number;
  thumbnail: string;
  directClipUrl: string;
  rawFileUrl: string | null;
  embedIframeUrl: string;
  createdMs: number;
  origin?: "library" | "url";
}

export interface YouTubePreview {
  title: string;
  uploader: string;
  duration: number;
  thumbnail: string;
  webpage_url: string;
}

export async function checkHealth(): Promise<{ ok: boolean; ffmpeg: boolean; ffmpeg_error: string | null }> {
  const r = await fetch("/api/health");
  if (!r.ok) throw new Error("health check failed");
  return r.json();
}

export async function listMedalClips(params: {
  apiKey: string;
  userId?: string;
  limit?: number;
}): Promise<MedalClip[]> {
  const q = new URLSearchParams();
  if (params.userId) q.set("user_id", params.userId);
  q.set("limit", String(params.limit ?? 50));

  const r = await fetch(`/api/medal/clips?${q.toString()}`, {
    headers: { "X-Medal-Key": params.apiKey },
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => null);
    throw new Error(detail?.detail || `Medal API: ${r.status}`);
  }
  const data = (await r.json()) as { clips: MedalClip[] };
  return data.clips;
}

export async function resolveMedalUrl(url: string): Promise<MedalClip> {
  const r = await fetch("/api/medal/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => null);
    throw new Error(detail?.detail || `Medal resolve: ${r.status}`);
  }
  const clip = (await r.json()) as MedalClip;
  return { ...clip, origin: "url" };
}

export async function probeYouTube(url: string): Promise<YouTubePreview> {
  const r = await fetch("/api/youtube/probe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => null);
    throw new Error(detail?.detail || `YouTube: ${r.status}`);
  }
  return r.json();
}

export type ClipSource =
  | { type: "upload"; files: File[] }
  | {
      type: "medal";
      apiKey?: string;
      userId?: string;
      clipIds: string[];
      shareUrls: string[];
    };

export type MusicSource =
  | { type: "upload"; file: File }
  | { type: "youtube"; url: string };

export async function createJob(params: {
  clips: ClipSource;
  music: MusicSource;
  duration: number;
  intensity: "chill" | "balanced" | "hype";
}): Promise<{ job_id: string }> {
  const fd = new FormData();
  fd.append("duration", String(params.duration));
  fd.append("intensity", params.intensity);

  if (params.clips.type === "upload") {
    for (const c of params.clips.files) fd.append("clips", c);
  } else {
    if (params.clips.clipIds.length > 0) {
      fd.append("medal_clip_ids", params.clips.clipIds.join(","));
    }
    if (params.clips.shareUrls.length > 0) {
      fd.append("medal_share_urls", params.clips.shareUrls.join("\n"));
    }
    if (params.clips.userId) fd.append("medal_user_id", params.clips.userId);
  }

  if (params.music.type === "upload") {
    fd.append("music", params.music.file);
  } else {
    fd.append("youtube_url", params.music.url);
  }

  const headers: Record<string, string> = {};
  if (params.clips.type === "medal" && params.clips.apiKey) {
    headers["X-Medal-Key"] = params.clips.apiKey;
  }

  const r = await fetch("/api/jobs", { method: "POST", body: fd, headers });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`upload failed: ${t}`);
  }
  return r.json();
}

export async function getJob(jobId: string): Promise<JobState> {
  const r = await fetch(`/api/jobs/${jobId}`);
  if (!r.ok) throw new Error(`job fetch failed: ${r.status}`);
  return r.json();
}

export function videoUrl(jobId: string): string {
  return `/api/jobs/${jobId}/video`;
}
