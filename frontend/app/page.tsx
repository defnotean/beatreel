"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Clock,
  Copy,
  Film,
  Music,
  RefreshCw,
  Settings,
  Sparkles,
  Upload,
  Youtube,
  Zap,
} from "lucide-react";

import { Logo } from "@/components/Logo";
import { DropZone } from "@/components/DropZone";
import { HistoryPanel } from "@/components/HistoryPanel";
import { IntensityPicker, type Intensity } from "@/components/IntensityPicker";
import { DurationSlider } from "@/components/DurationSlider";
import { AspectPicker, type Aspect } from "@/components/AspectPicker";
import { GamePicker, type Game } from "@/components/GamePicker";
import { ProgressPanel } from "@/components/ProgressPanel";
import { ResultPanel } from "@/components/ResultPanel";
import { SettingsModal } from "@/components/SettingsModal";
import { MedalPicker } from "@/components/MedalPicker";
import { YouTubeInput } from "@/components/YouTubeInput";
import { SourceTabs } from "@/components/SourceTabs";
import {
  checkHealth,
  createJob,
  getJob,
  listJobs,
  type JobHistoryEntry,
  type JobState,
  type MedalClip,
  type YouTubePreview,
} from "@/lib/api";
import { loadGeminiKeys, loadMedalSettings } from "@/lib/settings";

type View = "setup" | "processing" | "done" | "error";
type ClipTab = "upload" | "medal" | "auto_clip";
type MusicTab = "upload" | "youtube" | "reuse";
type PageTab = "create" | "history";

