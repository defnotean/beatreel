"""Tests for beat detection on synthetic pulse-train audio."""
from __future__ import annotations

from pathlib import Path

from beatreel.beats import detect_beats


def test_detect_beats_finds_tempo_near_target(sample_music: Path):
    grid = detect_beats(sample_music)
    # Fixture pulses at 2 Hz = 120 BPM. librosa may report 60 or 120 (octave ambiguity).
    assert grid.tempo > 0
    assert 50 < grid.tempo < 250
    assert grid.duration > 0
    assert len(grid.beat_times) > 5


def test_beat_grid_nearest_beat_picks_closest():
    from beatreel.beats import BeatGrid
    import numpy as np

    grid = BeatGrid(
        tempo=120.0,
        beat_times=np.array([0.0, 0.5, 1.0, 1.5, 2.0]),
        downbeat_times=np.array([0.0, 2.0]),
        duration=2.0,
    )
    assert grid.nearest_beat(0.9) == 1.0
    assert grid.nearest_beat(1.25) in (1.0, 1.5)  # equidistant; either is fine
    assert grid.nearest_beat(-10.0) == 0.0
    assert grid.nearest_beat(100.0) == 2.0
