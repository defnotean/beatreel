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
  minH?: string;
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

function totalMB(files: File[]): number {
  return files.reduce((acc, f) => acc + f.size, 0) / 1024 / 1024;
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
  minH = "min-h-[160px]",
  className,
}: DropZoneProps) {
  const [dragging, setDragging] = useState(false);
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

  const hasFiles = files.length > 0;

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          inputRef.current?.click();
        }
      }}
      className={cn(
        "relative flex flex-col cursor-pointer transition-colors",
        "border border-dashed bg-surface-1",
        minH,
        dragging
          ? "border-accent bg-surface-2"
          : hasFiles
            ? "border-border-strong"
            : "border-border hover:border-border-strong hover:bg-surface-2",
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

      {!hasFiles ? (
        <div className="flex flex-col items-center justify-center text-center flex-1 px-6 py-8">
          <div className="text-fg-muted mb-3">{icon}</div>
          <div className="text-[13px] text-fg">{title}</div>
          <div className="mt-1 font-mono text-[11px] text-fg-muted uppercase tracking-[0.08em]">
            {hint}
          </div>
        </div>
      ) : (
        <div className="flex-1 p-3 flex flex-col">
          <div className="flex items-baseline justify-between mb-2 font-mono text-[11px] uppercase tracking-[0.08em]">
            <span className="text-fg">
              {files.length} {files.length === 1 ? "file" : "files"}
            </span>
            <span className="text-fg-muted">
              {totalMB(files).toFixed(1)} MB
            </span>
          </div>
          <div className="flex-1 overflow-y-auto font-mono text-[11.5px] text-fg-dim space-y-0.5 pr-1">
            {files.slice(0, 12).map((f) => (
              <div
                key={f.name + f.size}
                className="flex justify-between gap-4 truncate"
              >
                <span className="truncate">{f.name}</span>
                <span className="shrink-0 text-fg-muted tabular-nums">
                  {(f.size / 1024 / 1024).toFixed(1)}M
                </span>
              </div>
            ))}
            {files.length > 12 && (
              <div className="text-fg-muted pt-1">
                + {files.length - 12} more
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
