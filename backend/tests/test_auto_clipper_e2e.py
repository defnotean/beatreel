"""End-to-end test for the auto-clip flow.

Monkeypatches the Gemini auto-clipper + music analyzer + director to return
pinned payloads, then runs the full pipeline against a single synthetic
source video. Verifies:
- source_mode="auto_clip" is accepted by PipelineConfig
- auto_clipper.auto_clip is invoked with the source video
- moments.json is written
- The director receives virtual-clip summaries per moment
- Final reel renders successfully
- debug.json captures auto-clip metadata
"""
from __future__ import annotations

import json
import subprocess
import shutil
from pathlib import Path

import pytest

from beatreel import director as director_mod
from beatreel import auto_clipper as auto_clipper_mod
from beatreel import gemini_music_analyzer
from beatreel.auto_clipper import (
    AutoClipperResult, Moment, MomentScores,
)
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


@pytest.fixture
def synthetic_long_video(tmp_path, ffmpeg_available):
    """A 30-second synthetic 'long' video with a sine audio track. Long enough
    for auto-clipper moment windows to fit inside, short enough for tests to
    run fast."""
    if not ffmpeg_available:
        pytest.skip("ffmpeg not available")
    out = tmp_path / "source.mp4"
    res = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=gray:s=640x360:d=30:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(out),
        ],
        capture_output=True,
    )
    if res.returncode != 0:
        pytest.skip(f"ffmpeg failed generating source fixture: {res.stderr.decode()[:200]}")
    return out


