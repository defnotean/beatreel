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
from . import auto_clipper as auto_clipper_mod
from .gemini_pool import GeminiPool, GeminiPoolExhausted

logger = logging.getLogger(__name__)

Intensity = Literal["chill", "balanced", "hype", "auto"]
Game = Literal["valorant_ai", "valorant", "generic"]
SourceMode = Literal["clips", "auto_clip"]

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv"}

DETECTOR_VERSION = "audio+scene-v4"

MUSIC_ANALYSIS_TIMEOUT_S = 120


@dataclass
class PipelineConfig:
    clips_dir: Path
    music_path: Optional[Path]  # None in auto_clip mode → use source video's original audio
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
    # Source mode: "clips" (user supplied pre-segmented clips, default) or
    # "auto_clip" (Gemini scans a full source_video and auto-selects moments).
    source_mode: SourceMode = "clips"
    # When source_mode == "auto_clip", the long VOD to analyze.
    source_video: Optional[Path] = None
    # Experimental: boost game audio by +8dB during voice_comm reaction windows
    # (otherwise ducked to -18dB uniformly). Flag-gated because ±500ms Gemini
    # timing tolerance can land a boost on a gunshot instead of the callout.
    experimental_audio_boost: bool = False
    # Opt-in for the long-form tier in auto-clip mode. When True (and source
    # has ≥8 qualifying moments), produces a 4th longer-cadence reel alongside
    # headline/bsides/vibes.
    include_long_form: bool = False

    @property
    def gemini_api_key(self) -> Optional[str]:
        return self.gemini_api_keys[0] if self.gemini_api_keys else None


@dataclass
class TieredOutput:
    """One rendered output for an auto-clip tier. A 60-min source can produce
    up to 3-4 tiered reels (headline/bsides/vibes/long_form) from the same
    moments.json; each has its own composite-score range and cut cadence."""
    tier: str  # Literal["headline","bsides","vibes","long_form"]
    path: Path
    thumbnail_path: Path
    composite_range: tuple[float, float]
    num_cuts: int
    final_duration: float
    num_captions: int
    num_moments_in_range: int


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
    # Multi-tier outputs (populated only in auto_clip mode). output_path is
    # kept as the first-rendered tier's path for backwards compatibility with
    # callers that expect a single reel. Tests can assert either `output_path`
    # or `outputs[0].path` — they're the same.
    outputs: list[TieredOutput] = field(default_factory=list)
    # Which detector/planner produced the final cuts. Values:
    #   "director"                    — Gemini director over user-supplied clips
    #   "director-partial-N-failed"   — director ran but N clip analyses failed
    #   "ai-greedy-fallback"          — AI kills used with greedy planner (director failed)
    #   "audio-signature"             — Valorant template-match
    #   "generic-audio"               — generic RMS/onset
    #   "auto-clipper+director"       — auto-clipper found moments, director arranged them
    detector_used: str = "generic"
    clips_analyzed: int = 0
    clips_failed: int = 0
    captions_placed: int = 0
    source_mode: str = "clips"
    moments_found: int = 0
    moments_selected: int = 0


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
    *,
    clip_source_offsets: Optional[list[float]] = None,
    clip_window_durations: Optional[list[float]] = None,
    clip_meme_tags: Optional[list[Optional[str]]] = None,
) -> list[CutPlan]:
    """Convert a validated DirectedReel into concrete CutPlans.

    clip_source_offsets is used in auto_clip mode — each clip_index refers to a
    moment-window within a single source video, and the offset shifts the
    director's moment-relative start/end into source-absolute coordinates for
    ffmpeg's -ss seek. clip_window_durations caps each window to its moment's
    length (rather than the whole source video's length).

    Structural validation (overlap, reuse, durations) already ran inside the
    Pydantic model. Here we do the cross-referential checks the schema can't:
    clip_index bounds against the live clips list, clamp against true clip
    durations, and verify captions refer to a reaction the analysis flagged."""
    out: list[CutPlan] = []
    dropped_captions = 0

    for dc in directed.cuts:
        if dc.clip_index < 0 or dc.clip_index >= len(clips):
            logger.warning("director: bad clip_index %d (have %d clips) — skipping cut", dc.clip_index, len(clips))
            continue
        clip_path = clips[dc.clip_index]
        # Cap to moment window when in auto_clip mode; else full-clip duration.
        if clip_window_durations is not None:
            clip_dur = float(clip_window_durations[dc.clip_index])
        else:
            clip_dur = clip_durations.get(clip_path, 0.0)

        start = max(0.0, float(dc.clip_start_seconds))
        end = max(start + 0.8, float(dc.clip_end_seconds))
        if clip_dur > 0:
            end = min(end, clip_dur)
        duration = end - start
        if duration < 0.8:
            continue

        # Translate to source-absolute coordinates for auto_clip mode
        if clip_source_offsets is not None:
            source_offset = float(clip_source_offsets[dc.clip_index])
            start_for_cutplan = source_offset + start
        else:
            start_for_cutplan = start

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

        meme_tag_for_cut: Optional[str] = None
        if clip_meme_tags is not None and 0 <= dc.clip_index < len(clip_meme_tags):
            meme_tag_for_cut = clip_meme_tags[dc.clip_index]

        out.append(CutPlan(
            clip_path=clip_path,
            start=start_for_cutplan,
            duration=duration,
            caption=caption_text,
            caption_start_in_cut=caption_start,
            caption_duration=caption_dur,
            emphasis=str(getattr(dc, "emphasis", "normal") or "normal"),
            meme_tag=meme_tag_for_cut,
        ))

    if dropped_captions:
        logger.info("director: dropped %d fabricated/unanchored captions", dropped_captions)
    return out


