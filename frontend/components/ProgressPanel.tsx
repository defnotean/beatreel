"use client";

import type { JobState } from "@/lib/api";

export function ProgressPanel({ job }: { job: JobState }) {
  const pct = Math.round(job.progress * 100);

  return (
    <div className="border border-border bg-surface-1">
      <header className="flex items-baseline justify-between px-4 py-3 border-b border-border">
        <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-fg-muted">
          Job {job.id.slice(0, 8)} · {job.status}
        </span>
        <span className="font-mono text-[18px] text-fg tabular-nums">
          {pct}%
        </span>
      </header>

      <div className="px-4 pt-4">
        <div
          className="font-mono text-[13px] text-fg truncate"
          title={job.stage}
        >
          {job.stage || "queued"}
        </div>

        <div className="mt-3 relative h-[6px] bg-bg border border-border">
          <div
            className="absolute inset-y-0 left-0 bg-accent transition-[width] duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <dl className="grid grid-cols-4 px-4 py-4 mt-3 gap-x-4 gap-y-3 border-t border-border font-mono text-[11px]">
        <Stat label="Clips" value={job.num_clips_scanned} />
        <Stat label="Candidates" value={job.num_candidates} />
        <Stat
          label="Cuts"
          value={job.num_cuts}
        />
        <Stat
          label="Tempo"
          value={job.tempo !== null ? `${Math.round(job.tempo)} BPM` : null}
        />
      </dl>
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
