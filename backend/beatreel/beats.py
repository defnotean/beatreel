"""Beat detection for the music track."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np


@dataclass
class BeatGrid:
    tempo: float
    beat_times: np.ndarray  # seconds
    downbeat_times: np.ndarray  # seconds, every 4th beat
    duration: float

    def nearest_beat(self, t: float) -> float:
        if len(self.beat_times) == 0:
            return t
        idx = int(np.argmin(np.abs(self.beat_times - t)))
        return float(self.beat_times[idx])

    def beats_in_window(self, start: float, end: float) -> np.ndarray:
        mask = (self.beat_times >= start) & (self.beat_times <= end)
        return self.beat_times[mask]


def detect_beats(music_path: str | Path, sr: int = 22050) -> BeatGrid:
    y, sr = librosa.load(str(music_path), sr=sr, mono=True)
    duration = float(len(y) / sr)

    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)

    # librosa >=0.10 returns tempo as a 1-D ndarray; older versions a scalar.
    tempo_scalar = float(np.asarray(tempo).reshape(-1)[0]) if np.size(tempo) else 0.0

    downbeat_times = beat_times[::4] if len(beat_times) else beat_times

    return BeatGrid(
        tempo=tempo_scalar,
        beat_times=beat_times,
        downbeat_times=downbeat_times,
        duration=duration,
    )
