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

    # Emphasis level — drives selective dynamic effects (velocity ramp,
    # impact burst). "normal" = no effects; "hold" = dwell longer;
    # "drop_hit" = ramp + flash + zoom pulse.
    emphasis: str = "normal"  # Literal["normal", "hold", "drop_hit"]


# ─── Color grades ─────────────────────────────────────────────────────────
# Expressed as ffmpeg filter chains rather than 3D LUT files. Tradeoff:
# filter-chain grades are less nuanced than a real .cube but add zero binary
# weight, are deterministic across ffmpeg versions, carry no licensing
# baggage, and swap in place for real .cube files later via the same function.
#
# One grade is picked by the director based on music vibe:
#   hype -> clinical   (punchy, high-contrast, slightly desaturated)
#   balanced -> teal_orange   (Hollywood / esports broadcast default)
#   chill / emotional -> cinematic   (desaturated, lifted blacks, warm roll-off)
COLOR_GRADES: dict[str, str] = {
    "teal_orange": (
        "curves=all='0/0.05 0.5/0.48 1/0.94',"
        "colorbalance=rs=-0.08:gs=-0.02:bs=0.12:rh=0.12:gh=0.02:bh=-0.10,"
        "eq=contrast=1.10:saturation=1.08"
    ),
    "clinical": (
        "curves=all='0/0 0.25/0.22 0.75/0.82 1/1',"
        "eq=contrast=1.22:saturation=0.88:brightness=0.02,"
        "colorbalance=rs=0.02:gs=0.02:bs=0.05"
    ),
    "cinematic": (
        "curves=all='0/0.10 0.45/0.42 1/0.88',"
        "eq=contrast=0.94:saturation=0.78,"
        "colorbalance=rs=0.06:gs=-0.02:bs=-0.06:rh=0.10:gh=0.00:bh=-0.08"
    ),
}


def _grade_filter(grade: Optional[str]) -> Optional[str]:
    if not grade:
        return None
    return COLOR_GRADES.get(grade)


# ─── Velocity ramp on drop_hit cuts ──────────────────────────────────────
# Research (universal across Val + action-sports + gaming montages): slow
# down INTO the kill, snap back out. Ramping AFTER the kill is the #1
# amateur tell. Applied only on emphasis=drop_hit, only when the cut is
# long enough to actually contain a slow-mo window.
RAMP_PARAMS: dict[str, dict] = {
    "drop_hit": {"ramp_duration_s": 0.7, "target_speed": 0.40, "interp_fps": 120},
    "hold":     {"ramp_duration_s": 1.0, "target_speed": 0.55, "interp_fps": 60},
}
RAMP_MIN_CUT_S = 1.4  # sub-1.4s cuts have no room for a meaningful ramp

# Impact burst: a flash + zoom-pulse composite applied at a single point in
# the cut — the kill-confirm frame, approximated as caption_start if present
# else duration - 0.5s. Gated on emphasis=drop_hit only (preset-abuse tell).
IMPACT_BURST_FLASH_S = 0.06   # ~3-4 frames of white flash
IMPACT_BURST_ZOOM_S = 0.35    # full zoom-pulse window (ramp up + settle)
IMPACT_BURST_ZOOM_MAX = 1.08  # 108% peak zoom


