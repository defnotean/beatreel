"use client";

import { cn } from "@/lib/cn";

export interface SourceTab {
  key: string;
  label: string;
  icon: React.ReactNode;
}

export function SourceTabs({
  tabs,
  active,
  onChange,
}: {
  tabs: SourceTab[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="grid auto-cols-fr grid-flow-col border border-border bg-surface-1">
      {tabs.map((t, i) => {
        const isActive = t.key === active;
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onChange(t.key)}
            aria-pressed={isActive}
            className={cn(
              "flex items-center justify-center gap-2 py-2 px-3 transition-colors",
              "font-mono text-[11px] uppercase tracking-[0.1em]",
              isActive
                ? "bg-surface-3 text-fg"
                : "text-fg-dim hover:text-fg hover:bg-surface-2",
              i > 0 && "border-l border-border",
            )}
          >
            <span className="flex h-3.5 w-3.5 items-center justify-center">
              {t.icon}
            </span>
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
