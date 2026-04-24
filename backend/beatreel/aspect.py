"""Aspect-ratio presets and their ffmpeg video filter expressions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AspectPreset = Literal["landscape", "portrait", "square"]


@dataclass(frozen=True)
class AspectSpec:
    width: int
    height: int
    # A -vf filter chain that scales + crops/pads a source to (width, height).
    # We always prefer center-crop for non-landscape (social aspect ratios)
    # because letterboxed content looks unprofessional on mobile feeds.
    video_filter: str

    @property
    def label(self) -> str:
        return f"{self.width}x{self.height}"


_LANDSCAPE = AspectSpec(
    width=1920,
    height=1080,
    video_filter=(
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1,fps=60"
    ),
)

_PORTRAIT = AspectSpec(
    width=1080,
    height=1920,
    video_filter=(
        # Center-crop to 9:16 from whatever the source is (typically 16:9 gameplay).
        "scale=-2:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920:(iw-1080)/2:(ih-1920)/2,"
        "setsar=1,fps=60"
    ),
)

_SQUARE = AspectSpec(
    width=1080,
    height=1080,
    video_filter=(
        "scale=-2:1080:force_original_aspect_ratio=increase,"
        "crop=1080:1080:(iw-1080)/2:(ih-1080)/2,"
        "setsar=1,fps=60"
    ),
)

_PRESETS: dict[AspectPreset, AspectSpec] = {
    "landscape": _LANDSCAPE,
    "portrait": _PORTRAIT,
    "square": _SQUARE,
}


def get_aspect(preset: AspectPreset) -> AspectSpec:
    if preset not in _PRESETS:
        raise ValueError(f"Unknown aspect preset: {preset!r}")
    return _PRESETS[preset]


def available() -> list[AspectPreset]:
    return list(_PRESETS.keys())
