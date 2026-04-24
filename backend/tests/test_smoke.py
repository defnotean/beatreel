"""Smoke tests for planning logic and data shapes (no ffmpeg required)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from beatreel.beats import BeatGrid
from beatreel.highlights import Highlight
from beatreel.pipeline import _cut_length_for, _plan_cuts


def _beats_at_120_bpm(duration: float = 60.0) -> BeatGrid:
    return BeatGrid(
        tempo=120.0,
        beat_times=np.arange(0, duration, 0.5),
        downbeat_times=np.arange(0, duration, 2.0),
        duration=duration,
    )


def _mk_highlights(clip_paths: list[str], per_clip: int = 3) -> list[Highlight]:
    out: list[Highlight] = []
    for ci, cp in enumerate(clip_paths):
        for k in range(per_clip):
            out.append(
                Highlight(
                    clip_path=Path(cp),
                    peak_time=2.0 + k * 1.5,
                    score=1.0 - (ci * 0.1 + k * 0.05),
                    clip_duration=10.0,
                )
            )
    return out


def test_cut_length_intensity_ordering():
    c_min, c_max = _cut_length_for("chill", 120)
    b_min, b_max = _cut_length_for("balanced", 120)
    h_min, h_max = _cut_length_for("hype", 120)
    assert c_max > b_max > h_max
    assert c_min > b_min >= h_min


def test_plan_cuts_respects_target_duration():
    beats = _beats_at_120_bpm()
    highlights = _mk_highlights(["a.mp4", "b.mp4", "c.mp4"], per_clip=4)
    plans = _plan_cuts(highlights, beats, target_duration=10.0, intensity="balanced")
    total = sum(p.duration for p in plans)
    assert 0 < total <= 10.0 + 0.1
    assert len(plans) > 0


def test_plan_cuts_empty_highlights():
    beats = BeatGrid(tempo=120.0, beat_times=np.array([]), downbeat_times=np.array([]), duration=0.0)
    assert _plan_cuts([], beats, 60.0, "balanced") == []


def test_plan_cuts_seed_determinism_same_seed():
    beats = _beats_at_120_bpm()
    highlights = _mk_highlights([f"clip_{i}.mp4" for i in range(5)], per_clip=3)
    a = _plan_cuts(highlights, beats, target_duration=20.0, intensity="balanced", seed=42)
    b = _plan_cuts(highlights, beats, target_duration=20.0, intensity="balanced", seed=42)
    assert [(p.clip_path, p.start, p.duration) for p in a] == [
        (p.clip_path, p.start, p.duration) for p in b
    ]


def test_plan_cuts_different_seeds_differ_sometimes():
    """Different seeds should produce different plans for at least some seed pairs.

    We're not asserting *every* pair differs — score bucketing can collapse choices —
    but across 10 seeds there should be at least 2 distinct plan signatures.
    """
    beats = _beats_at_120_bpm()
    highlights = _mk_highlights([f"clip_{i}.mp4" for i in range(6)], per_clip=3)
    signatures = set()
    for seed in range(10):
        plans = _plan_cuts(highlights, beats, target_duration=15.0, intensity="balanced", seed=seed)
        sig = tuple((str(p.clip_path), round(p.start, 2)) for p in plans)
        signatures.add(sig)
    assert len(signatures) >= 2, f"seeds produced only {len(signatures)} distinct plans"


def test_plan_cuts_does_not_overlap_same_source_clip():
    beats = _beats_at_120_bpm()
    # Stack three highlights on the same clip, close together
    highlights = [
        Highlight(clip_path=Path("dup.mp4"), peak_time=2.0, score=1.0, clip_duration=10.0),
        Highlight(clip_path=Path("dup.mp4"), peak_time=2.3, score=0.9, clip_duration=10.0),
        Highlight(clip_path=Path("dup.mp4"), peak_time=7.0, score=0.8, clip_duration=10.0),
    ]
    plans = _plan_cuts(highlights, beats, target_duration=20.0, intensity="balanced")
    used = [(p.start, p.start + p.duration) for p in plans if p.clip_path == Path("dup.mp4")]
    for i, (s1, e1) in enumerate(used):
        for s2, e2 in used[i + 1:]:
            assert e1 <= s2 or e2 <= s1, f"overlap between {(s1, e1)} and {(s2, e2)}"
