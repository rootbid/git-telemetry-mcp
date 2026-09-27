"""get_session_timeline — unified view of recent dev activity.

Phase 1: raw events are segmented into logical work sessions
(`sessions.segment_sessions`) so callers can ask for "Session #3" without
timestamps via the optional ``session`` argument.
"""

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from git_telemetry_mcp.schema import serialize_telemetry_payload
from git_telemetry_mcp.sessions import segment_sessions, select_session
from git_telemetry_mcp.temporal import GitTemporalProvider, resolve_time_bounds

_MAX_TIMELINE_EVENTS = 1000


def _parse_event_datetime(value: str) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        try:
            parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S %z")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalized_event_date(value: str) -> str:
    parsed = _parse_event_datetime(value)
    return parsed.isoformat() if parsed is not None else value


def _in_iso_window(value: str, since: str, until: str) -> bool:
    event_dt = _parse_event_datetime(value)
    if event_dt is None:
        return False
    bounds: list[datetime | None] = []
    for bound in (since, until):
        if bound == "now":
            bounds.append(datetime.now(UTC))
        else:
            bounds.append(_parse_event_datetime(bound))
    lower, upper = bounds
    if lower is not None and event_dt < lower:
        return False
    return not (upper is not None and event_dt > upper)


async def get_session_timeline(arguments: dict) -> str:
    since_input = arguments["since"]
    until_input = arguments.get("until")
    repo_path = arguments.get("repo_path", ".")
    requested_session = arguments.get("session")
    resolved = await resolve_time_bounds(
        since_input, until_input, repo_path=repo_path
    )
    since = resolved["since"]
    until = resolved["until"]
    provider = GitTemporalProvider(repo_path)
    indexed_events = await provider.events()
    events: list[dict[str, Any]] = []
    for source_event in indexed_events:
        if not _in_iso_window(source_event["timestamp"], since, until):
            continue
        event: dict[str, Any] = {
            key: source_event[key]
            for key in ("type", "sha", "message", "author", "date", "anchor", "selector", "action", "branch")
            if key in source_event
        }
        events.append(event)
    events = events[:_MAX_TIMELINE_EVENTS]

    # The provider already normalized and bounded reflog, commit, stash, and
    # branch chronology into one stream. Keep filesystem mtimes as a separate
    # best-effort signal because they are not Git chronology.

    # Recently modified files (by mtime)
    modified_files: list[tuple[str, float]] = []
    try:
        find_cmd = [
            "find",
            repo_path,
            "-maxdepth",
            "3",
            "-name",
            "*.py",
            "-o",
            "-name",
            "*.ts",
            "-o",
            "-name",
            "*.js",
            "-o",
            "-name",
            "*.go",
            "-o",
            "-name",
            "*.rs",
        ]
        find_proc = await asyncio.create_subprocess_exec(
            *find_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        find_out, _ = await find_proc.communicate()
        for fpath in find_out.decode().strip().splitlines():
            if ".git" in fpath or not fpath:
                continue
            try:
                mtime = os.path.getmtime(fpath)
                modified_files.append((fpath, mtime))
            except OSError:
                pass
        modified_files.sort(key=lambda item: item[1], reverse=True)
        modified_files = modified_files[:10]
    except (OSError, RuntimeError):
        modified_files = []

    events.sort(key=lambda e: e.get("date", ""), reverse=True)
    events = events[:_MAX_TIMELINE_EVENTS]

    sessions = segment_sessions(events)
    session_summaries = [
        {k: v for k, v in s.items() if k != "events"} for s in sessions
    ]

    result: dict[str, Any] = {
        "range": {
            "since": since,
            "until": until,
            "resolved_from": resolved["resolved_from"],
            "confidence": resolved["confidence"],
            "anchor": resolved.get("anchor"),
        },
        "events": events[:50],
        "recently_modified_files": [fpath for fpath, _mtime in modified_files],
        "sessions": session_summaries,
        "summary": (
            f"{len(events)} events ({sum(1 for e in events if e['type'] == 'commit')} commits, "
            f"{sum(1 for e in events if e['type'] == 'reflog')} reflog, "
            f"{sum(1 for e in events if e['type'] == 'stash')} stashes) "
            f"across {len(sessions)} session(s)"
        ),
    }

    confidence = float(resolved["confidence"])
    if requested_session is not None:
        selected = select_session(sessions, requested_session)
        if selected is not None:
            result["events"] = selected["events"]
            result["selected_session"] = {
                k: v for k, v in selected.items() if k != "events"
            }
            result["summary"] = (
                f"{selected['label']}: {selected['event_count']} events "
                f"from {selected['start']} to {selected['end']}"
            )
        else:
            result["selected_session"] = None
            result["session_error"] = f"No session matching {requested_session!r}"
            confidence = 0.5

    return serialize_telemetry_payload(
        result, repo_path=repo_path, confidence_score=confidence
    )