def _effective_intensity(requested: str, music_rec: Optional[str]) -> str:
    if requested == "auto":
        return music_rec or "balanced"
    return requested


def _compute_voice_boost_windows(
    cuts: list[CutPlan],
    intro_hold_seconds: float = 0.0,
    max_boost_window_s: float = 1.5,
) -> list[tuple[float, float]]:
    """For cuts with a voice_comm-style caption, compute the reel-timeline
    window during which game audio should be boosted above the duck level.

    Reel timeline: cut i starts at `intro_hold_seconds + sum(durations[0..i-1])`.
    Boost window inside cut i: [cut_start + caption_start, cut_start + caption_start + caption_duration],
    clamped to `max_boost_window_s` total length so a runaway caption can't
    keep the boost on for 3+ seconds and surface gunfire.
    """
    windows: list[tuple[float, float]] = []
    cursor = float(intro_hold_seconds)
    for c in cuts:
        if c.caption and c.caption_duration > 0:
            win_start = cursor + max(0.0, c.caption_start_in_cut)
            win_end = win_start + min(c.caption_duration, max_boost_window_s)
            # Clamp to not extend past the cut's end in the reel timeline
            max_end = cursor + c.duration - 0.05
            if win_end > max_end:
                win_end = max_end
            if win_end - win_start >= 0.3:
                windows.append((win_start, win_end))
        cursor += c.duration
    return windows


def _compute_effects_applied(
    cuts: list[CutPlan],
    render_opts: dict,
    voice_windows: Optional[list[tuple[float, float]]],
    target_duration: float,
) -> dict:
    """Structured summary of every effect the renderer will apply. Dumped into
    debug.json so post-hoc debugging can answer 'why does this ace feel flat'
    without re-running the pipeline."""
    ramps = [
        {"cut_idx": i, "emphasis": c.emphasis, "duration_s": c.duration}
        for i, c in enumerate(cuts)
        if c.emphasis in ("drop_hit", "hold") and c.duration >= 1.4
    ]
    impact_bursts = [
        {"cut_idx": i} for i, c in enumerate(cuts)
        if c.emphasis == "drop_hit" and c.duration >= 1.0
    ]

    # Act-wise cut duration distribution (verifies pacing curve directive)
    total = sum(c.duration for c in cuts) or 1.0
    cursor = 0.0
    act_durations = {"act1": [], "act2": [], "act3": []}
    for c in cuts:
        midpoint = cursor + c.duration / 2.0
        frac = midpoint / total
        if frac < 0.15:
            act_durations["act1"].append(c.duration)
        elif frac < 0.70:
            act_durations["act2"].append(c.duration)
        else:
            act_durations["act3"].append(c.duration)
        cursor += c.duration
    act_means = {
        k: round(sum(v) / len(v), 2) if v else None
        for k, v in act_durations.items()
    }

    return {
        "color_grade": render_opts.get("color_grade"),
        "audio_crossfades_count": max(0, len(cuts) - 1),
        "audio_edge_fade_s": 0.067,
        "ramps": ramps,
        "impact_bursts": impact_bursts,
        "freeze_outro_s": render_opts.get("outro_hold_seconds", 0.0),
        "intro_hold_s": render_opts.get("intro_hold_seconds", 0.0),
        "voice_boosts": [
            {"start_s": round(s, 3), "end_s": round(e, 3)}
            for (s, e) in (voice_windows or [])
        ],
        "act_mean_durations": act_means,
        "target_duration": target_duration,
    }


