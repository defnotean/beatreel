"""Optional madmom-based beat / downbeat detector.

Imported lazily by `beats.detect_beats`. Any failure (madmom not installed,
runtime error, audio decode issue) propagates as an exception, which the
orchestrator catches and uses as a signal to fall back to the librosa path.

Why madmom: RNN-based beat/downbeat trackers are substantially more accurate
than spectral-flux methods on real music — particularly on tracks with
expressive timing, tempo modulation, or sparse/weak transients. The joint
beat+downbeat model handles meter inference better than running beat and
downbeat detection separately.

Install caveat: madmom's PyPI release breaks on Python 3.11+ due to NumPy
deprecations. Users wanting this path should `pip install beatreel[accurate]`
which pins to the main-branch git ref and forces numpy<2.0. See pyproject.toml.
"""
from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np

from .beats import (
    BeatGrid,
    _beat_confidence,
    _detect_accent_onsets,
    _detect_kick_onsets,
    _filter_bass_onsets_by_beat_grid,
    _select_bass_onsets,
)

_FPS = 100


def detect_beats_madmom(music_path: str | Path, sr: int = 22050) -> BeatGrid:
    """Detect beats and downbeats with madmom; reuse librosa for bass onsets."""
    # Lazy import — raises ImportError when madmom is unavailable; the
    # orchestrator catches it.
    from madmom.features.beats import (  # type: ignore[import-not-found]
        DBNBeatTrackingProcessor,
        RNNBeatProcessor,
    )
    from madmom.features.downbeats import (  # type: ignore[import-not-found]
        DBNDownBeatTrackingProcessor,
        RNNDownBeatProcessor,
    )

    path = str(music_path)
    y, sr = librosa.load(path, sr=sr, mono=True)
    duration = float(len(y) / sr)

    # Joint beat+downbeat path. The joint model produces refined beats AND
    # downbeats with meter inference (3/4 vs 4/4) in one pass.
    db_act = RNNDownBeatProcessor()(path)
    dbn_db = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=_FPS)
    db_out = dbn_db(db_act)  # shape: (N, 2) — (time_s, beat_index_within_bar)

    if db_out.size == 0:
        # Fall back to beat-only processor.
        beat_act = RNNBeatProcessor()(path)
        beats_only = DBNBeatTrackingProcessor(fps=_FPS)(beat_act)
        beat_times = np.asarray(beats_only, dtype=float)
        downbeat_times = np.asarray([], dtype=float)
        beat_act_for_conf = beat_act
    else:
        beat_times = np.asarray(db_out[:, 0], dtype=float)
        downbeat_mask = db_out[:, 1].astype(int) == 1
        downbeat_times = np.asarray(db_out[downbeat_mask, 0], dtype=float)
        # For per-beat confidence we use the joint-model activations summed
        # across the beat columns (col 0 = beat, col 1 = downbeat).
        beat_act_for_conf = db_act.sum(axis=1) if db_act.ndim == 2 else db_act

    if beat_times.size == 0:
        raise RuntimeError("madmom returned no beats")

    tempo = float(60.0 / max(np.median(np.diff(beat_times)), 1e-6)) if beat_times.size > 1 else 0.0

    # Bass onsets — madmom has no useful sub-bass onset model. Reuse the
    # librosa two-band detector.
    kick_onsets = _detect_kick_onsets(y, sr)
    accent_onsets = _detect_accent_onsets(y, sr)
    bass_onsets = _select_bass_onsets(kick_onsets, accent_onsets, duration)
    bass_onsets = _filter_bass_onsets_by_beat_grid(bass_onsets, beat_times)

    # Beat confidence from madmom activation magnitudes (preferred) — fall
    # back to the spectral-flux blend if activations aren't shaped as
    # expected.
    beat_conf = _beat_confidence_from_madmom(beat_times, beat_act_for_conf)
    if beat_conf.size == 0:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        beat_conf = _beat_confidence(beat_times, onset_env, sr, bass_onsets)

    if downbeat_times.size > 0 and beat_conf.size > 0:
        db_idx = [int(np.argmin(np.abs(beat_times - t))) for t in downbeat_times]
        downbeat_conf = beat_conf[db_idx]
    else:
        downbeat_conf = np.array([])

    return BeatGrid(
        tempo=tempo,
        beat_times=beat_times,
        downbeat_times=downbeat_times,
        duration=duration,
        bass_onsets=bass_onsets,
        beat_confidence=beat_conf,
        downbeat_confidence=downbeat_conf,
        _tempo_path="madmom",
    )


def _beat_confidence_from_madmom(
    beat_times: np.ndarray, activations: np.ndarray,
) -> np.ndarray:
    """Sample madmom activation magnitude at each beat's frame.

    Activations are at _FPS frames/sec. Normalize the sampled values to
    [0, 1] so the scale matches the librosa-path confidence blend.
    """
    if beat_times.size == 0 or activations.size == 0:
        return np.zeros(0)
    if activations.ndim != 1:
        return np.zeros(0)
    frames = np.clip((beat_times * _FPS).astype(int), 0, activations.size - 1)
    raw = activations[frames]
    rmax = float(raw.max()) if raw.size else 0.0
    if rmax <= 0:
        return np.zeros(beat_times.size)
    return np.clip(raw / rmax, 0.0, 1.0)
