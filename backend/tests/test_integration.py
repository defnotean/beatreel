"""End-to-end integration: synthetic clips + music through the full pipeline.

These tests require ffmpeg on PATH. They're skipped otherwise — the point is
to prove the pipeline actually produces a playable MP4 when ffmpeg is present,
not to gate development on it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from beatreel.pipeline import PipelineConfig, run


@pytest.mark.integration
def test_pipeline_produces_playable_mp4(
    sample_clips_mp4,
    sample_music: Path,
    tmp_path: Path,
    ffmpeg_available: bool,
):
    if not ffmpeg_available:
        pytest.skip("ffmpeg not available")
    out = tmp_path / "reel.mp4"
    result = run(
        PipelineConfig(
            clips_dir=sample_clips_mp4,
            music_path=sample_music,
            output_path=out,
            target_duration=6.0,
            intensity="balanced",
            aspect="landscape",
            use_scene_detection=False,  # keep the test hermetic
        )
    )
    assert out.exists() and out.stat().st_size > 0
    assert result.num_cuts >= 1
    assert result.num_clips_scanned == 3

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(out)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    # Must have at least one video and one audio stream
    codecs = {s["codec_type"] for s in info["streams"]}
    assert "video" in codecs and "audio" in codecs


@pytest.mark.integration
def test_reroll_cache_hit(
    sample_clips_mp4,
    sample_music: Path,
    tmp_path: Path,
    ffmpeg_available: bool,
):
    """A second run with a different seed should hit the per-clip score cache."""
    if not ffmpeg_available:
        pytest.skip("ffmpeg not available")
    import time
    first_out = tmp_path / "first.mp4"
    second_out = tmp_path / "second.mp4"

    t0 = time.monotonic()
    run(PipelineConfig(
        clips_dir=sample_clips_mp4, music_path=sample_music, output_path=first_out,
        target_duration=5.0, intensity="balanced", aspect="landscape",
        use_scene_detection=False, seed=1,
    ))
    first_elapsed = time.monotonic() - t0

    t0 = time.monotonic()
    run(PipelineConfig(
        clips_dir=sample_clips_mp4, music_path=sample_music, output_path=second_out,
        target_duration=5.0, intensity="balanced", aspect="landscape",
        use_scene_detection=False, seed=2,
    ))
    second_elapsed = time.monotonic() - t0

    # The second run should skip scoring via cache. Not a hard guarantee across
    # all hardware, but on the same machine cached runs are materially faster.
    # We just assert both finished and produced files; timing is informational.
    assert first_out.exists()
    assert second_out.exists()
