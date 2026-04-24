"""Per-clip score cache. Keyed by (file hash, detector version).

Makes re-rolls cheap: re-analysis is skipped and we only replan + re-render.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .highlights import Highlight

CACHE_VERSION = 1  # bump to invalidate existing cache files
CACHE_DIR_NAME = ".beatreel-cache"


def _hash_file(path: Path, chunk: int = 1 << 16) -> str:
    """Content hash. Fast enough for typical clip sizes; stable across runs."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()[:24]


class ClipCache:
    """Cache directory colocated with the clips (so it moves with them)."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir / CACHE_DIR_NAME
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _entry_path(self, file_hash: str, detector: str) -> Path:
        return self.base_dir / f"{detector}_v{CACHE_VERSION}_{file_hash}.json"

    def get(self, clip_path: Path, detector: str) -> Optional[list[Highlight]]:
        try:
            file_hash = _hash_file(clip_path)
        except OSError:
            return None
        entry = self._entry_path(file_hash, detector)
        if not entry.exists():
            return None
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return [
            Highlight(
                clip_path=Path(h["clip_path"]),
                peak_time=float(h["peak_time"]),
                score=float(h["score"]),
                clip_duration=float(h["clip_duration"]),
            )
            for h in data.get("highlights", [])
        ]

    def set(self, clip_path: Path, detector: str, highlights: list[Highlight]) -> None:
        try:
            file_hash = _hash_file(clip_path)
        except OSError:
            return
        entry = self._entry_path(file_hash, detector)
        payload = {
            "highlights": [
                {
                    "clip_path": str(h.clip_path),
                    "peak_time": h.peak_time,
                    "score": h.score,
                    "clip_duration": h.clip_duration,
                }
                for h in highlights
            ]
        }
        tmp = entry.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(entry)
