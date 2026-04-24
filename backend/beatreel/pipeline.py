"""Top-level orchestration: clips + music → highlight reel."""
from __future__ import annotations

import json
import logging
import random
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

from .aspect import AspectPreset
from .beats import BeatGrid, detect_beats
from .cache import ClipCache
from .highlights import Highlight, score_clip
from .render import CutPlan, render_reel
from .scenes import boost_highlights_near_scenes, detect_scene_changes
from . import valorant as valorant_detector
from . import gemini_detector
from . import gemini_music_analyzer
from . import director as director_mod
from .gemini_pool import GeminiPool, GeminiPoolExhausted

logger = logging.getLogger(__name__)

Intensity = Literal["chill", "balanced", "hype", "auto"]
Game = Literal["valorant_ai", "valorant", "generic"]

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv"}

DETECTOR_VERSION = "audio+scene-v4"

MUSIC_ANALYSIS_TIMEOUT_S = 120


@dataclass
class PipelineConfig:
    clips_dir: Path
    music_path: Path
    output_path: Path
    target_duration: float = 60.0
    intensity: Intensity = "balanced"
    aspect: AspectPreset = "landscape"
    seed: Optional[int] = None
    use_scene_detection: bool = True
    game: Game = "valorant"
    # Multi-key list. The director runs on any one of them; per-clip
    # analysis parallelizes across the pool.
    gemini_api_keys: list[str] = field(default_factory=list)

    @property
    def gemini_api_key(self) -> Optional[str]:
        return self.gemini_api_keys[0] if self.gemini_api_keys else None


@dataclass
class PipelineResult:
    output_path: Path
    tempo: float
    num_clips_scanned: int
    num_candidates: int
    num_cuts: int
    final_duration: float
    seed: Optional[int] = None
    cuts: list[CutPlan] = field(default_factory=list)
    # Which detector/planner produced the final cuts. Values:
    #   "director"                  — Gemini director
    #   "director-partial-N-failed" — Gemini director ran but N clip analyses failed
    #   "ai-greedy-fallback"        — AI kills used with greedy planner (director failed)
    #   "audio-signature"           — Valorant template-match
    #   "generic-audio"             — generic RMS/onset
    detector_used: str = "generic"
    clips_analyzed: int = 0
    clips_failed: int = 0
    captions_placed: int = 0


def _list_clips(clips_dir: Path) -> list[Path]:
    if not clips_dir.exists():
        raise FileNotFoundError(f"Clips directory not found: {clips_dir}")
    return sorted(
        p for p in clips_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )


def _cut_length_for(intensity: str, tempo: float) -> tuple[float, float]:
    beat_s = 60.0 / max(tempo, 1.0)
    if intensity == "hype":
        return (max(beat_s * 2.0, 1.2), max(beat_s * 4.0, 2.5))
    if intensity == "chill":
        return (max(beat_s * 6.0, 3.5), max(beat_s * 10.0, 6.5))
    # balanced / auto-fallback
    return (max(beat_s * 4.0, 2.0), max(beat_s * 7.0, 4.0))


def _plan_cuts_greedy(
    highlights: list[Highlight],
    beats: BeatGrid,
    target_duration: float,
    intensity: str,
    seed: Optional[int] = None,
) -> list[CutPlan]:
    """Score-greedy cut planner. Used when the AI director isn't available."""
    if not highlights:
        return []

    min_cut, max_cut = _cut_length_for(intensity, beats.tempo)
    rng = random.Random(seed) if seed is not None else None
    ordered = sorted(highlights, key=lambda h: h.score, reverse=True)
    if rng is not None and len(ordered) > 1:
        top_score = ordered[0].score
        bucket_size = max(top_score * 0.08, 1e-6)
        buckets: list[list[Highlight]] = []
        current: list[Highlight] = []
        last_score: Optional[float] = None
        for h in ordered:
            if last_score is None or abs(last_score - h.score) <= bucket_size:
                current.append(h)
            else:
                buckets.append(current)
                current = [h]
            last_score = h.score
        if current:
            buckets.append(current)
        for b in buckets:
            rng.shuffle(b)
        ordered = [h for b in buckets for h in b]

    plans: list[CutPlan] = []
    total = 0.0
    used_per_clip: dict[Path, list[tuple[float, float]]] = {}
    for h in ordered:
        if total >= target_duration:
            break
        half = max_cut / 2.0
        start = max(0.0, h.peak_time - half)
        end = min(h.clip_duration, h.peak_time + half)
        duration = end - start
        if duration < min_cut:
            continue
        remaining = target_duration - total
        if duration > remaining:
            end = start + remaining
            duration = remaining
        if duration < min_cut and total > 0:
            continue
        overlaps = used_per_clip.setdefault(h.clip_path, [])
        if any(not (end <= s or start >= e) for s, e in overlaps):
            continue
        overlaps.append((start, end))
        snapped = beats.nearest_beat(start)
        clip_end = h.clip_duration
        start = max(0.0, min(snapped, clip_end - duration))
        plans.append(CutPlan(clip_path=h.clip_path, start=start, duration=duration))
        total += duration

    if len(beats.downbeat_times) > 0:
        def beat_affinity(plan: CutPlan) -> float:
            return min(abs(b - plan.duration) for b in beats.downbeat_times[:8])
        plans.sort(key=beat_affinity)
    return plans


