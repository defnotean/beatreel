"""Gemini API key pool: round-robin, parallel dispatch across N keys.

Two motivations:
1. Per-project rate limits — one key has a per-minute quota. Multiple keys let
   us process clips in parallel without tripping RPM limits.
2. Wall-clock speed — a 4-clip reel that serially takes 4 * (upload + process)
   ≈ 90s drops to ~30s when each clip has its own key.

Usage:
    pool = GeminiPool.from_keys([key1, key2, key3])
    results = pool.map(lambda key, clip: detect_kills_ai(clip, api_key=key), clips)

Each work item is handed ONE key; the pool guarantees ≤ len(keys) concurrent
calls to distinct keys. If there's only one key, it's still usable — just no
parallelism gain.

Dead-key handling: when a worker raises an exception whose message looks like
an auth / permission failure, the offending key is marked dead for the rest
of this pool's lifetime and skipped by future dispenses. If every key dies,
subsequent dispense raises explicitly.
"""
from __future__ import annotations

import itertools
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

# Substrings that indicate a key itself is bad — not a transient 429 or server error.
_AUTH_FAILURE_MARKERS = (
    "PERMISSION_DENIED",
    "API key not valid",
    "INVALID_ARGUMENT",
    "api_key_invalid",
    " 401",
    " 403",
    "UNAUTHENTICATED",
)


def _looks_like_auth_failure(err: BaseException) -> bool:
    msg = str(err)
    return any(m in msg for m in _AUTH_FAILURE_MARKERS)


def parse_keys(raw: Optional[str]) -> list[str]:
    """Parse keys from a comma- or newline-separated string."""
    if not raw:
        return []
    out: list[str] = []
    for part in raw.replace("\r", "\n").replace(",", "\n").split("\n"):
        k = part.strip().strip('"').strip("'")
        if k:
            out.append(k)
    seen: set[str] = set()
    unique: list[str] = []
    for k in out:
        if k in seen:
            continue
        seen.add(k)
        unique.append(k)
    return unique


def keys_from_env() -> list[str]:
    """Read keys from GEMINI_API_KEYS (list) or GEMINI_API_KEY (single)."""
    multi = os.environ.get("GEMINI_API_KEYS") or ""
    keys = parse_keys(multi)
    single = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if single and single not in keys:
        keys.append(single)
    return keys


class GeminiPoolExhausted(RuntimeError):
    """All keys have been ejected."""


class GeminiPool:
    """Round-robin key dispenser + parallel executor with dead-key ejection."""

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("GeminiPool requires at least one key")
        self._keys: list[str] = list(keys)
        self._cycle = itertools.cycle(self._keys)
        self._dead: set[str] = set()
        # Count of consecutive failures per key. 3 strikes = dead even if we
        # can't classify the error string.
        self._consecutive_failures: dict[str, int] = {k: 0 for k in keys}
        self._lock = threading.Lock()

    @classmethod
    def from_keys(cls, keys: list[str]) -> "GeminiPool":
        return cls(keys)

    @classmethod
    def from_env(cls) -> Optional["GeminiPool"]:
        keys = keys_from_env()
        return cls(keys) if keys else None

    @property
    def size(self) -> int:
        return len(self._keys)

    @property
    def alive_size(self) -> int:
        with self._lock:
            return len(self._keys) - len(self._dead)

    def next_key(self) -> str:
        """Round-robin to the next alive key. Raises if all keys are dead."""
        with self._lock:
            for _ in range(len(self._keys) * 2):
                k = next(self._cycle)
                if k not in self._dead:
                    return k
            raise GeminiPoolExhausted(
                f"All {len(self._keys)} Gemini keys have been ejected (auth failures)"
            )

    def _mark_failure(self, key: str, err: BaseException) -> None:
        with self._lock:
            if _looks_like_auth_failure(err):
                self._dead.add(key)
                logger.warning(
                    "gemini-pool: ejecting key ...%s (auth failure: %s)",
                    key[-6:], str(err)[:120],
                )
                return
            self._consecutive_failures[key] = self._consecutive_failures.get(key, 0) + 1
            if self._consecutive_failures[key] >= 3:
                self._dead.add(key)
                logger.warning(
                    "gemini-pool: ejecting key ...%s (3 consecutive failures)",
                    key[-6:],
                )

    def _mark_success(self, key: str) -> None:
        with self._lock:
            self._consecutive_failures[key] = 0

    def map(
        self,
        fn: Callable[[str, T], R],
        items: Iterable[T],
        *,
        on_complete: Optional[Callable[[int, int, T, Optional[R], Optional[BaseException]], None]] = None,
    ) -> list[tuple[T, Optional[R], Optional[BaseException]]]:
        """Run fn(key, item) for each item concurrently, bounded by pool size.
        Results are returned in the original item order."""
        items_list = list(items)
        max_workers = min(max(1, self.alive_size), max(1, len(items_list)))
        results: list[tuple[T, Optional[R], Optional[BaseException]]] = [
            (it, None, None) for it in items_list
        ]
        if not items_list:
            return results

        total = len(items_list)
        done_count = 0
        done_count_lock = threading.Lock()

        def worker(index: int, item: T) -> tuple[int, Optional[R], Optional[BaseException]]:
            try:
                key = self.next_key()
            except GeminiPoolExhausted as exc:
                return index, None, exc
            try:
                out = fn(key, item)
                self._mark_success(key)
                return index, out, None
            except Exception as exc:  # NOT BaseException — let KeyboardInterrupt propagate
                logger.warning("gemini-pool: item %d failed on key ...%s: %s", index, key[-6:], exc)
                self._mark_failure(key, exc)
                return index, None, exc

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="gemini-pool") as pool:
            futures = [pool.submit(worker, i, it) for i, it in enumerate(items_list)]
            for fut in as_completed(futures):
                index, out, exc = fut.result()
                results[index] = (items_list[index], out, exc)
                with done_count_lock:
                    done_count += 1
                    snapshot = done_count
                if on_complete is not None:
                    on_complete(snapshot, total, items_list[index], out, exc)
        return results
