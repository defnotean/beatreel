"""AI reel director.

Given (music analysis, clip analyses, exact beat grid), produce a full cut
plan: which moment from which clip plays over which moment of the music,
with captions and reasoning. Gemini's job here is *structure* — the actual
frame-accurate rendering happens downstream in render.py.

This is text-only — no more file uploads — so it's cheap and fast. The
expensive per-clip + music analysis has already happened; the director just
reasons over the summaries.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, model_validator

from .gemini_detector import ClipAnalysis
from .gemini_music_analyzer import MusicAnalysis

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash"


class DirectedCut(BaseModel):
    clip_index: int = Field(description="Which source clip (0-indexed into the provided clips array).")
    clip_start_seconds: float = Field(description="Start time inside the source clip.")
    clip_end_seconds: float = Field(description="End time inside the source clip.")
    music_start_seconds: float = Field(description="Where this cut lands on the music timeline of the output reel.")
    caption: Optional[str] = Field(
        default=None,
        description="Optional overlay text for this cut. <=6 words, ALL CAPS hits hardest. Only when there's a real reaction or banner.",
    )
    caption_start_relative: Optional[float] = Field(
        default=None,
        description="When during the cut the caption appears, seconds from the cut's start. Null if no caption.",
    )
    caption_duration: Optional[float] = Field(
        default=None, description="How long the caption holds, seconds.",
    )
    emphasis: Literal["normal", "hold", "drop_hit"] = Field(
        default="normal",
        description="hold = slow-mo / sustained shot; drop_hit = lands on a music drop.",
    )
    reason: str = Field(description="One sentence why this cut exists here.")


class DirectedReel(BaseModel):
    intro_hold_seconds: float = Field(
        default=0.0,
        description="If the music has a quiet intro, how long to hold on a title/black before the first cut. 0 = cut in immediately.",
    )
    title_caption: Optional[str] = Field(
        default=None,
        description="Optional 1-3 word title-card text to overlay during the intro hold. Null = no card.",
    )
    outro_hold_seconds: float = Field(
        default=0.0,
        description="Extra black-out / fade after the last cut. 0.5-1.5 typical.",
    )
    chosen_intensity: Literal["chill", "balanced", "hype"]
    cuts: list[DirectedCut]

    @model_validator(mode="after")
    def _validate_cuts(self) -> "DirectedReel":
        """Structural checks. Cross-referential checks (captions referencing
        real reactions) live in the caller — this validator can only see what's
        inside the schema itself."""
        if not self.cuts:
            return self

        prev_music_end = -1e-6
        seen_windows: set[tuple[int, int, int]] = set()
        for i, c in enumerate(self.cuts):
            if c.clip_end_seconds <= c.clip_start_seconds:
                raise ValueError(
                    f"cut {i}: clip_end_seconds ({c.clip_end_seconds}) must exceed clip_start_seconds ({c.clip_start_seconds})"
                )
            cut_duration = c.clip_end_seconds - c.clip_start_seconds
            if cut_duration < 0.5:
                raise ValueError(f"cut {i}: duration {cut_duration:.2f}s below 0.5s floor")

            # music_start must be monotonic (cuts play in order, no overlap).
            # Allow a 50ms tolerance for LLM rounding.
            if c.music_start_seconds + 1e-3 < prev_music_end - 0.05:
                raise ValueError(
                    f"cut {i}: music_start ({c.music_start_seconds}) overlaps previous cut's window (prev end {prev_music_end:.2f})"
                )
            prev_music_end = c.music_start_seconds + cut_duration

            # No reused windows (quantized to 0.1s so near-identical windows collapse).
            key = (
                c.clip_index,
                int(round(c.clip_start_seconds * 10)),
                int(round(c.clip_end_seconds * 10)),
            )
            if key in seen_windows:
                raise ValueError(
                    f"cut {i}: reuses clip {c.clip_index} window "
                    f"[{c.clip_start_seconds:.1f},{c.clip_end_seconds:.1f}]"
                )
            seen_windows.add(key)

            # Caption-window sanity (if a caption is set, it must fit inside the cut)
            if c.caption:
                cs = c.caption_start_relative if c.caption_start_relative is not None else 0.0
                cd = c.caption_duration if c.caption_duration is not None else 1.5
                if cs < 0 or cd <= 0:
                    raise ValueError(f"cut {i}: caption_start/duration must be non-negative")
                if cs + cd > cut_duration + 0.1:
                    raise ValueError(
                        f"cut {i}: caption {cs:.2f}+{cd:.2f}s overruns cut duration {cut_duration:.2f}s"
                    )

        if self.intro_hold_seconds < 0 or self.outro_hold_seconds < 0:
            raise ValueError("intro/outro_hold_seconds must be non-negative")
        return self


SYSTEM_INSTRUCTION = (
    "You are directing a gameplay highlight reel. You get: (1) an analysis of "
    "the music (vibe, sections, drops), (2) a summary of each available clip "
    "including kills and reaction moments with suggested caption text, and "
    "(3) a precise beat grid from librosa. Your output is a complete cut plan.\n\n"
    "Rules:\n"
    "- Match drops to big moments. Multi-kills, aces, or clutches should land "
    "  ON a drop timestamp from the music analysis.\n"
    "- Cut durations: hype = 1.2-2.5s, balanced = 2-4s, chill/emotional = 3.5-6.5s. "
    "  Clamp floors — no cut shorter than 1.0s.\n"
    "- Total cuts duration + intro_hold + outro_hold should equal the target_duration. "
    "  It's OK to end earlier if we run out of good material (prefer a tight 30s "
    "  over a padded 60s).\n"
    "- Music-start alignment: the first cut's music_start = intro_hold. Each "
    "  subsequent cut's music_start = previous.music_start + previous.duration. "
    "  No overlaps, no gaps.\n"
    "- Snap each cut's music_start to the NEAREST librosa beat from the grid "
    "  (you'll be given it). This keeps cuts on the beat.\n"
    "- Captions: only include when the clip analysis flagged a reaction near "
    "  the chosen clip_start. Don't invent captions. If a reaction exists, map "
    "  caption_start_relative + caption_duration from that reaction's timing "
    "  inside the cut window.\n"
    "- Don't reuse the same ~3-second window of the same clip twice. Each cut "
    "  should cover a distinct moment.\n"
    "- chosen_intensity defaults to music_analysis.recommended_intensity unless "
    "  the clip set is sparse (few kills/reactions), in which case step down one.\n"
    "- intro_hold: 0 if the music opens hot. 0.3-1s if there's a soft intro. "
    "  Title card optional — short words like the player's tag or 'VALORANT'.\n"
    "- outro_hold: 0.5-1.2s is typical so the reel doesn't cut abruptly."
)


class DirectorError(RuntimeError):
    pass


def direct_reel(
    *,
    music_analysis: MusicAnalysis,
    clip_summaries: list[dict],
    beats_seconds: list[float],
    tempo_bpm: float,
    target_duration: float,
    api_key: str,
) -> DirectedReel:
    """Call Gemini as director. `clip_summaries` is a list of dicts with
    {index, filename, duration, kills: [...], reactions: [...]}."""
    if not api_key:
        raise DirectorError("Missing Gemini API key")

    client = genai.Client(api_key=api_key)

    payload = {
        "target_duration": target_duration,
        "tempo_bpm": tempo_bpm,
        "beat_grid_seconds": beats_seconds[:800],  # cap for prompt size
        "music_analysis": music_analysis.model_dump(),
        "clips": clip_summaries,
    }

    response = client.models.generate_content(
        model=MODEL,
        contents=[
            "Direct a highlight reel from this analysis payload. Output strict JSON.",
            json.dumps(payload, separators=(",", ":")),
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=DirectedReel,
            temperature=0.2,
        ),
    )

    parsed: Optional[DirectedReel] = getattr(response, "parsed", None)
    if parsed is None:
        try:
            parsed = DirectedReel.model_validate_json(response.text or "{}")
        except Exception as exc:
            raise DirectorError(
                f"Director returned unparseable JSON: {(response.text or '(empty)')[:500]}"
            ) from exc
    return parsed


def summarize_for_director(
    clip_paths: list[Path],
    analyses: dict[Path, ClipAnalysis],
    durations: dict[Path, float],
) -> list[dict]:
    """Build the compact per-clip summary the director prompt takes."""
    out: list[dict] = []
    for idx, p in enumerate(clip_paths):
        analysis = analyses.get(p)
        if analysis is None:
            continue
        out.append({
            "index": idx,
            "filename": p.name,
            "duration": float(durations.get(p, 0.0)),
            "kills": [k.model_dump() for k in analysis.kills],
            "reactions": [r.model_dump() for r in analysis.reactions],
        })
    return out
