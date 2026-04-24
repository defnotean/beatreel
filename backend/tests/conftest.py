"""Shared pytest fixtures."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


@pytest.fixture(scope="session")
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture
def sample_music(tmp_path: Path) -> Path:
    """Generate a 10s 120-BPM synthetic 'music' WAV (pulse train we can detect beats in)."""
    sr = 22050
    duration = 10.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    # 120 BPM = 2 Hz pulse. Carrier at 220 Hz, gated by a 2 Hz pulse envelope.
    carrier = np.sin(2 * np.pi * 220 * t)
    gate = np.where((t * 2) % 1 < 0.1, 1.0, 0.05)
    audio = (carrier * gate * 0.6).astype(np.float32)
    path = tmp_path / "music.wav"
    sf.write(path, audio, sr)
    return path


def _make_clip_wav(path: Path, duration: float, peak_times: list[float], sr: int = 22050) -> None:
    """Create a WAV clip with quiet baseline + loud gaussian 'peaks' at the given times."""
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    audio = 0.02 * np.random.randn(len(t)).astype(np.float32)
    for pt in peak_times:
        # Gaussian burst ~0.2s wide centered at pt
        sigma = 0.08
        burst = np.exp(-((t - pt) ** 2) / (2 * sigma**2))
        audio += (0.7 * burst * np.random.randn(len(t)) * 0.5).astype(np.float32)
        audio += (0.6 * burst * np.sin(2 * np.pi * 400 * t)).astype(np.float32)
    sf.write(path, np.clip(audio, -1.0, 1.0), sr)


@pytest.fixture
def sample_clips_audio_only(tmp_path: Path) -> Path:
    """Directory of audio-only WAV clips with known peak locations (for highlight-detection unit tests)."""
    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    _make_clip_wav(clips_dir / "clip_01.wav", duration=6.0, peak_times=[2.0, 4.5])
    _make_clip_wav(clips_dir / "clip_02.wav", duration=5.0, peak_times=[1.5, 3.0])
    _make_clip_wav(clips_dir / "clip_03.wav", duration=4.0, peak_times=[2.0])
    return clips_dir


def _make_clip_mp4(path: Path, duration: float, peak_times: list[float]) -> bool:
    """Create a real MP4 with video + audio peaks using ffmpeg. Returns True on success."""
    if shutil.which("ffmpeg") is None:
        return False
    # Build an audio track as a WAV first (can't easily drive ffmpeg's aevalsrc with multi-peak)
    wav_path = path.with_suffix(".wav")
    _make_clip_wav(wav_path, duration, peak_times)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=gray:s=640x360:d={duration}:r=30",
        "-i", str(wav_path),
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True)
    wav_path.unlink(missing_ok=True)
    return res.returncode == 0


@pytest.fixture
def sample_clips_mp4(tmp_path: Path, ffmpeg_available: bool) -> Path | None:
    """Directory of real MP4 clips. Skips the test if ffmpeg isn't available."""
    if not ffmpeg_available:
        pytest.skip("ffmpeg not available for MP4 fixture generation")
    clips_dir = tmp_path / "clips_mp4"
    clips_dir.mkdir()
    for i, (name, dur, peaks) in enumerate([
        ("a.mp4", 6.0, [2.0, 4.5]),
        ("b.mp4", 5.0, [1.5, 3.0]),
        ("c.mp4", 4.0, [2.0]),
    ]):
        ok = _make_clip_mp4(clips_dir / name, dur, peaks)
        if not ok:
            pytest.skip(f"ffmpeg failed generating fixture {name}")
    return clips_dir
