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

export async function checkHealth(): Promise<{
  ok: boolean;
  ffmpeg: boolean;
  ffmpeg_error: string | null;
  gemini_configured?: boolean;
  gemini_keys_configured?: number;
}> {
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

export async function listMedalUserClips(
  q: string,
  limit = 50,
): Promise<{ username: string; clips: MedalClip[] }> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  const r = await fetch(`/api/medal/user?${params.toString()}`);
  if (!r.ok) {
    const detail = await r.json().catch(() => null);
    throw new Error(detail?.detail || `Medal user: ${r.status}`);
  }
  return r.json();
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
      publicClips?: MedalClip[];
    }
  | { type: "auto_clip"; sourceVideo: File };

export type MusicSource =
  | { type: "upload"; file: File }
  | { type: "youtube"; url: string }
  | { type: "reuse"; jobId: string };

export interface JobHistoryEntry {
  job_id: string;
  status: string;
  created_at_ms: number;
  tiers: string[];
  num_cuts: number | null;
  has_music: boolean;
  music_filename: string | null;
  has_source_video: boolean;
  thumbnail_path: string | null;
}

export async function listJobs(limit = 50): Promise<JobHistoryEntry[]> {
  const r = await fetch(`/api/jobs?limit=${limit}`);
  if (!r.ok) throw new Error(`list jobs failed: ${r.status}`);
  const data = (await r.json()) as { jobs: JobHistoryEntry[] };
  return data.jobs;
}

export function jobThumbnailUrl(jobId: string): string {
  return `/api/jobs/${jobId}/thumbnail`;
}

export type Aspect = "landscape" | "portrait" | "square";

export async function createJob(params: {
  clips: ClipSource;
  music: MusicSource;
  duration: number;
  intensity: "chill" | "balanced" | "hype" | "auto";
  aspect: Aspect;
  game: "valorant_ai" | "valorant" | "generic";
  /** One OR MANY Gemini keys. Multiple enables parallel per-clip analysis. */
  geminiKeys?: string[];
  /** Auto-clip mode only: render a 4th long-form tier (3-4 min narrative cut). */
  includeLongForm?: boolean;
}): Promise<{ job_id: string }> {
  const fd = new FormData();
  fd.append("duration", String(params.duration));
  fd.append("intensity", params.intensity);
  fd.append("aspect", params.aspect);
  fd.append("game", params.game);

  if (params.clips.type === "upload") {
    fd.append("source_mode", "clips");
    for (const c of params.clips.files) fd.append("clips", c);
  } else if (params.clips.type === "auto_clip") {
    fd.append("source_mode", "auto_clip");
    fd.append("source_video", params.clips.sourceVideo);
  } else {
    fd.append("source_mode", "clips");
    if (params.clips.clipIds.length > 0) {
      fd.append("medal_clip_ids", params.clips.clipIds.join(","));
    }
    if (params.clips.shareUrls.length > 0) {
      fd.append("medal_share_urls", params.clips.shareUrls.join("\n"));
    }
    if (params.clips.publicClips && params.clips.publicClips.length > 0) {
      fd.append("medal_public_clips", JSON.stringify(params.clips.publicClips));
    }
    if (params.clips.userId) fd.append("medal_user_id", params.clips.userId);
  }

  if (params.music.type === "upload") {
    fd.append("music", params.music.file);
  } else if (params.music.type === "youtube") {
    fd.append("youtube_url", params.music.url);
  } else {
    fd.append("reuse_music_from_job", params.music.jobId);
  }

  if (params.includeLongForm) {
    fd.append("include_long_form", "true");
  }

  // HTTP headers must be Latin-1 (ISO-8859-1). Strip anything outside that
  // range or fetch() throws "String contains non ISO-8859-1 code point."
  // Covers the case where a pasted key has smart quotes / bullets / NBSP.
  const toAsciiHeader = (v: string) => v.replace(/[^\x20-\x7E]/g, "");
  const headers: Record<string, string> = {};
  if (params.clips.type === "medal" && params.clips.apiKey) {
    headers["X-Medal-Key"] = toAsciiHeader(params.clips.apiKey);
  }
  if (params.geminiKeys && params.geminiKeys.length > 0) {
    // Backend parses comma/newline-separated keys out of the header value.
    headers["X-Gemini-Key"] = toAsciiHeader(params.geminiKeys.join(","));
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

export async function rerollJob(jobId: string): Promise<{ job_id: string; seed: number }> {
  const r = await fetch(`/api/jobs/${jobId}/reroll`, { method: "POST" });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`reroll failed: ${t}`);
  }
  return r.json();
}

export function videoUrl(jobId: string): string {
  return `/api/jobs/${jobId}/video`;
}
