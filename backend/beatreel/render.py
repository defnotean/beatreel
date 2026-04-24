"""ffmpeg orchestration: cut selected clip windows, burn optional captions,
prepend/append fades, and concat with music overlaid."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .aspect import AspectSpec, get_aspect


@dataclass
class CutPlan:
    clip_path: Path
    start: float  # seconds into the source clip
    duration: float  # seconds of this cut

    # Optional overlay caption burned into this segment
    caption: Optional[str] = None
    caption_start_in_cut: float = 0.0  # seconds from segment start
    caption_duration: float = 2.0


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


_ENCODER_CACHE: dict[str, bool] = {}
_FONT_CACHE: list[Optional[str]] = []


def _encoder_works(name: str) -> bool:
    if name in _ENCODER_CACHE:
        return _ENCODER_CACHE[name]
    try:
        res = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1:r=10",
                "-c:v", name, "-frames:v", "1", "-f", "null", "-",
            ],
            capture_output=True, timeout=10,
        )
        ok = res.returncode == 0
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        ok = False
    _ENCODER_CACHE[name] = ok
    return ok


def _pick_video_encoder() -> tuple[str, list[str]]:
    if _encoder_works("h264_nvenc"):
        return "h264_nvenc", ["-preset", "p5", "-cq", "20"]
    if _encoder_works("h264_videotoolbox"):
        return "h264_videotoolbox", ["-b:v", "8M"]
    if _encoder_works("h264_qsv"):
        return "h264_qsv", ["-global_quality", "22"]
    return "libx264", ["-preset", "fast", "-crf", "20"]


def _find_font() -> Optional[str]:
    """Locate a bold sans font for drawtext. Returns None if nothing found —
    captions will fall back to ffmpeg's built-in default."""
    if _FONT_CACHE:
        return _FONT_CACHE[0]
    candidates: list[str] = []
    sysname = platform.system()
    if sysname == "Windows":
        winroot = os.environ.get("WINDIR", "C:/Windows")
        candidates += [
            f"{winroot}/Fonts/segoeuib.ttf",
            f"{winroot}/Fonts/arialbd.ttf",
            f"{winroot}/Fonts/arial.ttf",
        ]
    elif sysname == "Darwin":
        candidates += [
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ]
    else:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        ]
    for c in candidates:
        if Path(c).exists():
            _FONT_CACHE.append(c)
            return c
    _FONT_CACHE.append(None)
    return None


# Anything outside this set gets stripped from captions before drawtext rendering.
# FreeType via ffmpeg drawtext does NOT handle color emoji — Gemini's transcripts
# of player callouts often include 🔥 / ♠️ / 💀 which would render as tofu.
# Keep letters, digits, whitespace, and the common punctuation you'd actually want
# in a short callout overlay.
_CAPTION_ALLOWED = re.compile(r"[^A-Za-z0-9\s\-!?.,;:'\"&/+]", re.UNICODE)


def _sanitize_caption(text: str) -> str:
    cleaned = _CAPTION_ALLOWED.sub("", text)
    # Collapse any runs of whitespace that the strip left behind.
    return re.sub(r"\s+", " ", cleaned).strip()