def _write_debug_json(
    config: PipelineConfig,
    detector_used: str,
    analyses: Optional[dict],
    errors: Optional[list],
    music_analysis,
    directed,
    cuts: list[CutPlan],
    render_opts: Optional[dict] = None,
    voice_windows: Optional[list[tuple[float, float]]] = None,
) -> None:
    try:
        render_opts = render_opts or {}
        out = {
            "detector_used": detector_used,
            "game": config.game,
            "intensity": config.intensity,
            "target_duration": config.target_duration,
            "source_mode": config.source_mode,
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
                    "emphasis": c.emphasis,
                }
                for c in cuts
            ],
            "effects_applied": _compute_effects_applied(
                cuts, render_opts, voice_windows, config.target_duration,
            ),
        }
        debug_path = config.output_path.parent / "debug.json"
        debug_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("couldn't write debug.json: %s", exc)


def _plan_tier_cuts(
    *,
    tier_name: str,
    tier_params: dict,
    tier_moments: list,
    source_video: Path,
    source_duration: float,
    beats: BeatGrid,
    music_analysis,
    pool: GeminiPool,
    config: "PipelineConfig",
) -> tuple[list[CutPlan], Optional[object], dict, str]:
    """Build the cut list for one tier. Returns (cuts, directed_reel_or_None,
    render_opts, detector_used). Uses the director when music_analysis is
    available; falls through to greedy otherwise."""
    tier_target = float(tier_params["target_duration"])
    tier_intensity_override = tier_params.get("intensity_override")

    # Build a synthetic AutoClipperResult with only this tier's moments so
    # moments_to_clip_summaries produces the right shape.
    tier_result = auto_clipper_mod.AutoClipperResult(
        source_video=str(source_video),
        duration_seconds=source_duration,
        video_mood=getattr(music_analysis, "vibe", None) or "varied",
        moments=tier_moments,
    )
    clip_summaries = auto_clipper_mod.moments_to_clip_summaries(tier_result, source_video)
    clips: list[Path] = [source_video] * len(tier_moments)
    clip_window_durations = [float(m.end_seconds - m.start_seconds) for m in tier_moments]
    clip_source_offsets = [float(m.start_seconds) for m in tier_moments]
    clip_meme_tags: list[Optional[str]] = [getattr(m, "meme_tag", None) for m in tier_moments]

    merged_kills = []
    merged_reactions = []
    for m in tier_moments:
        merged_kills.append(gemini_detector.Kill(
            timestamp_seconds=float(m.caption_start_in_moment_seconds or 0.5),
            confidence=float(m.composite),
            description=m.description,
        ))
        if m.suggested_caption and m.caption_start_in_moment_seconds is not None:
            merged_reactions.append(gemini_detector.Reaction(
                timestamp_seconds=float(m.caption_start_in_moment_seconds),
                duration_seconds=float(m.caption_duration_seconds or 1.2),
                caption=m.suggested_caption,
                kind=m.caption_kind or "voice_comm",
            ))
    analyses = {source_video: gemini_detector.ClipAnalysis(
        kills=merged_kills, reactions=merged_reactions,
    )}

    cuts: list[CutPlan] = []
    directed = None
    detector_used = f"auto-clipper+director[{tier_name}]"
    render_opts: dict = {}

    if music_analysis is not None:
        try:
            beats_list = (beats.beat_times.tolist() if hasattr(beats.beat_times, "tolist") else list(beats.beat_times))
            bass_list = (beats.bass_onsets.tolist() if hasattr(beats.bass_onsets, "tolist") else list(beats.bass_onsets))
            directed = director_mod.direct_reel(
                music_analysis=music_analysis,
                clip_summaries=clip_summaries,
                beats_seconds=beats_list,
                bass_onsets_seconds=bass_list,
                tempo_bpm=float(beats.tempo),
                target_duration=tier_target,
                api_key=pool.next_key(),
            )
            cuts = _director_to_cuts(
                directed, clips, {source_video: source_duration}, analyses,
                clip_source_offsets=clip_source_offsets,
                clip_window_durations=clip_window_durations,
                clip_meme_tags=clip_meme_tags,
            )
            if cuts:
                intro_hold = float(getattr(music_analysis, "best_start_seconds", 0.0) or 0.0)
                if intro_hold < 0.3:
                    intro_hold = 0.0
                render_opts.update(dict(
                    intro_hold_seconds=intro_hold,
                    title_caption=getattr(directed, "title_caption", None),
                    outro_hold_seconds=float(getattr(directed, "outro_hold_seconds", 0.8)),
                    color_grade=getattr(directed, "color_grade", "teal_orange"),
                ))
        except Exception as exc:
            logger.warning(
                "director failed for tier %s: %s — using greedy fallback",
                tier_name, exc,
            )
            cuts = []

    # Greedy fallback
    if not cuts:
        highlights: list[Highlight] = []
        for m in tier_moments:
            peak_local = float(m.caption_start_in_moment_seconds or 0.5)
            peak_source = float(m.start_seconds) + peak_local
            highlights.append(Highlight(
                clip_path=source_video,
                peak_time=peak_source,
                score=float(m.composite),
                clip_duration=source_duration,
            ))
        plan_intensity = tier_intensity_override or _effective_intensity(
            config.intensity,
            music_analysis.recommended_intensity if music_analysis else None,
        )
        plan_intensity = plan_intensity if plan_intensity in ("chill", "balanced", "hype") else "balanced"
        cuts = _plan_cuts_greedy(
            highlights, beats, tier_target, plan_intensity, seed=config.seed,
        )
        # Annotate emphasis + captions + meme_tag from moment source
        for c in cuts:
            for m in tier_moments:
                if m.start_seconds <= c.start <= m.end_seconds:
                    c.emphasis = m.emphasis_hint
                    c.meme_tag = getattr(m, "meme_tag", None)
                    if m.suggested_caption:
                        c.caption = m.suggested_caption
                        c.caption_start_in_cut = max(0.0, float(m.caption_start_in_moment_seconds or 0.0)
                                                    - (c.start - float(m.start_seconds)))
                        c.caption_duration = float(m.caption_duration_seconds or 1.2)
                    break
        detector_used = f"auto-clipper+greedy[{tier_name}]"

    # If the tier doesn't allow effects (long_form), downgrade all emphasis
    # to "normal" so velocity ramps + impact bursts + freeze-frame don't fire.
    if not tier_params.get("allow_effects", True):
        for c in cuts:
            c.emphasis = "normal"

    return cuts, directed, render_opts, detector_used


