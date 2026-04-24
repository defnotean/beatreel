"""Beat detection for the music track."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import librosa
import numpy as np


@dataclass
class BeatGrid:
    tempo: float
    beat_times: np.ndarray  # seconds — all beats detected by librosa
    downbeat_times: np.ndarray  # seconds, every 4th beat (approx)
    duration: float
    # Bass-onset timestamps detected via low-band-filtered onset strength.
    # Editor research (across pro montage tutorials) consistently identifies
    # landing the kill-confirm on a BASS HIT as the #1 differentiator between
    # pro and amateur edits — generic beats include every subdivision; bass
    # hits are the accented downbeats that actually carry the track's weight.
    bass_onsets: np.ndarray = field(default_factory=lambda: np.array([]))

    def nearest_beat(self, t: float) -> float:
        if len(self.beat_times) == 0:
            return t
        idx = int(np.argmin(np.abs(self.beat_times - t)))
        return float(self.beat_times[idx])

    def nearest_bass_onset(self, t: float, max_dist_s: float = 0.35) -> float | None:
        """Snap to nearest bass onset within max_dist, else None."""
        if len(self.bass_onsets) == 0:
            return None
        idx = int(np.argmin(np.abs(self.bass_onsets - t)))
        cand = float(self.bass_onsets[idx])
        if abs(cand - t) <= max_dist_s:
            return cand
        return None

    def beats_in_window(self, start: float, end: float) -> np.ndarray:
        mask = (self.beat_times >= start) & (self.beat_times <= end)
        return self.beat_times[mask]


def _detect_bass_onsets(y: np.ndarray, sr: int, hop_length: int = 512) -> np.ndarray:
    """Timestamps of peaks in low-band (60-250 Hz) onset strength.

    The technique: compute a mel-spectrogram restricted to the bass region,
    run onset-strength on that spectrogram, and pick peaks above an adaptive
    prominence threshold. Generic librosa.onset picks up high-frequency
    transients too (snares, hats) — we want the kick/808 hits only.
    """
    # Mel-spec in the bass band only.
    mel = librosa.feature.melspectrogram(
        y=y, sr=sr, hop_length=hop_length, n_mels=16, fmin=60, fmax=250,
    )
    log_mel = librosa.power_to_db(mel, ref=np.max)
    onset_env = librosa.onset.onset_strength(
        S=log_mel, sr=sr, hop_length=hop_length, aggregate=np.median,
    )
    if onset_env.size == 0:
        return np.array([])

    # Adaptive threshold: peaks must clear both absolute floor and local std.
    median = float(np.median(onset_env))
    std = float(onset_env.std())
    peaks = librosa.util.peak_pick(
        onset_env,
        pre_max=10, post_max=10,
        pre_avg=25, post_avg=25,
        delta=max(0.15, std * 0.6),
        wait=int(0.18 * sr / hop_length),  # at least 180ms between bass hits
    )
    if len(peaks) == 0:
        return np.array([])
    return librosa.frames_to_time(peaks, sr=sr, hop_length=hop_length)


def detect_beats(music_path: str | Path, sr: int = 22050) -> BeatGrid:
    y, sr = librosa.load(str(music_path), sr=sr, mono=True)
    duration = float(len(y) / sr)

    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)

    tempo_scalar = float(np.asarray(tempo).reshape(-1)[0]) if np.size(tempo) else 0.0

    downbeat_times = beat_times[::4] if len(beat_times) else beat_times
    bass_onsets = _detect_bass_onsets(y, sr)

    return BeatGrid(
        tempo=tempo_scalar,
        beat_times=beat_times,
        downbeat_times=downbeat_times,
        duration=duration,
        bass_onsets=bass_onsets,
    )