export default function Home() {
  // Top-level nav
  const [pageTab, setPageTab] = useState<PageTab>("create");
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);

  // Source selection
  const [clipTab, setClipTab] = useState<ClipTab>("upload");
  const [musicTab, setMusicTab] = useState<MusicTab>("upload");

  // Upload state
  const [clips, setClips] = useState<File[]>([]);
  const [sourceVideo, setSourceVideo] = useState<File[]>([]);
  const [music, setMusic] = useState<File[]>([]);
  // Reused music: if set, the Music section uses this job's music file.
  const [reusedMusic, setReusedMusic] = useState<JobHistoryEntry | null>(null);
  // Auto-clip long-form tier opt-in
  const [includeLongForm, setIncludeLongForm] = useState(false);

  // Medal state
  const [medalLibrarySelected, setMedalLibrarySelected] = useState<Set<string>>(new Set());
  const [medalProfileSelected, setMedalProfileSelected] = useState<MedalClip[]>([]);
  const [medalUrlClips, setMedalUrlClips] = useState<MedalClip[]>([]);
  const [medalReloadKey, setMedalReloadKey] = useState(0);

  // YouTube state
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [youtubePreview, setYoutubePreview] = useState<YouTubePreview | null>(null);

  // Params
  const [duration, setDuration] = useState(60);
  const [intensity, setIntensity] = useState<Intensity>("balanced");
  const [aspect, setAspect] = useState<Aspect>("landscape");
  const [game, setGame] = useState<Game>("valorant");
  const [geminiKeys, setGeminiKeys] = useState<string[]>([]);

  // App state
  const [view, setView] = useState<View>("setup");
  const [job, setJob] = useState<JobState | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [health, setHealth] = useState<{
    ok: boolean;
    ffmpeg: boolean;
    ffmpeg_error: string | null;
    gemini_configured?: boolean;
    gemini_keys_configured?: number;
  } | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const ks = loadGeminiKeys();
    setGeminiKeys(ks);
    checkHealth()
      .then((h) => {
        setHealth(h);
        const aiAvailable = ks.length > 0 || !!h.gemini_configured || (h.gemini_keys_configured ?? 0) > 0;
        if (aiAvailable) {
          setGame("valorant_ai");
          setIntensity("auto");
        }
      })
      .catch(() => setHealth({ ok: false, ffmpeg: false, ffmpeg_error: "backend unreachable" }));
  }, []);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const medalTotal =
    medalLibrarySelected.size + medalProfileSelected.length + medalUrlClips.length;
  const clipsReady =
    clipTab === "upload"
      ? clips.length > 0
      : clipTab === "auto_clip"
        ? sourceVideo.length > 0
        : medalTotal > 0;
  const musicReady =
    musicTab === "upload"
      ? music.length > 0
      : musicTab === "reuse"
        ? reusedMusic !== null
        : youtubePreview !== null;
  const canSubmit = clipsReady && musicReady && (health?.ffmpeg ?? false);

  const clipsSummary = useMemo(() => {
    if (clipTab === "upload") {
      if (clips.length === 0) return null;
      const mb = clips.reduce((a, f) => a + f.size, 0) / 1024 / 1024;
      return `${clips.length} file${clips.length === 1 ? "" : "s"} · ${mb.toFixed(1)} MB`;
    }
    if (clipTab === "auto_clip") {
      if (sourceVideo.length === 0) return null;
      const f = sourceVideo[0];
      const mb = f.size / 1024 / 1024;
      return `${f.name} · ${mb.toFixed(1)} MB`;
    }
    if (medalTotal === 0) return null;
    return `${medalTotal} Medal clip${medalTotal === 1 ? "" : "s"}`;
  }, [clipTab, clips, sourceVideo, medalTotal]);

  const musicSummary = useMemo(() => {
    if (musicTab === "upload") {
      if (music.length === 0) return null;
      return music[0].name;
    }
    if (musicTab === "reuse") {
      if (!reusedMusic) return null;
      return reusedMusic.music_filename ?? `reel ${reusedMusic.job_id.slice(0, 8)}`;
    }
    if (!youtubePreview) return null;
    return youtubePreview.title;
  }, [musicTab, music, youtubePreview, reusedMusic]);

  async function onSubmit() {
    if (!canSubmit) return;
    setView("processing");
    setErrMsg(null);

    try {
      const clipsParam =
        clipTab === "upload"
          ? ({ type: "upload" as const, files: clips })
          : clipTab === "auto_clip"
            ? ({ type: "auto_clip" as const, sourceVideo: sourceVideo[0] })
            : (() => {
                const s = loadMedalSettings();
                return {
                  type: "medal" as const,
                  apiKey: s.apiKey || undefined,
                  userId: s.userId || undefined,
                  clipIds: Array.from(medalLibrarySelected),
                  shareUrls: medalUrlClips.map((c) => c.directClipUrl),
                  publicClips: medalProfileSelected,
                };
              })();

      const musicParam =
        musicTab === "upload"
          ? ({ type: "upload" as const, file: music[0] })
          : musicTab === "reuse" && reusedMusic
            ? ({ type: "reuse" as const, jobId: reusedMusic.job_id })
            : ({ type: "youtube" as const, url: youtubeUrl });

      const { job_id } = await createJob({
        clips: clipsParam,
        music: musicParam,
        duration,
        intensity,
        aspect,
        game,
        geminiKeys: geminiKeys.length > 0 ? geminiKeys : undefined,
        includeLongForm: clipTab === "auto_clip" ? includeLongForm : undefined,
      });

      startPolling(job_id);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
      setView("error");
    }
  }

  function reset() {
    if (pollRef.current) clearInterval(pollRef.current);
    setClips([]);
    setSourceVideo([]);
    setMusic([]);
    setMedalLibrarySelected(new Set());
    setMedalProfileSelected([]);
    setMedalUrlClips([]);
    setYoutubeUrl("");
    setYoutubePreview(null);
    setJob(null);
    setErrMsg(null);
    setView("setup");
  }

  function backToSetup() {
    if (pollRef.current) clearInterval(pollRef.current);
    setJob(null);
    setErrMsg(null);
    setView("setup");
  }

  function startPolling(jobId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    setView("processing");
    setErrMsg(null);
    setJob({
      id: jobId,
      status: "queued",
      stage: "queued",
      progress: 0,
      tempo: null,
      num_cuts: null,
      num_candidates: null,
      num_clips_scanned: null,
      final_duration: null,
      error: null,
    });
    pollRef.current = setInterval(async () => {
      try {
        const state = await getJob(jobId);
        setJob(state);
        if (state.status === "done") {
          if (pollRef.current) clearInterval(pollRef.current);
          setView("done");
          setHistoryRefreshKey((n) => n + 1);
        } else if (state.status === "error") {
          if (pollRef.current) clearInterval(pollRef.current);
          setErrMsg(state.error || "Pipeline failed");
          setView("error");
        }
      } catch {
        /* keep polling on transient blips */
      }
    }, 500);
  }

  const submitLabel = !clipsReady
    ? clipTab === "upload"
      ? "Drop gameplay clips"
      : "Add a Medal clip"
    : !musicReady
      ? musicTab === "upload"
        ? "Add a music track"
        : "Paste a YouTube URL"
      : !health?.ffmpeg
        ? "ffmpeg required"
        : "Generate reel";

  return (
    <main className="min-h-screen">
      <div className="mx-auto max-w-[960px] px-6 py-8">
        <header className="flex items-center justify-between pb-5 border-b border-border mb-6">
          <Logo />
          <div className="flex items-center gap-4">
            <HealthDot health={health} />
            <button
              onClick={() => setSettingsOpen(true)}
              className="flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.12em] text-fg-dim hover:text-fg"
            >
              <Settings className="h-3 w-3" />
              Settings
            </button>
          </div>
        </header>

        <nav className="flex items-stretch border border-border bg-surface-1 mb-6">
          <TopTab
            active={pageTab === "create"}
            onClick={() => setPageTab("create")}
            label="Create"
            icon={<Sparkles className="h-3 w-3" />}
          />
          <TopTab
            active={pageTab === "history"}
            onClick={() => {
              setPageTab("history");
              setHistoryRefreshKey((n) => n + 1);
            }}
            label="History"
            icon={<Clock className="h-3 w-3" />}
          />
        </nav>

        {pageTab === "history" && (
          <HistoryPanel
            refreshKey={historyRefreshKey}
            onPickForMusicReuse={(entry) => {
              setReusedMusic(entry);
              setMusicTab("reuse");
              setPageTab("create");
            }}
          />
        )}

        {pageTab === "create" && view === "setup" && (
          <div className="space-y-6">
            <Section index="01" title="Clips" right={clipsSummary}>
              <SourceTabs
                tabs={[
                  { key: "upload", label: "Upload", icon: <Upload className="h-3 w-3" /> },
                  { key: "auto_clip", label: "Auto-clip", icon: <Sparkles className="h-3 w-3" /> },
                  { key: "medal", label: "Medal", icon: <Zap className="h-3 w-3" /> },
                ]}
                active={clipTab}
                onChange={(k) => setClipTab(k as ClipTab)}
              />
              <div className="mt-3">
                {clipTab === "upload" ? (
                  <DropZone
                    icon={<Film className="h-5 w-5" />}
                    title="Drop a folder or select video files"
                    hint="MP4 · MOV · MKV · WEBM"
                    accept="video/*"
                    multiple
                    folder
                    files={clips}
                    onFiles={(fs) => setClips((prev) => dedupe([...prev, ...fs]))}
                    minH="min-h-[200px]"
                  />
                ) : clipTab === "auto_clip" ? (
                  <div className="space-y-2">
                    <DropZone
                      icon={<Sparkles className="h-5 w-5" />}
                      title="Drop one full video; Gemini will find the entertaining moments"
                      hint="Any genre · up to 60 min · MP4 · MOV · MKV"
                      accept="video/*"
                      files={sourceVideo}
                      onFiles={(fs) => setSourceVideo([fs[0]])}
                      minH="min-h-[200px]"
                    />
                    <p className="font-mono text-[10.5px] text-fg-muted leading-relaxed">
                      Requires a Gemini API key. Gemini scores every moment across
                      5 dimensions (visual interest, audio peaks, emotional charge,
                      narrative payoff, technical skill) and the director arranges
                      the top-scored moments to your music.
                    </p>
                    <label className="flex items-start gap-2 mt-2 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={includeLongForm}
                        onChange={(e) => setIncludeLongForm(e.target.checked)}
                        className="mt-0.5 accent-accent"
                      />
                      <span className="font-mono text-[10.5px] text-fg-muted leading-relaxed">
                        <span className="text-fg">+ long-form tier</span> — render an
                        extra 3-4 minute narrative cut alongside the short-form tiers.
                        Needs 8+ qualifying moments; effects (ramps, zoom-bursts) are
                        disabled so it reads as a compilation, not a highlight reel.
                      </span>
                    </label>
                  </div>
                ) : (
                  <MedalPicker
                    librarySelected={medalLibrarySelected}
                    onLibraryChange={setMedalLibrarySelected}
                    profileSelected={medalProfileSelected}
                    onProfileSelectedChange={setMedalProfileSelected}
                    urlClips={medalUrlClips}
                    onUrlClipsChange={setMedalUrlClips}
                    onOpenSettings={() => setSettingsOpen(true)}
                    reloadKey={medalReloadKey}
                  />
                )}
              </div>
            </Section>

            <Section index="02" title="Music" right={musicSummary}>
              <SourceTabs
                tabs={[
                  { key: "upload", label: "Upload", icon: <Upload className="h-3 w-3" /> },
                  { key: "youtube", label: "YouTube", icon: <Youtube className="h-3 w-3" /> },
                  { key: "reuse", label: "Reuse", icon: <RefreshCw className="h-3 w-3" /> },
                ]}
                active={musicTab}
                onChange={(k) => setMusicTab(k as MusicTab)}
              />
              <div className="mt-3">
                {musicTab === "upload" ? (
                  <DropZone
                    icon={<Music className="h-5 w-5" />}
                    title="Drop an MP3, WAV, or FLAC"
                    hint="MP3 · WAV · FLAC · M4A"
                    accept="audio/*"
                    files={music}
                    onFiles={(fs) => setMusic([fs[0]])}
                    minH="min-h-[110px]"
                  />
                ) : musicTab === "reuse" ? (
                  <ReuseMusicPicker
                    selected={reusedMusic}
                    onSelect={setReusedMusic}
                    refreshKey={historyRefreshKey}
                  />
                ) : (
                  <YouTubeInput
                    value={youtubeUrl}
                    onChange={setYoutubeUrl}
                    onPreview={setYoutubePreview}
                  />
                )}
              </div>
            </Section>

            <Section index="03" title="Output">
              <div className="space-y-4">
                <Row label="Game">
                  <GamePicker
                    value={game}
                    onChange={setGame}
                    aiDisabled={geminiKeys.length === 0 && !health?.gemini_configured}
                  />
                </Row>
                <Row label="Intensity">
                  <IntensityPicker
                    value={intensity}
                    onChange={setIntensity}
                    showAuto={geminiKeys.length > 0 || !!health?.gemini_configured}
                  />
                </Row>
                <Row label="Aspect">
                  <AspectPicker value={aspect} onChange={setAspect} />
                </Row>
                <div>
                  <DurationSlider value={duration} onChange={setDuration} />
                </div>
              </div>
            </Section>

            <StatusRail
              clipsReady={clipsReady}
              musicReady={musicReady}
              backendReady={!!health?.ffmpeg}
              ffmpegError={health?.ffmpeg_error}
            />

            <button
              onClick={onSubmit}
              disabled={!canSubmit}
              className={
                "w-full py-3.5 border font-mono text-[12.5px] uppercase tracking-[0.16em] transition-colors " +
                (canSubmit
                  ? "bg-accent border-accent text-white hover:brightness-110"
                  : "bg-surface-1 border-border text-fg-muted cursor-not-allowed")
              }
            >
              {submitLabel} {canSubmit && "→"}
            </button>
          </div>
        )}

        {pageTab === "create" && view === "processing" && job && <ProgressPanel job={job} />}

        {pageTab === "create" && view === "done" && job && (
          <ResultPanel
            job={job}
            onReset={reset}
            onReroll={(newJobId) => startPolling(newJobId)}
          />
        )}

        {pageTab === "create" && view === "error" && (
          <ErrorPanel
            message={errMsg ?? "Unknown error"}
            stage={job?.stage ?? null}
            onBack={backToSetup}
            onStartOver={reset}
          />
        )}

        <footer className="mt-12 pt-4 border-t border-border font-mono text-[10.5px] text-fg-muted uppercase tracking-[0.14em]">
          Local-only · No uploads leave this machine
        </footer>
      </div>

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={() => {
          setMedalReloadKey((n) => n + 1);
          const ks = loadGeminiKeys();
          setGeminiKeys(ks);
          if (ks.length > 0 && game !== "valorant_ai") setGame("valorant_ai");
        }}
      />
    </main>
  );
}

