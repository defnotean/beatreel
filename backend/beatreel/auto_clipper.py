"""Genre-agnostic auto-clipper.

Takes a long source video (VOD / game session / vlog / reaction / sports /
dash-cam / anything up to ~60min on Flash) and asks Gemini to identify the
most entertaining moments. Each moment is scored along five dimensions; the
director downstream uses the composite score to rank, and the scored
dimensions let music-vibe-driven re-weighting change what bubbles up for a
'hype' reel vs a 'chill' reel.

Design decisions (see council v3):
- ONE Gemini call over the full video (up to Flash's ~60min practical ceiling)
- Universal rubric, not genre-classify-then-detect — Gemini's priors already
  cover the genres, and a hand-maintained rubric catalog rots
- Output schema carries everything downstream needs (moment timing + caption
  timing inside the moment + emphasis hint), so we don't pay for a second
  per-moment analysis pass
- Moments become "virtual clips" by emitting start/end offsets into the
  single source video; the renderer already seeks via -ss so no physical
  extraction is required
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash"

# Flash context ceiling (tokens) × video tokens-per-second yields a practical
# 60-min upper bound at default media resolution. Reject longer videos at
# the boundary rather than surface a confusing "context exceeded" error
# partway through processing.
MAX_VIDEO_SECONDS = 60 * 60

UPLOAD_TIMEOUT_S = 180
UPLOAD_POLL_INTERVAL_S = 3

# Minimum composite score (weighted mean of five dimensions) for a moment to
# enter the cut plan. Below this we treat the moment as filler and drop it.
MIN_COMPOSITE_SCORE = 0.55


class MomentScores(BaseModel):
    visual_interest: float = Field(ge=0.0, le=1.0, description="Is something visually striking happening — action, reaction, motion, striking composition.")
    audio_peak: float = Field(ge=0.0, le=1.0, description="Audio intensity peak — cheering, impact, laughter, shouting, music crescendo, gunshots, applause.")
    emotional_charge: float = Field(ge=0.0, le=1.0, description="Emotional payload — surprise, triumph, shock, comedic timing, vulnerability, heartfelt reaction.")
    narrative_payoff: float = Field(ge=0.0, le=1.0, description="Is this a punchline, climax, or the cashing-in of a setup — moment that makes sense of what came before.")
    technical_skill: float = Field(ge=0.0, le=1.0, description="Impressive skill / precision / craft on display — a perfect aim flick, knife-edge parry, difficult recipe step executed cleanly, acrobatic move.")


class Moment(BaseModel):
    start_seconds: float = Field(ge=0.0, description="Moment start in source video.")
    end_seconds: float = Field(description="Moment end in source video.")
    scores: MomentScores
    composite: float = Field(
        ge=0.0, le=1.0,
        description="Weighted composite across scores. Compute as: 0.25*visual_interest + 0.25*audio_peak + 0.20*emotional_charge + 0.15*narrative_payoff + 0.15*technical_skill",
    )
    description: str = Field(
        description="One-sentence plain-English description of what happens. Used for debug.json and for future feature: letting the user pick moments.",
    )
    suggested_caption: Optional[str] = Field(
        default=None,
        description="2-6 word caption, ALL CAPS if it's a voice callout. Only set when there's a real audible callout or visible banner — don't fabricate.",
    )
    caption_kind: Optional[Literal["voice_comm", "visual_text", "narrative"]] = Field(
        default=None,
        description="voice_comm = audible callout we'd transcribe; visual_text = an on-screen banner / caption; narrative = editor-chosen summary text.",
    )
    caption_start_in_moment_seconds: Optional[float] = Field(
        default=None,
        description="When inside the moment the caption should appear (seconds relative to moment start). Null if no caption.",
    )
    caption_duration_seconds: Optional[float] = Field(
        default=None, description="How long the caption holds, seconds.",
    )
    emphasis_hint: Literal["normal", "hold", "drop_hit"] = Field(
        description=(
            "Maps from composite: >=0.85 -> drop_hit (the big moments that get "
            "velocity ramps + impact burst), 0.70-0.85 -> hold (longer dwell), "
            "else -> normal."
        ),
    )
    content_tags: list[str] = Field(
        default_factory=list,
        description="Short descriptive tags like ['gaming','multi_kill','voice_reaction'] or ['cooking','close_up','reaction']. Free-form; used for observability.",
    )

    @model_validator(mode="after")
    def _validate(self) -> "Moment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError(f"end ({self.end_seconds}) must exceed start ({self.start_seconds})")
        dur = self.end_seconds - self.start_seconds
        if dur < 1.0:
            raise ValueError(f"moment duration {dur:.2f}s below 1.0s floor")
        if dur > 15.0:
            raise ValueError(f"moment duration {dur:.2f}s exceeds 15s ceiling")
        if self.suggested_caption:
            cs = self.caption_start_in_moment_seconds
            cd = self.caption_duration_seconds
            if cs is None or cd is None:
                raise ValueError("caption set but caption_start/duration missing")
            if cs < 0 or cd <= 0 or cs + cd > dur + 0.05:
                raise ValueError(
                    f"caption window [{cs:.2f}+{cd:.2f}] doesn't fit inside moment duration {dur:.2f}"
                )
        return self


class AutoClipperResult(BaseModel):
    source_video: str
    duration_seconds: float
    analyzer_version: str = "auto_clip_v1"
    video_mood: Literal["hype", "calm", "varied", "narrative", "emotional"] = Field(
        description="One-word summary of the VOD's overall energy profile. Informs music pairing downstream.",
    )
    moments: list[Moment]

    @model_validator(mode="after")
    def _validate_moments(self) -> "AutoClipperResult":
        # Sort by start_seconds and reject overlaps >0.5s (short overlaps are
        # fine and can indicate a single long beat with two peaks).
        sorted_moments = sorted(self.moments, key=lambda m: m.start_seconds)
        for i in range(1, len(sorted_moments)):
            prev = sorted_moments[i - 1]
            curr = sorted_moments[i]
            if curr.start_seconds < prev.end_seconds - 0.5:
                raise ValueError(
                    f"moments {i-1} and {i} overlap by more than 0.5s: "
                    f"prev ends {prev.end_seconds}, curr starts {curr.start_seconds}"
                )
        self.moments = sorted_moments
        return self


SYSTEM_INSTRUCTION = (
    "You are a short-form video editor finding the most entertaining moments in a "
    "potentially long source video for an automated highlight reel. The source "
    "could be any genre: gameplay, sports, vlog, cooking, pets, dash-cam, comedy, "
    "IRL, reaction content, anything. Your job is to find moments a typical viewer "
    "would rewind and share, and score them across five universal dimensions.\n\n"
    "SCORING\n"
    "- visual_interest (0-1): visually striking — action, reaction, motion, composition.\n"
    "- audio_peak (0-1): audio intensity — cheering, shouting, laughter, impact, crescendo.\n"
    "- emotional_charge (0-1): emotional payload — surprise, triumph, shock, heartfelt.\n"
    "- narrative_payoff (0-1): punchline / climax / cashing-in on a setup.\n"
    "- technical_skill (0-1): impressive skill / craft on display.\n"
    "- composite: 0.25*visual_interest + 0.25*audio_peak + 0.20*emotional_charge + "
    "  0.15*narrative_payoff + 0.15*technical_skill\n\n"
    "MOMENT SELECTION\n"
    "- Report moments as 3-8 second windows by default. Extend to ~10s for narrative "
    "  moments that need setup. Clip to 2-3s for pure reactions. Never under 1s or over 15s.\n"
    "- Only include moments with composite >= 0.55. Short, tight reels beat padded "
    "  ones. If the video has 30 minutes of dead air and 2 genuine moments, report 2.\n"
    "- No overlapping moments. Each window distinct.\n\n"
    "CAPTIONS\n"
    "- Only set suggested_caption when there's a real audible voice callout "
    "  ('THAT'S CRAZY', 'HOLY', 'LET'S GOOO') or a visible banner text in-frame "
    "  ('KILL', 'TOUCHDOWN', 'ACE'). Never fabricate captions from dead air.\n"
    "- ALL CAPS for voice_comm; natural case for narrative / visual_text.\n"
    "- 2-6 words max. Caption timing must fit inside the moment window.\n\n"
    "EMPHASIS HINT\n"
    "- composite >= 0.85 -> drop_hit (big moments that get velocity ramps + flash/zoom).\n"
    "- 0.70-0.85 -> hold (sustain longer, less punch).\n"
    "- else -> normal.\n\n"
    "VIDEO MOOD\n"
    "- One word summarizing the VOD's overall energy: hype (competitive gaming/sports), "
    "  calm (vlog, lo-fi content), varied (mixed stream), narrative (story-driven), "
    "  emotional (heart-heavy content). Drives music pairing.\n\n"
    "Prefer fewer high-quality moments over many lukewarm ones. Be ruthless."
)


class AutoClipperError(RuntimeError):
    pass


def probe_duration(video_path: Path) -> float:
    """Return video duration via ffprobe. Raises AutoClipperError on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                str(video_path),
            ],
            capture_output=True, text=True, check=True,
        )
        return float(json.loads(result.stdout)["format"]["duration"])
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise AutoClipperError(f"Couldn't probe duration of {video_path}: {exc}") from exc


