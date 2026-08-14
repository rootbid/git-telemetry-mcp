"""Deterministic acceleration cache.

An in-memory LRU with per-entry TTL, keyed by
``SHA256(repo_path + resolved window + git tip)``. Purely derivable
acceleration: a cold cache reproduces identical output (minus the ``_meta``
cache-state marker), and a moved git tip changes the key so stale windows are
never served. Never authoritative state (PLAN.md §2).
"""

import asyncio
import hashlib
import time
from collections import OrderedDict
from pathlib import Path

__all__ = ["TTLCache", "make_cache_key", "git_tip", "SNAPSHOT_CACHE"]


class TTLCache:
    """Bounded LRU cache with per-entry time-to-live."""

    def __init__(self, maxsize: int = 128, ttl_seconds: float = 60.0):
        self._store: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self.maxsize = maxsize
        self.ttl = ttl_seconds
        self.hits = 0
        self.misses = 0

    def _clock(self) -> float:
        return time.monotonic()

    def get(self, key: str):
        item = self._store.get(key)
        if item is None:
            self.misses += 1
            return None
        expiry, value = item
        if expiry < self._clock():
            del self._store[key]
            self.misses += 1
            return None
        self._store.move_to_end(key)
        self.hits += 1
        return value

    def set(self, key: str, value) -> None:
        self._store[key] = (self._clock() + self.ttl, value)
        self._store.move_to_end(key)
        while len(self._store) > self.maxsize:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()
        self.hits = 0
        self.misses = 0

    def stats(self) -> dict:
        return {"hits": self.hits, "misses": self.misses, "size": len(self._store)}


def make_cache_key(repo_path: str, since: str, until: str, tip: str, extra: str = "") -> str:
    """SHA-256 over canonical repo path, resolved window, git tip, and extra."""
    try:
        canonical = str(Path(repo_path).resolve())
    except OSError:
        canonical = str(repo_path)
    raw = f"{canonical}|{since}|{until}|{tip}|{extra}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def git_tip(repo_path: str) -> str:
    """Current HEAD sha of `repo_path`, or ``"no-head"`` when unavailable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo_path, "rev-parse", "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
    except (OSError, ValueError):
        return "no-head"
    tip = out.decode(errors="replace").strip()
    return tip or "no-head"


# Process-wide singleton used by temporal tools.
SNAPSHOT_CACHE = TTLCache(maxsize=128, ttl_seconds=60.0)
