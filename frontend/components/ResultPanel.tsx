"use client";

import { Download, Dices } from "lucide-react";
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
    <div className="border border-border bg-surface-1">
      <header className="flex items-baseline justify-between px-4 py-3 border-b border-border">
        <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-ok">
          ● Ready · {job.id.slice(0, 8)}
        </span>
        <button
          onClick={onReset}
          className="font-mono text-[11px] uppercase tracking-[0.12em] text-fg-dim hover:text-fg"
        >
          New reel
        </button>
      </header>

      <div className="bg-black border-b border-border">
        <video
          src={url}
          controls
          autoPlay
          key={job.id}
          className="w-full aspect-video"
        />
      </div>

      <dl className="grid grid-cols-4 px-4 py-4 gap-x-4 border-b border-border font-mono text-[11px]">
        <Stat label="Cuts" value={job.num_cuts} />
        <Stat label="Clips" value={job.num_clips_scanned} />
        <Stat
          label="Duration"
          value={
            job.final_duration !== null
              ? `${job.final_duration.toFixed(1)}s`
              : null
          }
        />
        <Stat
          label="Tempo"
          value={job.tempo !== null ? `${Math.round(job.tempo)} BPM` : null}
        />
      </dl>

      {rerollError && (
        <div className="px-4 py-2 border-b border-border font-mono text-[11.5px] text-err">
          {rerollError}
        </div>
      )}

      <div className="grid grid-cols-2">
        <button
          onClick={handleReroll}
          disabled={rerolling}
          className="flex items-center justify-center gap-2 py-3 border-r border-border font-mono text-[12px] uppercase tracking-[0.1em] text-fg hover:bg-surface-2 disabled:opacity-50"
          title="Generate another cut from the same inputs"
        >
          <Dices className={`h-3.5 w-3.5 ${rerolling ? "animate-spin" : ""}`} />
          {rerolling ? "Re-rolling" : "Re-roll"}
        </button>
        <a
          href={url}
          download="beatreel.mp4"
          className="flex items-center justify-center gap-2 py-3 bg-accent text-white font-mono text-[12px] uppercase tracking-[0.1em] hover:brightness-110"
        >
          <Download className="h-3.5 w-3.5" />
          Download MP4
        </a>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number | null }) {
  const empty = value === null || value === undefined;
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="uppercase tracking-[0.12em] text-fg-muted">{label}</dt>
      <dd
        className={
          empty
            ? "text-fg-muted tabular-nums"
            : "text-fg text-[13px] tabular-nums"
        }
      >
        {empty ? "—" : value}
      </dd>
    </div>
  );
}
