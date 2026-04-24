from __future__ import annotations

from pathlib import Path

from beatreel.highlights import Highlight
from beatreel.scenes import boost_highlights_near_scenes, scene_detection_available


def _mk(peak: float, score: float = 1.0) -> Highlight:
    return Highlight(clip_path=Path("clip.mp4"), peak_time=peak, score=score, clip_duration=20.0)


def test_boost_is_no_op_without_scene_times():
    highlights = [_mk(1.0), _mk(5.0), _mk(10.0)]
    result = boost_highlights_near_scenes(highlights, [])
    assert [h.score for h in result] == [1.0, 1.0, 1.0]


def test_boost_raises_score_for_nearby_peaks_only():
    highlights = [_mk(1.0, 1.0), _mk(5.0, 1.0), _mk(10.0, 1.0)]
    scene_times = [5.3]
    result = boost_highlights_near_scenes(highlights, scene_times, window_seconds=0.6, boost_multiplier=1.5)
    # Only the peak near 5.0 should be boosted
    assert result[0].score == 1.0
    assert result[1].score == 1.5
    assert result[2].score == 1.0


def test_boost_window_boundary():
    highlights = [_mk(5.0, 1.0)]
    # Exactly at window edge
    assert boost_highlights_near_scenes(highlights, [5.6], window_seconds=0.6)[0].score == 1.5 * 0 + 1.0 * 1.4  # default multiplier
    # Just outside
    assert boost_highlights_near_scenes(highlights, [5.7], window_seconds=0.6)[0].score == 1.0


def test_scene_detection_availability_is_boolean():
    assert isinstance(scene_detection_available(), bool)
