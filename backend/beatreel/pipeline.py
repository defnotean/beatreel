"""Top-level orchestration: clips + music → highlight reel."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

from .aspect import AspectPreset
from .beats import BeatGrid, detect_beats
from .cache import ClipCache
from .highlights import Highlight, score_clip
from .render import CutPlan, render_reel
from .scenes import boost_highlights_near_scenes, detect_scene_changes

Intensity = Literal["chill", "balanced", "hype"]

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv"}

# Bump when detection output shape changes in a way that invalidates cached results.
DETECTOR_VERSION = "audio+scene-v1"


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


def _list_clips(clips_dir: Path) -> list[Path]:
    if not clips_dir.exists():
        raise FileNotFoundError(f"Clips directory not found: {clips_dir}")
    return sorted(
        p for p in clips_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )


def _cut_length_for(intensity: Intensity, tempo: float) -> tuple[float, float]:
    """(min_cut, max_cut) in seconds based on intensity and tempo."""
    beat_s = 60.0 / max(tempo, 1.0)
    if intensity == "hype":
        return (beat_s * 1.0, beat_s * 2.0)
    if intensity == "chill":
        return (beat_s * 4.0, beat_s * 8.0)
    return (beat_s * 2.0, beat_s * 4.0)


def _plan_cuts(
    highlights: list[Highlight],
    beats: BeatGrid,
    target_duration: float,
    intensity: Intensity,
    seed: Optional[int] = None,
) -> list[CutPlan]:
    """Select highlights + snap to beats until target duration reached.

    `seed` adds deterministic randomness to tie-breaking so re-rolls with the
    same inputs produce visibly different selections while still favoring the
    higher-scored peaks.
    """
    if not highlights:
        return []

    min_cut, max_cut = _cut_length_for(intensity, beats.tempo)

    # Bucket by score magnitude so the randomness only moves things within a
    # similar-quality band — we're not going to surface a bad highlight over
    # a great one just to vary the output.
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

        # Snap start to nearest beat
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
    highlights = _score_with_cache(clips, cache, config.use_scene_detection, report)

    report("planning cuts", 0.75)
    cuts = _plan_cuts(
        highlights,
        beats,
        config.target_duration,
        config.intensity,
        seed=config.seed,
    )
    if not cuts:
        raise RuntimeError(
            "No highlights detected. Try a longer target duration, different "
            "clips, or the 'hype' intensity profile."
        )

    def render_log(msg: str) -> None:
        report(f"rendering: {msg}", 0.85)

    report("rendering", 0.80)
    render_reel(
        cuts=cuts,
        music_path=config.music_path,
        output_path=config.output_path,
        aspect=config.aspect,
        on_log=render_log,
    )
    report("done", 1.0)

    return PipelineResult(
        output_path=config.output_path,
        tempo=beats.tempo,
        num_clips_scanned=len(clips),
        num_candidates=len(highlights),
        num_cuts=len(cuts),
        final_duration=sum(c.duration for c in cuts),
        seed=config.seed,
        cuts=cuts,
    )
