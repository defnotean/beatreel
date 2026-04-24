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
        # Need >=3 moments per tier to pass min_moments=3 per-tier floor.
        # 9 non-overlapping moments across a 30s source: 3s each.
        pinned_moments = [
            # headline (>= 0.85)
            Moment(start_seconds=0.5, end_seconds=3.0,
                   scores=MomentScores(visual_interest=0.9, audio_peak=0.9, emotional_charge=0.9, narrative_payoff=0.85, technical_skill=0.9),
                   composite=0.89, description="headline 1",
                   suggested_caption="HOLY", caption_kind="voice_comm",
                   caption_start_in_moment_seconds=1.0, caption_duration_seconds=1.0,
                   emphasis_hint="drop_hit", content_tags=["top"],
                   meme_tag="clutch"),
            Moment(start_seconds=3.5, end_seconds=6.0,
                   scores=MomentScores(visual_interest=0.95, audio_peak=0.9, emotional_charge=0.85, narrative_payoff=0.85, technical_skill=0.9),
                   composite=0.90, description="headline 2",
                   emphasis_hint="drop_hit", content_tags=["top"]),
            Moment(start_seconds=6.5, end_seconds=9.0,
                   scores=MomentScores(visual_interest=0.9, audio_peak=0.95, emotional_charge=0.8, narrative_payoff=0.85, technical_skill=0.85),
                   composite=0.87, description="headline 3",
                   suggested_caption="ACE", caption_kind="visual_text",
                   caption_start_in_moment_seconds=1.0, caption_duration_seconds=1.0,
                   emphasis_hint="drop_hit", content_tags=["top"]),
            # bsides (0.70-0.85)
            Moment(start_seconds=9.5, end_seconds=12.0,
                   scores=MomentScores(visual_interest=0.8, audio_peak=0.8, emotional_charge=0.75, narrative_payoff=0.7, technical_skill=0.8),
                   composite=0.78, description="bsides 1",
                   emphasis_hint="hold", content_tags=["mid"]),
            Moment(start_seconds=12.5, end_seconds=15.0,
                   scores=MomentScores(visual_interest=0.75, audio_peak=0.8, emotional_charge=0.75, narrative_payoff=0.75, technical_skill=0.75),
                   composite=0.76, description="bsides 2",
                   emphasis_hint="normal", content_tags=["mid"]),
            Moment(start_seconds=15.5, end_seconds=18.0,
                   scores=MomentScores(visual_interest=0.75, audio_peak=0.75, emotional_charge=0.7, narrative_payoff=0.75, technical_skill=0.7),
                   composite=0.73, description="bsides 3",
                   emphasis_hint="normal", content_tags=["mid"]),
            # vibes (0.55-0.70)
            Moment(start_seconds=18.5, end_seconds=21.0,
                   scores=MomentScores(visual_interest=0.6, audio_peak=0.65, emotional_charge=0.7, narrative_payoff=0.65, technical_skill=0.6),
                   composite=0.64, description="vibes 1",
                   emphasis_hint="normal", content_tags=["low"]),
            Moment(start_seconds=21.5, end_seconds=24.0,
                   scores=MomentScores(visual_interest=0.55, audio_peak=0.6, emotional_charge=0.7, narrative_payoff=0.6, technical_skill=0.6),
                   composite=0.61, description="vibes 2",
                   emphasis_hint="normal", content_tags=["low"]),
            Moment(start_seconds=24.5, end_seconds=27.0,
                   scores=MomentScores(visual_interest=0.55, audio_peak=0.6, emotional_charge=0.6, narrative_payoff=0.6, technical_skill=0.55),
                   composite=0.58, description="vibes 3",
                   emphasis_hint="normal", content_tags=["low"]),
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
        assert output_path.exists(), "config.output_path (backcompat alias) not rendered"
        assert output_path.stat().st_size > 5_000

        # Multi-tier outputs: all three tiers should have rendered
        assert result.source_mode == "auto_clip"
        assert result.moments_found == 9
        assert len(result.outputs) == 3, f"expected 3 tier outputs, got {len(result.outputs)}"

        tier_names = [o.tier for o in result.outputs]
        assert "headline" in tier_names and "bsides" in tier_names and "vibes" in tier_names

        for out in result.outputs:
            assert out.path.exists(), f"tier {out.tier} not rendered"
            assert out.thumbnail_path.exists(), f"tier {out.tier} thumbnail missing"
            assert out.num_cuts > 0

        # moments.json + debug.json present
        assert (tmp_path / "moments.json").exists()
        assert (tmp_path / "debug.json").exists()

        moments_dump = json.loads((tmp_path / "moments.json").read_text())
        assert moments_dump["video_mood"] == "hype"
        assert len(moments_dump["moments"]) == 9
        # meme_tag on the first headline moment survived the JSON round-trip.
        tagged = [m for m in moments_dump["moments"] if m.get("meme_tag")]
        assert any(m["meme_tag"] == "clutch" for m in tagged), (
            f"expected meme_tag='clutch' in moments.json, got {[(m.get('description'), m.get('meme_tag')) for m in moments_dump['moments']]}"
        )

        debug_dump = json.loads((tmp_path / "debug.json").read_text())
        assert debug_dump["source_mode"] == "auto_clip"
        assert debug_dump["moments_found"] == 9

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

    def test_auto_clip_without_music_uses_source_audio(
        self, synthetic_long_video, tmp_path, monkeypatch,
    ):
        """Music-optional: when config.music_path is None, use the source
        video's own audio. Beat grid on that audio should be invalid (speech/
        silence), pipeline falls through to moment-boundary placement."""
        # 3 moments all in the bsides tier (0.70-0.85) so exactly one tier
        # renders — the point of this test is that source audio works.
        pinned_moments = [
            Moment(start_seconds=2.0, end_seconds=5.0,
                   scores=MomentScores(visual_interest=0.8, audio_peak=0.75, emotional_charge=0.7, narrative_payoff=0.7, technical_skill=0.75),
                   composite=0.75, description="moment 1", emphasis_hint="hold"),
            Moment(start_seconds=10.0, end_seconds=13.0,
                   scores=MomentScores(visual_interest=0.8, audio_peak=0.8, emotional_charge=0.75, narrative_payoff=0.7, technical_skill=0.75),
                   composite=0.76, description="moment 2", emphasis_hint="hold"),
            Moment(start_seconds=18.0, end_seconds=21.0,
                   scores=MomentScores(visual_interest=0.8, audio_peak=0.75, emotional_charge=0.7, narrative_payoff=0.75, technical_skill=0.8),
                   composite=0.77, description="moment 3", emphasis_hint="hold"),
        ]
        pinned_result = AutoClipperResult(
            source_video=str(synthetic_long_video),
            duration_seconds=30.0,
            video_mood="calm",
            moments=pinned_moments,
        )

        def fake_auto_clip(video_path, api_key, *, on_progress=None):
            return pinned_result

        # Music analyzer should NOT be called when music is extracted from source
        analyze_called = {"count": 0}
        def fake_analyze_music(*a, **kw):
            analyze_called["count"] += 1
            raise RuntimeError("music analyzer should not be called when no music uploaded")

        monkeypatch.setattr(auto_clipper_mod, "auto_clip", fake_auto_clip)
        monkeypatch.setattr(gemini_music_analyzer, "analyze_music", fake_analyze_music)

        output_path = tmp_path / "reel.mp4"
        cfg = PipelineConfig(
            clips_dir=tmp_path / "unused",
            music_path=None,  # <-- the point of the test
            output_path=output_path,
            target_duration=8.0,
            intensity="balanced",
            aspect="landscape",
            game="valorant_ai",
            gemini_api_keys=["test-key"],
            source_mode="auto_clip",
            source_video=synthetic_long_video,
        )
        result = run(cfg)
        assert output_path.exists()
        assert analyze_called["count"] == 0, "Music analyzer called despite no music uploaded"
        assert result.detector_used.startswith("auto-clipper+"), (
            f"Expected auto-clipper path, got {result.detector_used}"
        )
        # Silent music track should have been created alongside the output
        assert (tmp_path / "silent_music.m4a").exists()
        # At least one tier should have rendered (bsides — 3 moments at ~0.75)
        assert len(result.outputs) >= 1

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
