"use client";

import { useEffect, useState } from "react";
import {
  Check,
  Key,
  Link2,
  Loader2,
  RefreshCw,
  Trash2,
  User,
  X,
} from "lucide-react";
import { cn } from "@/lib/cn";
import {
  listMedalClips,
  listMedalUserClips,
  resolveMedalUrl,
  type MedalClip,
} from "@/lib/api";
import { loadMedalSettings } from "@/lib/settings";

export function MedalPicker({
  librarySelected,
  onLibraryChange,
  profileSelected,
  onProfileSelectedChange,
  urlClips,
  onUrlClipsChange,
  onOpenSettings,
  reloadKey,
}: {
  librarySelected: Set<string>;
  onLibraryChange: (next: Set<string>) => void;
  profileSelected: MedalClip[];
  onProfileSelectedChange: (clips: MedalClip[]) => void;
  urlClips: MedalClip[];
  onUrlClipsChange: (clips: MedalClip[]) => void;
  onOpenSettings: () => void;
  reloadKey: number;
}) {
  const profileSelectedIds = new Set(profileSelected.map((c) => c.contentId));

  function toggleProfile(clip: MedalClip) {
    if (profileSelectedIds.has(clip.contentId)) {
      onProfileSelectedChange(profileSelected.filter((c) => c.contentId !== clip.contentId));
    } else {
      onProfileSelectedChange([...profileSelected, clip]);
    }
  }
  // ── Public profile (username / URL) ────────────────────────────────────
  const [profileInput, setProfileInput] = useState("");
  const [profileClips, setProfileClips] = useState<MedalClip[] | null>(null);
  const [profileUsername, setProfileUsername] = useState<string | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);

  async function loadProfile() {
    const q = profileInput.trim();
    if (!q) return;
    setProfileLoading(true);
    setProfileError(null);
    try {
      const r = await listMedalUserClips(q);
      setProfileClips(r.clips.map((c) => ({ ...c, origin: "library" as const })));
      setProfileUsername(r.username);
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : String(e));
      setProfileClips(null);
    } finally {
      setProfileLoading(false);
    }
  }

  function clearProfile() {
    setProfileClips(null);
    setProfileUsername(null);
    setProfileInput("");
    setProfileError(null);
  }

  // ── Authenticated library (API key) ────────────────────────────────────
  const [hasKey, setHasKey] = useState(false);
  const [libClips, setLibClips] = useState<MedalClip[] | null>(null);
  const [libLoading, setLibLoading] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);

  // ── Share-URL single clip ──────────────────────────────────────────────
  const [urlInput, setUrlInput] = useState("");
  const [urlBusy, setUrlBusy] = useState(false);
  const [urlError, setUrlError] = useState<string | null>(null);

  async function loadLibrary() {
    const { apiKey, userId } = loadMedalSettings();
    setHasKey(!!apiKey);
    if (!apiKey) {
      setLibClips(null);
      return;
    }
    setLibLoading(true);
    setLibraryError(null);
    try {
      const cs = await listMedalClips({ apiKey, userId: userId || undefined, limit: 50 });
      setLibClips(cs.map((c) => ({ ...c, origin: "library" as const })));
    } catch (e) {
      setLibraryError(e instanceof Error ? e.message : String(e));
      setLibClips([]);
    } finally {
      setLibLoading(false);
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

  // The profile clips replace the library display when loaded; the key-based
  // library remains available if the user has a Medal API key saved.
  const showingProfile = profileClips !== null;
  const activeClips = showingProfile ? profileClips : libClips;

  return (
    <div className="space-y-5">
      {/* ── Username / profile URL ───────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-fg-muted">
            Profile — username or URL
          </span>
          {showingProfile && (
            <button
              onClick={clearProfile}
              className="flex items-center gap-1 font-mono text-[10.5px] uppercase tracking-[0.12em] text-fg-muted hover:text-err"
            >
              <X className="h-3 w-3" />
              Clear
            </button>
          )}
        </div>
        <div className="flex gap-0">
          <div className="relative flex-1">
            <User className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-fg-muted" />
            <input
              type="text"
              value={profileInput}
              onChange={(e) => setProfileInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  loadProfile();
                }
              }}
              placeholder="defnotean  or  https://medal.tv/u/defnotean"
              className={cn(
                "w-full bg-surface-1 border border-border border-r-0 pl-9 pr-3 py-2",
                "font-mono text-[12px] text-fg placeholder:text-fg-muted",
                "focus:border-accent focus:outline-none",
              )}
              disabled={profileLoading}
            />
          </div>
          <button
            onClick={loadProfile}
            disabled={profileLoading || !profileInput.trim()}
            className={cn(
              "px-4 font-mono text-[11px] uppercase tracking-[0.1em] border",
              profileLoading || !profileInput.trim()
                ? "border-border bg-surface-1 text-fg-muted cursor-not-allowed"
                : "border-accent bg-accent text-white hover:brightness-110",
            )}
          >
            {profileLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Load"}
          </button>
        </div>
        {profileError && (
          <div className="mt-2 border border-err/50 bg-err/5 px-3 py-2 font-mono text-[11px] text-err break-all">
            {profileError}
          </div>
        )}
        {showingProfile && profileUsername && (
          <div className="mt-2 font-mono text-[10.5px] text-fg-dim uppercase tracking-[0.1em]">
            {profileClips!.length} public clip{profileClips!.length === 1 ? "" : "s"} from{" "}
            <span className="text-fg">{profileUsername}</span>
            {profileSelected.length > 0 && (
              <span className="ml-2 text-accent">
                · {profileSelected.length} selected
              </span>
            )}
          </div>
        )}
      </section>

      {/* ── Share URL (single clip) ──────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-fg-muted">
            Add single clip by URL
          </span>
          {urlClips.length > 0 && (
            <button
              onClick={clearAllUrls}
              className="flex items-center gap-1 font-mono text-[10.5px] uppercase tracking-[0.12em] text-fg-muted hover:text-err"
            >
              <Trash2 className="h-3 w-3" />
              Clear
            </button>
          )}
        </div>
        <div className="flex gap-0">
          <div className="relative flex-1">
            <Link2 className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-fg-muted" />
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
              placeholder="https://medal.tv/clips/..."
              className={cn(
                "w-full bg-surface-1 border border-border border-r-0 pl-9 pr-3 py-2",
                "font-mono text-[12px] text-fg placeholder:text-fg-muted",
                "focus:border-accent focus:outline-none",
              )}
              disabled={urlBusy}
            />
          </div>
          <button
            onClick={addUrl}
            disabled={urlBusy || !urlInput.trim()}
            className={cn(
              "px-4 font-mono text-[11px] uppercase tracking-[0.1em] border",
              urlBusy || !urlInput.trim()
                ? "border-border bg-surface-1 text-fg-muted cursor-not-allowed"
                : "border-accent bg-accent text-white hover:brightness-110",
            )}
          >
            {urlBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Add"}
          </button>
        </div>
        {urlError && (
          <div className="mt-2 border border-err/50 bg-err/5 px-3 py-2 font-mono text-[11px] text-err break-all">
            {urlError}
          </div>
        )}

        {urlClips.length > 0 && (
          <div className="mt-3 grid grid-cols-2 md:grid-cols-3 gap-2">
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
      </section>

      {/* ── Active clip grid: profile-loaded OR API-key library ───── */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-baseline gap-2 font-mono text-[10.5px] uppercase tracking-[0.12em]">
            <span className="text-fg-muted">
              {showingProfile ? "Clips" : "Library (API key)"}
            </span>
            {!showingProfile && libClips && (
              <span className="text-fg-dim tabular-nums">
                {libClips.length}
                {librarySelected.size > 0 && (
                  <span className="ml-1 text-accent">
                    · {librarySelected.size} selected
                  </span>
                )}
              </span>
            )}
          </div>
          {!showingProfile && hasKey && (
            <button
              onClick={loadLibrary}
              disabled={libLoading}
              className="flex items-center gap-1 font-mono text-[10.5px] uppercase tracking-[0.12em] text-fg-muted hover:text-fg disabled:opacity-40"
            >
              <RefreshCw className={cn("h-3 w-3", libLoading && "animate-spin")} />
              Refresh
            </button>
          )}
        </div>

        {!showingProfile && !hasKey && (
          <div className="border border-dashed border-border p-6 text-center">
            <Key className="h-4 w-4 text-fg-muted mx-auto mb-2" />
            <div className="text-[12.5px] text-fg mb-1">
              Browse any user's clips with the field above
            </div>
            <div className="text-[11.5px] text-fg-dim max-w-sm mx-auto mb-3">
              Or add your own Medal API key to list your full private library.
            </div>
            <button
              onClick={onOpenSettings}
              className="px-4 py-2 bg-surface-2 border border-border text-fg font-mono text-[11px] uppercase tracking-[0.1em] hover:border-border-strong"
            >
              Open settings
            </button>
          </div>
        )}

        {!showingProfile && libraryError && (
          <div className="mb-2 border border-err/50 bg-err/5 px-3 py-2 font-mono text-[11px] text-err break-all">
            {libraryError}
          </div>
        )}

        {!showingProfile && hasKey && libLoading && !libClips && (
          <div className="flex items-center justify-center h-40 border border-border bg-surface-1">
            <Loader2 className="h-4 w-4 text-fg-dim animate-spin" />
          </div>
        )}

        {activeClips && activeClips.length === 0 && !profileLoading && !libLoading && (
          <div className="border border-border bg-surface-1 p-6 text-center text-[12px] text-fg-dim">
            No clips found.
          </div>
        )}

        {activeClips && activeClips.length > 0 && (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 max-h-[420px] overflow-y-auto">
            {activeClips.map((c) =>
              showingProfile ? (
                <ClipCard
                  key={c.contentId}
                  clip={c}
                  checked={profileSelectedIds.has(c.contentId)}
                  onToggle={() => toggleProfile(c)}
                />
              ) : (
                <ClipCard
                  key={c.contentId}
                  clip={c}
                  checked={librarySelected.has(c.contentId)}
                  onToggle={() => toggleLibrary(c.contentId)}
                />
              )
            )}
          </div>
        )}
      </section>
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
        "group relative block overflow-hidden border text-left aspect-video bg-black transition-colors",
        checked
          ? "border-accent"
          : "border-border hover:border-border-strong",
      )}
    >
      {clip.thumbnail ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={clip.thumbnail}
          alt={clip.title}
          className="h-full w-full object-cover"
          loading="lazy"
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-surface-2" />
      )}

      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 to-transparent p-2">
        <div className="truncate text-[11.5px] text-fg">
          {clip.title}
        </div>
        <div className="flex items-center justify-between font-mono text-[10px] text-fg-dim mt-0.5 tabular-nums">
          <span>{durLabel}</span>
          <span className="uppercase tracking-[0.08em] text-fg-muted">
            {clip.origin === "url" ? "url" : clip.rawFileUrl ? "direct" : "scrape"}
          </span>
        </div>
      </div>

      <div
        className={cn(
          "absolute top-1.5 right-1.5 flex h-5 w-5 items-center justify-center transition-opacity",
          checked
            ? removable
              ? "bg-err text-white"
              : "bg-accent text-white"
            : "bg-black/60 border border-border-strong opacity-0 group-hover:opacity-100",
        )}
      >
        {checked ? (
          removable ? <X className="h-3 w-3" /> : <Check className="h-3 w-3" />
        ) : null}
      </div>
    </button>
  );
}
