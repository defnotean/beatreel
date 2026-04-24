"""Score clips by finding audio-energy peaks (highlight candidates)."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np
from scipy.signal import find_peaks


@dataclass
class Highlight:
    clip_path: Path
    peak_time: float  # seconds into the clip
    score: float  # peak prominence (higher = bigger audio event)
    clip_duration: float


def _probe_duration(path: Path) -> float:
    """Get duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def _extract_signals(clip_path: Path, sr: int = 22050) -> tuple[np.ndarray, float]:
    """Load clip audio and return a transient-weighted energy envelope + hop seconds.

    Spectral-flux onset strength is the primary signal — it lights up on sudden
    broadband energy increases (gunshots, hit-markers, impact reactions) instead
    of sustained loudness. RMS is blended in at a lower weight so long reactions
    (yells, explosions with a tail) still register. Both are max-normalized so
    they contribute on comparable scales regardless of overall clip loudness.
    """
    y, sr = librosa.load(str(clip_path), sr=sr, mono=True)
    hop_length = 512
    onset_env = librosa.onset.onset_strength(
        y=y, sr=sr, hop_length=hop_length, aggregate=np.median,
    )
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop_length)[0]
    n = min(len(onset_env), len(rms))
    onset_env = onset_env[:n]
    rms = rms[:n]

    def _norm(x: np.ndarray) -> np.ndarray:
        m = float(x.max())
        return x / m if m > 0 else x

    signal = 0.7 * _norm(onset_env) + 0.3 * _norm(rms)
    hop_seconds = hop_length / sr
    return signal, hop_seconds


def score_clip(clip_path: Path, min_separation_s: float = 1.5) -> list[Highlight]:
    """Find audio-spike highlights inside a single clip."""
    try:
        signal, hop_s = _extract_signals(clip_path)
    except Exception:
        return []

    if len(signal) == 0:
        return []

    duration = len(signal) * hop_s
    baseline = float(np.median(signal))
    spread = float(np.std(signal))
    prominence = max(spread * 0.6, baseline * 0.3, 1e-4)

    distance_frames = max(1, int(min_separation_s / hop_s))
    peaks, props = find_peaks(
        signal,
        prominence=prominence,
        distance=distance_frames,
    )

    highlights: list[Highlight] = []
    for idx, peak_idx in enumerate(peaks):
        peak_time = float(peak_idx * hop_s)
        # Trim highlights too close to clip edges — we need room to cut around them
        if peak_time < 0.5 or peak_time > duration - 0.5:
            continue
        highlights.append(
            Highlight(
                clip_path=clip_path,
                peak_time=peak_time,
                score=float(props["prominences"][idx]),
                clip_duration=duration,
            )
        )
    return highlights


def score_clips(clip_paths: list[Path], on_progress=None) -> list[Highlight]:
    """Score a batch of clips. on_progress(done, total, current_path) is optional."""
    all_highlights: list[Highlight] = []
    total = len(clip_paths)
    for i, path in enumerate(clip_paths):
        if on_progress:
            on_progress(i, total, path)
        all_highlights.extend(score_clip(path))
    if on_progress:
        on_progress(total, total, None)
    return all_highlights