def _score_with_cache(
    clips: list[Path],
    cache: ClipCache,
    use_scene_detection: bool,
    on_progress: Callable[[str, float], None],
) -> list[Highlight]:
    all_highlights: list[Highlight] = []
    total = max(len(clips), 1)
    for i, clip_path in enumerate(clips):
        frac = 0.10 + 0.60 * (i / total)
        on_progress(f"scoring clips ({i + 1}/{len(clips)})", frac)
        cached = cache.get(clip_path, DETECTOR_VERSION)
        if cached is not None:
            all_highlights.extend(cached)
            continue
        hs = score_clip(clip_path)
        if use_scene_detection and hs:
            scenes = detect_scene_changes(clip_path)
            hs = boost_highlights_near_scenes(hs, scenes)
        cache.set(clip_path, DETECTOR_VERSION, hs)
        all_highlights.extend(hs)
    return all_highlights


def _director_to_cuts(
    directed: director_mod.DirectedReel,
    clips: list[Path],
    clip_durations: dict[Path, float],
    clip_analyses: dict[Path, "gemini_detector.ClipAnalysis"],
) -> list[CutPlan]:
    """Convert a validated DirectedReel into concrete CutPlans.

    Structural validation (overlap, reuse, durations) already ran inside the
    Pydantic model. Here we do the cross-referential checks the schema can't:
    clip_index bounds against the live clips list, clamp against true clip
    durations from ffprobe, and verify captions refer to a reaction the
    per-clip analysis actually flagged (no fabricated voice comms)."""
    out: list[CutPlan] = []
    dropped_captions = 0

    for dc in directed.cuts:
        if dc.clip_index < 0 or dc.clip_index >= len(clips):
            logger.warning("director: bad clip_index %d (have %d clips) — skipping cut", dc.clip_index, len(clips))
            continue
        clip_path = clips[dc.clip_index]
        clip_dur = clip_durations.get(clip_path, 0.0)

        start = max(0.0, float(dc.clip_start_seconds))
        end = max(start + 0.8, float(dc.clip_end_seconds))
        if clip_dur > 0:
            end = min(end, clip_dur)
        duration = end - start
        if duration < 0.8:
            continue

        caption_text = (dc.caption or "").strip()[:60] or None
        caption_start = 0.0
        caption_dur = 2.0

        if caption_text:
            analysis = clip_analyses.get(clip_path)
            if analysis is None:
                logger.warning("director: caption on clip with no analysis — dropping: %r", caption_text)
                caption_text = None
                dropped_captions += 1
            else:
                # Caption must reference a reaction the clip analysis flagged
                # somewhere near this cut window. Otherwise the director
                # fabricated a voice-callout from silence.
                near = [
                    r for r in analysis.reactions
                    if (start - 1.0) <= r.timestamp_seconds <= (end + 1.0)
                ]
                if not near:
                    logger.warning(
                        "director: caption %r has no reaction source in clip %s — dropping",
                        caption_text, clip_path.name,
                    )
                    caption_text = None
                    dropped_captions += 1

        if caption_text:
            caption_start = float(dc.caption_start_relative if dc.caption_start_relative is not None else 0.15)
            caption_dur = float(dc.caption_duration if dc.caption_duration is not None else 1.8)
            if caption_start < 0:
                caption_start = 0.0
            max_end = duration - 0.1
            if caption_start + caption_dur > max_end:
                caption_dur = max(0.8, max_end - caption_start)
            if caption_dur < 0.6:
                caption_text = None
                caption_dur = 2.0

        out.append(CutPlan(
            clip_path=clip_path,
            start=start,
            duration=duration,
            caption=caption_text,
            caption_start_in_cut=caption_start,
            caption_duration=caption_dur,
        ))

    if dropped_captions:
        logger.info("director: dropped %d fabricated/unanchored captions", dropped_captions)
    return out


def _effective_intensity(requested: str, music_rec: Optional[str]) -> str:
    if requested == "auto":
        return music_rec or "balanced"
    return requested


