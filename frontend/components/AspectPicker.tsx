"use client";

import { cn } from "@/lib/cn";

export type Aspect = "landscape" | "portrait" | "square";

const OPTIONS: { value: Aspect; label: string; hint: string; w: number; h: number }[] = [
  { value: "landscape", label: "16:9", hint: "YouTube", w: 28, h: 16 },
  { value: "portrait", label: "9:16", hint: "TikTok / Shorts", w: 16, h: 28 },
  { value: "square", label: "1:1", hint: "Instagram", w: 22, h: 22 },
];

export function AspectPicker({
  value,
  onChange,
}: {
  value: Aspect;
  onChange: (v: Aspect) => void;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-[13px] text-white/70">Aspect ratio</span>
        <span className="font-mono text-[11.5px] text-accent-glow">
          {OPTIONS.find((o) => o.value === value)?.hint}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 rounded-xl bg-black/30 p-1.5 border border-border">
        {OPTIONS.map((opt) => {
          const active = value === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => onChange(opt.value)}
              className={cn(
                "flex flex-col items-center gap-1.5 py-3 px-3 rounded-lg transition-all",
                active
                  ? "bg-accent-gradient text-white shadow-glow"
                  : "text-white/60 hover:bg-white/5 hover:text-white/90",
              )}
            >
              <div
                className={cn(
                  "rounded-sm border-2 transition-colors",
                  active ? "border-white" : "border-white/50",
                )}
                style={{ width: opt.w, height: opt.h }}
                aria-hidden
              />
              <span className="text-[12.5px] font-medium">{opt.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
