"""ffmpeg orchestration: cut selected clip windows and concat with music overlaid."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CutPlan:
    clip_path: Path
    start: float  # seconds into the source clip
    duration: float  # seconds of this cut


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install it:\n"
            "  Windows: winget install ffmpeg\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   apt install ffmpeg (or distro equivalent)"
        )
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found on PATH (ships with ffmpeg).")


def _has_encoder(name: str) -> bool:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, check=True,
        ).stdout
        return f" {name} " in out
    except Exception:
        return False


def _pick_video_encoder() -> tuple[str, list[str]]:
    """Prefer hardware encoder if available, fall back to libx264."""
    if _has_encoder("h264_nvenc"):
        return "h264_nvenc", ["-preset", "p5", "-cq", "20"]
    if _has_encoder("h264_videotoolbox"):
        return "h264_videotoolbox", ["-b:v", "8M"]
    if _has_encoder("h264_qsv"):
        return "h264_qsv", ["-global_quality", "22"]
    return "libx264", ["-preset", "fast", "-crf", "20"]


def render_reel(
    cuts: list[CutPlan],
    music_path: Path,
    output_path: Path,
    music_gain_db: float = 0.0,
    game_gain_db: float = -18.0,
    on_log=None,
) -> Path:
    """Render the final highlight reel."""
    ensure_ffmpeg()
    if not cuts:
        raise ValueError("No cuts provided — nothing to render.")

    encoder, enc_args = _pick_video_encoder()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        segment_paths: list[Path] = []

        # 1) Cut each segment to a normalized intermediate (same codec, same size)
        for i, cut in enumerate(cuts):
            seg = tmp / f"seg_{i:04d}.mp4"
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{cut.start:.3f}",
                "-i", str(cut.clip_path),
                "-t", f"{cut.duration:.3f}",
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=60",
                "-c:v", encoder, *enc_args,
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                "-pix_fmt", "yuv420p",
                str(seg),
            ]
            if on_log:
                on_log(f"cutting segment {i + 1}/{len(cuts)}")
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            segment_paths.append(seg)

        # 2) Concat via concat demuxer (requires matching codec + params)
        concat_list = tmp / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in segment_paths),
            encoding="utf-8",
        )
        concatted = tmp / "concatted.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                str(concatted),
            ],
            check=True, capture_output=True, text=True,
        )

        # 3) Mix music over the concatted video (duck the game audio)
        if on_log:
            on_log("mixing audio with music track")
        filter_complex = (
            f"[0:a]volume={game_gain_db}dB[ga];"
            f"[1:a]volume={music_gain_db}dB[ma];"
            "[ga][ma]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(concatted),
                "-i", str(music_path),
                "-filter_complex", filter_complex,
                "-map", "0:v:0", "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                str(output_path),
            ],
            check=True, capture_output=True, text=True,
        )

    return output_path