def _apply_pre_kill_ramp(
    seg_in: Path,
    seg_out: Path,
    duration: float,
    emphasis: str,
    aspect_spec: AspectSpec,
    encoder: str,
    enc_args: list[str],
) -> bool:
    """Apply a pre-kill velocity ramp to seg_in → seg_out. Returns True if
    the ramp was applied; False if the cut was too short or emphasis didn't
    warrant it (caller should use seg_in unchanged in that case)."""
    params = RAMP_PARAMS.get(emphasis)
    if params is None or duration < RAMP_MIN_CUT_S:
        return False

    ramp_dur = float(params["ramp_duration_s"])
    target_speed = float(params["target_speed"])
    interp_fps = int(params["interp_fps"])
    if ramp_dur >= duration - 0.3:
        ramp_dur = max(0.4, duration - 0.3)

    pre_dur = duration - ramp_dur
    # setpts factor for slow-mo: PTS multiplier = 1 / speed.
    # So at target_speed=0.4, we need setpts=2.5*PTS.
    pts_factor = 1.0 / max(0.1, target_speed)

    # filter_complex splits the segment into pre-ramp + ramp-with-minterpolate
    # and concatenates them; audio is slowed on the ramp half with atempo
    # (atempo range is 0.5-100, for 0.4 speed we need atempo=0.4 which is
    # below 0.5 — chain two atempos: 0.5 * 0.8 = 0.4).
    atempo_chain = "atempo=0.5,atempo=0.8" if target_speed < 0.5 else f"atempo={target_speed:.3f}"
    filter_complex = (
        f"[0:v]trim=0:{pre_dur:.3f},setpts=PTS-STARTPTS[vpre];"
        f"[0:a]atrim=0:{pre_dur:.3f},asetpts=PTS-STARTPTS[apre];"
        f"[0:v]trim={pre_dur:.3f}:{duration:.3f},setpts=(PTS-STARTPTS)*{pts_factor:.3f},"
        f"minterpolate=fps={interp_fps}:mi_mode=mci:mc_mode=aobmc[vramp];"
        f"[0:a]atrim={pre_dur:.3f}:{duration:.3f},asetpts=PTS-STARTPTS,{atempo_chain}[aramp];"
        f"[vpre][apre][vramp][aramp]concat=n=2:v=1:a=1[vout][aout]"
    )

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(seg_in),
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", encoder, *enc_args,
        "-c:a", "aac", "-b:a", "160k",
        "-pix_fmt", "yuv420p",
        str(seg_out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError:
        # minterpolate can fail on rare source-frame pathologies; fall back
        # to the un-ramped segment and log-skip.
        return False


def _impact_burst_filter_frag(cut: CutPlan) -> Optional[str]:
    """Filter-chain fragment (video side) for flash + zoom-pulse on drop_hit.
    Returns None if emphasis isn't drop_hit or the cut is too short.

    Composition:
    - Flash: brightness boost for ~60ms centered on the impact frame.
    - Zoom pulse: a gaussian scale bump up to ~108% peaked at impact, then
      center-cropped so the output dimensions stay fixed.
    Both effects are gated to the window around the impact so the rest of the
    cut renders at native speed/scale with no overhead from the expressions.
    """
    if cut.emphasis != "drop_hit" or cut.duration < 1.0:
        return None

    # Impact frame = caption start when we have a caption, else 0.5s before end.
    if cut.caption and cut.caption_start_in_cut > 0:
        t_hit = max(0.3, float(cut.caption_start_in_cut))
    else:
        t_hit = max(0.3, float(cut.duration) - 0.5)
    t_hit = min(t_hit, cut.duration - 0.1)

    flash_end = t_hit + IMPACT_BURST_FLASH_S
    zoom_start = max(0.0, t_hit - 0.1)
    zoom_end = t_hit + IMPACT_BURST_ZOOM_S

    flash = (
        f"eq=brightness='0.55*if(between(t\\,{t_hit:.3f}\\,{flash_end:.3f})\\,1\\,0)'"
    )

    zoom_peak = IMPACT_BURST_ZOOM_MAX - 1.0
    zoom_sigma = max(0.08, IMPACT_BURST_ZOOM_S / 3.0)
    # zoom_expr is 1.0 outside the window and a gaussian peaking at zoom_peak
    # at t=t_hit inside the window. Clamping to 1.0 outside avoids paying for
    # scale math across the whole cut.
    zoom_expr = (
        f"if(between(t\\,{zoom_start:.3f}\\,{zoom_end:.3f})"
        f"\\,1+{zoom_peak:.3f}*exp(-((t-{t_hit:.3f})/{zoom_sigma:.3f})^2)\\,1)"
    )
    zoom = (
        f"scale=w='iw*({zoom_expr})':h='ih*({zoom_expr})':eval=frame,"
        f"crop=w='iw/({zoom_expr})':h='ih/({zoom_expr})':x='(iw-ow)/2':y='(ih-oh)/2'"
    )
    return f"{flash},{zoom}"


def _build_freeze_segment(
    source_seg: Path,
    out_path: Path,
    duration: float,
    aspect_spec: AspectSpec,
    encoder: str,
    enc_args: list[str],
) -> None:
    """Freeze-frame ending: extract the LAST frame of source_seg, hold it
    for `duration` seconds with silent audio, matching the aspect spec so
    the concat demuxer accepts it."""
    # Extract the last frame via `-sseof -0.1 -vframes 1` which seeks to
    # ~0.1s before end then grabs one frame. Using a small nonzero seek
    # avoids failing on short clips where -sseof 0 returns nothing.
    still_png = out_path.with_suffix(".still.png")
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-sseof", "-0.1",
            "-i", str(source_seg),
            "-vframes", "1",
            str(still_png),
        ],
        check=True, capture_output=True, text=True,
    )

    width = aspect_spec.width
    height = aspect_spec.height
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-loop", "1", "-framerate", "60",
        "-t", f"{duration:.3f}",
        "-i", str(still_png),
        "-f", "lavfi", "-t", f"{duration:.3f}",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=60",
        "-c:v", encoder, *enc_args,
        "-c:a", "aac", "-b:a", "160k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    try:
        still_png.unlink()
    except Exception:
        pass


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


# Audio edge-fade duration per segment boundary. Research ("amateur tell #2") flags
# simultaneous audio+video hard cuts as the second-largest amateur tell after audio
# sync drift. A symmetric 4-frame (~67ms at 60fps) fade-in at each segment's start
# and fade-out at each segment's end softens the boundary without introducing AV
# drift. A filter_complex crossfade would shorten each boundary by the crossfade
# duration and accumulate ~1s of drift on a 15-cut reel — per-segment edge fades
# avoid that entirely.
AUDIO_EDGE_FADE_S = 0.067


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


