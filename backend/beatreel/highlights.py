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


def _extract_audio_rms(clip_path: Path, sr: int = 22050) -> tuple[np.ndarray, float]:
    """Load clip audio and return RMS envelope + hop time in seconds per frame."""
    # librosa.load can read video containers via audioread/ffmpeg
    y, sr = librosa.load(str(clip_path), sr=sr, mono=True)
    hop_length = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop_length)[0]
    # Smooth to emphasize sustained loud moments over single frame spikes
    window = max(3, int(0.2 * sr / hop_length))
    if len(rms) > window:
        kernel = np.ones(window) / window
        rms = np.convolve(rms, kernel, mode="same")
    hop_seconds = hop_length / sr
    return rms, hop_seconds


def score_clip(clip_path: Path, min_separation_s: float = 2.0) -> list[Highlight]:
    """Find audio-spike highlights inside a single clip."""
    try:
        rms, hop_s = _extract_audio_rms(clip_path)
    except Exception:
        return []

    if len(rms) == 0:
        return []

    duration = len(rms) * hop_s
    # Adaptive prominence threshold: highlights must stand out from the clip's baseline
    baseline = float(np.median(rms))
    spread = float(np.std(rms))
    prominence = max(spread * 0.8, baseline * 0.4, 1e-4)

    distance_frames = max(1, int(min_separation_s / hop_s))
    peaks, props = find_peaks(
        rms,
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