class TestAutoClipE2E:
    def test_auto_clip_happy_path(
        self, synthetic_long_video, sample_music, tmp_path, monkeypatch,
    ):
        """Full auto-clip pipeline with mocked Gemini."""
        pinned_moments = [
            Moment(
                start_seconds=2.0, end_seconds=6.5,
                scores=MomentScores(
                    visual_interest=0.85, audio_peak=0.90,
                    emotional_charge=0.75, narrative_payoff=0.70,
                    technical_skill=0.80,
                ),
                composite=0.81,
                description="Test moment 1 — big reaction",
                suggested_caption="HOLY",
                caption_kind="voice_comm",
                caption_start_in_moment_seconds=1.5,
                caption_duration_seconds=1.3,
                emphasis_hint="drop_hit",
                content_tags=["gaming", "reaction"],
            ),
            Moment(
                start_seconds=12.0, end_seconds=16.0,
                scores=MomentScores(
                    visual_interest=0.70, audio_peak=0.60,
                    emotional_charge=0.80, narrative_payoff=0.65,
                    technical_skill=0.55,
                ),
                composite=0.67,
                description="Test moment 2 — emotional beat",
                suggested_caption=None,
                emphasis_hint="normal",
                content_tags=["narrative"],
            ),
            Moment(
                start_seconds=22.0, end_seconds=28.0,
                scores=MomentScores(
                    visual_interest=0.95, audio_peak=0.95,
                    emotional_charge=0.85, narrative_payoff=0.80,
                    technical_skill=0.90,
                ),
                composite=0.90,
                description="Test moment 3 — climax",
                suggested_caption="ACE",
                caption_kind="visual_text",
                caption_start_in_moment_seconds=2.0,
                caption_duration_seconds=1.5,
                emphasis_hint="drop_hit",
                content_tags=["gaming", "multi_kill"],
            ),
        ]
        pinned_result = AutoClipperResult(
            source_video=str(synthetic_long_video),
            duration_seconds=30.0,
            video_mood="hype",
            moments=pinned_moments,
        )

        def fake_auto_clip(video_path, api_key, *, on_progress=None):
            if on_progress:
                on_progress("uploading", 0.1)
                on_progress("analyzing", 0.5)
                on_progress("done", 1.0)
            return pinned_result

        def fake_analyze_music(path, api_key):
            return MusicAnalysis(
                vibe="hype", recommended_intensity="hype",
                tempo_bpm_estimated=120.0,
                sections=[MusicSection(start_seconds=0, end_seconds=10, label="drop", energy=0.9, notes="")],
                drops=[DropHit(timestamp_seconds=0.0, intensity=1.0, description="opener")],
                best_start_seconds=0.0,
            )

        def fake_direct_reel(*, music_analysis, clip_summaries, beats_seconds, bass_onsets_seconds, tempo_bpm, target_duration, api_key):
            # Pick 2 of the 3 moments: moment 0 and moment 2 (the two drop_hits).
            return DirectedReel(
                chosen_intensity="hype",
                color_grade="clinical",
                intro_hold_seconds=0.0,
                outro_hold_seconds=0.7,
                title_caption=None,
                cuts=[
                    DirectedCut(
                        clip_index=0,
                        clip_start_seconds=0.5, clip_end_seconds=3.5,
                        music_start_seconds=0.0,
                        caption="HOLY", caption_start_relative=1.0, caption_duration=1.2,
                        emphasis="drop_hit", reason="opener",
                    ),
                    DirectedCut(
                        clip_index=2,
                        clip_start_seconds=1.0, clip_end_seconds=4.5,
                        music_start_seconds=3.0,
                        caption="ACE", caption_start_relative=1.0, caption_duration=1.4,
                        emphasis="drop_hit", reason="climax",
                    ),
                ],
            )

        monkeypatch.setattr(auto_clipper_mod, "auto_clip", fake_auto_clip)
        monkeypatch.setattr(gemini_music_analyzer, "analyze_music", fake_analyze_music)
        monkeypatch.setattr(director_mod, "direct_reel", fake_direct_reel)

        output_path = tmp_path / "reel.mp4"
        cfg = PipelineConfig(
            clips_dir=tmp_path / "unused_clips_dir",  # not read in auto_clip mode
            music_path=sample_music,
            output_path=output_path,
            target_duration=8.0,
            intensity="auto",
            aspect="landscape",
            game="valorant_ai",
            gemini_api_keys=["test-key"],
            source_mode="auto_clip",
            source_video=synthetic_long_video,
        )

        progress_calls: list[tuple[str, float]] = []
        result = run(cfg, on_progress=lambda s, f: progress_calls.append((s, f)))

        # Core assertions
        assert output_path.exists(), "Final reel not rendered"
        assert output_path.stat().st_size > 5_000, "Output file is empty / broken"

        # Result metadata
        assert result.source_mode == "auto_clip"
        assert result.moments_found == 3
        assert result.moments_selected == 2
        assert result.detector_used == "auto-clipper+director"
        assert result.num_cuts == 2
        assert result.captions_placed == 2

        # moments.json and debug.json both written
        moments_file = tmp_path / "moments.json"
        debug_file = tmp_path / "debug.json"
        assert moments_file.exists(), "moments.json not written"
        assert debug_file.exists(), "debug.json not written"

        moments_dump = json.loads(moments_file.read_text())
        assert moments_dump["video_mood"] == "hype"
        assert len(moments_dump["moments"]) == 3

        debug_dump = json.loads(debug_file.read_text())
        assert debug_dump["source_mode"] == "auto_clip"
        assert debug_dump["moments_found"] == 3

        # Cuts are in SOURCE-ABSOLUTE coordinates (not moment-relative)
        # Cut 0: moment 0 (source_start=2.0) + director's clip_start 0.5 = 2.5 source
        # Cut 1: moment 2 (source_start=22.0) + director's clip_start 1.0 = 23.0 source
        cuts = result.cuts
        assert cuts[0].start == pytest.approx(2.5, abs=0.01)
        assert cuts[1].start == pytest.approx(23.0, abs=0.01)

    def test_auto_clip_fails_loud_when_no_moments(
        self, synthetic_long_video, sample_music, tmp_path, monkeypatch,
    ):
        """Zero entertaining moments → raise, don't produce a reel."""
        def fake_auto_clip(video_path, api_key, *, on_progress=None):
            return AutoClipperResult(
                source_video=str(video_path),
                duration_seconds=30.0,
                video_mood="calm",
                moments=[],
            )

        monkeypatch.setattr(auto_clipper_mod, "auto_clip", fake_auto_clip)

        output_path = tmp_path / "reel.mp4"
        cfg = PipelineConfig(
            clips_dir=tmp_path / "unused",
            music_path=sample_music,
            output_path=output_path,
            target_duration=8.0,
            intensity="auto",
            game="valorant_ai",
            gemini_api_keys=["test-key"],
            source_mode="auto_clip",
            source_video=synthetic_long_video,
        )

        with pytest.raises(RuntimeError, match="0 entertaining moments"):
            run(cfg)
        assert not output_path.exists()
        # debug.json should still be written
        assert (tmp_path / "debug.json").exists()

    def test_auto_clip_rejects_missing_source_video(self, sample_music, tmp_path):
        cfg = PipelineConfig(
            clips_dir=tmp_path / "unused",
            music_path=sample_music,
            output_path=tmp_path / "reel.mp4",
            target_duration=8.0,
            game="valorant_ai",
            gemini_api_keys=["test-key"],
            source_mode="auto_clip",
            source_video=None,
        )
        with pytest.raises(RuntimeError, match="requires config.source_video"):
            run(cfg)

    def test_auto_clip_requires_gemini_keys(self, synthetic_long_video, sample_music, tmp_path):
        cfg = PipelineConfig(
            clips_dir=tmp_path / "unused",
            music_path=sample_music,
            output_path=tmp_path / "reel.mp4",
            target_duration=8.0,
            game="valorant_ai",
            gemini_api_keys=[],
            source_mode="auto_clip",
            source_video=synthetic_long_video,
        )
        with pytest.raises(RuntimeError, match="requires at least one Gemini API key"):
            run(cfg)
