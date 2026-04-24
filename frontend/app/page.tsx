"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Film,
  Music,
  Settings,
  Sparkles,
  Upload,
  Youtube,
  Zap,
} from "lucide-react";

import { Logo } from "@/components/Logo";
import { DropZone } from "@/components/DropZone";
import { IntensityPicker, type Intensity } from "@/components/IntensityPicker";
import { DurationSlider } from "@/components/DurationSlider";
import { AspectPicker, type Aspect } from "@/components/AspectPicker";
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
import { loadMedalSettings } from "@/lib/settings";

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
  const [medalUrlClips, setMedalUrlClips] = useState<MedalClip[]>([]);
  const [medalReloadKey, setMedalReloadKey] = useState(0);

  // YouTube state
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [youtubePreview, setYoutubePreview] = useState<YouTubePreview | null>(null);

  // Params
  const [duration, setDuration] = useState(60);
  const [intensity, setIntensity] = useState<Intensity>("balanced");
  const [aspect, setAspect] = useState<Aspect>("landscape");

  // App state
  const [view, setView] = useState<View>("setup");
  const [job, setJob] = useState<JobState | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [health, setHealth] = useState<{ ok: boolean; ffmpeg: boolean; ffmpeg_error: string | null } | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    checkHealth()
      .then(setHealth)
      .catch(() => setHealth({ ok: false, ffmpeg: false, ffmpeg_error: "backend unreachable" }));
  }, []);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const medalTotal = medalLibrarySelected.size + medalUrlClips.length;
  const clipsReady = clipTab === "upload" ? clips.length > 0 : medalTotal > 0;
  const musicReady =
    musicTab === "upload" ? music.length > 0 : youtubePreview !== null;
  const canSubmit = clipsReady && musicReady && (health?.ffmpeg ?? false);

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
    setMedalUrlClips([]);
    setYoutubeUrl("");
    setYoutubePreview(null);
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
      ? "Drop gameplay clips to begin"
      : "Add a Medal clip to begin"
    : !musicReady
      ? musicTab === "upload"
        ? "Add a music track"
        : "Paste a YouTube URL"
      : !health?.ffmpeg
        ? "ffmpeg required"
        : "Generate highlight reel";

  return (
    <main className="min-h-screen px-6 py-10">
      <div className="mx-auto max-w-3xl">
        <header className="flex items-center justify-between mb-12">
          <Logo />
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSettingsOpen(true)}
              className="flex items-center gap-1.5 rounded-lg border border-border bg-black/20 px-3 py-2 text-[12px] text-white/70 hover:text-white hover:border-accent/40 hover:bg-accent/5 transition-all"
            >
              <Settings className="h-3.5 w-3.5" />
              Settings
            </button>
            <span className="text-[12px] text-white/30">v0.1.0</span>
          </div>
        </header>

        <div className="mb-12 text-center">
          <div className="inline-flex items-center gap-1.5 rounded-full border border-accent/30 bg-accent/5 px-3 py-1 mb-5">
            <Sparkles className="h-3 w-3 text-accent-glow" />
            <span className="text-[11px] uppercase tracking-[0.16em] text-accent-glow">
              no editor required
            </span>
          </div>
          <h1 className="text-4xl sm:text-5xl font-semibold tracking-tight leading-[1.1] mb-4">
            Raw clips in.
            <br />
            <span className="text-gradient">Highlight reel out.</span>
          </h1>
          <p className="text-white/55 max-w-md mx-auto text-[15px]">
            Drop a folder of gameplay clips or pick from Medal. Bring your own
            music or paste a YouTube URL. We do the rest.
          </p>
        </div>

        {!health?.ffmpeg && health !== null && (
          <div className="mb-6 rounded-xl border border-red-500/30 bg-red-500/5 p-4 flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-red-400 shrink-0 mt-0.5" />
            <div className="text-[13px]">
              <div className="font-medium text-red-300 mb-0.5">
                ffmpeg not available
              </div>
              <div className="text-white/60 whitespace-pre-line">
                {health?.ffmpeg_error ?? "Backend is not reachable. Make sure the Python server is running on port 8000."}
              </div>
            </div>
          </div>
        )}

        {view === "setup" && (
          <div className="space-y-5">
            {/* Clips source */}
            <div className="glass rounded-2xl p-5 space-y-4">
              <div className="flex items-center gap-2">
                <Film className="h-4 w-4 text-accent-glow" />
                <h3 className="text-[14px] font-medium">Gameplay clips</h3>
              </div>
              <SourceTabs
                tabs={[
                  { key: "upload", label: "Upload", icon: <Upload className="h-3.5 w-3.5" /> },
                  { key: "medal", label: "Medal", icon: <Zap className="h-3.5 w-3.5" /> },
                ]}
                active={clipTab}
                onChange={(k) => setClipTab(k as ClipTab)}
              />
              {clipTab === "upload" ? (
                <DropZone
                  icon={<Film className="h-6 w-6" />}
                  title="Drop a folder or select video files"
                  hint="MP4, MOV, MKV, WEBM"
                  accept="video/*"
                  multiple
                  folder
                  files={clips}
                  onFiles={(fs) => setClips((prev) => dedupe([...prev, ...fs]))}
                />
              ) : (
                <MedalPicker
                  librarySelected={medalLibrarySelected}
                  onLibraryChange={setMedalLibrarySelected}
                  urlClips={medalUrlClips}
                  onUrlClipsChange={setMedalUrlClips}
                  onOpenSettings={() => setSettingsOpen(true)}
                  reloadKey={medalReloadKey}
                />
              )}
            </div>

            {/* Music source */}
            <div className="glass rounded-2xl p-5 space-y-4">
              <div className="flex items-center gap-2">
                <Music className="h-4 w-4 text-accent-glow" />
                <h3 className="text-[14px] font-medium">Music</h3>
              </div>
              <SourceTabs
                tabs={[
                  { key: "upload", label: "Upload", icon: <Upload className="h-3.5 w-3.5" /> },
                  { key: "youtube", label: "YouTube", icon: <Youtube className="h-3.5 w-3.5" /> },
                ]}
                active={musicTab}
                onChange={(k) => setMusicTab(k as MusicTab)}
              />
              {musicTab === "upload" ? (
                <DropZone
                  icon={<Music className="h-6 w-6" />}
                  title="Drop an MP3, WAV, or FLAC"
                  hint="Your own music file"
                  accept="audio/*"
                  files={music}
                  onFiles={(fs) => setMusic([fs[0]])}
                />
              ) : (
                <YouTubeInput
                  value={youtubeUrl}
                  onChange={setYoutubeUrl}
                  onPreview={setYoutubePreview}
                />
              )}
            </div>

            {/* Params */}
            <div className="glass rounded-2xl p-6 space-y-6">
              <IntensityPicker value={intensity} onChange={setIntensity} />
              <AspectPicker value={aspect} onChange={setAspect} />
              <DurationSlider value={duration} onChange={setDuration} />
            </div>

            <button
              onClick={onSubmit}
              disabled={!canSubmit}
              className={`w-full py-4 rounded-2xl font-medium text-[15px] transition-all ${
                canSubmit
                  ? "bg-accent-gradient text-white shadow-glow hover:shadow-glow-lg hover:scale-[1.005]"
                  : "bg-white/5 text-white/30 cursor-not-allowed"
              }`}
            >
              {submitLabel}
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
          <div className="glass rounded-2xl p-8 text-center">
            <AlertTriangle className="h-10 w-10 text-red-400 mx-auto mb-3" />
            <div className="font-medium text-lg mb-1">Something broke</div>
            <div className="text-white/60 text-[13px] mb-6 max-w-sm mx-auto">
              {errMsg ?? "Unknown error"}
            </div>
            <button
              onClick={reset}
              className="px-5 py-2.5 rounded-lg bg-white/5 hover:bg-white/10 border border-border text-[14px] transition-all"
            >
              Try again
            </button>
          </div>
        )}

        <footer className="mt-16 text-center text-[11px] text-white/30">
          Local-first. Your clips and keys never leave your machine.
        </footer>
      </div>

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={() => setMedalReloadKey((n) => n + 1)}
      />
    </main>
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