# ─── Tier definitions for auto-clip multi-output ─────────────────────────
# Each tier gets its own director call and render. Moments are partitioned
# by composite score; tiers below min_moments are skipped (no padded filler).
# Phase 2 adds "long_form" which requires an explicit opt-in via PipelineConfig.
TIER_PARAMS: dict[str, dict] = {
    "headline": {
        "composite_range": (0.85, 1.01),
        "target_duration": 45.0,
        "min_moments": 3,
        "allow_effects": True,
        "intensity_override": "hype",
    },
    "bsides": {
        "composite_range": (0.70, 0.85),
        "target_duration": 45.0,
        "min_moments": 3,
        "allow_effects": True,
        "intensity_override": None,
    },
    "vibes": {
        "composite_range": (0.55, 0.70),
        "target_duration": 45.0,
        "min_moments": 3,
        "allow_effects": True,
        "intensity_override": "chill",
    },
    # Long-form tier: sweeps the full composite range (vibes+bsides+headline
    # all eligible) into a longer cut. Effects disabled so the edit reads as
    # a narrative compilation rather than a TikTok-style highlight reel —
    # velocity ramps and impact bursts look jarring at the 3-4 minute length.
    "long_form": {
        "composite_range": (0.55, 1.01),
        "target_duration": 240.0,
        "min_moments": 8,
        "allow_effects": False,
        "intensity_override": None,
    },
}


def _filter_moments_for_tier(moments: list, tier_params: dict) -> list:
    """Return moments whose composite score falls in the tier's range."""
    lo, hi = tier_params["composite_range"]
    return [m for m in moments if lo <= m.composite < hi]


def _select_active_tiers(config: "PipelineConfig", moments: list) -> list[str]:
    """Which tiers to render given the config and available moments. In auto-clip
    mode we always attempt headline/bsides/vibes; long_form only runs when
    the user opts in via config.include_long_form AND enough moments exist.
    Tiers with too few moments get skipped per-tier anyway; this is just the
    starting candidate list."""
    tiers = ["headline", "bsides", "vibes"]
    if getattr(config, "include_long_form", False):
        tiers.append("long_form")
    return tiers


def _generate_thumbnail(
    reel_path: Path,
    thumbnail_path: Path,
    seek_seconds: float = 1.0,
) -> bool:
    """Extract one frame at seek_seconds as a JPEG thumbnail. Returns True on
    success; failures are non-fatal (we render the reel regardless)."""
    try:
        import subprocess
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{seek_seconds:.2f}",
                "-i", str(reel_path),
                "-vframes", "1",
                "-vf", "scale=640:360:force_original_aspect_ratio=decrease",
                "-q:v", "4",
                str(thumbnail_path),
            ],
            check=True, capture_output=True, text=True,
        )
        return thumbnail_path.exists()
    except Exception as exc:
        logger.warning("thumbnail generation failed for %s: %s", reel_path.name, exc)
        return False


