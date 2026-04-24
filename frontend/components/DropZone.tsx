"use client";

import { useCallback, useRef, useState } from "react";
import { cn } from "@/lib/cn";

interface DropZoneProps {
  icon: React.ReactNode;
  title: string;
  hint: string;
  accept: string;
  multiple?: boolean;
  folder?: boolean;
  files: File[];
  onFiles: (files: File[]) => void;
  className?: string;
}

const VIDEO_EXTS = new Set([".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv"]);

function filterFiles(files: FileList | File[], accept: string): File[] {
  const list = Array.from(files);
  if (accept === "video/*") {
    return list.filter((f) => {
      const ext = "." + (f.name.split(".").pop() || "").toLowerCase();
      return f.type.startsWith("video/") || VIDEO_EXTS.has(ext);
    });
  }
  if (accept === "audio/*") {
    return list.filter((f) => f.type.startsWith("audio/") || /\.(mp3|wav|flac|m4a|ogg|aac)$/i.test(f.name));
  }
  return list;
}

export function DropZone({
  icon,
  title,
  hint,
  accept,
  multiple,
  folder,
  files,
  onFiles,
  className,
}: DropZoneProps) {
  const [dragging, setDragging] = useState(false);
  const [mouse, setMouse] = useState({ x: 50, y: 50 });
  const inputRef = useRef<HTMLInputElement>(null);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      if (!e.dataTransfer) return;
      const accepted = filterFiles(e.dataTransfer.files, accept);
      if (accepted.length) onFiles(accepted);
    },
    [accept, onFiles],
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onMouseMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        setMouse({
          x: ((e.clientX - r.left) / r.width) * 100,
          y: ((e.clientY - r.top) / r.height) * 100,
        });
      }}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          inputRef.current?.click();
        }
      }}
      style={
        {
          "--mx": `${mouse.x}%`,
          "--my": `${mouse.y}%`,
        } as React.CSSProperties
      }
      className={cn(
        "group relative flex flex-col items-center justify-center rounded-2xl p-8 cursor-pointer",
        "transition-all duration-300 drop-glow",
        "border border-dashed",
        dragging
          ? "border-accent shadow-glow-lg scale-[1.01] bg-bg-panel/60"
          : "border-border hover:border-accent/60 hover:bg-bg-panel/40",
        files.length > 0 && "border-accent/40 bg-bg-panel/30",
        className,
      )}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        {...(folder ? ({ webkitdirectory: "", directory: "" } as any) : {})}
        onChange={(e) => {
          if (e.target.files) {
            const accepted = filterFiles(e.target.files, accept);
            if (accepted.length) onFiles(accepted);
          }
        }}
        className="hidden"
      />

      <div
        className={cn(
          "mb-4 rounded-xl p-3 transition-all duration-300",
          dragging || files.length > 0
            ? "bg-accent/20 text-accent-glow"
            : "bg-white/5 text-white/60 group-hover:text-accent-glow group-hover:bg-accent/10",
        )}
      >
        {icon}
      </div>

      <div className="text-center">
        <div className="text-[15px] font-medium text-white/90">{title}</div>
        <div className="mt-1 text-[13px] text-white/45">{hint}</div>
      </div>

      {files.length > 0 && (
        <div className="mt-5 w-full">
          <div className="text-[11px] uppercase tracking-wider text-accent-glow mb-2 text-center">
            {files.length} file{files.length === 1 ? "" : "s"} selected
          </div>
          <div className="max-h-28 overflow-y-auto rounded-lg bg-black/30 px-3 py-2 text-[12px] font-mono text-white/60 space-y-0.5">
            {files.slice(0, 8).map((f) => (
              <div key={f.name + f.size} className="flex justify-between gap-4 truncate">
                <span className="truncate">{f.name}</span>
                <span className="shrink-0 text-white/30">
                  {(f.size / 1024 / 1024).toFixed(1)} MB
                </span>
              </div>
            ))}
            {files.length > 8 && (
              <div className="text-white/40 text-center pt-1">
                + {files.length - 8} more
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
