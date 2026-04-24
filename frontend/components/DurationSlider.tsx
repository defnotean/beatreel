"use client";

export function DurationSlider({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  const min = 15;
  const max = 180;
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-fg-muted">
          Duration
        </span>
        <span className="font-mono text-[14px] text-fg tabular-nums">
          {value}s
        </span>
      </div>
      <div className="relative h-[6px] bg-surface-1 border border-border">
        <div
          className="absolute inset-y-0 left-0 bg-accent"
          style={{ width: `${pct}%` }}
        />
        <input
          type="range"
          min={min}
          max={max}
          step={5}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="absolute inset-0 w-full h-full cursor-pointer"
          aria-label="Target duration in seconds"
        />
      </div>
      <div className="flex justify-between font-mono text-[10px] text-fg-muted tabular-nums">
        <span>{min}s</span>
        <span>60s</span>
        <span>{max}s</span>
      </div>
    </div>
  );
}
