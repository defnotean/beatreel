"use client";

import { Download, RefreshCw } from "lucide-react";
import type { JobState } from "@/lib/api";
import { videoUrl } from "@/lib/api";

export function ResultPanel({ job, onReset }: { job: JobState; onReset: () => void }) {
  const url = videoUrl(job.id);
  return (
    <div className="glass rounded-2xl p-6 shadow-glow-lg">
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <div className="text-[11px] uppercase tracking-[0.18em] text-emerald-400 mb-1">
            ready
          </div>
          <div className="text-lg font-medium">Your highlight reel</div>
        </div>
        <button
          onClick={onReset}
          className="flex items-center gap-1.5 text-[13px] text-white/60 hover:text-white transition-colors"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Start over
        </button>
      </div>

      <div className="rounded-xl overflow-hidden border border-border bg-black aspect-video">
        <video
          src={url}
          controls
          autoPlay
          className="w-full h-full"
        />
      </div>

      <div className="mt-5 grid grid-cols-4 gap-3">
        <Stat label="Cuts" value={job.num_cuts ?? "—"} />
        <Stat label="Clips used" value={job.num_clips_scanned ?? "—"} />
        <Stat label="Duration" value={job.final_duration ? `${job.final_duration.toFixed(1)}s` : "—"} />
        <Stat label="Tempo" value={job.tempo ? `${Math.round(job.tempo)} BPM` : "—"} />
      </div>

      <a
        href={url}
        download="beatreel.mp4"
        className="mt-5 flex items-center justify-center gap-2 w-full py-3 rounded-xl bg-accent-gradient text-white font-medium shadow-glow hover:shadow-glow-lg transition-all"
      >
        <Download className="h-4 w-4" />
        Download MP4
      </a>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-black/30 border border-border px-3 py-2.5 text-center">
      <div className="font-mono text-[15px] text-white/90 tabular-nums">
        {value}
      </div>
      <div className="text-[10px] uppercase tracking-wider text-white/40 mt-0.5">
        {label}
      </div>
    </div>
  );
}
