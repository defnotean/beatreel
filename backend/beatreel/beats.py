"""Beat detection for the music track.

Two-tier design:

1. Default path (librosa, no extra deps): HPSS-cleaned percussive onset
   envelope drives librosa.beat.beat_track, followed by octave correction
   (autocorrelation over {T/2, T, T*2}), a PLP fallback for tempo-variable
   tracks, real bass-energy-weighted downbeat picking with a 4/4-vs-3/4
   hypothesis test, a two-band onset detector (40-150 Hz kicks + 150-250 Hz
   accents) with the stronger band selected per-track, beat-grid cross-
   validation of bass onsets, and per-beat confidence scoring.

2. Optional madmom path (pip install beatreel[accurate]): RNN+DBN beat and
   downbeat tracking. Imported lazily; any failure (ImportError, runtime
   error, ...) falls back to the librosa path so the default install is
   never broken by madmom problems.

Public surface:
    detect_beats(music_path, sr=22050, *, force_librosa=False) -> BeatGrid
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import librosa
import numpy as np

logger = logging.getLogger(__name__)

# Tunables --------------------------------------------------------------------

_HOP = 512
_HPSS_FULL_TRACK_CAP_S = 180.0  # tracks longer than this skip HPSS pre-clean
_TEMPO_INSTABILITY_RATIO = 0.08  # per-window tempo std/mean → use PLP
_OCTAVE_CANDIDATES = (0.5, 1.0, 2.0)
_DOWNBEAT_WINDOW_S = 0.050  # ±50 ms window for bass-onset scoring per beat
_BASS_BAND_HZ = (40, 150)
_ACCENT_BAND_HZ = (150, 250)
_BASS_BEAT_PROXIMITY_S = 0.070
_KICK_MIN_GAP_S = 0.18
_ACCENT_MIN_GAP_S = 0.12
_TEMPO_RANGE = (60.0, 200.0)
_MIN_BEATS_FOR_VALID = 5
_IBI_REGULARITY_THRESHOLD = 0.25
_BEAT_COVERAGE_THRESHOLD = 0.60
_BEAT_CONFIDENCE_MEDIAN_FLOOR = 0.30
_METER_3_OVER_4_BIAS = 1.3  # 3/4 must beat 4/4 by ≥this ratio to win


TempoPath = Literal["beat_track", "plp", "madmom"]


@dataclass
class BeatGrid:
    tempo: float
    beat_times: np.ndarray
    downbeat_times: np.ndarray
    duration: float
    # Editor research (across pro montage tutorials) consistently identifies
    # landing the kill-confirm on a BASS HIT as the #1 differentiator between
    # pro and amateur edits — generic beats include every subdivision; bass
    # hits are the accented downbeats that actually carry the track's weight.
    bass_onsets: np.ndarray = field(default_factory=lambda: np.array([]))
    # Per-beat confidence in [0, 1], aligned with beat_times. Older callers
    # that construct BeatGrid by hand get all-ones from __post_init__.
    beat_confidence: np.ndarray = field(default_factory=lambda: np.array([]))
    downbeat_confidence: np.ndarray = field(default_factory=lambda: np.array([]))
    _tempo_path: TempoPath = "beat_track"

    def __post_init__(self) -> None:
        self.beat_times = np.asarray(self.beat_times, dtype=float)
        self.downbeat_times = np.asarray(self.downbeat_times, dtype=float)
        self.bass_onsets = np.asarray(self.bass_onsets, dtype=float)
        self.beat_confidence = np.asarray(self.beat_confidence, dtype=float)
        self.downbeat_confidence = np.asarray(self.downbeat_confidence, dtype=float)
        if self.beat_confidence.size == 0 and self.beat_times.size > 0:
            self.beat_confidence = np.ones_like(self.beat_times)
        if self.downbeat_confidence.size == 0 and self.downbeat_times.size > 0:
            self.downbeat_confidence = np.ones_like(self.downbeat_times)

    def nearest_beat(self, t: float, max_dist_s: float | None = None) -> float:
        if self.beat_times.size == 0:
            return t
        idx = int(np.argmin(np.abs(self.beat_times - t)))
        cand = float(self.beat_times[idx])
        if max_dist_s is not None and abs(cand - t) > max_dist_s:
            return t
        return cand

    def nearest_downbeat(self, t: float, max_dist_s: float = 0.25) -> float:
        if self.downbeat_times.size == 0:
            return t
        idx = int(np.argmin(np.abs(self.downbeat_times - t)))
        cand = float(self.downbeat_times[idx])
        if abs(cand - t) > max_dist_s:
            return t
        return cand

    def nearest_bass_onset(self, t: float, max_dist_s: float = 0.35) -> float | None:
        """Snap to nearest bass onset within max_dist, else None."""
        if self.bass_onsets.size == 0:
            return None
        idx = int(np.argmin(np.abs(self.bass_onsets - t)))
        cand = float(self.bass_onsets[idx])
        if abs(cand - t) <= max_dist_s:
            return cand
        return None

    def beat_confidence_at(self, t: float, max_dist_s: float = 0.1) -> float:
        if self.beat_times.size == 0:
            return 0.0
        idx = int(np.argmin(np.abs(self.beat_times - t)))
        if abs(self.beat_times[idx] - t) > max_dist_s:
            return 0.0
        if idx < self.beat_confidence.size:
            return float(self.beat_confidence[idx])
        return 1.0

    def beats_in_window(self, start: float, end: float) -> np.ndarray:
        mask = (self.beat_times >= start) & (self.beat_times <= end)
        return self.beat_times[mask]

    def is_valid(self) -> bool:
        """Whether this beat grid looks like genuine music beats.

        librosa happily returns garbage when run against speech / silence /
        non-musical audio. For music-optional mode we need to know whether
        to trust the result or fall back to moment-boundary placement.

        Hard checks (any failure → invalid):
          - tempo in [60, 200]
          - at least 5 beats

        Soft checks (all three must fail simultaneously → invalid):
          - inter-beat-interval std/mean < 0.25 (regularity)
          - beat coverage > 0.60 of duration
          - median beat confidence ≥ 0.30

        A single soft fail is tolerated (quiet outro, intro pickup, etc.).
        """
        if not (_TEMPO_RANGE[0] <= self.tempo <= _TEMPO_RANGE[1]):
            return False
        if self.beat_times.size < _MIN_BEATS_FOR_VALID:
            return False
        ibi = np.diff(self.beat_times)
        if ibi.size == 0:
            return False
        regularity = float(ibi.std() / max(ibi.mean(), 1e-6))
        coverage = float(
            (self.beat_times[-1] - self.beat_times[0]) / max(self.duration, 1e-6)
        )
        med_conf = (
            float(np.median(self.beat_confidence)) if self.beat_confidence.size else 1.0
        )
        soft_fails = sum([
            regularity > _IBI_REGULARITY_THRESHOLD,
            coverage < _BEAT_COVERAGE_THRESHOLD,
            med_conf < _BEAT_CONFIDENCE_MEDIAN_FLOOR,
        ])
        return soft_fails < 3


# Stages ---------------------------------------------------------------------


def _percussive_onset_envelope(y: np.ndarray, sr: int, duration: float) -> np.ndarray:
    """HPSS-cleaned percussive onset envelope.

    HPSS on a 4-min song adds 2-5s; on a 10-min auto-clip extracted-audio
    track it scales linearly. Skip HPSS on long tracks — onset_strength on
    the raw signal is still good enough for tempo tracking when HPSS would
    dominate runtime.
    """
    if duration <= _HPSS_FULL_TRACK_CAP_S:
        try:
            _, y_perc = librosa.effects.hpss(y, margin=(1.0, 5.0))
        except Exception:
            y_perc = y
        return librosa.onset.onset_strength(y=y_perc, sr=sr, hop_length=_HOP)
    return librosa.onset.onset_strength(y=y, sr=sr, hop_length=_HOP)


def _window_tempo(onset_env_chunk: np.ndarray, sr: int) -> float:
    try:
        arr = librosa.feature.tempo(
            onset_envelope=onset_env_chunk, sr=sr, hop_length=_HOP,
        )
    except Exception:
        try:
            arr = librosa.beat.tempo(
                onset_envelope=onset_env_chunk, sr=sr, hop_length=_HOP,
            )
        except Exception:
            return 0.0
    if arr is None or np.size(arr) == 0:
        return 0.0
    return float(np.asarray(arr).reshape(-1)[0])


def _per_window_tempos(onset_env: np.ndarray, sr: int, window_s: float = 10.0) -> np.ndarray:
    """Estimate tempo per ~10s window. Used to detect tempo-variable tracks."""
    frames_per_window = max(1, int(window_s * sr / _HOP))
    n_windows = max(1, onset_env.size // frames_per_window)
    out: list[float] = []
    for i in range(n_windows):
        chunk = onset_env[i * frames_per_window:(i + 1) * frames_per_window]
        if chunk.size < 8:
            continue
        t = _window_tempo(chunk, sr)
        if t > 0:
            out.append(t)
    return np.asarray(out, dtype=float)


def _octave_correct(tempo: float, onset_env: np.ndarray, sr: int) -> float:
    """Pick the best multiple of `tempo` from {0.5, 1, 2}.

    Score each candidate by autocorrelation of the onset envelope at the
    candidate's period. Tie-break toward librosa's reliable 80-160 BPM
    sweet spot. Kills the classic 75→150 and 180→90 octave errors on
    drum-heavy material.
    """
    if tempo <= 0 or onset_env.size < 16:
        return tempo
    frames_per_sec = sr / _HOP
    env = onset_env - onset_env.mean()
    ac = np.correlate(env, env, mode="full")
    ac = ac[ac.size // 2:]
    ac_norm = ac / max(ac[0], 1e-9)

    best_score = -np.inf
    best_tempo = tempo
    for mult in _OCTAVE_CANDIDATES:
        cand = tempo * mult
        if not (_TEMPO_RANGE[0] <= cand <= _TEMPO_RANGE[1]):
            continue
        period_frames = int(round(60.0 / cand * frames_per_sec))
        if period_frames <= 0 or period_frames >= ac_norm.size:
            continue
        lo = max(0, period_frames - 2)
        hi = min(ac_norm.size, period_frames + 3)
        ac_score = float(ac_norm[lo:hi].max())
        sweet_bonus = 0.05 if 80 <= cand <= 160 else 0.0
        score = ac_score + sweet_bonus
        if score > best_score:
            best_score = score
            best_tempo = cand
    return float(best_tempo)


def _beats_from_plp(onset_env: np.ndarray, sr: int) -> np.ndarray:
    """Beat times from a predominant-local-pulse curve (variable tempo).

    Follows the librosa-docs canonical pattern: local maxima of the PLP
    curve are beat candidates. We add a magnitude floor (PLP-normalized
    value > 0.4) to suppress weak peaks during quieter sections.
    """
    try:
        plp = librosa.beat.plp(onset_envelope=onset_env, sr=sr, hop_length=_HOP)
    except Exception:
        return np.asarray([])
    if plp.size == 0:
        return np.asarray([])
    pmax = float(plp.max())
    if pmax <= 0:
        return np.asarray([])
    plp_norm = plp / pmax
    localmax = librosa.util.localmax(plp_norm)
    mask = localmax & (plp_norm > 0.4)
    peaks = np.flatnonzero(mask)
    if peaks.size == 0:
        return np.asarray([])
    return librosa.frames_to_time(peaks, sr=sr, hop_length=_HOP)


def _onset_in_band(y: np.ndarray, sr: int, fmin: int, fmax: int) -> np.ndarray:
    mel = librosa.feature.melspectrogram(
        y=y, sr=sr, hop_length=_HOP, n_mels=16, fmin=fmin, fmax=fmax,
    )
    log_mel = librosa.power_to_db(mel, ref=np.max)
    return librosa.onset.onset_strength(
        S=log_mel, sr=sr, hop_length=_HOP, aggregate=np.median,
    )


def _peak_pick_onsets(onset_env: np.ndarray, sr: int, min_gap_s: float) -> np.ndarray:
    if onset_env.size == 0:
        return np.asarray([])
    std = float(onset_env.std())
    peaks = librosa.util.peak_pick(
        onset_env,
        pre_max=10, post_max=10,
        pre_avg=25, post_avg=25,
        delta=max(0.15, std * 0.6),
        wait=int(min_gap_s * sr / _HOP),
    )
    if peaks.size == 0:
        return np.asarray([])
    return librosa.frames_to_time(peaks, sr=sr, hop_length=_HOP)


def _detect_kick_onsets(y: np.ndarray, sr: int) -> np.ndarray:
    """Sub-bass kick onsets (40-150 Hz). Replaces the old 60-250 Hz detector."""
    env = _onset_in_band(y, sr, *_BASS_BAND_HZ)
    return _peak_pick_onsets(env, sr, _KICK_MIN_GAP_S)


def _detect_accent_onsets(y: np.ndarray, sr: int) -> np.ndarray:
    """Snare/clap accents (150-250 Hz). Fallback for kick-anemic tracks."""
    env = _onset_in_band(y, sr, *_ACCENT_BAND_HZ)
    return _peak_pick_onsets(env, sr, _ACCENT_MIN_GAP_S)


def _select_bass_onsets(
    kick_onsets: np.ndarray, accent_onsets: np.ndarray, duration: float,
) -> np.ndarray:
    """Use kicks if the track has them; fall back to accent band for snare-driven music."""
    if duration <= 0:
        return kick_onsets
    kick_rate = kick_onsets.size / duration
    accent_rate = accent_onsets.size / duration
    if kick_rate < 0.5 and accent_rate > kick_rate * 1.5:
        return accent_onsets
    return kick_onsets


def _filter_bass_onsets_by_beat_grid(
    bass_onsets: np.ndarray, beat_times: np.ndarray,
) -> np.ndarray:
    """Keep only bass onsets within ±70 ms of a beat.

    Drops sub-bass rumble / handle-noise transients that survive peak
    picking but aren't musical. The peak-pick threshold already gates on
    magnitude, so genuinely strong off-grid kicks (intro fills, pickups)
    still pass this filter if they're close enough to a beat — we just
    trim the long tail of spurious low-frequency events.
    """
    if bass_onsets.size == 0 or beat_times.size == 0:
        return bass_onsets
    kept: list[float] = []
    for t in bass_onsets:
        idx = int(np.argmin(np.abs(beat_times - t)))
        if abs(beat_times[idx] - t) <= _BASS_BEAT_PROXIMITY_S:
            kept.append(float(t))
    return np.asarray(kept, dtype=float)


def _downbeat_score_per_beat(
    beat_times: np.ndarray, bass_onsets: np.ndarray,
) -> np.ndarray:
    """Score each beat by whether a bass onset lands within ±50 ms."""
    if beat_times.size == 0:
        return np.zeros(0)
    if bass_onsets.size == 0:
        return np.zeros(beat_times.size)
    out = np.zeros(beat_times.size)
    for i, t in enumerate(beat_times):
        idx = int(np.argmin(np.abs(bass_onsets - t)))
        if abs(bass_onsets[idx] - t) <= _DOWNBEAT_WINDOW_S:
            out[i] = 1.0
    return out


def _pick_downbeats(
    beat_times: np.ndarray, scores: np.ndarray,
) -> tuple[np.ndarray, int, int]:
    """Pick downbeats by testing 4/4 and 3/4 phase hypotheses.

    For each meter M, try every offset in [0, M). Sum the bass-score at
    `beat_times[offset::M]`. Pick the meter+offset with the highest sum.
    Prefer 3/4 only if it beats 4/4 by ≥1.3× (waltz/folk material). On
    weak signal both sums are 0; default to 4/4 offset 0.

    Returns (downbeat_times, meter, offset).
    """
    if beat_times.size < 4:
        return beat_times[:1] if beat_times.size else np.asarray([]), 4, 0

    def best_for_meter(meter: int) -> tuple[int, float]:
        best_off, best_sum = 0, -np.inf
        for off in range(meter):
            s = float(scores[off::meter].sum())
            if s > best_sum:
                best_sum, best_off = s, off
        return best_off, best_sum

    off4, sum4 = best_for_meter(4)
    off3, sum3 = best_for_meter(3)
    if sum3 > sum4 * _METER_3_OVER_4_BIAS and sum3 > 0:
        meter, offset = 3, off3
    else:
        meter, offset = 4, off4
    return beat_times[offset::meter], meter, offset


def _beat_confidence(
    beat_times: np.ndarray, onset_env: np.ndarray, sr: int,
    bass_onsets: np.ndarray,
) -> np.ndarray:
    """Blend normalized onset strength (60%) and bass-proximity (40%) per beat."""
    if beat_times.size == 0:
        return np.zeros(0)
    frames = librosa.time_to_frames(beat_times, sr=sr, hop_length=_HOP)
    frames = np.clip(frames, 0, max(onset_env.size - 1, 0))
    if onset_env.size == 0:
        strength = np.zeros(beat_times.size)
    else:
        strength = onset_env[frames]
    s_max = float(strength.max()) if strength.size else 0.0
    strength_norm = strength / s_max if s_max > 0 else np.zeros_like(strength)

    bass_score = np.zeros(beat_times.size)
    if bass_onsets.size > 0:
        for i, t in enumerate(beat_times):
            idx = int(np.argmin(np.abs(bass_onsets - t)))
            dist = abs(bass_onsets[idx] - t)
            bass_score[i] = max(0.0, 1.0 - dist / 0.20)

    conf = 0.6 * strength_norm + 0.4 * bass_score
    return np.clip(conf, 0.0, 1.0)


# Orchestrator ---------------------------------------------------------------


def _detect_beats_librosa(music_path: str | Path, sr: int = 22050) -> BeatGrid:
    y, sr = librosa.load(str(music_path), sr=sr, mono=True)
    duration = float(len(y) / sr)

    onset_env = _percussive_onset_envelope(y, sr, duration)

    win_tempos = _per_window_tempos(onset_env, sr)
    use_plp = False
    if win_tempos.size >= 2:
        mean = float(win_tempos.mean())
        std = float(win_tempos.std())
        if mean > 0 and std / mean > _TEMPO_INSTABILITY_RATIO:
            use_plp = True

    tempo_path: TempoPath = "beat_track"
    beat_times: np.ndarray
    tempo: float

    if use_plp:
        plp_beats = _beats_from_plp(onset_env, sr)
        if plp_beats.size >= _MIN_BEATS_FOR_VALID:
            tempo_path = "plp"
            beat_times = plp_beats
            ibi = np.diff(beat_times)
            tempo = float(60.0 / max(np.median(ibi), 1e-6))
        else:
            use_plp = False

    if not use_plp:
        tempo_raw, beat_frames = librosa.beat.beat_track(
            onset_envelope=onset_env, sr=sr, hop_length=_HOP, units="frames",
        )
        tempo_scalar = (
            float(np.asarray(tempo_raw).reshape(-1)[0]) if np.size(tempo_raw) else 0.0
        )
        tempo = _octave_correct(tempo_scalar, onset_env, sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=_HOP)

    kick_onsets = _detect_kick_onsets(y, sr)
    accent_onsets = _detect_accent_onsets(y, sr)
    bass_onsets = _select_bass_onsets(kick_onsets, accent_onsets, duration)
    bass_onsets = _filter_bass_onsets_by_beat_grid(bass_onsets, beat_times)

    db_scores = _downbeat_score_per_beat(beat_times, bass_onsets)
    downbeat_times, _meter, _offset = _pick_downbeats(beat_times, db_scores)

    beat_conf = _beat_confidence(beat_times, onset_env, sr, bass_onsets)
    if downbeat_times.size > 0 and beat_times.size > 0 and beat_conf.size > 0:
        db_idx = [int(np.argmin(np.abs(beat_times - t))) for t in downbeat_times]
        downbeat_conf = beat_conf[db_idx]
    else:
        downbeat_conf = np.array([])

    return BeatGrid(
        tempo=float(tempo),
        beat_times=beat_times,
        downbeat_times=downbeat_times,
        duration=duration,
        bass_onsets=bass_onsets,
        beat_confidence=beat_conf,
        downbeat_confidence=downbeat_conf,
        _tempo_path=tempo_path,
    )


def detect_beats(
    music_path: str | Path,
    sr: int = 22050,
    *,
    force_librosa: bool = False,
) -> BeatGrid:
    """Detect beats, downbeats, and bass onsets in a music file.

    Tries madmom first (if installed and `force_librosa=False`); falls back
    to the librosa path on any failure. The librosa path is always
    available and itself substantially better than naive beat_track.
    """
    if not force_librosa:
        try:
            from . import beats_madmom
        except ImportError:
            beats_madmom = None  # type: ignore[assignment]
        if beats_madmom is not None:
            try:
                return beats_madmom.detect_beats_madmom(music_path, sr=sr)
            except Exception as exc:
                logger.warning(
                    "madmom beat detection failed (%s) — falling back to librosa", exc,
                )
    return _detect_beats_librosa(music_path, sr=sr)
