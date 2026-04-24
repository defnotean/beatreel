"use client";

import { useEffect, useState } from "react";
import { Clock, Film, Music, Video } from "lucide-react";

import { jobThumbnailUrl, listJobs, type JobHistoryEntry } from "@/lib/api";

export function HistoryPanel({
  refreshKey = 0,
  onPickForMusicReuse,
}: {
  /** Bump to force a refetch (e.g. after a new job finishes). */
  refreshKey?: number;
  /** Optional: when provided, renders a 'Use this music' action per entry. */
  onPickForMusicReuse?: (entry: JobHistoryEntry) => void;
}) {
  const [jobs, setJobs] = useState<JobHistoryEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setErr(null);
    listJobs(50)
      .then((js) => {
        if (!cancelled) setJobs(js);
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (err) {
    return (
      <div className="border border-err/50 bg-surface-1 p-4 font-mono text-[12px] text-err">
        Couldn&apos;t load history: {err}
      </div>
    );
  }

  if (jobs === null) {
    return (
      <div className="border border-border bg-surface-1 p-6 font-mono text-[11px] uppercase tracking-[0.12em] text-fg-dim">
        Loading history…
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="border border-border bg-surface-1 p-8 text-center">
        <Clock className="h-5 w-5 text-fg-dim mx-auto mb-2" />
        <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-fg-dim">
          No reels rendered yet
        </p>
        <p className="font-mono text-[10.5px] text-fg-muted mt-1">
          Finished reels show up here so you can preview, reuse music, or grab assets.
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {jobs.map((j) => (
        <HistoryCard
          key={j.job_id}
          entry={j}
          onPickForMusicReuse={onPickForMusicReuse}
        />
      ))}
    </div>
  );
}

function HistoryCard({
  entry,
  onPickForMusicReuse,
}: {
  entry: JobHistoryEntry;
  onPickForMusicReuse?: (entry: JobHistoryEntry) => void;
}) {
  const when = formatWhen(entry.created_at_ms);
  const tierList = entry.tiers.length > 0 ? entry.tiers.join(" · ") : "—";
  return (
    <div className="border border-border bg-surface-1">
      <div className="aspect-video bg-surface-2 border-b border-border overflow-hidden flex items-center justify-center">
        {entry.thumbnail_path ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={jobThumbnailUrl(entry.job_id)}
            alt="reel thumbnail"
            className="w-full h-full object-cover"
          />
        ) : (
          <Film className="h-8 w-8 text-fg-dim" />
        )}
      </div>
      <div className="p-3 space-y-2">
        <div className="flex items-center justify-between font-mono text-[10.5px] uppercase tracking-[0.12em]">
          <span className="text-fg truncate">{entry.job_id}</span>
          <span className="text-fg-muted">{when}</span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px] text-fg-dim">
          <span className="flex items-center gap-1">
            <Video className="h-3 w-3" />
            {tierList}
          </span>
          {entry.num_cuts !== null && (
            <span>{entry.num_cuts} cuts</span>
          )}
        </div>
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 font-mono text-[10.5px] text-fg-muted min-w-0 flex-1">
            <Music className="h-3 w-3 shrink-0" />
            <span className="truncate">
              {entry.has_music ? entry.music_filename ?? "music" : "no music"}
            </span>
          </div>
          {entry.has_music && onPickForMusicReuse && (
            <button
              onClick={() => onPickForMusicReuse(entry)}
              className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-accent hover:brightness-110 border border-accent/40 px-2 py-1 hover:bg-accent/10 shrink-0"
            >
              Use music
            </button>
          )}
          <a
            href={`/api/jobs/${entry.job_id}/video`}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-fg-dim hover:text-fg border border-border px-2 py-1 hover:bg-surface-2 shrink-0"
          >
            Open
          </a>
        </div>
      </div>
    </div>
  );
}

function formatWhen(ms: number): string {
  const d = new Date(ms);
  const now = Date.now();
  const delta = now - ms;
  const MIN = 60 * 1000;
  const HR = 60 * MIN;
  const DAY = 24 * HR;
  if (delta < MIN) return "just now";
  if (delta < HR) return `${Math.floor(delta / MIN)}m ago`;
  if (delta < DAY) return `${Math.floor(delta / HR)}h ago`;
  if (delta < 7 * DAY) return `${Math.floor(delta / DAY)}d ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