function TopTab({
  active,
  onClick,
  label,
  icon,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  icon: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "flex-1 flex items-center justify-center gap-2 px-4 py-3 font-mono text-[11.5px] uppercase tracking-[0.14em] transition-colors " +
        (active
          ? "bg-surface-2 text-fg"
          : "text-fg-dim hover:text-fg hover:bg-surface-2/60")
      }
    >
      {icon}
      {label}
    </button>
  );
}

function ReuseMusicPicker({
  selected,
  onSelect,
  refreshKey,
}: {
  selected: JobHistoryEntry | null;
  onSelect: (entry: JobHistoryEntry | null) => void;
  refreshKey: number;
}) {
  const [jobs, setJobs] = useState<JobHistoryEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setErr(null);
    listJobs(50)
      .then((js) => {
        if (cancelled) return;
        setJobs(js.filter((j) => j.has_music));
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (err) {
    return (
      <div className="font-mono text-[11px] text-err">
        Couldn&apos;t load history: {err}
      </div>
    );
  }
  if (jobs === null) {
    return (
      <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-fg-dim py-3">
        Loading…
      </div>
    );
  }
  if (jobs.length === 0) {
    return (
      <div className="font-mono text-[11px] text-fg-muted py-3">
        No prior reels with music to reuse yet. Upload a track on the first run.
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <select
        value={selected?.job_id ?? ""}
        onChange={(e) => {
          const id = e.target.value;
          if (!id) {
            onSelect(null);
            return;
          }
          const hit = jobs.find((j) => j.job_id === id);
          onSelect(hit ?? null);
        }}
        className="w-full bg-surface-2 border border-border px-3 py-2.5 font-mono text-[12px] text-fg"
      >
        <option value="">Pick a prior reel…</option>
        {jobs.map((j) => (
          <option key={j.job_id} value={j.job_id}>
            {j.music_filename ?? j.job_id.slice(0, 8)} —{" "}
            {new Date(j.created_at_ms).toLocaleString(undefined, {
              month: "short",
              day: "numeric",
              hour: "numeric",
              minute: "2-digit",
            })}
          </option>
        ))}
      </select>
      {selected && (
        <div className="font-mono text-[10.5px] text-fg-muted">
          Using music from job {selected.job_id.slice(0, 8)}. The file will be
          copied into the new job directory.
        </div>
      )}
    </div>
  );
}

function Section({
  index,
  title,
  right,
  children,
}: {
  index: string;
  title: string;
  right?: string | null;
  children: React.ReactNode;
}) {
  return (
    <section className="border border-border bg-surface-1">
      <header className="flex items-baseline justify-between px-4 py-2.5 border-b border-border">
        <div className="flex items-baseline gap-3 font-mono uppercase tracking-[0.12em]">
          <span className="text-[10.5px] text-fg-muted tabular-nums">
            {index}
          </span>
          <span className="text-[11px] text-fg">{title}</span>
        </div>
        {right && (
          <span className="font-mono text-[11px] text-fg-dim truncate max-w-[60%]">
            {right}
          </span>
        )}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-fg-muted">
        {label}
      </div>
      {children}
    </div>
  );
}

function StatusRail({
  clipsReady,
  musicReady,
  backendReady,
  ffmpegError,
}: {
  clipsReady: boolean;
  musicReady: boolean;
  backendReady: boolean;
  ffmpegError?: string | null;
}) {
  const items = [
    { label: "Clips", ready: clipsReady },
    { label: "Music", ready: musicReady },
    { label: "Backend", ready: backendReady, error: ffmpegError },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border border-border bg-surface-1 px-4 py-2.5">
      {items.map((it) => (
        <div
          key={it.label}
          className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.1em]"
        >
          <Dot ok={it.ready} />
          <span className={it.ready ? "text-fg" : "text-fg-dim"}>
            {it.label}
          </span>
          <span className={it.ready ? "text-ok" : "text-warn"}>
            {it.ready ? "ready" : "pending"}
          </span>
        </div>
      ))}
      {ffmpegError && (
        <span className="font-mono text-[11px] text-err break-all">
          {ffmpegError}
        </span>
      )}
    </div>
  );
}

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      aria-hidden
      className={
        "inline-block h-1.5 w-1.5 " +
        (ok ? "bg-ok" : "bg-warn")
      }
    />
  );
}

