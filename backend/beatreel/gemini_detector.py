"""Gemini-based kill detector for Valorant clips.

Uses `gemini-2.5-flash`'s video understanding to identify actual kill moments
in a gameplay clip — not just audio signatures. The model watches the clip
and returns a structured list of kill timestamps with confidence scores and
short descriptions. Those map directly onto our existing `Highlight` shape
so the rest of the pipeline (cut planning, beat snapping, rendering) doesn't
need to change.

Why this beats template matching:
- The kill feed is unambiguously visible in the HUD; a video model reads it.
- No dependence on the user's audio mix (music volume, teammate voice).
- No cluster-discovery bootstrap to fail.
- Generalizes to headshot / multi-kill / ace callouts without templates.

Cost (approx, Gemini 2.5 Flash): a 30s 1080p clip is ~8k tokens of video +
~500 tokens of prompt + ~200 tokens of JSON output = ~0.0015 USD/clip. A
typical 4-clip reel is under $0.01.

Contract:
  detect_kills_ai(clip_path, api_key) -> list[Highlight]
  Raises GeminiDetectorError on any non-recoverable failure. Caller is
  responsible for falling back to the audio detector if desired.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .highlights import Highlight

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash"

# How long a kill moment should earn in the final reel (seconds context
# we want around the kill). The actual cut length is decided by the pipeline
# based on intensity; this is only used to set each Highlight's effective
# clip_duration so the planner has room to cut around the peak.
# (We pass the real clip duration instead of tight-cropping.)

# Max time to wait for Gemini to finish processing the uploaded video
UPLOAD_TIMEOUT_S = 120
UPLOAD_POLL_INTERVAL_S = 2


class GeminiDetectorError(RuntimeError):
    pass


class Kill(BaseModel):
    timestamp_seconds: float = Field(
        description="Time (in seconds from the start of the clip) at which the kill is confirmed — when the kill feed entry appears."
    )
    confidence: float = Field(
        description="0.0-1.0. 1.0 means unambiguous (kill feed + enemy body visible). 0.5 for less certain.",
        ge=0.0, le=1.0,
    )
    description: str = Field(
        description="One short phrase describing the kill. e.g. 'Vandal headshot on site'.",
    )


class Reaction(BaseModel):
    """A voice comm or hype moment that deserves a caption overlay."""
    timestamp_seconds: float = Field(description="When the callout / reaction begins.")
    duration_seconds: float = Field(
        description="How long the caption should hold on screen. Typical 1.5-3s.",
        ge=0.3, le=6.0,
    )
    caption: str = Field(
        description=(
            "The overlay text. Short — 2-6 words, all caps hits hardest. "
            "If it's a verbatim voice callout from the player or teammates, "
            "transcribe that ('HOLY SHIT!', 'ONE TAP'). Otherwise summarize "
            "the beat ('TRIPLE KILL', 'CLUTCH 1v3')."
        ),
    )
    kind: Literal["voice_comm", "hype_moment", "kill_banner"] = Field(
        description="voice_comm = audible callout; hype_moment = visual celebration; kill_banner = in-game KILL/TRIPLE text."
    )


class ClipAnalysis(BaseModel):
    kills: list[Kill]
    reactions: list[Reaction] = Field(
        default_factory=list,
        description="Moments that should get a caption overlay in the final reel.",
    )


# Kept for backwards compatibility — older calls return a plain list of kills.
class KillList(BaseModel):
    kills: list[Kill]


SYSTEM_INSTRUCTION = (
    "You are a Valorant gameplay analyst extracting TWO things from a first-person "
    "POV gameplay clip for a highlight reel:\n"
    "1. Every KILL the camera-player scores.\n"
    "2. Every REACTION worth captioning in the final cut.\n\n"
    "KILLS — what counts:\n"
    "- The camera-player's name/agent icon appears in the kill feed (top-right) "
    "  crossing out an enemy.\n"
    "- The on-screen 'KILL' / 'HEADSHOT' / multi-kill banner flashes.\n"
    "- The enemy ragdolls in front of the camera with a hitmarker or kill sound.\n"
    "What does NOT count as a kill: assists, teammate kills, enemy-on-teammate "
    "kills, body shots that don't kill, the camera-player dying.\n"
    "Kill timestamp = the moment the kill confirms (feed appears / banner flashes), "
    "not the first shot of the engagement. Precise to 0.1s.\n\n"
    "REACTIONS — what counts:\n"
    "- An audible voice callout (player or teammate): 'HOLY SHIT', 'ONE TAP', "
    "  'CLUTCH', 'NO WAY'. Transcribe verbatim, ALL CAPS, <=6 words.\n"
    "- A multi-kill / ace / clutch banner: caption 'TRIPLE KILL', 'ACE', '1v3'.\n"
    "- Visible teammate celebration / reaction in voice chat.\n"
    "Do NOT caption every kill — only moments that genuinely POP. Err toward "
    "fewer, higher-impact captions. A good reel has 3-8 captions, not 20.\n\n"
    "Output strict JSON matching the schema. Prefer precision over completeness."
)


def _upload_and_wait(client: genai.Client, clip_path: Path) -> types.File:
    logger.info("gemini: uploading %s", clip_path.name)
    uploaded = client.files.upload(file=str(clip_path))
    start = time.time()
    while uploaded.state and uploaded.state.name == "PROCESSING":
        if time.time() - start > UPLOAD_TIMEOUT_S:
            raise GeminiDetectorError(
                f"Gemini file upload stuck in PROCESSING for >{UPLOAD_TIMEOUT_S}s: {clip_path.name}"
            )
        time.sleep(UPLOAD_POLL_INTERVAL_S)
        uploaded = client.files.get(name=uploaded.name)
    if not uploaded.state or uploaded.state.name != "ACTIVE":
        raise GeminiDetectorError(
            f"Gemini file upload ended in state {uploaded.state and uploaded.state.name!r}: {clip_path.name}"
        )
    return uploaded


def _probe_duration(clip_path: Path) -> float:
    """Fall back to ffprobe so we don't load the whole clip through librosa."""
    import json
    import subprocess
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(clip_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def analyze_clip_ai(
    clip_path: Path,
    api_key: str,
    *,
    client: Optional[genai.Client] = None,
) -> tuple[ClipAnalysis, float]:
    """Return (analysis, clip_duration) for one clip. Raises GeminiDetectorError on any failure."""
    if not api_key:
        raise GeminiDetectorError("Missing Gemini API key")
    if not clip_path.exists():
        raise GeminiDetectorError(f"Clip not found: {clip_path}")

    if client is None:
        client = genai.Client(api_key=api_key)

    uploaded = _upload_and_wait(client, clip_path)
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=[uploaded, "Identify kills and caption-worthy reactions in this clip."],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=ClipAnalysis,
                temperature=0.1,
            ),
        )
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

    parsed: Optional[ClipAnalysis] = getattr(response, "parsed", None)
    if parsed is None:
        try:
            parsed = ClipAnalysis.model_validate_json(response.text or "{}")
        except Exception as exc:
            raise GeminiDetectorError(
                f"Gemini returned unparseable JSON: {(response.text or '(empty)')[:500]}"
            ) from exc

    try:
        duration = _probe_duration(clip_path)
    except Exception:
        duration = max(
            (k.timestamp_seconds for k in parsed.kills),
            default=60.0,
        ) + 2.0
    return parsed, duration