def _escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext. Only used when text is inlined; prefer
    writing to a file and using `textfile=` which sidesteps filter-graph
    escaping entirely."""
    return (
        text
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "\\%")
    )


def _ffpath(p: Path) -> str:
    """ffmpeg filter-graph path. Forward slashes + escaped colons so the
    filter-graph parser doesn't interpret `C:` as an option separator
    (the single-quoted-value rule doesn't protect colons on Windows paths
    inside drawtext — tested empirically)."""
    return str(p).replace("\\", "/").replace(":", r"\:")


def _drawtext_filter(
    caption: str,
    start: float,
    duration: float,
    tmp_dir: Path,
    *,
    fade: float = 0.25,
    size_divisor: float = 14.0,
    y_expr: str = "h*0.78",
) -> Optional[str]:
    """Build a drawtext filter using `textfile=` so we dodge filter-graph
    escaping for weird caption content. Returns None if sanitization leaves
    nothing renderable."""
    safe = _sanitize_caption(caption)
    if not safe:
        return None
    # Write caption to a stable file inside tmp_dir. Hash so duplicate captions
    # across cuts share one file.
    fname = f"cap_{abs(hash(safe)) % 10_000_000}.txt"
    txt_path = tmp_dir / fname
    if not txt_path.exists():
        txt_path.write_text(safe, encoding="utf-8")

    font = _find_font()
    font_arg = f"fontfile='{_ffpath(Path(font))}':" if font else ""
    end = start + duration
    alpha = (
        f"if(lt(t\\,{start}-{fade})\\,0\\,"
        f"if(lt(t\\,{start})\\,(t-({start}-{fade}))/{fade}\\,"
        f"if(lt(t\\,{end}-{fade})\\,1\\,"
        f"if(lt(t\\,{end})\\,({end}-t)/{fade}\\,0))))"
    )
    return (
        f"drawtext={font_arg}"
        f"textfile='{_ffpath(txt_path)}':"
        f"fontcolor=white:"
        f"fontsize=(h/{size_divisor}):"
        f"borderw=3:"
        f"bordercolor=black@0.9:"
        f"x=(w-text_w)/2:"
        f"y={y_expr}:"
        f"alpha='{alpha}':"
        f"enable='between(t,{start - fade},{end})'"
    )


def _build_segment_vf(
    aspect_filter: str,
    cut: CutPlan,
    tmp_dir: Path,
) -> str:
    parts = [aspect_filter]
    if cut.caption:
        start = max(0.0, float(cut.caption_start_in_cut))
        dur = max(0.4, float(cut.caption_duration))
        max_end = max(0.5, float(cut.duration) - 0.05)
        if start >= max_end:
            start = max(0.0, max_end - 1.0)
        dur = min(dur, max_end - start)
        if dur >= 0.3:
            flt = _drawtext_filter(cut.caption, start, dur, tmp_dir)
            if flt:
                parts.append(flt)
    return ",".join(parts)


def _build_hold_segment(
    out_path: Path,
    duration: float,
    aspect_spec: AspectSpec,
    encoder: str,
    enc_args: list[str],
    tmp_dir: Path,
    title: Optional[str] = None,
    fade_in: float = 0.0,
) -> None:
    """Generate a black-frame segment of given duration, optional title card."""
    width = aspect_spec.width
    height = aspect_spec.height
    vfilters = [f"scale={width}:{height}"]
    if title:
        safe = _sanitize_caption(title)
        if safe:
            fname = f"title_{abs(hash(safe)) % 10_000_000}.txt"
            txt_path = tmp_dir / fname
            txt_path.write_text(safe, encoding="utf-8")
            font = _find_font()
            font_arg = f"fontfile='{_ffpath(Path(font))}':" if font else ""
            alpha = (
                f"if(lt(t\\,{fade_in})\\,t/{max(fade_in, 0.001)}\\,"
                f"if(lt(t\\,{duration}-0.3)\\,1\\,({duration}-t)/0.3))"
            )
            vfilters.append(
                f"drawtext={font_arg}"
                f"textfile='{_ffpath(txt_path)}':"
                f"fontcolor=white:"
                f"fontsize=(h/8):"
                f"borderw=4:"
                f"bordercolor=black@0.9:"
                f"x=(w-text_w)/2:"
                f"y=(h-text_h)/2:"
                f"alpha='{alpha}'"
            )

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=60:d={duration:.3f}",
        "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", f"{duration:.3f}",
        "-vf", ",".join(vfilters),
        "-c:v", encoder, *enc_args,
        "-c:a", "aac", "-b:a", "160k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def render_reel(
    cuts: list[CutPlan],
    music_path: Path,
    output_path: Path,
    aspect: AspectSpec | str = "landscape",
    music_gain_db: float = 0.0,
    game_gain_db: float = -18.0,
    *,
    intro_hold_seconds: float = 0.0,
    title_caption: Optional[str] = None,
    outro_hold_seconds: float = 0.0,
    fade_in_seconds: float = 0.3,
    fade_out_seconds: float = 0.8,
    on_log=None,
) -> Path:
    """Render the final highlight reel."""
    ensure_ffmpeg()
    if not cuts:
        raise ValueError("No cuts provided — nothing to render.")

    aspect_spec = aspect if isinstance(aspect, AspectSpec) else get_aspect(aspect)  # type: ignore[arg-type]
    encoder, enc_args = _pick_video_encoder()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        segment_paths: list[Path] = []

        # Optional intro hold segment (black + title)
        if intro_hold_seconds and intro_hold_seconds > 0.05:
            intro_seg = tmp / "intro.mp4"
            _build_hold_segment(
                intro_seg,
                duration=intro_hold_seconds,
                aspect_spec=aspect_spec,
                encoder=encoder,
                enc_args=enc_args,
                tmp_dir=tmp,
                title=title_caption,
                fade_in=fade_in_seconds,
            )
            segment_paths.append(intro_seg)

        # 1) Cut each source segment with optional caption burn-in
        for i, cut in enumerate(cuts):
            seg = tmp / f"seg_{i:04d}.mp4"
            vf = _build_segment_vf(aspect_spec.video_filter, cut, tmp)
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{cut.start:.3f}",
                "-i", str(cut.clip_path),
                "-t", f"{cut.duration:.3f}",
                "-vf", vf,
                "-c:v", encoder, *enc_args,
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                "-pix_fmt", "yuv420p",
                str(seg),
            ]
            if on_log:
                on_log(f"cutting segment {i + 1}/{len(cuts)}")
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            segment_paths.append(seg)

        # Optional outro hold segment (black)
        if outro_hold_seconds and outro_hold_seconds > 0.05:
            outro_seg = tmp / "outro.mp4"
            _build_hold_segment(
                outro_seg,
                duration=outro_hold_seconds,
                aspect_spec=aspect_spec,
                encoder=encoder,
                enc_args=enc_args,
                tmp_dir=tmp,
                title=None,
                fade_in=0.0,
            )
            segment_paths.append(outro_seg)

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

        # 3) Probe the total duration so we know where to place the fade-out
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nokey=1:noprint_wrappers=1",
                str(concatted),
            ],
            capture_output=True, text=True, check=True,
        )
        total_duration = float(probe.stdout.strip() or "0")

        # 4) Mix music + apply global fade in/out on video and audio
        if on_log:
            on_log("mixing audio and applying fades")

        vf_final_parts: list[str] = []
        fo = max(0.0, min(fade_out_seconds, total_duration * 0.5))
        fi = max(0.0, min(fade_in_seconds, total_duration * 0.5))
        if fi > 0.01:
            vf_final_parts.append(f"fade=t=in:st=0:d={fi:.3f}")
        if fo > 0.01:
            vf_final_parts.append(f"fade=t=out:st={max(0.0, total_duration - fo):.3f}:d={fo:.3f}")
        video_label = "0:v"
        if vf_final_parts:
            filter_complex_video = f"[0:v]{','.join(vf_final_parts)}[vout];"
            video_label = "[vout]"
        else:
            filter_complex_video = ""

        af_parts = [
            f"[0:a]volume={game_gain_db}dB[ga]",
            f"[1:a]volume={music_gain_db}dB,atrim=0:{total_duration:.3f}[ma]",
            "[ga][ma]amix=inputs=2:duration=first:dropout_transition=0[amix]",
        ]
        # Audio fade in/out to match video
        af_last = "[amix]"
        if fi > 0.01 or fo > 0.01:
            afade_parts = []
            if fi > 0.01:
                afade_parts.append(f"afade=t=in:st=0:d={fi:.3f}")
            if fo > 0.01:
                afade_parts.append(f"afade=t=out:st={max(0.0, total_duration - fo):.3f}:d={fo:.3f}")
            af_parts.append(f"{af_last}{','.join(afade_parts)}[aout]")
            af_last = "[aout]"

        filter_complex = filter_complex_video + ";".join(af_parts)

        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(concatted),
                "-i", str(music_path),
                "-filter_complex", filter_complex,
                "-map", video_label, "-map", af_last,
                "-c:v", encoder, *enc_args,
                "-c:a", "aac", "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-t", f"{total_duration:.3f}",
                str(output_path),
            ],
            check=True, capture_output=True, text=True,
        )

    return output_path
