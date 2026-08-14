"""Unit and integration tests for the deterministic cache (Phase 1)."""

import json

import pytest

from git_telemetry_mcp.cache import SNAPSHOT_CACHE, TTLCache, make_cache_key
from git_telemetry_mcp.tools.temporal_snapshot import get_temporal_snapshot


def test_ttl_cache_hit_miss():
    cache = TTLCache(maxsize=8, ttl_seconds=60)
    assert cache.get("k") is None
    assert cache.misses == 1
    cache.set("k", 123)
    assert cache.get("k") == 123
    assert cache.hits == 1


def test_ttl_cache_expiry(monkeypatch):
    cache = TTLCache(maxsize=8, ttl_seconds=10)
    clock = {"t": 1000.0}
    monkeypatch.setattr(cache, "_clock", lambda: clock["t"])
    cache.set("k", "v")
    assert cache.get("k") == "v"
    clock["t"] += 11  # advance past TTL
    assert cache.get("k") is None


def test_ttl_cache_lru_eviction():
    cache = TTLCache(maxsize=2, ttl_seconds=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # 'a' now most-recently-used
    cache.set("c", 3)  # evicts LRU -> 'b'
    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3


def test_make_cache_key_deterministic_and_tip_sensitive(tmp_path):
    k1 = make_cache_key(str(tmp_path), "last 45m", "", "tip1", extra="raw")
    k2 = make_cache_key(str(tmp_path), "last 45m", "", "tip1", extra="raw")
    k3 = make_cache_key(str(tmp_path), "last 45m", "", "tip2", extra="raw")
    assert k1 == k2
    assert k1 != k3


@pytest.mark.asyncio
async def test_snapshot_cache_hit_miss(repo_with_history):
    SNAPSHOT_CACHE.clear()
    repo_path = str(repo_with_history)

    first = json.loads(
        await get_temporal_snapshot({"time_range": "last 45m", "repo_path": repo_path})
    )
    assert first["data"]["_meta"]["cache"] == "miss"

    second = json.loads(
        await get_temporal_snapshot({"time_range": "last 45m", "repo_path": repo_path})
    )
    assert second["data"]["_meta"]["cache"] == "hit"

    # Warm result reproduces the cold payload apart from the cache marker.
    del first["data"]["_meta"]
    del second["data"]["_meta"]
    assert first["data"] == second["data"]


@pytest.mark.asyncio
async def test_snapshot_bounded_window(repo_with_history):
    from datetime import datetime

    SNAPSHOT_CACHE.clear()
    payload = json.loads(
        await get_temporal_snapshot(
            {"time_range": "last 45m", "repo_path": str(repo_with_history)}
        )
    )
    rng = payload["data"]["range"]
    assert rng["resolved_from"] == "relative_duration"
    since = datetime.fromisoformat(rng["since"])
    until = datetime.fromisoformat(rng["until"])
    delta = (until - since).total_seconds()
    assert abs(delta - 45 * 60) < 1


@pytest.mark.asyncio
async def test_snapshot_cache_invalidates_on_worktree_change(repo_with_history):
    SNAPSHOT_CACHE.clear()
    repo_path = str(repo_with_history)
    first = json.loads(
        await get_temporal_snapshot({"time_range": "last 45m", "repo_path": repo_path})
    )
    assert first["data"]["_meta"]["cache"] == "miss"
    (repo_with_history / "new-work.py").write_text("print('changed')\n")
    second = json.loads(
        await get_temporal_snapshot({"time_range": "last 45m", "repo_path": repo_path})
    )
    assert second["data"]["_meta"]["cache"] == "miss"