function HealthDot({
  health,
}: {
  health: { ok: boolean; ffmpeg: boolean } | null;
}) {
  if (health === null) return null;
  const ok = health.ffmpeg;
  return (
    <div
      className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.1em]"
      title={ok ? "Backend + ffmpeg ready" : "Backend not ready"}
    >
      <Dot ok={ok} />
      <span className={ok ? "text-ok" : "text-err"}>
        {ok ? "backend ok" : "offline"}
      </span>
    </div>
  );
}

function ErrorPanel({
  message,
  stage,
  onBack,
  onStartOver,
}: {
  message: string;
  stage: string | null;
  onBack: () => void;
  onStartOver: () => void;
}) {
  const [copied, setCopied] = useState(false);
  async function copyError() {
    try {
      await navigator.clipboard.writeText(message);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore
    }
  }
  return (
    <div className="border border-err/50 bg-surface-1">
      <header className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <AlertTriangle className="h-4 w-4 text-err" />
        <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-err">
          Pipeline failed
        </span>
        {stage && (
          <span className="ml-auto font-mono text-[11px] text-fg-dim truncate">
            at {stage}
          </span>
        )}
      </header>

      <div className="p-4 border-b border-border">
        <div className="font-mono text-[12px] text-fg whitespace-pre-wrap break-all max-h-64 overflow-y-auto">
          {message}
        </div>
      </div>

      <div className="grid grid-cols-3">
        <button
          onClick={copyError}
          className="flex items-center justify-center gap-2 py-3 border-r border-border font-mono text-[11px] uppercase tracking-[0.1em] text-fg-dim hover:text-fg hover:bg-surface-2"
        >
          <Copy className="h-3 w-3" />
          {copied ? "Copied" : "Copy"}
        </button>
        <button
          onClick={onBack}
          className="py-3 border-r border-border font-mono text-[11px] uppercase tracking-[0.1em] text-fg hover:bg-surface-2"
        >
          Back to setup
        </button>
        <button
          onClick={onStartOver}
          className="py-3 font-mono text-[11px] uppercase tracking-[0.1em] text-fg-dim hover:text-fg hover:bg-surface-2"
        >
          Start over
        </button>
      </div>
    </div>
  );
}

function dedupe(files: File[]): File[] {
  const seen = new Set<string>();
  const out: File[] = [];
  for (const f of files) {
    const key = `${f.name}:${f.size}:${f.lastModified}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(f);
  }
  return out;
}
