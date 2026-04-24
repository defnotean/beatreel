"""Smoke tests that don't require ffmpeg."""
from __future__ import annotations

import numpy as np

from beatreel.pipeline import _cut_length_for, _plan_cuts
from beatreel.beats import BeatGrid
from beatreel.highlights import Highlight
from pathlib import Path


def test_cut_length_intensity_ordering():
    chill_min, chill_max = _cut_length_for("chill", 120)
    bal_min, bal_max = _cut_length_for("balanced", 120)
    hype_min, hype_max = _cut_length_for("hype", 120)
    assert chill_max > bal_max > hype_max
    assert chill_min > bal_min >= hype_min


def test_plan_cuts_respects_target_duration():
    beats = BeatGrid(
        tempo=120.0,
        beat_times=np.arange(0, 60, 0.5),
        downbeat_times=np.arange(0, 60, 2.0),
        duration=60.0,
    )
    highlights = [
        Highlight(clip_path=Path(f"clip_{i}.mp4"), peak_time=5.0, score=1.0 - i * 0.01, clip_duration=10.0)
        for i in range(20)
    ]
    plans = _plan_cuts(highlights, beats, target_duration=10.0, intensity="balanced")
    total = sum(p.duration for p in plans)
    assert total <= 10.0 + 0.1
    assert len(plans) > 0


def test_plan_cuts_empty_highlights():
    beats = BeatGrid(tempo=120.0, beat_times=np.array([]), downbeat_times=np.array([]), duration=0.0)
    assert _plan_cuts([], beats, 60.0, "balanced") == []
