"use client";

import { cn } from "@/lib/cn";

const OPTIONS = [
  { value: "chill", label: "Chill", hint: "Longer cuts, slower vibe", dots: 1 },
  { value: "balanced", label: "Balanced", hint: "The default", dots: 2 },
  { value: "hype", label: "Hype", hint: "Rapid cuts on every beat", dots: 3 },
] as const;

export type Intensity = (typeof OPTIONS)[number]["value"];

export function IntensityPicker({
  value,
  onChange,
}: {
  value: Intensity;
  onChange: (v: Intensity) => void;
}) {
  return (
    <div className="grid grid-cols-3 gap-2 rounded-xl bg-black/30 p-1.5 border border-border">
      {OPTIONS.map((opt) => {
        const active = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={cn(
              "relative flex flex-col items-center py-3 px-3 rounded-lg transition-all duration-200",
              active
                ? "bg-accent-gradient text-white shadow-glow"
                : "text-white/60 hover:bg-white/5 hover:text-white/90",
            )}
          >
            <div className="flex gap-0.5 mb-1">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className={cn(
                    "w-1 h-3 rounded-full transition-all",
                    i < opt.dots
                      ? active ? "bg-white" : "bg-white/70"
                      : active ? "bg-white/30" : "bg-white/15",
                  )}
                  style={{ height: 6 + i * 3 }}
                />
              ))}
            </div>
            <span className="text-[13px] font-medium">{opt.label}</span>
            <span
              className={cn(
                "text-[10.5px] mt-0.5 transition-opacity",
                active ? "text-white/80" : "text-white/40",
              )}
            >
              {opt.hint}
            </span>
          </button>
        );
      })}
    </div>
  );
}
