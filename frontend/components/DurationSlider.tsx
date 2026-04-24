"use client";

export function DurationSlider({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  const pct = ((value - 15) / (180 - 15)) * 100;
  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <span className="text-[13px] text-white/70">Target duration</span>
        <span className="font-mono text-[15px] text-accent-glow">
          {value}s
        </span>
      </div>
      <div className="relative h-2 rounded-full bg-black/40 overflow-hidden">
        <div
          className="absolute inset-y-0 left-0 bg-accent-gradient rounded-full transition-all"
          style={{ width: `${pct}%` }}
        />
        <input
          type="range"
          min={15}
          max={180}
          step={5}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="absolute inset-0 w-full h-full cursor-pointer opacity-0"
          aria-label="target duration in seconds"
        />
      </div>
      <div className="flex justify-between text-[10.5px] text-white/35 font-mono">
        <span>15s</span>
        <span>60s</span>
        <span>180s</span>
      </div>
    </div>
  );
}
