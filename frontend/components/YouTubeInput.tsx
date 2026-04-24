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
    <div className="space-y-3">
      <div className="relative">
        <Link2 className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-white/30" />
        <input
          type="url"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Paste a YouTube URL..."
          className={cn(
            "w-full bg-black/40 border border-border rounded-xl pl-10 pr-10 py-3",
            "text-[13.5px] placeholder:text-white/25",
            "focus:outline-none focus:border-accent/60 focus:ring-1 focus:ring-accent/30",
          )}
        />
        {loading && (
          <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-accent-glow animate-spin" />
        )}
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-[12.5px] text-red-300">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {preview && (
        <div className="flex gap-3 rounded-xl border border-border bg-black/30 p-3">
          {preview.thumbnail ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={preview.thumbnail}
              alt=""
              className="h-16 w-28 shrink-0 rounded-lg object-cover border border-border"
            />
          ) : (
            <div className="h-16 w-28 shrink-0 rounded-lg bg-gradient-to-br from-red-500/20 to-orange-500/10 flex items-center justify-center">
              <Youtube className="h-5 w-5 text-red-400" />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13.5px] font-medium">
              {preview.title}
            </div>
            <div className="mt-0.5 truncate text-[11.5px] text-white/50">
              {preview.uploader}
            </div>
            <div className="mt-1 font-mono text-[11px] text-accent-glow">
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