def _run_auto_clip(
    config: PipelineConfig,
    on_progress: Callable[[str, float], None] | None,
    report: Callable[[str, float], None],
) -> PipelineResult:
    """Auto-clip flow: single long source video → Gemini moment detection →
    director arranges moments as virtual clips → renderer."""
    if not config.source_video:
        raise RuntimeError("source_mode='auto_clip' requires config.source_video")
    if not config.gemini_api_keys:
        raise RuntimeError(
            "Auto-clip mode requires at least one Gemini API key. "
            "Add one in Settings or set GEMINI_API_KEYS in backend/.env."
        )
    if not config.source_video.exists():
        raise RuntimeError(f"source_video does not exist: {config.source_video}")

    pool = GeminiPool.from_keys(config.gemini_api_keys)

    # Music-optional: if no music uploaded, extract the source video's own
    # audio and use it as the reel's audio bed. Skip Gemini music analysis —
    # the source video's audio is unlikely to be a structured music track.
    music_path = config.music_path
    extracted_from_source = False
    if music_path is None:
        # Music-optional: use the source video's own audio. Generate a silent
        # "music" track for the render's music-mix input so the renderer's
        # amix just passes the game-audio through at 0dB. The game audio
        # track is the original callouts / effects / background — exactly
        # what the user wants when they didn't upload separate music.
        silent_music = config.output_path.parent / "silent_music.m4a"
        report("preparing source audio", 0.03)
        try:
            import subprocess
            # 10min of silence is more than enough for any reel we'd render.
            # ffmpeg clamps the mix to the video duration via `duration=first`.
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-t", "600",
                    "-c:a", "aac", "-b:a", "64k",
                    str(silent_music),
                ],
                check=True, capture_output=True, text=True,
            )
            music_path = silent_music
            extracted_from_source = True
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Failed to prepare silent music track: "
                f"{exc.stderr[-300:] if exc.stderr else 'unknown error'}"
            ) from exc

    # Kick off music analysis in parallel with the auto-clipper call. Skip
    # the analysis entirely when using extracted source audio — a vlog /
    # gaming VOD's audio isn't a structured track Gemini can analyze as music.
    if not extracted_from_source:
        music_analyze_exec: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="music-analyze",
        )
        music_future = music_analyze_exec.submit(
            gemini_music_analyzer.analyze_music,
            music_path,
            pool.next_key(),
        )
    else:
        music_analyze_exec = None
        music_future = None

    def clip_progress(stage: str, frac: float) -> None:
        # Scale auto-clipper progress (5% → 60% of the total) so subsequent
        # stages have room.
        report(stage, 0.05 + frac * 0.55)

    try:
        result = auto_clipper_mod.auto_clip(
            config.source_video, pool.next_key(),
            on_progress=clip_progress,
        )
    except auto_clipper_mod.AutoClipperError as exc:
        # Cancel music analysis if it's still running
        if music_future:
            music_future.cancel()
        if music_analyze_exec:
            music_analyze_exec.shutdown(wait=False)
        raise RuntimeError(f"Auto-clipper failed: {exc}") from exc

    music_analysis = None
    if music_future is not None:
        try:
            music_analysis = music_future.result(timeout=MUSIC_ANALYSIS_TIMEOUT_S)
        except FutureTimeoutError:
            music_future.cancel()
            logger.warning("music analysis timed out; continuing without music vibe")
        except Exception as exc:
            logger.warning("music analysis failed: %s", exc)
        finally:
            if music_analyze_exec:
                music_analyze_exec.shutdown(wait=False)

    if not result.moments:
        _write_auto_clip_debug(config, result, None, None, [])
        raise RuntimeError(
            f"Auto-clipper found 0 entertaining moments (min composite "
            f"{auto_clipper_mod.MIN_COMPOSITE_SCORE}) in "
            f"{result.duration_seconds/60:.1f} min of source. "
            "Try a different source video or lower the threshold."
        )

    # Dump moments.json as the primary artifact for debugging / re-edit workflows
    try:
        (config.output_path.parent / "moments.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("couldn't write moments.json: %s", exc)

    report("detecting beats", 0.62)
    beats = detect_beats(music_path)
    beats_valid = beats.is_valid()
    if not beats_valid:
        logger.info(
            "beat grid invalid (tempo=%.1f, beats=%d) — will place cuts on moment boundaries",
            beats.tempo, len(beats.beat_times),
        )
        # Substitute a sane default tempo so downstream cut-length math
        # (_cut_length_for) doesn't produce nonsense clamps. The actual
        # cut placement won't beat-snap — we fall through to moment-boundary
        # placement — but the planner still uses tempo to compute
        # intensity-appropriate cut durations.
        beats = BeatGrid(
            tempo=120.0,
            beat_times=beats.beat_times if len(beats.beat_times) > 0 else __import__("numpy").array([]),
            downbeat_times=beats.downbeat_times,
            duration=beats.duration,
            bass_onsets=beats.bass_onsets,
        )

    source_video = config.source_video
    n_total = len(result.moments)
    active_tiers = _select_active_tiers(config, result.moments)

    outputs: list[TieredOutput] = []
    all_cuts: list[CutPlan] = []  # aggregated across tiers for debug.json
    last_render_opts: dict = {}
    last_detector_used = "auto-clipper"
    last_voice_windows: Optional[list[tuple[float, float]]] = None

    for tier_idx, tier_name in enumerate(active_tiers):
        tier_params = TIER_PARAMS[tier_name]
        tier_moments = _filter_moments_for_tier(result.moments, tier_params)

        if len(tier_moments) < tier_params["min_moments"]:
            logger.info(
                "skipping tier %s: %d moments < min %d",
                tier_name, len(tier_moments), tier_params["min_moments"],
            )
            continue

        tier_frac_base = 0.62 + (0.30 * tier_idx / max(1, len(active_tiers)))
        report(f"planning {tier_name} ({len(tier_moments)} moments)", tier_frac_base)

        cuts, directed, render_opts, detector_used = _plan_tier_cuts(
            tier_name=tier_name,
            tier_params=tier_params,
            tier_moments=tier_moments,
            source_video=source_video,
            source_duration=float(result.duration_seconds),
            beats=beats,
            music_analysis=music_analysis,
            pool=pool,
            config=config,
        )
        if not cuts:
            logger.info("tier %s produced no cuts — skipping", tier_name)
            continue

        # Experimental voice boost per-tier
        voice_windows: Optional[list[tuple[float, float]]] = None
        if config.experimental_audio_boost:
            voice_windows = _compute_voice_boost_windows(
                cuts, intro_hold_seconds=float(render_opts.get("intro_hold_seconds", 0.0)),
            )

        tier_output_path = config.output_path.parent / f"{tier_name}.mp4"
        thumbnail_path = config.output_path.parent / f"thumbnail_{tier_name}.jpg"

        def tier_render_log(msg: str, _tn=tier_name) -> None:
            report(f"rendering {_tn}: {msg}", tier_frac_base + 0.08)

        try:
            render_reel(
                cuts=cuts,
                music_path=music_path,
                output_path=tier_output_path,
                aspect=config.aspect,
                on_log=tier_render_log,
                fade_in_seconds=0.3,
                fade_out_seconds=0.8,
                voice_boost_windows=voice_windows,
                game_gain_db=0.0 if extracted_from_source else -18.0,
                music_gain_db=0.0 if extracted_from_source else 0.0,
                **render_opts,
            )
        except Exception as exc:
            logger.warning("tier %s render failed: %s — skipping", tier_name, exc)
            continue

        # Thumbnail: seek into the middle of the first cut
        first_cut_mid = cuts[0].duration / 2.0 if cuts else 0.5
        _generate_thumbnail(tier_output_path, thumbnail_path, seek_seconds=first_cut_mid)

        outputs.append(TieredOutput(
            tier=tier_name,
            path=tier_output_path,
            thumbnail_path=thumbnail_path,
            composite_range=tier_params["composite_range"],
            num_cuts=len(cuts),
            final_duration=sum(c.duration for c in cuts)
                + render_opts.get("intro_hold_seconds", 0.0)
                + render_opts.get("outro_hold_seconds", 0.0),
            num_captions=sum(1 for c in cuts if c.caption),
            num_moments_in_range=len(tier_moments),
        ))
        all_cuts.extend(cuts)
        last_render_opts = render_opts
        last_voice_windows = voice_windows
        last_detector_used = detector_used

    if not outputs:
        _write_auto_clip_debug(config, result, music_analysis, None, [])
        raise RuntimeError(
            f"No tier had enough moments to render (found {n_total} moments; "
            f"each tier needs at least {min(p['min_moments'] for p in TIER_PARAMS.values())})"
        )

    # Back-compat: copy first rendered tier to config.output_path. Existing
    # callers (tests, main.py serving) that expect `reel.mp4` still work;
    # new callers read `outputs` for the full tier list.
    try:
        import shutil
        shutil.copyfile(outputs[0].path, config.output_path)
    except Exception as exc:
        logger.warning("couldn't copy top tier to output_path: %s", exc)

    report("done", 1.0)

    _write_auto_clip_debug(
        config, result, music_analysis, None, all_cuts,
        render_opts=last_render_opts, voice_windows=last_voice_windows,
    )
    _write_plan_json(config, all_cuts, beats.tempo, last_render_opts)

    # Aggregate return values
    total_cuts = sum(o.num_cuts for o in outputs)
    total_captions = sum(o.num_captions for o in outputs)
    total_duration = sum(o.final_duration for o in outputs) / len(outputs)  # average per tier

    return PipelineResult(
        output_path=config.output_path,
        outputs=outputs,
        tempo=beats.tempo,
        num_clips_scanned=1,
        num_candidates=n_total,
        num_cuts=total_cuts,
        final_duration=total_duration,
        seed=config.seed,
        cuts=all_cuts,
        detector_used=last_detector_used,
        clips_analyzed=1,
        clips_failed=0,
        captions_placed=total_captions,
        source_mode="auto_clip",
        moments_found=n_total,
        moments_selected=len(cuts),
    )


PLAN_SCHEMA_VERSION = "1.0"


def _cuts_to_plan_dict(
    cuts: list[CutPlan],
    music_path: Path,
    tempo: float,
    render_opts: dict,
    aspect: AspectPreset,
    target_duration: float,
    source_mode: str,
) -> dict:
    """Serialize the pipeline's chosen cuts + render options into a renderer-
    agnostic JSON spec. Human- and agent-editable; `run_from_plan()` consumes
    it to re-render without invoking Gemini at all."""
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "music": {"path": str(music_path), "bpm": round(tempo, 2)},
        "aspect": aspect,
        "target_duration": target_duration,
        "source_mode": source_mode,
        "intro_hold_seconds": float(render_opts.get("intro_hold_seconds", 0.0)),
        "title_caption": render_opts.get("title_caption"),
        "outro_hold_seconds": float(render_opts.get("outro_hold_seconds", 0.0)),
        "color_grade": render_opts.get("color_grade"),
        "segments": [
            {
                "id": f"seg_{i:03d}",
                "source": str(c.clip_path),
                "source_start_seconds": round(c.start, 3),
                "source_end_seconds": round(c.start + c.duration, 3),
                "duration_seconds": round(c.duration, 3),
                "caption": c.caption,
                "caption_start_in_cut": round(c.caption_start_in_cut, 3) if c.caption else None,
                "caption_duration": round(c.caption_duration, 3) if c.caption else None,
                "emphasis": c.emphasis,
            }
            for i, c in enumerate(cuts)
        ],
    }


