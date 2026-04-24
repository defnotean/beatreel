"""Tests for plan.json I/O and run_from_plan()."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from beatreel.pipeline import (
    PLAN_SCHEMA_VERSION, _cuts_to_plan_dict, run_from_plan,
)
from beatreel.render import CutPlan


class TestPlanSerialization:
    def test_cuts_to_plan_dict_roundtrip_shape(self, tmp_path):
        """Schema round-trips: serialized plan has the fields we expect and the
        segment count matches."""
        cuts = [
            CutPlan(
                clip_path=Path("/fake/clip_a.mp4"),
                start=1.5, duration=3.0,
                caption="ACE", caption_start_in_cut=0.5, caption_duration=1.2,
                emphasis="drop_hit",
            ),
            CutPlan(
                clip_path=Path("/fake/clip_b.mp4"),
                start=0.0, duration=2.5,
                emphasis="normal",
            ),
        ]
        plan = _cuts_to_plan_dict(
            cuts=cuts,
            music_path=Path("/fake/song.mp3"),
            tempo=128.0,
            render_opts={
                "intro_hold_seconds": 0.5,
                "outro_hold_seconds": 0.8,
                "title_caption": "VALORANT",
                "color_grade": "clinical",
            },
            aspect="landscape",
            target_duration=60.0,
            source_mode="clips",
        )
        assert plan["schema_version"] == PLAN_SCHEMA_VERSION
        assert plan["music"]["bpm"] == 128.0
        assert plan["music"]["path"].replace("\\", "/").endswith("/fake/song.mp3")
        assert plan["color_grade"] == "clinical"
        assert plan["title_caption"] == "VALORANT"
        assert len(plan["segments"]) == 2
        assert plan["segments"][0]["emphasis"] == "drop_hit"
        assert plan["segments"][0]["caption"] == "ACE"
        assert plan["segments"][1]["caption"] is None

    def test_plan_json_is_json_serializable(self, tmp_path):
        """The plan must survive json.dumps without custom encoders."""
        plan = _cuts_to_plan_dict(
            cuts=[CutPlan(clip_path=Path("/x/y.mp4"), start=0, duration=2, emphasis="hold")],
            music_path=Path("/music.mp3"),
            tempo=120.0,
            render_opts={},
            aspect="portrait",
            target_duration=30.0,
            source_mode="auto_clip",
        )
        json.dumps(plan)  # must not raise


class TestRunFromPlan:
    def test_rejects_wrong_schema_version(self, tmp_path):
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps({
            "schema_version": "0.9",
            "music": {"path": "whatever"},
            "segments": [],
        }))
        with pytest.raises(RuntimeError, match="schema version"):
            run_from_plan(plan_path)

    def test_rejects_missing_music_file(self, tmp_path):
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps({
            "schema_version": PLAN_SCHEMA_VERSION,
            "music": {"path": "/does/not/exist.mp3"},
            "segments": [],
        }))
        with pytest.raises(RuntimeError, match="Music file"):
            run_from_plan(plan_path)

    def test_rejects_empty_segments(self, tmp_path, sample_music):
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps({
            "schema_version": PLAN_SCHEMA_VERSION,
            "music": {"path": str(sample_music)},
            "segments": [],
        }))
        with pytest.raises(RuntimeError, match="no segments"):
            run_from_plan(plan_path)

    def test_rejects_missing_source_clip(self, tmp_path, sample_music):
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps({
            "schema_version": PLAN_SCHEMA_VERSION,
            "music": {"path": str(sample_music)},
            "aspect": "landscape",
            "target_duration": 10.0,
            "intro_hold_seconds": 0.0,
            "outro_hold_seconds": 0.0,
            "color_grade": None,
            "segments": [{
                "id": "seg_000",
                "source": "/bogus/clip.mp4",
                "source_start_seconds": 0,
                "source_end_seconds": 2,
                "duration_seconds": 2,
                "emphasis": "normal",
            }],
        }))
        with pytest.raises(RuntimeError, match="Source clip"):
            run_from_plan(plan_path)

    def test_happy_path_renders_from_plan(self, tmp_path, sample_music, sample_clips_mp4):
        clip_a = next(sample_clips_mp4.glob("*.mp4"))
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps({
            "schema_version": PLAN_SCHEMA_VERSION,
            "music": {"path": str(sample_music), "bpm": 120.0},
            "aspect": "landscape",
            "target_duration": 6.0,
            "intro_hold_seconds": 0.0,
            "outro_hold_seconds": 0.5,
            "color_grade": "teal_orange",
            "title_caption": None,
            "source_mode": "clips",
            "segments": [
                {
                    "id": "seg_000",
                    "source": str(clip_a),
                    "source_start_seconds": 1.0,
                    "source_end_seconds": 4.0,
                    "duration_seconds": 3.0,
                    "caption": None,
                    "caption_start_in_cut": None,
                    "caption_duration": None,
                    "emphasis": "normal",
                },
            ],
        }))
        output_path = tmp_path / "out.mp4"
        result = run_from_plan(plan_path, output_path=output_path)
        assert output_path.exists()
        assert output_path.stat().st_size > 5_000
        assert result.detector_used == "plan_json"
        assert result.num_cuts == 1
