"""Tests for audio-peak highlight detection using synthetic audio."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from beatreel.highlights import score_clip


def test_score_clip_finds_known_peaks(sample_clips_audio_only: Path):
    clip = sample_clips_audio_only / "clip_01.wav"
    highlights = score_clip(clip)
    # Fixture has bursts at 2.0s and 4.5s
    assert len(highlights) >= 1
    peak_times = sorted(h.peak_time for h in highlights)
    # Each detected peak should be within 0.3s of one of the known bursts
    known = [2.0, 4.5]
    for pt in peak_times:
        assert any(abs(pt - k) <= 0.3 for k in known), f"peak {pt} not near any known burst"


def test_score_clip_on_silent_clip_returns_empty(tmp_path: Path):
    import soundfile as sf
    path = tmp_path / "silent.wav"
    sr = 22050
    sf.write(path, np.zeros(int(sr * 3.0), dtype=np.float32), sr)
    assert score_clip(path) == []


def test_score_clip_on_missing_file_returns_empty(tmp_path: Path):
    assert score_clip(tmp_path / "does_not_exist.wav") == []
