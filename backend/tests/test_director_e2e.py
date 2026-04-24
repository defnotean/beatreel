"""End-to-end pipeline test for the AI director path.

Monkeypatches every Gemini-facing function to return pinned payloads, then
runs the full `pipeline.run()` against synthetic clips + music. If the
pipeline wires analyzers, director, validators, and the renderer together
correctly, this test passes without a network call.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from beatreel import director as director_mod
from beatreel import gemini_detector
from beatreel import gemini_music_analyzer
from beatreel.gemini_detector import ClipAnalysis, Kill, Reaction
from beatreel.gemini_music_analyzer import MusicAnalysis, MusicSection, DropHit
from beatreel.director import DirectedReel, DirectedCut
from beatreel.pipeline import PipelineConfig, run


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip() or "0")


class TestDirectorE2E:
    def test_pipeline_happy_path_with_director(
        self, sample_clips_mp4, sample_music, tmp_path, monkeypatch,
    ):
        """Director produces a valid plan with captions → pipeline renders successfully."""
        clips_dir = sample_clips_mp4
        clips = sorted(clips_dir.glob("*.mp4"))
        assert len(clips) >= 3

        # Pin per-clip analyses. Kill + matching reaction so captions survive cross-ref.
        pinned_analyses = {
            p: ClipAnalysis(
                kills=[Kill(timestamp_seconds=2.0, confidence=0.95, description="test kill")],
                reactions=[Reaction(
                    timestamp_seconds=2.0, duration_seconds=1.2,
                    caption="HOLY", kind="voice_comm",
                )],
            )
            for p in clips
        }
        pinned_durations = {p: _probe_duration(p) for p in clips}

        def fake_analyze_clips_parallel(clip_paths, pool, on_progress=None):
            # Drive the progress callback once so we exercise it
            if on_progress:
                for i, p in enumerate(clip_paths):
                    on_progress(i, len(clip_paths), p)
                on_progress(len(clip_paths), len(clip_paths), None)
            return (
                {p: pinned_analyses[p] for p in clip_paths},
                {p: pinned_durations[p] for p in clip_paths},
                [],
            )

        def fake_analyze_music(path, api_key):
            return MusicAnalysis(
                vibe="hype",
                recommended_intensity="hype",
                tempo_bpm_estimated=120.0,
                sections=[
                    MusicSection(start_seconds=0, end_seconds=10, label="drop", energy=0.9, notes="main"),
                ],
                drops=[DropHit(timestamp_seconds=0.0, intensity=1.0, description="opener")],
                best_start_seconds=0.0,
            )

        def fake_direct_reel(*, music_analysis, clip_summaries, beats_seconds, tempo_bpm, target_duration, api_key):
            # Two cuts from two different clips, one with a valid caption.
            return DirectedReel(
                chosen_intensity="hype",
                intro_hold_seconds=0.0,
                outro_hold_seconds=0.6,
                title_caption=None,
                cuts=[
                    DirectedCut(
                        clip_index=0, clip_start_seconds=1.0, clip_end_seconds=3.0,
                        music_start_seconds=0.0,
                        caption="HOLY", caption_start_relative=0.3, caption_duration=1.2,
                        emphasis="drop_hit", reason="opener",
                    ),
                    DirectedCut(
                        clip_index=1, clip_start_seconds=0.5, clip_end_seconds=2.5,
                        music_start_seconds=2.0,
                        caption=None, caption_start_relative=None, caption_duration=None,
                        emphasis="normal", reason="follow-up",
                    ),
                ],
            )

        monkeypatch.setattr(gemini_detector, "analyze_clips_parallel", fake_analyze_clips_parallel)
        monkeypatch.setattr(gemini_music_analyzer, "analyze_music", fake_analyze_music)
        monkeypatch.setattr(director_mod, "direct_reel", fake_direct_reel)

        output_path = tmp_path / "reel.mp4"
        cfg = PipelineConfig(
            clips_dir=clips_dir,
            music_path=sample_music,
            output_path=output_path,
            target_duration=10.0,
            intensity="auto",
            aspect="landscape",
            game="valorant_ai",
            gemini_api_keys=["test-key-1", "test-key-2"],
        )

        progress_calls: list[tuple[str, float]] = []
        result = run(cfg, on_progress=lambda s, f: progress_calls.append((s, f)))

        # ── Assertions ────────────────────────────────────────────────
        assert output_path.exists()
        assert output_path.stat().st_size > 10_000  # real file, not empty
        assert result.detector_used == "director"
        assert result.num_cuts == 2
        assert result.clips_analyzed == len(clips)
        assert result.clips_failed == 0
        assert result.captions_placed == 1

        # debug.json must have been written next to the output
        debug_path = output_path.parent / "debug.json"
        assert debug_path.exists()
        debug = json.loads(debug_path.read_text())
        assert debug["detector_used"] == "director"
        assert debug["director_output"]["chosen_intensity"] == "hype"
        assert debug["music_analysis"]["vibe"] == "hype"

    def test_pipeline_fails_loud_when_all_clip_analyses_fail(
        self, sample_clips_mp4, sample_music, tmp_path, monkeypatch,
    ):
        """No clips analyzed + valorant_ai mode → raise, don't silently fall back."""
        def fake_analyze_all_fail(clip_paths, pool, on_progress=None):
            return ({}, {}, [(p, "API key not valid") for p in clip_paths])

        monkeypatch.setattr(gemini_detector, "analyze_clips_parallel", fake_analyze_all_fail)
        # Music doesn't matter — the pipeline should fail before getting there.

        output_path = tmp_path / "reel.mp4"
        cfg = PipelineConfig(
            clips_dir=sample_clips_mp4,
            music_path=sample_music,
            output_path=output_path,
            target_duration=10.0,
            intensity="auto",
            game="valorant_ai",
            gemini_api_keys=["bad-key"],
        )

        with pytest.raises(RuntimeError, match="Valorant AI detection failed"):
            run(cfg)

        assert not output_path.exists()
        # debug.json still gets written so the user can see what failed
        assert (output_path.parent / "debug.json").exists()

    def test_pipeline_falls_back_to_greedy_when_director_fails(
        self, sample_clips_mp4, sample_music, tmp_path, monkeypatch,
    ):
        """Clip analyses succeed, director throws → use AI kills with greedy planner."""
        clips = sorted(sample_clips_mp4.glob("*.mp4"))
        pinned_analyses = {
            p: ClipAnalysis(
                kills=[Kill(timestamp_seconds=2.0, confidence=0.9, description="kill")],
                reactions=[],
            )
            for p in clips
        }
        pinned_durations = {p: _probe_duration(p) for p in clips}

        def fake_analyses(clip_paths, pool, on_progress=None):
            return (
                {p: pinned_analyses[p] for p in clip_paths},
                {p: pinned_durations[p] for p in clip_paths},
                [],
            )

        def fake_music(path, api_key):
            return MusicAnalysis(
                vibe="balanced", recommended_intensity="balanced",
                tempo_bpm_estimated=120.0,
                sections=[MusicSection(start_seconds=0, end_seconds=10, label="verse", energy=0.5, notes="")],
                drops=[],
                best_start_seconds=0.0,
            )

        def fake_director_raises(**kw):
            raise RuntimeError("director API down")

        monkeypatch.setattr(gemini_detector, "analyze_clips_parallel", fake_analyses)
        monkeypatch.setattr(gemini_music_analyzer, "analyze_music", fake_music)
        monkeypatch.setattr(director_mod, "direct_reel", fake_director_raises)

        output_path = tmp_path / "reel.mp4"
        cfg = PipelineConfig(
            clips_dir=sample_clips_mp4,
            music_path=sample_music,
            output_path=output_path,
            target_duration=10.0,
            intensity="balanced",
            game="valorant_ai",
            gemini_api_keys=["k1"],
        )
        result = run(cfg)

        assert output_path.exists()
        assert result.detector_used == "ai-greedy-fallback"
        assert result.num_cuts >= 1
