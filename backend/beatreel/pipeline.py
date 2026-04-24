"""Top-level orchestration: clips + music → highlight reel."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from .beats import BeatGrid, detect_beats
from .highlights import Highlight, score_clips
from .render import CutPlan, render_reel

Intensity = Literal["chill", "balanced", "hype"]

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv"}


@dataclass
class PipelineConfig:
    clips_dir: Path
    music_path: Path
    output_path: Path
    target_duration: float = 60.0
    intensity: Intensity = "balanced"


@dataclass
class PipelineResult:
    output_path: Path
    tempo: float
    num_clips_scanned: int
    num_candidates: int
    num_cuts: int
    final_duration: float
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
) -> list[CutPlan]:
    """Select highlights + snap to beats until target duration reached."""
    if not highlights:
        return []

    min_cut, max_cut = _cut_length_for(intensity, beats.tempo)
    # Greedy: take best-scoring highlights until target duration is hit
    ordered = sorted(highlights, key=lambda h: h.score, reverse=True)

    plans: list[CutPlan] = []
    total = 0.0
    used_per_clip: dict[Path, list[tuple[float, float]]] = {}

    for h in ordered:
        if total >= target_duration:
            break

        # Clip window centered on the peak, clamped to clip bounds
        half = max_cut / 2.0
        start = max(0.0, h.peak_time - half)
        end = min(h.clip_duration, h.peak_time + half)
        duration = end - start
        if duration < min_cut:
            continue

        # Trim so we don't exceed target
        remaining = target_duration - total
        if duration > remaining:
            # Pull the end in; keep peak near the middle
            end = start + remaining
            duration = remaining
        if duration < min_cut and total > 0:
            continue

        # Avoid overlapping the same source-clip region twice
        overlaps = used_per_clip.setdefault(h.clip_path, [])
        if any(not (end <= s or start >= e) for s, e in overlaps):
            continue
        overlaps.append((start, end))

        plans.append(CutPlan(clip_path=h.clip_path, start=start, duration=duration))
        total += duration

    # Sort plans by the closest downbeat so the montage builds on the music's structure
    if len(beats.downbeat_times) > 0:
        def beat_affinity(plan: CutPlan) -> float:
            return min(abs(b - plan.duration) for b in beats.downbeat_times[:8])
        plans.sort(key=beat_affinity)
    return plans


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

    def per_clip_progress(done: int, total: int, _path: Path | None) -> None:
        # Scoring covers 10% → 70%
        frac = 0.10 + 0.60 * (done / max(total, 1))
        report(f"scoring clips ({done}/{total})", frac)

    highlights = score_clips(clips, on_progress=per_clip_progress)

    report("planning cuts", 0.75)
    cuts = _plan_cuts(highlights, beats, config.target_duration, config.intensity)
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
        cuts=cuts,
    )
