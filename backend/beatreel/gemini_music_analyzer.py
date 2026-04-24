"""Gemini-based music analysis.

Feeds the music track to Gemini 2.5 Flash and asks for structured
understanding of its arc so the rest of the pipeline can pick cuts that
land on the right emotional beats (drops, breakdowns, outros).

Returns a MusicAnalysis object the director consumes.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash"
UPLOAD_TIMEOUT_S = 60
POLL_S = 2


Vibe = Literal["hype", "balanced", "chill", "emotional"]


class MusicSection(BaseModel):
    start_seconds: float
    end_seconds: float
    label: Literal["intro", "build", "drop", "verse", "chorus", "breakdown", "bridge", "outro"]
    energy: float = Field(ge=0.0, le=1.0, description="0 = quiet/sparse, 1 = peak energy.")
    notes: str = Field(description="1-phrase description of what's happening musically.")


class DropHit(BaseModel):
    timestamp_seconds: float = Field(description="Moment of impact — kick hits, chorus opens, vocal punches in.")
    intensity: float = Field(ge=0.0, le=1.0)
    description: str


class MusicAnalysis(BaseModel):
    vibe: Vibe = Field(description="Overall feel. Drives the default reel intensity.")
    recommended_intensity: Literal["chill", "balanced", "hype"] = Field(
        description="What cut cadence to use. Map: emotional/chill -> chill, balanced -> balanced, hype -> hype.",
    )
    tempo_bpm_estimated: float = Field(description="Rough BPM. Librosa will compute the precise value — this is a sanity check.")
    sections: list[MusicSection]
    drops: list[DropHit] = Field(description="Big moments the reel should anchor to.")
    best_start_seconds: float = Field(
        default=0.0,
        description="Where the reel should START in the music. 0 unless the track has a long ambient intro we should skip.",
    )


SYSTEM_INSTRUCTION = (
    "You are scoring a piece of music for a gameplay highlight reel. Your job is "
    "to identify the structural moments that a video editor would cut on, and to "
    "characterize the track's emotional arc.\n\n"
    "Rules:\n"
    "- Drops are the specific timestamps where energy peaks — kick hits, chorus "
    "  openings, vocal punches. Be precise to 0.1s.\n"
    "- Sections must cover the entire track without gaps or overlaps.\n"
    "- recommended_intensity comes from vibe: hype/emotional music -> hype or chill; "
    "  steady/mid-energy -> balanced.\n"
    "- best_start_seconds > 0 only if there is a genuinely long (>3s) ambient "
    "  intro the reel should skip over. Otherwise 0.\n"
    "- Prefer precision over completeness: 6 well-placed drops beat 20 weak ones."
)


class MusicAnalysisError(RuntimeError):
    pass


def _upload_and_wait(client: genai.Client, path: Path) -> types.File:
    uploaded = client.files.upload(file=str(path))
    start = time.time()
    while uploaded.state and uploaded.state.name == "PROCESSING":
        if time.time() - start > UPLOAD_TIMEOUT_S:
            raise MusicAnalysisError(f"Gemini stuck on music upload >{UPLOAD_TIMEOUT_S}s")
        time.sleep(POLL_S)
        uploaded = client.files.get(name=uploaded.name)
    if not uploaded.state or uploaded.state.name != "ACTIVE":
        raise MusicAnalysisError(
            f"Gemini music upload ended in {uploaded.state and uploaded.state.name!r}"
        )
    return uploaded


def analyze_music(music_path: Path, api_key: str) -> MusicAnalysis:
    if not api_key:
        raise MusicAnalysisError("Missing Gemini API key")
    if not music_path.exists():
        raise MusicAnalysisError(f"Music not found: {music_path}")

    client = genai.Client(api_key=api_key)
    uploaded = _upload_and_wait(client, music_path)
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=[uploaded, "Analyze this track for a gameplay highlight reel."],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=MusicAnalysis,
                temperature=0.2,
            ),
        )
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

    parsed: Optional[MusicAnalysis] = getattr(response, "parsed", None)
    if parsed is None:
        try:
            parsed = MusicAnalysis.model_validate_json(response.text or "{}")
        except Exception as exc:
            raise MusicAnalysisError(
                f"Gemini returned unparseable JSON: {(response.text or '(empty)')[:400]}"
            ) from exc
    return parsed
