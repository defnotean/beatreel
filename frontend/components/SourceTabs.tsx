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
    <div className="flex gap-1 rounded-xl bg-black/30 border border-border p-1">
      {tabs.map((t) => {
        const isActive = t.key === active;
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onChange(t.key)}
            className={cn(
              "flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-lg transition-all",
              "text-[13px] font-medium",
              isActive
                ? "bg-accent-gradient text-white shadow-glow"
                : "text-white/55 hover:text-white/90 hover:bg-white/5",
            )}
          >
            <span className="flex h-4 w-4 items-center justify-center">
              {t.icon}
            </span>
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
