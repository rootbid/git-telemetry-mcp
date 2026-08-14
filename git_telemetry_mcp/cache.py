"""Deterministic acceleration cache.

An in-memory LRU with per-entry TTL, keyed by
``SHA256(repo_path + resolved window + git tip)``. Purely derivable
acceleration: a cold cache reproduces identical output (minus the ``_meta``
cache-state marker), and a moved git tip changes the key so stale windows are
never served. Never authoritative state (PLAN.md §2).
"""

import asyncio
import hashlib
import os
import time
from collections import OrderedDict
from pathlib import Path

from git_telemetry_mcp.process import safe_create_subprocess_exec

__all__ = ["SNAPSHOT_CACHE", "TTLCache", "git_state", "git_tip", "make_cache_key"]


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


def make_cache_key(
    repo_path: str, since: str, until: str, tip: str, extra: str = ""
) -> str:
    """SHA-256 over canonical repo path, resolved window, git tip, and extra."""
    try:
        canonical = str(Path(repo_path).resolve())
    except OSError:
        canonical = str(repo_path)
    raw = f"{canonical}|{since}|{until}|{tip}|{extra}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _run_git_state_command(repo_path: str, *args: str) -> bytes:
    try:
        proc = await safe_create_subprocess_exec(
            "git",
            "-C",
            repo_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
    except (OSError, ValueError):
        return b""
    return out + b"\0" + err + str(getattr(proc, "returncode", "")).encode()


def _filesystem_state(repo_path: str) -> bytes:
    """Include metadata Git output does not expose (notably index writes)."""
    root = Path(repo_path)
    git_dir = root / ".git"
    if git_dir.is_file():
        try:
            marker = git_dir.read_text(errors="replace").strip()
            if marker.startswith("gitdir:"):
                git_dir = Path(marker[7:].strip())
                if not git_dir.is_absolute():
                    git_dir = (root / git_dir).resolve()
        except OSError:
            pass
    paths = [git_dir / name for name in ("index", "HEAD", "packed-refs", "logs/HEAD")]
    paths.extend(
        Path(path)
        for path in (
            os.environ.get("HISTFILE", ""),
            str(Path.home() / ".zsh_history"),
            str(Path.home() / ".bash_history"),
        )
        if path
    )
    state: list[str] = []
    for path in paths:
        try:
            stat = path.stat()
            state.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            state.append(f"{path}:missing")
    return "|".join(state).encode()


async def git_tip(repo_path: str) -> str:
    """Return the current HEAD SHA, or ``"no-head"`` when unavailable."""
    output = await _run_git_state_command(repo_path, "rev-parse", "HEAD")
    head = output.split(b"\0", 1)[0].decode(errors="replace").strip()
    return head or "no-head"


async def git_state(repo_path: str) -> str:
    """Return a fingerprint covering HEAD, Git history, reflogs, and dirty state."""
    commands = (
        ("rev-parse", "HEAD"),
        ("status", "--porcelain=v2", "--untracked-files=all"),
        ("diff", "--no-ext-diff"),
        ("diff", "--cached", "--no-ext-diff"),
        ("reflog", "--all", "-n", "1000", "--format=%H|%gd|%gs|%ct"),
        ("log", "--all", "-n", "1000", "--format=%H"),
        ("show-ref",),
    )
    outputs = await asyncio.gather(
        *(_run_git_state_command(repo_path, *command) for command in commands)
    )
    head = outputs[0].split(b"\0", 1)[0].decode(errors="replace").strip() or "no-head"
    digest = hashlib.sha256()
    for output in outputs:
        digest.update(output)
        digest.update(b"\0")
    digest.update(_filesystem_state(repo_path))
    return f"{head}|{digest.hexdigest()}"


# Process-wide singleton used by temporal tools.
SNAPSHOT_CACHE = TTLCache(maxsize=128, ttl_seconds=60.0)
