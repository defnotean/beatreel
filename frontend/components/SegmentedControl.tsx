"use client";

import { cn } from "@/lib/cn";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  /** Short secondary label, rendered below in mono when present. */
  sublabel?: string;
  /** Optional leading visual (e.g., a small aspect indicator). */
  leading?: React.ReactNode;
}

interface SegmentedControlProps<T extends string> {
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (v: T) => void;
  /** Text-only compact mode (no sublabels, no leading). */
  compact?: boolean;
  ariaLabel?: string;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  compact = false,
  ariaLabel,
}: SegmentedControlProps<T>) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className="grid auto-cols-fr grid-flow-col border border-border bg-surface-1"
    >
      {options.map((opt, i) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(opt.value)}
            className={cn(
              "relative flex flex-col items-center justify-center gap-1 px-3 transition-colors",
              compact ? "py-2" : "py-2.5",
              active
                ? "bg-accent text-white"
                : "text-fg-dim hover:text-fg hover:bg-surface-2",
              i > 0 && "border-l border-border",
            )}
          >
            {opt.leading && (
              <span
                className={cn(
                  "flex items-center justify-center",
                  active ? "text-white" : "text-fg-dim",
                )}
              >
                {opt.leading}
              </span>
            )}
            <span className="font-mono text-[12px] uppercase tracking-[0.08em]">
              {opt.label}
            </span>
            {!compact && opt.sublabel && (
              <span
                className={cn(
                  "font-mono text-[9.5px] uppercase tracking-[0.12em]",
                  active ? "text-white/75" : "text-fg-muted",
                )}
              >
                {opt.sublabel}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