def _upload_and_wait(client: genai.Client, video_path: Path) -> types.File:
    uploaded = client.files.upload(file=str(video_path))
    start = time.time()
    while uploaded.state and uploaded.state.name == "PROCESSING":
        if time.time() - start > UPLOAD_TIMEOUT_S:
            raise AutoClipperError(
                f"Gemini stuck processing uploaded video for >{UPLOAD_TIMEOUT_S}s"
            )
        time.sleep(UPLOAD_POLL_INTERVAL_S)
        uploaded = client.files.get(name=uploaded.name)
    if not uploaded.state or uploaded.state.name != "ACTIVE":
        raise AutoClipperError(
            f"Gemini upload ended in state {uploaded.state and uploaded.state.name!r}"
        )
    return uploaded


def auto_clip(
    video_path: Path,
    api_key: str,
    *,
    on_progress=None,
) -> AutoClipperResult:
    """Analyze `video_path` with Gemini and return a list of scored moments.

    on_progress(stage, fraction_0_to_1) if supplied gets called at upload,
    processing, and analysis stages. No chunking — videos >60min are rejected.
    """
    if not api_key:
        raise AutoClipperError("Missing Gemini API key")
    if not video_path.exists():
        raise AutoClipperError(f"Video not found: {video_path}")

    duration = probe_duration(video_path)
    if duration > MAX_VIDEO_SECONDS:
        raise AutoClipperError(
            f"Video is {duration/60:.1f} minutes; auto-clip max is "
            f"{MAX_VIDEO_SECONDS/60:.0f} minutes. "
            "Trim the source or split into multiple uploads."
        )

    if on_progress:
        on_progress("uploading to gemini", 0.05)

    client = genai.Client(api_key=api_key)
    uploaded = _upload_and_wait(client, video_path)

    if on_progress:
        on_progress("gemini analyzing moments", 0.50)

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=[
                uploaded,
                "Find the most entertaining moments in this video. "
                "Return strict JSON matching the schema.",
            ],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=AutoClipperResult,
                temperature=0.2,
            ),
        )
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

    parsed: Optional[AutoClipperResult] = getattr(response, "parsed", None)
    if parsed is None:
        try:
            parsed = AutoClipperResult.model_validate_json(response.text or "{}")
        except Exception as exc:
            raise AutoClipperError(
                f"Gemini returned unparseable JSON: {(response.text or '(empty)')[:500]}"
            ) from exc

    # Filter by composite threshold (defense-in-depth; prompt asks for 0.55+ already).
    parsed.moments = [m for m in parsed.moments if m.composite >= MIN_COMPOSITE_SCORE]
    if on_progress:
        on_progress(f"auto-clipper found {len(parsed.moments)} moments", 0.65)
    return parsed