def _write_debug_json(
    config: PipelineConfig,
    detector_used: str,
    analyses: Optional[dict],
    errors: Optional[list],
    music_analysis,
    directed,
    cuts: list[CutPlan],
) -> None:
    try:
        out = {
            "detector_used": detector_used,
            "game": config.game,
            "intensity": config.intensity,
            "target_duration": config.target_duration,
            "clips_analyzed": len(analyses) if analyses else 0,
            "clips_failed": len(errors) if errors else 0,
            "errors": [
                {"clip": str(p), "error": str(e)[:300]}
                for p, e in (errors or [])
            ],
            "music_analysis": music_analysis.model_dump() if music_analysis else None,
            "director_output": directed.model_dump() if directed else None,
            "cuts": [
                {
                    "clip": str(c.clip_path),
                    "start": c.start,
                    "duration": c.duration,
                    "caption": c.caption,
                    "caption_start_in_cut": c.caption_start_in_cut,
                    "caption_duration": c.caption_duration,
                }
                for c in cuts
            ],
        }
        debug_path = config.output_path.parent / "debug.json"
        debug_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("couldn't write debug.json: %s", exc)


def run(
    config: PipelineConfig,
    on_progress: Callable[[str, float], None] | None = None,
) -> PipelineResult:
    """Run the full pipeline. on_progress(stage, fraction_0_to_1) is optional."""
    def report(stage: str, frac: float) -> None:
        if on_progress:
            on_progress(stage, max(0.0, min(1.0, frac)))

    report("scanning clips", 0.01)
    clips = _list_clips(config.clips_dir)
    if not clips:
        raise ValueError(f"No video files found in {config.clips_dir}")

    report("detecting beats", 0.05)
    beats = detect_beats(config.music_path)

    cache = ClipCache(config.clips_dir)
    highlights: list[Highlight] = []
    directed_cuts: list[CutPlan] = []
    detector_used = "generic"
    captions_placed = 0
    render_opts: dict = {}
    clips_analyzed = 0
    clips_failed = 0
    analyses: dict = {}
    errors: list = []
    music_analysis = None
    directed = None

    # ── Valorant AI director path ──────────────────────────────────────
    if config.game == "valorant_ai":
        if not config.gemini_api_keys:
            raise RuntimeError(
                "Valorant AI detector requires at least one Gemini API key. "
                "Add one in Settings or set GEMINI_API_KEYS in backend/.env."
            )

        pool = GeminiPool.from_keys(config.gemini_api_keys)

        # Music analysis runs in its own single-worker executor so we can
        # cancel cleanly on timeout rather than leaking a hung thread.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="music-analyze") as music_exec:
            music_future = music_exec.submit(
                gemini_music_analyzer.analyze_music,
                config.music_path,
                pool.next_key(),
            )

            def ai_progress(done: int, total: int, path) -> None:
                frac = 0.08 + 0.50 * (done / max(total, 1))
                name = path.name if path else ""
                report(f"gemini analyzing clips {done}/{total} · {name}", frac)

            analyses, durations, errors = gemini_detector.analyze_clips_parallel(
                clips, pool, on_progress=ai_progress,
            )
            clips_analyzed = len(analyses)
            clips_failed = len(errors)

            try:
                music_analysis = music_future.result(timeout=MUSIC_ANALYSIS_TIMEOUT_S)
            except FutureTimeoutError:
                logger.warning("music analysis timed out after %ds", MUSIC_ANALYSIS_TIMEOUT_S)
                music_future.cancel()
                music_analysis = None
            except Exception as exc:
                logger.warning("music analysis failed: %s", exc)
                music_analysis = None

        # Convert successful analyses into Highlights for a possible greedy fallback
        for p, a in analyses.items():
            dur = durations.get(p, 0.0)
            for k in a.kills:
                t = float(k.timestamp_seconds)
                if 0 <= t <= dur:
                    highlights.append(Highlight(
                        clip_path=p, peak_time=t, score=float(k.confidence), clip_duration=dur,
                    ))

        # If EVERY clip failed analysis, fail loud — no silent drift to template-match.
        if not analyses:
            err_summary = "; ".join(f"{p.name}: {str(e)[:120]}" for p, e in errors[:3])
            _write_debug_json(config, "failed", analyses, errors, music_analysis, None, [])
            raise RuntimeError(
                "Valorant AI detection failed on every clip. "
                f"Check your Gemini API keys. Errors: {err_summary}"
            )

        # Director attempt — needs music analysis to reason about drops.
        if music_analysis is not None:
            report("ai director planning reel", 0.62)
            try:
                clip_summaries = director_mod.summarize_for_director(clips, analyses, durations)
                beats_list = (
                    beats.beat_times.tolist()
                    if hasattr(beats.beat_times, "tolist")
                    else list(beats.beat_times)
                )
                directed = director_mod.direct_reel(
                    music_analysis=music_analysis,
                    clip_summaries=clip_summaries,
                    beats_seconds=beats_list,
                    tempo_bpm=float(beats.tempo),
                    target_duration=config.target_duration,
                    api_key=pool.next_key(),
                )
                directed_cuts = _director_to_cuts(directed, clips, durations, analyses)
                captions_placed = sum(1 for c in directed_cuts if c.caption)
                if directed_cuts:
                    detector_used = (
                        "director"
                        if clips_failed == 0
                        else f"director-partial-{clips_failed}-failed"
                    )
                    intro_hold = float(getattr(music_analysis, "best_start_seconds", 0.0) or 0.0)
                    if intro_hold < 0.3:
                        intro_hold = 0.0
                    render_opts.update(dict(
                        intro_hold_seconds=intro_hold,
                        title_caption=getattr(directed, "title_caption", None),
                        outro_hold_seconds=float(getattr(directed, "outro_hold_seconds", 0.8)),
                    ))
                    report(
                        f"director placed {len(directed_cuts)} cuts · "
                        f"{captions_placed} captions · mood: {directed.chosen_intensity}",
                        0.72,
                    )
            except Exception as exc:
                logger.warning("director failed: %s — falling back to greedy plan over AI kills", exc)
                report(f"director failed ({str(exc)[:100]}) — fallback", 0.60)
                directed = None
                directed_cuts = []

        # Narrowed fallback: if director failed, still use AI-detected kills with
        # the greedy planner. Don't drift to the template-match detector.
        if not directed_cuts:
            if not highlights:
                err_summary = "; ".join(f"{p.name}: {str(e)[:120]}" for p, e in errors[:3]) or "director produced no cuts"
                _write_debug_json(config, "failed", analyses, errors, music_analysis, directed, [])
                raise RuntimeError(
                    "Valorant AI produced no usable cuts. "
                    f"{err_summary}"
                )
            report("using AI kills with greedy planner", 0.74)
            plan_intensity = _effective_intensity(
                config.intensity,
                music_analysis.recommended_intensity if music_analysis else None,
            )
            plan_intensity = plan_intensity if plan_intensity in ("chill", "balanced", "hype") else "balanced"
            directed_cuts = _plan_cuts_greedy(
                highlights, beats, config.target_duration, plan_intensity, seed=config.seed,
            )
            detector_used = "ai-greedy-fallback"

        if not directed_cuts:
            _write_debug_json(config, "failed", analyses, errors, music_analysis, directed, [])
            raise RuntimeError("Valorant AI produced zero cuts even after fallback")

        cuts = directed_cuts

    # ── Non-AI modes: template-match (valorant) or generic ─────────────
    else:
        if config.game == "valorant":
            def valorant_progress(done: int, total: int, _path) -> None:
                frac = 0.10 + 0.60 * (done / max(total, 1))
                report(f"matching valorant kill-sound ({done}/{total})", frac)

            vhl, status = valorant_detector.score_clips_valorant(
                config.clips_dir, clips, on_progress=valorant_progress,
            )
            if status is not None and len(vhl) > 0:
                highlights = vhl
                detector_used = f"audio-signature-{status}"
                report(f"kill-sound matched: {len(vhl)} hits", 0.70)

        if not highlights:
            highlights = _score_with_cache(clips, cache, config.use_scene_detection, report)
            detector_used = "generic-audio" if highlights else detector_used

        report("planning cuts", 0.75)
        plan_intensity = config.intensity if config.intensity in ("chill", "balanced", "hype") else "balanced"
        cuts = _plan_cuts_greedy(
            highlights, beats, config.target_duration, plan_intensity, seed=config.seed,
        )
        if not cuts:
            raise RuntimeError(
                "No highlights detected. Try a longer target duration, different clips, "
                "or switch detector mode."
            )

    # ── Render ─────────────────────────────────────────────────────────
    def render_log(msg: str) -> None:
        report(f"rendering: {msg}", 0.85)

    report("rendering", 0.80)
    render_reel(
        cuts=cuts,
        music_path=config.music_path,
        output_path=config.output_path,
        aspect=config.aspect,
        on_log=render_log,
        fade_in_seconds=0.3,
        fade_out_seconds=0.8,
        **render_opts,
    )
    report("done", 1.0)

    _write_debug_json(config, detector_used, analyses, errors, music_analysis, directed, cuts)

    return PipelineResult(
        output_path=config.output_path,
        tempo=beats.tempo,
        num_clips_scanned=len(clips),
        num_candidates=len(highlights) if highlights else len(cuts),
        num_cuts=len(cuts),
        final_duration=sum(c.duration for c in cuts)
            + render_opts.get("intro_hold_seconds", 0.0)
            + render_opts.get("outro_hold_seconds", 0.0),
        seed=config.seed,
        cuts=cuts,
        detector_used=detector_used,
        clips_analyzed=clips_analyzed,
        clips_failed=clips_failed,
        captions_placed=captions_placed,
    )