def _write_plan_json(
    config: PipelineConfig,
    cuts: list[CutPlan],
    tempo: float,
    render_opts: dict,
) -> None:
    try:
        plan = _cuts_to_plan_dict(
            cuts=cuts,
            music_path=config.music_path,
            tempo=tempo,
            render_opts=render_opts,
            aspect=config.aspect,
            target_duration=config.target_duration,
            source_mode=config.source_mode,
        )
        (config.output_path.parent / "plan.json").write_text(
            json.dumps(plan, indent=2, default=str), encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("couldn't write plan.json: %s", exc)


def run_from_plan(
    plan_path: Path,
    output_path: Optional[Path] = None,
    *,
    on_progress: Callable[[str, float], None] | None = None,
    experimental_audio_boost: bool = False,
) -> PipelineResult:
    """Render a reel from an edited plan.json. Skips Gemini entirely — the
    plan.json IS the director's output, user-editable. This is the re-edit
    workflow: tweak the JSON (swap clip paths, reorder segments, change
    captions) and re-render without paying another Gemini call.

    If output_path is None, writes to plan_path.parent / 'reel.mp4'.
    """
    def report(stage: str, frac: float) -> None:
        if on_progress:
            on_progress(stage, max(0.0, min(1.0, frac)))

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise RuntimeError(
            f"Plan schema version {plan.get('schema_version')!r} does not match "
            f"supported {PLAN_SCHEMA_VERSION!r}. Upgrade or regenerate the plan."
        )

    music_path = Path(plan["music"]["path"])
    if not music_path.exists():
        raise RuntimeError(f"Music file referenced in plan not found: {music_path}")

    if output_path is None:
        output_path = plan_path.parent / "reel.mp4"

    # Reconstruct CutPlans from segments
    cuts: list[CutPlan] = []
    for seg in plan.get("segments", []):
        source = Path(seg["source"])
        if not source.exists():
            raise RuntimeError(f"Source clip in plan not found: {source}")
        cuts.append(CutPlan(
            clip_path=source,
            start=float(seg["source_start_seconds"]),
            duration=float(seg["duration_seconds"]),
            caption=seg.get("caption"),
            caption_start_in_cut=float(seg.get("caption_start_in_cut") or 0.0),
            caption_duration=float(seg.get("caption_duration") or 2.0),
            emphasis=str(seg.get("emphasis") or "normal"),
        ))
    if not cuts:
        raise RuntimeError("Plan has no segments")

    report("loaded plan", 0.10)

    # Compose voice windows if experimental flag is on
    intro_hold = float(plan.get("intro_hold_seconds", 0.0) or 0.0)
    voice_windows: Optional[list[tuple[float, float]]] = None
    if experimental_audio_boost:
        voice_windows = _compute_voice_boost_windows(cuts, intro_hold_seconds=intro_hold)

    render_opts = dict(
        intro_hold_seconds=intro_hold,
        title_caption=plan.get("title_caption"),
        outro_hold_seconds=float(plan.get("outro_hold_seconds", 0.0) or 0.0),
        color_grade=plan.get("color_grade"),
    )

    def render_log(msg: str) -> None:
        report(f"rendering: {msg}", 0.60)

    report("rendering from plan", 0.20)
    render_reel(
        cuts=cuts,
        music_path=music_path,
        output_path=output_path,
        aspect=plan.get("aspect", "landscape"),
        on_log=render_log,
        fade_in_seconds=0.3,
        fade_out_seconds=0.8,
        voice_boost_windows=voice_windows,
        **render_opts,
    )
    report("done", 1.0)

    final_duration = (
        sum(c.duration for c in cuts)
        + render_opts.get("intro_hold_seconds", 0.0)
        + render_opts.get("outro_hold_seconds", 0.0)
    )
    return PipelineResult(
        output_path=output_path,
        tempo=float(plan["music"].get("bpm", 0.0) or 0.0),
        num_clips_scanned=len({str(c.clip_path) for c in cuts}),
        num_candidates=len(cuts),
        num_cuts=len(cuts),
        final_duration=final_duration,
        cuts=cuts,
        detector_used="plan_json",
        source_mode=str(plan.get("source_mode") or "clips"),
        captions_placed=sum(1 for c in cuts if c.caption),
    )


def _write_auto_clip_debug(
    config: PipelineConfig,
    result,
    music_analysis,
    directed,
    cuts: list[CutPlan],
    render_opts: Optional[dict] = None,
    voice_windows: Optional[list[tuple[float, float]]] = None,
) -> None:
    try:
        render_opts = render_opts or {}
        out = {
            "detector_used": "auto-clipper",
            "source_mode": "auto_clip",
            "source_video": str(config.source_video) if config.source_video else None,
            "intensity": config.intensity,
            "target_duration": config.target_duration,
            "moments_found": len(result.moments) if result else 0,
            "moments_selected": len(cuts),
            "video_mood": result.video_mood if result else None,
            "music_analysis": music_analysis.model_dump() if music_analysis else None,
            "director_output": directed.model_dump() if directed else None,
            "cuts": [
                {
                    "source_start": c.start,
                    "duration": c.duration,
                    "caption": c.caption,
                    "emphasis": c.emphasis,
                }
                for c in cuts
            ],
            "effects_applied": _compute_effects_applied(
                cuts, render_opts, voice_windows, config.target_duration,
            ),
        }
        debug_path = config.output_path.parent / "debug.json"
        debug_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("couldn't write auto-clip debug.json: %s", exc)


def run(
    config: PipelineConfig,
    on_progress: Callable[[str, float], None] | None = None,
) -> PipelineResult:
    """Run the full pipeline. on_progress(stage, fraction_0_to_1) is optional."""
    def report(stage: str, frac: float) -> None:
        if on_progress:
            on_progress(stage, max(0.0, min(1.0, frac)))

    # ─── AUTO-CLIP PATH (source_mode == "auto_clip") ──────────────────
    # Runs Gemini over a full source video, identifies entertaining moments,
    # and hands them to the director flow as virtual clips. Downstream —
    # music analysis, director, render — is unchanged.
    if config.source_mode == "auto_clip":
        return _run_auto_clip(config, on_progress, report)

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
                bass_list = (
                    beats.bass_onsets.tolist()
                    if hasattr(beats.bass_onsets, "tolist")
                    else list(beats.bass_onsets)
                )
                directed = director_mod.direct_reel(
                    music_analysis=music_analysis,
                    clip_summaries=clip_summaries,
                    beats_seconds=beats_list,
                    bass_onsets_seconds=bass_list,
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
                        color_grade=getattr(directed, "color_grade", "teal_orange"),
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

    # Experimental voice boost on voice_comm captions — expose reel-timeline
    # windows to the renderer so game audio lifts during callouts.
    voice_windows: Optional[list[tuple[float, float]]] = None
    if config.experimental_audio_boost:
        voice_windows = _compute_voice_boost_windows(
            cuts,
            intro_hold_seconds=float(render_opts.get("intro_hold_seconds", 0.0)),
        )

    report("rendering", 0.80)
    render_reel(
        cuts=cuts,
        music_path=config.music_path,
        output_path=config.output_path,
        aspect=config.aspect,
        on_log=render_log,
        fade_in_seconds=0.3,
        fade_out_seconds=0.8,
        voice_boost_windows=voice_windows,
        **render_opts,
    )
    report("done", 1.0)

    _write_debug_json(
        config, detector_used, analyses, errors, music_analysis, directed, cuts,
        render_opts=render_opts, voice_windows=voice_windows,
    )
    _write_plan_json(config, cuts, beats.tempo, render_opts)

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
