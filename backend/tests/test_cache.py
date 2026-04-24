from __future__ import annotations

from pathlib import Path

from beatreel.cache import ClipCache, _hash_file
from beatreel.highlights import Highlight


def _mk_clip(dir_: Path, name: str, data: bytes) -> Path:
    p = dir_ / name
    p.write_bytes(data)
    return p


def test_hash_is_deterministic(tmp_path: Path):
    p = _mk_clip(tmp_path, "a.bin", b"hello world")
    h1 = _hash_file(p)
    h2 = _hash_file(p)
    assert h1 == h2
    assert len(h1) == 24


def test_hash_differs_on_content_change(tmp_path: Path):
    p = _mk_clip(tmp_path, "a.bin", b"hello")
    h1 = _hash_file(p)
    p.write_bytes(b"goodbye")
    h2 = _hash_file(p)
    assert h1 != h2


def test_cache_roundtrip(tmp_path: Path):
    clip = _mk_clip(tmp_path, "clip.mp4", b"A" * 1024)
    cache = ClipCache(tmp_path)
    highlights = [
        Highlight(clip_path=clip, peak_time=1.5, score=0.9, clip_duration=6.0),
        Highlight(clip_path=clip, peak_time=4.2, score=0.7, clip_duration=6.0),
    ]
    assert cache.get(clip, "detector-v1") is None
    cache.set(clip, "detector-v1", highlights)
    recovered = cache.get(clip, "detector-v1")
    assert recovered is not None
    assert len(recovered) == 2
    assert recovered[0].score == 0.9
    assert recovered[1].peak_time == 4.2


def test_cache_miss_on_different_detector(tmp_path: Path):
    clip = _mk_clip(tmp_path, "clip.mp4", b"A" * 1024)
    cache = ClipCache(tmp_path)
    cache.set(clip, "audio-only", [Highlight(clip_path=clip, peak_time=1.0, score=0.5, clip_duration=6.0)])
    assert cache.get(clip, "audio+scene") is None


def test_cache_miss_after_content_change(tmp_path: Path):
    clip = _mk_clip(tmp_path, "clip.mp4", b"A" * 1024)
    cache = ClipCache(tmp_path)
    cache.set(clip, "d1", [Highlight(clip_path=clip, peak_time=1.0, score=0.5, clip_duration=6.0)])
    # Modify file — hash changes
    clip.write_bytes(b"B" * 1024)
    assert cache.get(clip, "d1") is None


def test_cache_get_on_missing_file_returns_none(tmp_path: Path):
    cache = ClipCache(tmp_path)
    assert cache.get(tmp_path / "does_not_exist.mp4", "d1") is None
