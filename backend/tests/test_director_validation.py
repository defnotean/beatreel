"""Structural validation on DirectedReel + cross-ref logic in _director_to_cuts."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from beatreel.director import DirectedCut, DirectedReel
from beatreel.gemini_detector import ClipAnalysis, Kill, Reaction
from beatreel.pipeline import _director_to_cuts


# ── Pydantic structural validation ─────────────────────────────────────

def _mk_cut(**kw) -> DirectedCut:
    base = dict(
        clip_index=0,
        clip_start_seconds=0.0,
        clip_end_seconds=2.0,
        music_start_seconds=0.0,
        emphasis="normal",
        reason="test",
    )
    base.update(kw)
    return DirectedCut(**base)


def _mk_reel(cuts: list[DirectedCut], **kw) -> DirectedReel:
    base = dict(
        chosen_intensity="balanced",
        cuts=cuts,
    )
    base.update(kw)
    return DirectedReel(**base)


class TestDirectedReelValidators:
    def test_valid_two_cut_reel(self):
        reel = _mk_reel([
            _mk_cut(clip_index=0, clip_start_seconds=1.0, clip_end_seconds=3.0, music_start_seconds=0.0),
            _mk_cut(clip_index=1, clip_start_seconds=2.0, clip_end_seconds=4.0, music_start_seconds=2.0),
        ])
        assert len(reel.cuts) == 2

    def test_empty_cuts_allowed(self):
        # Director might legitimately produce 0 cuts if clips are all bad — pipeline handles it.
        reel = _mk_reel([])
        assert reel.cuts == []

    def test_rejects_inverted_clip_window(self):
        with pytest.raises(ValidationError, match="clip_end_seconds"):
            _mk_reel([_mk_cut(clip_start_seconds=3.0, clip_end_seconds=2.0)])

    def test_rejects_sub_floor_duration(self):
        with pytest.raises(ValidationError, match="below 0.5s floor"):
            _mk_reel([_mk_cut(clip_start_seconds=1.0, clip_end_seconds=1.3)])

    def test_rejects_overlapping_music_windows(self):
        # Cut 0 takes music [0.0, 2.0); cut 1 starts at music 1.0 — overlap.
        with pytest.raises(ValidationError, match="overlaps"):
            _mk_reel([
                _mk_cut(clip_index=0, clip_start_seconds=0.0, clip_end_seconds=2.0, music_start_seconds=0.0),
                _mk_cut(clip_index=1, clip_start_seconds=0.0, clip_end_seconds=2.0, music_start_seconds=1.0),
            ])

    def test_rejects_reused_clip_window(self):
        with pytest.raises(ValidationError, match="reuses clip"):
            _mk_reel([
                _mk_cut(clip_index=2, clip_start_seconds=4.0, clip_end_seconds=6.0, music_start_seconds=0.0),
                _mk_cut(clip_index=2, clip_start_seconds=4.0, clip_end_seconds=6.0, music_start_seconds=2.0),
            ])

    def test_rejects_negative_intro_hold(self):
        with pytest.raises(ValidationError, match="intro/outro_hold"):
            _mk_reel([_mk_cut()], intro_hold_seconds=-0.5)

    def test_rejects_caption_overrunning_cut(self):
        with pytest.raises(ValidationError, match="overruns"):
            _mk_reel([
                _mk_cut(
                    clip_start_seconds=0.0, clip_end_seconds=2.0,
                    caption="TEST", caption_start_relative=1.5, caption_duration=2.0,
                ),
            ])


# ── _director_to_cuts cross-referential checks ─────────────────────────

class TestDirectorToCuts:
    @pytest.fixture
    def clips(self, tmp_path):
        clip_a = tmp_path / "a.mp4"; clip_a.write_bytes(b"")
        clip_b = tmp_path / "b.mp4"; clip_b.write_bytes(b"")
        return [clip_a, clip_b]

    @pytest.fixture
    def durations(self, clips):
        return {clips[0]: 10.0, clips[1]: 8.0}

    def _analyses_with_reaction(self, clips, clip_idx: int, reaction_t: float):
        return {
            clips[clip_idx]: ClipAnalysis(
                kills=[Kill(timestamp_seconds=reaction_t, confidence=0.9, description="kill")],
                reactions=[Reaction(
                    timestamp_seconds=reaction_t,
                    duration_seconds=1.5,
                    caption="HOLY",
                    kind="voice_comm",
                )],
            ),
        }

    def _analyses_no_reactions(self, clips, clip_idx: int):
        return {
            clips[clip_idx]: ClipAnalysis(
                kills=[Kill(timestamp_seconds=2.0, confidence=0.9, description="kill")],
                reactions=[],
            ),
        }

    def test_out_of_bounds_clip_index_is_dropped(self, clips, durations):
        reel = _mk_reel([
            _mk_cut(clip_index=0, clip_start_seconds=1.0, clip_end_seconds=3.0, music_start_seconds=0.0),
            _mk_cut(clip_index=99, clip_start_seconds=1.0, clip_end_seconds=3.0, music_start_seconds=2.0),
        ])
        result = _director_to_cuts(
            reel, clips, durations,
            self._analyses_no_reactions(clips, 0),
        )
        assert len(result) == 1
        assert result[0].clip_path == clips[0]

    def test_clip_end_clamped_to_true_duration(self, clips, durations):
        # Director claims the clip is 15s long but ffprobe says 10s
        reel = _mk_reel([
            _mk_cut(clip_index=0, clip_start_seconds=5.0, clip_end_seconds=15.0, music_start_seconds=0.0),
        ])
        result = _director_to_cuts(reel, clips, durations, self._analyses_no_reactions(clips, 0))
        assert len(result) == 1
        assert result[0].duration == pytest.approx(5.0)  # 10.0 - 5.0

    def test_fabricated_caption_dropped_when_no_reaction_nearby(self, clips, durations):
        # Cut window is 1.0-3.0s, reaction is at 7.0s — too far
        reel = _mk_reel([
            _mk_cut(
                clip_index=0, clip_start_seconds=1.0, clip_end_seconds=3.0, music_start_seconds=0.0,
                caption="HOLY SHIT", caption_start_relative=0.2, caption_duration=1.5,
            ),
        ])
        analyses = self._analyses_with_reaction(clips, 0, reaction_t=7.0)
        result = _director_to_cuts(reel, clips, durations, analyses)
        assert len(result) == 1
        assert result[0].caption is None, "caption should be dropped when no reaction is nearby"

    def test_caption_preserved_when_reaction_is_within_window(self, clips, durations):
        reel = _mk_reel([
            _mk_cut(
                clip_index=0, clip_start_seconds=1.0, clip_end_seconds=3.0, music_start_seconds=0.0,
                caption="HOLY SHIT", caption_start_relative=0.2, caption_duration=1.5,
            ),
        ])
        analyses = self._analyses_with_reaction(clips, 0, reaction_t=2.0)  # inside [1,3]
        result = _director_to_cuts(reel, clips, durations, analyses)
        assert len(result) == 1
        assert result[0].caption == "HOLY SHIT"

    def test_caption_preserved_when_reaction_is_just_past_window(self, clips, durations):
        # Reaction 0.5s after cut end — within 1s tolerance
        reel = _mk_reel([
            _mk_cut(
                clip_index=0, clip_start_seconds=1.0, clip_end_seconds=3.0, music_start_seconds=0.0,
                caption="WOW", caption_start_relative=0.5, caption_duration=1.0,
            ),
        ])
        analyses = self._analyses_with_reaction(clips, 0, reaction_t=3.6)
        result = _director_to_cuts(reel, clips, durations, analyses)
        assert result[0].caption == "WOW"

    def test_caption_on_clip_with_no_analysis_is_dropped(self, clips, durations):
        reel = _mk_reel([
            _mk_cut(
                clip_index=1, clip_start_seconds=1.0, clip_end_seconds=3.0, music_start_seconds=0.0,
                caption="NOPE", caption_start_relative=0.2, caption_duration=1.0,
            ),
        ])
        # Analyses only include clip[0], not clip[1]
        analyses = self._analyses_with_reaction(clips, 0, reaction_t=2.0)
        result = _director_to_cuts(reel, clips, durations, analyses)
        assert len(result) == 1
        assert result[0].caption is None
