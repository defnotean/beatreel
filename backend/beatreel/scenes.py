"""Visual scene-change detection as a co-signal for highlights.

PySceneDetect is an optional dependency (pulls in OpenCV, which is heavy).
If it's not installed we return an empty list and the pipeline still works
using audio signals alone.
"""
from __future__ import annotations

from pathlib import Path

try:
    from scenedetect import detect, ContentDetector  # type: ignore
    _HAS_SCENEDETECT = True
except ImportError:
    _HAS_SCENEDETECT = False


def scene_detection_available() -> bool:
    return _HAS_SCENEDETECT


def detect_scene_changes(clip_path: Path, threshold: float = 27.0) -> list[float]:
    """Return scene boundary timestamps (seconds into the clip).

    Returns [] if PySceneDetect isn't installed, or on any failure. Callers
    should treat an empty list as "no scene info", not "no scene changes".
    """
    if not _HAS_SCENEDETECT:
        return []
    try:
        scene_list = detect(str(clip_path), ContentDetector(threshold=threshold))
    except Exception:
        return []
    # Each entry is (start, end) FrameTimecode tuple; we want the transition points
    return [float(s.get_seconds()) for s, _e in scene_list if s.get_seconds() > 0.0]


def boost_highlights_near_scenes(
    highlights,
    scene_times: list[float],
    window_seconds: float = 0.6,
    boost_multiplier: float = 1.4,
):
    """Boost the score of any highlight within `window_seconds` of a scene cut.

    Audio-spike + scene-change coincidence is a strong "moment" signal. Leaving
    both signals independent and combining via a multiplier keeps the ranking
    interpretable.
    """
    if not scene_times:
        return highlights
    boosted = []
    for h in highlights:
        near = any(abs(h.peak_time - s) <= window_seconds for s in scene_times)
        if near:
            boosted.append(
                h.__class__(
                    clip_path=h.clip_path,
                    peak_time=h.peak_time,
                    score=h.score * boost_multiplier,
                    clip_duration=h.clip_duration,
                )
            )
        else:
            boosted.append(h)
    return boosted