def _game_volume_expression(
    game_gain_db: float,
    boost_db: float,
    windows: Optional[list[tuple[float, float]]],
) -> str:
    """Return an ffmpeg `volume` argument — a linear-gain expression that's
    time-varying iff voice-boost windows are supplied.

    Default (no windows): constant linear gain for `game_gain_db` (e.g. -18dB).
    With windows: evaluates to boost linear gain inside each window, duck
    outside. Expressed as "duck + sum(window indicator * delta)"."""
    duck_lin = 10.0 ** (game_gain_db / 20.0)
    if not windows:
        # Use the simple constant form — still pass as an expression so
        # eval=frame is harmless and the filter spec is uniform.
        return f"{duck_lin:.4f}"
    boost_lin = 10.0 ** (boost_db / 20.0)
    delta = max(0.0, boost_lin - duck_lin)
    # Build: duck + delta*(0 or 1 based on which window we're in)
    gates = "+".join(
        f"between(t\\,{s:.3f}\\,{e:.3f})"
        for (s, e) in windows
    )
    # Wrapping in min(..,1) so overlapping windows don't double-boost.
    expr = f"{duck_lin:.4f}+{delta:.4f}*min(1\\,{gates})"
    return f"'{expr}'"


def _build_segment_af(duration: float) -> str:
    """Audio filter for one segment — a symmetric fade at each edge so the
    boundary with adjacent segments doesn't register as a hard splice. Short
    cuts get a proportionally shorter fade so the fade never exceeds half the
    duration."""
    d = min(AUDIO_EDGE_FADE_S, max(0.02, duration / 4.0))
    fade_out_start = max(0.0, duration - d)
    return f"afade=t=in:st=0:d={d:.3f},afade=t=out:st={fade_out_start:.3f}:d={d:.3f}"


def _build_segment_vf(
    aspect_filter: str,
    cut: CutPlan,
    tmp_dir: Path,
    color_grade: Optional[str] = None,
) -> str:
    parts = [aspect_filter]
    grade = _grade_filter(color_grade)
    if grade:
        parts.append(grade)
    burst = _impact_burst_filter_frag(cut)
    if burst:
        parts.append(burst)
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
    color_grade: Optional[str] = None,
    voice_boost_windows: Optional[list[tuple[float, float]]] = None,
    voice_boost_db: float = -8.0,
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

        # 1) Cut each source segment with optional caption burn-in + audio edge fades.
        #    For emphasis=drop_hit cuts, optionally apply a pre-kill velocity ramp
        #    as a second pass on the rendered segment.
        for i, cut in enumerate(cuts):
            seg = tmp / f"seg_{i:04d}.mp4"
            vf = _build_segment_vf(aspect_spec.video_filter, cut, tmp, color_grade=color_grade)
            af = _build_segment_af(cut.duration)
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{cut.start:.3f}",
                "-i", str(cut.clip_path),
                "-t", f"{cut.duration:.3f}",
                "-vf", vf,
                "-af", af,
                "-c:v", encoder, *enc_args,
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                "-pix_fmt", "yuv420p",
                str(seg),
            ]
            if on_log:
                on_log(f"cutting segment {i + 1}/{len(cuts)}")
            subprocess.run(cmd, check=True, capture_output=True, text=True)

            # Apply velocity ramp on drop_hit cuts. If the ramp step fails
            # (minterpolate can reject pathological frames), fall back to
            # the un-ramped segment.
            if cut.emphasis in RAMP_PARAMS and cut.duration >= RAMP_MIN_CUT_S:
                ramped = tmp / f"seg_{i:04d}_ramp.mp4"
                applied = _apply_pre_kill_ramp(
                    seg, ramped, cut.duration, cut.emphasis,
                    aspect_spec, encoder, enc_args,
                )
                if applied:
                    seg = ramped
            segment_paths.append(seg)

        # Optional outro: freeze-frame on the last cut's final frame (replaces
        # the former black hold). Research consensus across sports / gaming /
        # action highlights: the "micro-pause on the hit" creates rewatch pull;
        # freezing the actual frame beats fading to black.
        if outro_hold_seconds and outro_hold_seconds > 0.05 and segment_paths:
            outro_seg = tmp / "outro.mp4"
            try:
                _build_freeze_segment(
                    source_seg=segment_paths[-1],
                    out_path=outro_seg,
                    duration=outro_hold_seconds,
                    aspect_spec=aspect_spec,
                    encoder=encoder,
                    enc_args=enc_args,
                )
            except Exception:
                # Fallback: black hold if freeze extraction failed.
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

        # Game-audio volume: constant -18dB (duck) unless voice_boost_windows is
        # populated, in which case we use a time-varying expression that lifts
        # the gain to voice_boost_db (typically -8dB) during reaction windows.
        game_volume_expr = _game_volume_expression(
            game_gain_db, voice_boost_db, voice_boost_windows
        )
        af_parts = [
            f"[0:a]volume={game_volume_expr}:eval=frame[ga]",
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
