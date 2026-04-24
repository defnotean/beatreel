"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Copy,
  Film,
  Music,
  Settings,
  Upload,
  Youtube,
  Zap,
} from "lucide-react";

import { Logo } from "@/components/Logo";
import { DropZone } from "@/components/DropZone";
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
  type JobState,
  type MedalClip,
  type YouTubePreview,
} from "@/lib/api";
import { loadGeminiKeys, loadMedalSettings } from "@/lib/settings";

type View = "setup" | "processing" | "done" | "error";
type ClipTab = "upload" | "medal";
type MusicTab = "upload" | "youtube";

export default function Home() {
  // Source selection
  const [clipTab, setClipTab] = useState<ClipTab>("upload");
  const [musicTab, setMusicTab] = useState<MusicTab>("upload");

  // Upload state
  const [clips, setClips] = useState<File[]>([]);
  const [music, setMusic] = useState<File[]>([]);

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
  const clipsReady = clipTab === "upload" ? clips.length > 0 : medalTotal > 0;
  const musicReady =
    musicTab === "upload" ? music.length > 0 : youtubePreview !== null;
  const canSubmit = clipsReady && musicReady && (health?.ffmpeg ?? false);

  const clipsSummary = useMemo(() => {
    if (clipTab === "upload") {
      if (clips.length === 0) return null;
      const mb = clips.reduce((a, f) => a + f.size, 0) / 1024 / 1024;
      return `${clips.length} file${clips.length === 1 ? "" : "s"} · ${mb.toFixed(1)} MB`;
    }
    if (medalTotal === 0) return null;
    return `${medalTotal} Medal clip${medalTotal === 1 ? "" : "s"}`;
  }, [clipTab, clips, medalTotal]);

  const musicSummary = useMemo(() => {
    if (musicTab === "upload") {
      if (music.length === 0) return null;
      return music[0].name;
    }
    if (!youtubePreview) return null;
    return youtubePreview.title;
  }, [musicTab, music, youtubePreview]);

  async function onSubmit() {
    if (!canSubmit) return;
    setView("processing");
    setErrMsg(null);

    try {
      const clipsParam =
        clipTab === "upload"
          ? ({ type: "upload" as const, files: clips })
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
          : ({ type: "youtube" as const, url: youtubeUrl });

      const { job_id } = await createJob({
        clips: clipsParam,
        music: musicParam,
        duration,
        intensity,
        aspect,
        game,
        geminiKeys: geminiKeys.length > 0 ? geminiKeys : undefined,
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

        {view === "setup" && (
          <div className="space-y-6">
            <Section index="01" title="Clips" right={clipsSummary}>
              <SourceTabs
                tabs={[
                  { key: "upload", label: "Upload", icon: <Upload className="h-3 w-3" /> },
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

        {view === "processing" && job && <ProgressPanel job={job} />}

        {view === "done" && job && (
          <ResultPanel
            job={job}
            onReset={reset}
            onReroll={(newJobId) => startPolling(newJobId)}
          />
        )}

        {view === "error" && (
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
