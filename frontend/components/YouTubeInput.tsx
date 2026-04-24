"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, Link2, Loader2, Youtube } from "lucide-react";
import { cn } from "@/lib/cn";
import { probeYouTube, type YouTubePreview } from "@/lib/api";

const YT_PATTERN =
  /^https?:\/\/(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)\//i;

export function YouTubeInput({
  value,
  onChange,
  onPreview,
}: {
  value: string;
  onChange: (url: string) => void;
  onPreview: (p: YouTubePreview | null) => void;
}) {
  const [preview, setPreview] = useState<YouTubePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    setError(null);

    if (!value) {
      setPreview(null);
      onPreview(null);
      return;
    }
    if (!YT_PATTERN.test(value)) {
      setPreview(null);
      onPreview(null);
      return;
    }

    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const p = await probeYouTube(value);
        setPreview(p);
        onPreview(p);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setPreview(null);
        onPreview(null);
      } finally {
        setLoading(false);
      }
    }, 600);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  return (
    <div className="space-y-2">
      <div className="relative">
        <Link2 className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-fg-muted" />
        <input
          type="url"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="https://youtube.com/..."
          className={cn(
            "w-full bg-surface-1 border border-border",
            "pl-9 pr-9 py-2.5",
            "font-mono text-[12px] text-fg placeholder:text-fg-muted",
            "focus:border-accent focus:outline-none",
          )}
        />
        {loading && (
          <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-fg-dim animate-spin" />
        )}
      </div>

      {error && (
        <div className="flex items-start gap-2 border border-err/50 bg-err/5 p-2.5 font-mono text-[11.5px] text-err">
          <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          <span className="break-all">{error}</span>
        </div>
      )}

      {preview && (
        <div className="flex gap-3 border border-border bg-surface-1 p-2.5">
          {preview.thumbnail ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={preview.thumbnail}
              alt=""
              className="h-14 w-24 shrink-0 object-cover border border-border"
            />
          ) : (
            <div className="h-14 w-24 shrink-0 bg-surface-2 flex items-center justify-center border border-border">
              <Youtube className="h-4 w-4 text-fg-muted" />
            </div>
          )}
          <div className="min-w-0 flex-1 flex flex-col">
            <div className="truncate text-[12.5px] text-fg">
              {preview.title}
            </div>
            <div className="mt-0.5 truncate font-mono text-[11px] text-fg-dim">
              {preview.uploader}
            </div>
            <div className="mt-auto font-mono text-[10.5px] text-fg-muted uppercase tracking-[0.08em]">
              {formatDuration(preview.duration)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function formatDuration(seconds: number): string {
  if (!seconds || !Number.isFinite(seconds)) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}
