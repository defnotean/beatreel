"use client";

import { useEffect, useState } from "react";
import {
  Check,
  Key,
  Link2,
  Loader2,
  RefreshCw,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { listMedalClips, resolveMedalUrl, type MedalClip } from "@/lib/api";
import { loadMedalSettings } from "@/lib/settings";

export function MedalPicker({
  librarySelected,
  onLibraryChange,
  urlClips,
  onUrlClipsChange,
  onOpenSettings,
  reloadKey,
}: {
  librarySelected: Set<string>;
  onLibraryChange: (next: Set<string>) => void;
  urlClips: MedalClip[];
  onUrlClipsChange: (clips: MedalClip[]) => void;
  onOpenSettings: () => void;
  reloadKey: number;
}) {
  const [hasKey, setHasKey] = useState(false);
  const [clips, setClips] = useState<MedalClip[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);

  const [urlInput, setUrlInput] = useState("");
  const [urlBusy, setUrlBusy] = useState(false);
  const [urlError, setUrlError] = useState<string | null>(null);

  async function loadLibrary() {
    const { apiKey, userId } = loadMedalSettings();
    setHasKey(!!apiKey);
    if (!apiKey) {
      setClips(null);
      return;
    }
    setLoading(true);
    setLibraryError(null);
    try {
      const cs = await listMedalClips({ apiKey, userId: userId || undefined, limit: 50 });
      setClips(cs.map((c) => ({ ...c, origin: "library" as const })));
    } catch (e) {
      setLibraryError(e instanceof Error ? e.message : String(e));
      setClips([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadLibrary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  function toggleLibrary(id: string) {
    const next = new Set(librarySelected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onLibraryChange(next);
  }

  async function addUrl() {
    const raw = urlInput.trim();
    if (!raw) return;
    setUrlBusy(true);
    setUrlError(null);
    try {
      const clip = await resolveMedalUrl(raw);
      const existing = urlClips.find((c) => c.contentId === clip.contentId);
      if (!existing) {
        onUrlClipsChange([...urlClips, clip]);
      }
      setUrlInput("");
    } catch (e) {
      setUrlError(e instanceof Error ? e.message : String(e));
    } finally {
      setUrlBusy(false);
    }
  }

  function removeUrl(contentId: string) {
    onUrlClipsChange(urlClips.filter((c) => c.contentId !== contentId));
  }

  function clearAllUrls() {
    onUrlClipsChange([]);
  }

  return (
    <div className="space-y-5">
      {/* Share URL section — always available */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] uppercase tracking-[0.18em] text-accent-glow">
            paste medal share url
          </span>
          {urlClips.length > 0 && (
            <button
              onClick={clearAllUrls}
              className="flex items-center gap-1 text-[11.5px] text-white/40 hover:text-red-300 transition-colors"
            >
              <Trash2 className="h-3 w-3" />
              Clear
            </button>
          )}
        </div>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Link2 className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-white/30" />
            <input
              type="url"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addUrl();
                }
              }}
              placeholder="https://medal.tv/..."
              className={cn(
                "w-full bg-black/40 border border-border rounded-lg pl-10 pr-3 py-2.5",
                "text-[13px] font-mono placeholder:text-white/20",
                "focus:outline-none focus:border-accent/60 focus:ring-1 focus:ring-accent/30",
              )}
              disabled={urlBusy}
            />
          </div>
          <button
            onClick={addUrl}
            disabled={urlBusy || !urlInput.trim()}
            className={cn(
              "px-4 rounded-lg text-[13px] font-medium transition-all",
              urlBusy || !urlInput.trim()
                ? "bg-white/5 text-white/30 cursor-not-allowed"
                : "bg-accent-gradient text-white shadow-glow hover:shadow-glow-lg",
            )}
          >
            {urlBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Add"}
          </button>
        </div>
        {urlError && (
          <div className="mt-2 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-300">
            {urlError}
          </div>
        )}

        {urlClips.length > 0 && (
          <div className="mt-3 grid grid-cols-2 md:grid-cols-3 gap-3">
            {urlClips.map((c) => (
              <ClipCard
                key={c.contentId}
                clip={c}
                checked
                removable
                onToggle={() => removeUrl(c.contentId)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Library section */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-baseline gap-2">
            <span className="text-[11px] uppercase tracking-[0.18em] text-accent-glow">
              your library
            </span>
            {clips && (
              <span className="text-[11.5px] text-white/40">
                · {clips.length} clip{clips.length === 1 ? "" : "s"}
                {librarySelected.size > 0 && (
                  <span className="ml-1 text-accent-glow">
                    ({librarySelected.size} selected)
                  </span>
                )}
              </span>
            )}
          </div>
          {hasKey && (
            <button
              onClick={loadLibrary}
              disabled={loading}
              className="flex items-center gap-1.5 text-[12px] text-white/50 hover:text-white transition-colors disabled:opacity-40"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
              Refresh
            </button>
          )}
        </div>

        {!hasKey && (
          <div className="rounded-xl border border-dashed border-border p-6 text-center">
            <div className="mx-auto mb-3 inline-flex rounded-xl bg-white/5 p-2.5 text-white/50">
              <Key className="h-4 w-4" />
            </div>
            <div className="text-[13.5px] font-medium text-white/80 mb-1">
              Browse your full library
            </div>
            <div className="text-[12.5px] text-white/45 max-w-sm mx-auto mb-4">
              Add your Medal API key in settings to list and multi-select your own clips.
            </div>
            <button
              onClick={onOpenSettings}
              className="px-3.5 py-2 rounded-lg bg-accent-gradient text-white text-[12.5px] font-medium shadow-glow hover:shadow-glow-lg transition-all"
            >
              Open settings
            </button>
          </div>
        )}

        {libraryError && (
          <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-[12.5px] text-red-300">
            {libraryError}
          </div>
        )}

        {hasKey && loading && !clips && (
          <div className="flex items-center justify-center h-48 rounded-xl bg-black/20 border border-border">
            <Loader2 className="h-5 w-5 text-accent-glow animate-spin" />
          </div>
        )}

        {hasKey && clips && clips.length === 0 && !loading && (
          <div className="rounded-xl border border-border p-6 text-center text-[13px] text-white/50">
            No clips found. Check your API key and user ID.
          </div>
        )}

        {hasKey && clips && clips.length > 0 && (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 max-h-[420px] overflow-y-auto pr-1 -mr-1">
            {clips.map((c) => (
              <ClipCard
                key={c.contentId}
                clip={c}
                checked={librarySelected.has(c.contentId)}
                onToggle={() => toggleLibrary(c.contentId)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ClipCard({
  clip,
  checked,
  removable = false,
  onToggle,
}: {
  clip: MedalClip;
  checked: boolean;
  removable?: boolean;
  onToggle: () => void;
}) {
  const mins = Math.floor(clip.duration / 60);
  const secs = Math.round(clip.duration % 60);
  const durLabel =
    clip.duration > 0
      ? mins > 0
        ? `${mins}:${secs.toString().padStart(2, "0")}`
        : `${secs}s`
      : "—";

  return (
    <button
      type="button"
      onClick={onToggle}
      className={cn(
        "group relative block rounded-xl overflow-hidden border transition-all text-left",
        "aspect-video bg-black/60",
        checked
          ? "border-accent shadow-glow ring-2 ring-accent/40"
          : "border-border hover:border-accent/50",
      )}
    >
      {clip.thumbnail ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={clip.thumbnail}
          alt={clip.title}
          className="h-full w-full object-cover transition-transform group-hover:scale-[1.03]"
          loading="lazy"
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-accent/20 to-accent-secondary/10">
          <Zap className="h-6 w-6 text-accent-glow" />
        </div>
      )}

      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/95 via-black/50 to-transparent p-2.5">
        <div className="truncate text-[12px] font-medium text-white/95">
          {clip.title}
        </div>
        <div className="flex items-center justify-between text-[10.5px] text-white/60 mt-0.5 font-mono">
          <span>{durLabel}</span>
          <span
            className={cn(
              "uppercase tracking-wider",
              clip.origin === "url" ? "text-accent-glow" : "text-white/40",
            )}
          >
            {clip.origin === "url" ? "url" : clip.rawFileUrl ? "api" : "scrape"}
          </span>
        </div>
      </div>

      <div
        className={cn(
          "absolute top-2 right-2 flex h-6 w-6 items-center justify-center rounded-full transition-all",
          checked
            ? removable
              ? "bg-red-500/90 shadow-[0_0_12px_rgba(239,68,68,0.6)]"
              : "bg-accent-gradient shadow-glow"
            : "bg-black/60 border border-white/20 opacity-0 group-hover:opacity-100",
        )}
      >
        {checked ? (
          removable ? (
            <X className="h-3.5 w-3.5 text-white" />
          ) : (
            <Check className="h-3.5 w-3.5 text-white" />
          )
        ) : null}
      </div>
    </button>
  );
}
