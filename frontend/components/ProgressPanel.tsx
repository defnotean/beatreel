"use client";

import { BeatVisualizer } from "./BeatVisualizer";
import type { JobState } from "@/lib/api";

export function ProgressPanel({ job }: { job: JobState }) {
  const pct = Math.round(job.progress * 100);
  const stages = [
    { key: "scan", label: "Scanning", threshold: 0.0 },
    { key: "beats", label: "Beats", threshold: 0.05 },
    { key: "score", label: "Scoring", threshold: 0.1 },
    { key: "plan", label: "Planning", threshold: 0.75 },
    { key: "render", label: "Rendering", threshold: 0.8 },
  ];

  return (
    <div className="glass rounded-2xl p-8 shadow-glow-lg">
      <div className="flex items-center justify-between mb-5">
        <div>
          <div className="text-[11px] uppercase tracking-[0.18em] text-accent-glow mb-1">
            in progress
          </div>
          <div className="text-lg font-medium">{job.stage}</div>
        </div>
        <div className="font-mono text-3xl text-gradient font-semibold tabular-nums">
          {pct}%
        </div>
      </div>

      <BeatVisualizer active intensity={0.7 + job.progress * 0.6} />

      <div className="mt-5 h-1.5 rounded-full bg-black/40 overflow-hidden">
        <div
          className="h-full bg-accent-gradient transition-all duration-500 shadow-glow"
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="mt-5 grid grid-cols-5 gap-2">
        {stages.map((s) => {
          const active = job.progress >= s.threshold;
          return (
            <div key={s.key} className="text-center">
              <div
                className={`mx-auto h-1.5 w-1.5 rounded-full mb-1.5 transition-all ${
                  active
                    ? "bg-accent-glow shadow-[0_0_10px_rgba(192,132,252,0.9)]"
                    : "bg-white/15"
                }`}
              />
              <div
                className={`text-[10.5px] uppercase tracking-wider transition-colors ${
                  active ? "text-white/80" : "text-white/30"
                }`}
              >
                {s.label}
              </div>
            </div>
          );
        })}
      </div>

      {job.num_clips_scanned !== null && (
        <div className="mt-6 pt-5 border-t border-border grid grid-cols-3 gap-3 text-center">
          <Stat label="Clips" value={job.num_clips_scanned} />
          <Stat label="Candidates" value={job.num_candidates ?? "—"} />
          <Stat label="Tempo" value={job.tempo ? `${Math.round(job.tempo)} BPM` : "—"} />
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="font-mono text-lg text-white/90 tabular-nums">{value}</div>
      <div className="text-[10.5px] uppercase tracking-wider text-white/40 mt-0.5">
        {label}
      </div>
    </div>
  );
}
