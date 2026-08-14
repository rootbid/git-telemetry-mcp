"""get_temporal_snapshot — unified temporal context window.

Phase 1: fuzzy time resolution (`temporal.resolve_time_range`) replaces the
former pass-through string, and a deterministic cache keyed by the resolved
window + git tip skips re-scans on identical consecutive calls. The cache is
derivable acceleration only; cold and warm calls yield identical data apart from
the ``_meta.cache`` marker.
"""

import asyncio
import json
from datetime import UTC, datetime

from git_telemetry_mcp.cache import SNAPSHOT_CACHE, git_state, make_cache_key
from git_telemetry_mcp.schema import serialize_telemetry_payload
from git_telemetry_mcp.temporal import resolve_time_range
from git_telemetry_mcp.tools.dev_activity import dev_activity
from git_telemetry_mcp.tools.git_timeline import git_timeline
from git_telemetry_mcp.tools.working_dir_delta import working_dir_delta


def _emit(
    snapshot: dict, confidence: float, repo_path, cache_state: str, key: str
) -> str:
    out = dict(snapshot)
    out["_meta"] = {"cache": cache_state, "cache_key": key[:12]}
    return serialize_telemetry_payload(
        out, repo_path=repo_path, confidence_score=confidence
    )


def _cache_window_value(value: str) -> str:
    """Canonicalize dynamic timestamps to one-second buckets for cache hits."""
    if not isinstance(value, str):
        return str(value)
    if value.strip().lower() == "now":
        return datetime.now(UTC).replace(microsecond=0).isoformat()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.replace(microsecond=0).isoformat()


async def get_temporal_snapshot(arguments: dict) -> str:
    time_range_str = arguments["time_range"]
    repo_path = arguments.get("repo_path", ".")
    granularity = arguments.get("granularity", "raw")

    resolved = await resolve_time_range(time_range_str, repo_path=repo_path)
    since = resolved["since"]
    until = resolved["until"]
    confidence = resolved["confidence"]

    state = await git_state(repo_path)
    # relative expressions cannot reuse an old range indefinitely.
    key = make_cache_key(
        repo_path,
        _cache_window_value(since),
        _cache_window_value(until),
        state,
        extra=f"{time_range_str.strip().lower()}|{granularity}",
    )

    cached = SNAPSHOT_CACHE.get(key)
    if cached is not None:
        snapshot, cached_confidence = cached
        return _emit(snapshot, cached_confidence, repo_path, "hit", key)

    (
        git_timeline_result,
        working_dir_delta_result,
        dev_activity_result,
    ) = await asyncio.gather(
        git_timeline({"since": since, "until": until, "repo_path": repo_path}),
        working_dir_delta({"include_diff": True, "repo_path": repo_path}),
        dev_activity({"since": since, "until": until, "repo_path": repo_path}),
    )

    gt_parsed = json.loads(git_timeline_result)
    wd_parsed = json.loads(working_dir_delta_result)
    da_parsed = json.loads(dev_activity_result)

    snapshot = {
        "range": {
            "time_range_str": time_range_str,
            "since": since,
            "until": until,
            "resolved_from": resolved["resolved_from"],
        },
        "repo_path": repo_path,
        "granularity": granularity,
        "git_timeline": gt_parsed.get("data", gt_parsed),
        "working_dir_delta": wd_parsed.get("data", wd_parsed),
        "dev_activity": da_parsed.get("data", da_parsed),
        "summary": (
            f"Temporal snapshot for '{time_range_str}' "
            f"(resolved via {resolved['resolved_from']}) in {repo_path}"
        ),
    }

    SNAPSHOT_CACHE.set(key, (snapshot, confidence))
    return _emit(snapshot, confidence, repo_path, "miss", key)
