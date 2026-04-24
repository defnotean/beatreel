"use client";

import { Download, Dices, RefreshCw } from "lucide-react";
import { useState } from "react";
import type { JobState } from "@/lib/api";
import { rerollJob, videoUrl } from "@/lib/api";

export function ResultPanel({
  job,
  onReset,
  onReroll,
}: {
  job: JobState;
  onReset: () => void;
  onReroll: (newJobId: string) => void;
}) {
  const [rerolling, setRerolling] = useState(false);
  const [rerollError, setRerollError] = useState<string | null>(null);
  const url = videoUrl(job.id);

  async function handleReroll() {
    setRerolling(true);
    setRerollError(null);
    try {
      const { job_id } = await rerollJob(job.id);
      onReroll(job_id);
    } catch (e) {
      setRerollError(e instanceof Error ? e.message : String(e));
      setRerolling(false);
    }
  }

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
          key={job.id}
          className="w-full h-full"
        />
      </div>

      <div className="mt-5 grid grid-cols-4 gap-3">
        <Stat label="Cuts" value={job.num_cuts ?? "—"} />
        <Stat label="Clips" value={job.num_clips_scanned ?? "—"} />
        <Stat label="Duration" value={job.final_duration ? `${job.final_duration.toFixed(1)}s` : "—"} />
        <Stat label="Tempo" value={job.tempo ? `${Math.round(job.tempo)} BPM` : "—"} />
      </div>

      {rerollError && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-[12.5px] text-red-300">
          {rerollError}
        </div>
      )}

      <div className="mt-5 grid grid-cols-5 gap-2">
        <button
          onClick={handleReroll}
          disabled={rerolling}
          className="col-span-2 flex items-center justify-center gap-2 py-3 rounded-xl border border-accent/40 bg-accent/10 text-white font-medium hover:bg-accent/20 hover:border-accent disabled:opacity-50 transition-all"
          title="Generate another cut from the same inputs"
        >
          <Dices className={`h-4 w-4 ${rerolling ? "animate-spin" : ""}`} />
          {rerolling ? "Re-rolling..." : "Re-roll"}
        </button>
        <a
          href={url}
          download="beatreel.mp4"
          className="col-span-3 flex items-center justify-center gap-2 py-3 rounded-xl bg-accent-gradient text-white font-medium shadow-glow hover:shadow-glow-lg transition-all"
        >
          <Download className="h-4 w-4" />
          Download MP4
        </a>
      </div>
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