def detect_kills_ai(
    clip_path: Path,
    api_key: str,
    *,
    client: Optional[genai.Client] = None,
) -> list[Highlight]:
    """Legacy shim: return Highlights for kills only (no reactions).

    Kept for the existing pipeline.py code path until the full director flow
    replaces it. New code should use analyze_clip_ai directly.
    """
    analysis, duration = analyze_clip_ai(clip_path, api_key, client=client)
    out: list[Highlight] = []
    for k in analysis.kills:
        t = float(k.timestamp_seconds)
        if t < 0 or t > duration:
            logger.warning(
                "gemini: dropping kill outside clip bounds (%.2fs, clip %.2fs): %s",
                t, duration, k.description,
            )
            continue
        out.append(Highlight(
            clip_path=clip_path,
            peak_time=t,
            score=float(k.confidence),
            clip_duration=duration,
        ))
    return out


def detect_kills_ai_batch(
    clip_paths: list[Path],
    api_key: str,
    *,
    on_progress=None,
) -> tuple[list[Highlight], list[tuple[Path, str]]]:
    """Legacy serial batch. Use analyze_clips_parallel for the pool-driven flow."""
    client = genai.Client(api_key=api_key)
    all_hl: list[Highlight] = []
    errors: list[tuple[Path, str]] = []
    total = len(clip_paths)
    for i, clip in enumerate(clip_paths):
        if on_progress:
            on_progress(i, total, clip)
        try:
            hls = detect_kills_ai(clip, api_key, client=client)
            all_hl.extend(hls)
        except GeminiDetectorError as exc:
            errors.append((clip, str(exc)))
            logger.warning("gemini: %s failed: %s", clip.name, exc)
        except Exception as exc:
            errors.append((clip, f"{type(exc).__name__}: {exc}"))
            logger.exception("gemini: unexpected error on %s", clip.name)
    if on_progress:
        on_progress(total, total, None)
    return all_hl, errors


def analyze_clips_parallel(
    clip_paths: list[Path],
    pool,
    *,
    on_progress=None,
) -> tuple[dict[Path, ClipAnalysis], dict[Path, float], list[tuple[Path, str]]]:
    """Run per-clip analysis in parallel across the key pool.

    Returns (analyses, durations, errors):
      - analyses: {clip_path: ClipAnalysis} for each success
      - durations: {clip_path: seconds} for each success
      - errors: [(clip_path, error_message)] for each failure
    """
    from .gemini_pool import GeminiPool  # local import to avoid cycle

    if not isinstance(pool, GeminiPool):
        raise GeminiDetectorError("analyze_clips_parallel requires a GeminiPool")

    def per_clip(api_key: str, clip: Path) -> tuple[ClipAnalysis, float]:
        # One client per worker (per-key) — cheap and isolates files API state.
        client = genai.Client(api_key=api_key)
        return analyze_clip_ai(clip, api_key, client=client)

    def progress_cb(done: int, total: int, clip, _out, _exc) -> None:
        if on_progress:
            on_progress(done, total, clip)

    results = pool.map(per_clip, clip_paths, on_complete=progress_cb)

    analyses: dict[Path, ClipAnalysis] = {}
    durations: dict[Path, float] = {}
    errors: list[tuple[Path, str]] = []
    for clip, out, exc in results:
        if exc is not None:
            errors.append((clip, str(exc)))
            continue
        if out is None:
            continue
        analysis, duration = out
        analyses[clip] = analysis
        durations[clip] = duration
    return analyses, durations, errors
