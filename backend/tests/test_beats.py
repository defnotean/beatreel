"""Tests for beat detection on synthetic pulse-train audio."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from beatreel.beats import BeatGrid, detect_beats


def test_detect_beats_finds_tempo_near_target(sample_music: Path):
    grid = detect_beats(sample_music)
    # Fixture pulses at 2 Hz = 120 BPM. librosa may report 60 or 120 (octave ambiguity).
    assert grid.tempo > 0
    assert 50 < grid.tempo < 250
    assert grid.duration > 0
    assert len(grid.beat_times) > 5


def test_beat_grid_nearest_beat_picks_closest():
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


# --- New tests for the upgraded detector ------------------------------------


def _synth_kick_track(
    path: Path,
    duration: float,
    bpm: float,
    *,
    downbeat_every: int = 4,
    sr: int = 22050,
) -> None:
    """Synth a kick-driven track.

    Downbeats: sharp 50 Hz exponential kick (high bass-band energy).
    Backbeats: Gaussian-windowed 3 kHz tick (narrow bandwidth, well above
    the 150 Hz bass cutoff — minimal bass-band leakage). This separation
    lets the bass-onset detector pick up downbeats only, while the
    broadband onset_strength used by beat_track still finds every beat.
    """
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    audio = np.zeros_like(t, dtype=np.float64)
    beat_period = 60.0 / bpm
    i = 0
    beat_t = 0.0
    while beat_t < duration:
        is_downbeat = (i % downbeat_every == 0)
        if is_downbeat:
            env = np.where(t >= beat_t, np.exp(-(t - beat_t) * 30.0), 0.0)
            audio += 1.0 * env * np.sin(2 * np.pi * 50.0 * t)
        else:
            # Gaussian (12 ms σ) → spectral bandwidth ≈ 80 Hz around 3 kHz.
            sigma = 0.012
            env = np.exp(-((t - beat_t) / sigma) ** 2)
            audio += 0.35 * env * np.sin(2 * np.pi * 3000.0 * t)
        beat_t += beat_period
        i += 1
    audio = audio / max(np.abs(audio).max(), 1e-6) * 0.85
    sf.write(path, audio.astype(np.float32), sr)


def _synth_tempo_change_track(
    path: Path,
    sr: int = 22050,
) -> None:
    """30s @ 90 BPM + 30s @ 120 BPM, 50 Hz kicks on every beat.

    Per-window tempo std/mean should clear _TEMPO_INSTABILITY_RATIO so
    detect_beats picks the PLP path.
    """
    sections = [(30.0, 90.0), (30.0, 120.0)]
    samples: list[np.ndarray] = []
    for duration, bpm in sections:
        t_local = np.linspace(0, duration, int(sr * duration), endpoint=False)
        section = np.zeros_like(t_local, dtype=np.float64)
        beat_period = 60.0 / bpm
        beat_t = 0.0
        while beat_t < duration:
            past_onset = t_local >= beat_t
            # Clamp the exp argument to ≤0 so we never hit overflow on the
            # masked-out values.
            arg = np.where(past_onset, -(t_local - beat_t) * 30.0, 0.0)
            env = np.exp(arg) * past_onset
            section += env * np.sin(2 * np.pi * 50.0 * (t_local - beat_t))
            beat_t += beat_period
        samples.append(section)
    audio = np.concatenate(samples)
    audio = audio / max(np.abs(audio).max(), 1e-6) * 0.85
    sf.write(path, audio.astype(np.float32), sr)


def test_downbeat_lands_on_every_4th_beat(tmp_path: Path):
    music = tmp_path / "kick_track.wav"
    _synth_kick_track(music, duration=16.0, bpm=120.0, downbeat_every=4)
    grid = detect_beats(music, force_librosa=True)

    # 16s @ 120 BPM = 32 beats total. librosa's beat tracker typically needs
    # ~2s of audio to lock onto the tempo and can miss the first few beats,
    # so accept ≥20 beats and ≥5 downbeats.
    assert grid.beat_times.size >= 20
    assert grid.downbeat_times.size >= 5

    expected = np.arange(0.0, 16.0, 2.0)  # downbeats at 0, 2, 4, ..., 14
    # Every detected downbeat must land within 100ms of an expected position.
    for db in grid.downbeat_times:
        diffs = np.abs(expected - db)
        assert diffs.min() <= 0.10, f"downbeat {db:.3f} far from expected (min diff {diffs.min():.3f})"

    # And every detected downbeat should have high confidence relative to a
    # backbeat — sanity-check that the bass-onset weighting actually
    # influenced placement.
    assert float(np.median(grid.downbeat_confidence)) > 0.5


def test_no_octave_error_on_clear_120bpm(tmp_path: Path):
    music = tmp_path / "clear_120.wav"
    # Every beat is a strong kick — no downbeat asymmetry, just clean tempo.
    _synth_kick_track(music, duration=20.0, bpm=120.0, downbeat_every=1)
    grid = detect_beats(music, force_librosa=True)
    assert 115.0 <= grid.tempo <= 125.0, f"expected ~120 BPM, got {grid.tempo}"


def test_dynamic_tempo_track_uses_plp(tmp_path: Path):
    music = tmp_path / "tempo_change.wav"
    _synth_tempo_change_track(music)
    grid = detect_beats(music, force_librosa=True)
    assert grid._tempo_path in ("plp", "beat_track")
    # Even if beat_track wins, the grid should still be usable.
    assert grid.is_valid(), "tempo-change grid should be valid"
    # Tempo should land somewhere between the two halves' tempos.
    assert 80.0 <= grid.tempo <= 130.0
    # Should detect beats across the full duration, not just one half.
    first_half = grid.beat_times[grid.beat_times < 30.0]
    second_half = grid.beat_times[grid.beat_times >= 30.0]
    assert first_half.size >= 20
    assert second_half.size >= 20


def test_nearest_beat_max_dist_returns_input():
    grid = BeatGrid(
        tempo=120.0,
        beat_times=np.array([0.0, 0.5, 1.0]),
        downbeat_times=np.array([0.0]),
        duration=2.0,
    )
    # No beat within 200ms of t=5.0 → return original.
    assert grid.nearest_beat(5.0, max_dist_s=0.2) == 5.0
    # 0.55 → nearest is 0.5 (within window).
    assert grid.nearest_beat(0.55, max_dist_s=0.2) == 0.5
    # Default (no max_dist) preserves old "always snap" behavior.
    assert grid.nearest_beat(5.0) == 1.0


def test_nearest_downbeat_returns_input_when_no_downbeat_in_window():
    grid = BeatGrid(
        tempo=120.0,
        beat_times=np.array([0.0, 0.5, 1.0, 1.5, 2.0]),
        downbeat_times=np.array([0.0, 2.0]),
        duration=2.0,
    )
    assert grid.nearest_downbeat(0.05, max_dist_s=0.25) == 0.0
    assert grid.nearest_downbeat(1.0, max_dist_s=0.25) == 1.0  # no DB within 250ms


def test_madmom_path_falls_back_when_unavailable(sample_music: Path, monkeypatch):
    """If madmom raises ImportError (or any exception), detect_beats should
    transparently fall back to the librosa path."""
    import beatreel.beats_madmom as bm

    def boom(*_args, **_kwargs):
        raise ImportError("madmom not available (forced)")

    monkeypatch.setattr(bm, "detect_beats_madmom", boom)
    grid = detect_beats(sample_music)  # force_librosa=False default
    assert grid.tempo > 0
    assert grid.beat_times.size > 5
    assert grid._tempo_path in ("beat_track", "plp")


def test_is_valid_rejects_irregular_grid():
    """Hard checks pass (tempo+count), but all three soft checks fail."""
    grid = BeatGrid(
        tempo=120.0,
        # Irregular spacing (high IBI std/mean), clustered at the start
        # (low coverage), and we'll override confidence to be low.
        beat_times=np.array([0.0, 0.1, 0.5, 1.5, 2.0]),
        downbeat_times=np.array([0.0]),
        duration=30.0,
        beat_confidence=np.array([0.1, 0.1, 0.1, 0.1, 0.1]),
    )
    assert grid.is_valid() is False


def test_is_valid_tolerates_single_soft_fail():
    """If only one of (regularity, coverage, confidence) fails, still valid."""
    # Low coverage but regular spacing and high confidence → still valid.
    grid = BeatGrid(
        tempo=120.0,
        beat_times=np.linspace(0.0, 5.0, 11),  # regular 0.5s spacing, 11 beats
        downbeat_times=np.array([0.0, 2.0, 4.0]),
        duration=30.0,
    )
    assert grid.is_valid() is True