def moments_to_clip_summaries(
    result: AutoClipperResult,
    source_video: Path,
) -> list[dict]:
    """Convert AutoClipperResult into the existing director clip_summaries format.

    Each moment becomes a 'virtual clip' where the per-clip analysis shape
    is synthesized from the moment's scored dimensions and caption data.
    Downstream director + renderer treat these identically to user-uploaded
    clips; the only difference is that every 'clip_path' points at the same
    source video, with start/end offsets selecting the moment window.
    """
    summaries: list[dict] = []
    for i, m in enumerate(result.moments):
        dur = m.end_seconds - m.start_seconds
        kills = [{
            "timestamp_seconds": m.caption_start_in_moment_seconds
                if m.caption_start_in_moment_seconds is not None
                else min(dur - 0.3, max(0.3, dur * 0.6)),
            "confidence": m.composite,
            "description": m.description,
        }]
        reactions = []
        if m.suggested_caption and m.caption_start_in_moment_seconds is not None:
            reactions.append({
                "timestamp_seconds": float(m.caption_start_in_moment_seconds),
                "duration_seconds": float(m.caption_duration_seconds or 1.2),
                "caption": m.suggested_caption,
                "kind": m.caption_kind or "voice_comm",
            })

        summaries.append({
            "index": i,
            "filename": source_video.name,
            "source_path": str(source_video),
            "source_start": float(m.start_seconds),
            "source_end": float(m.end_seconds),
            "duration": float(dur),
            "kills": kills,
            "reactions": reactions,
            "emphasis_hint": m.emphasis_hint,
            "content_tags": m.content_tags,
            "composite_score": m.composite,
        })
    return summaries
