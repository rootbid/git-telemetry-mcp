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
    since = arguments["since"]
    until = arguments.get("until", "now")
    repo_path = arguments.get("repo_path", ".")
    requested_session = arguments.get("session")

    reflog_cmd = [
        "git",
        "-C",
        repo_path,
        "reflog",
        "-n",
        str(_MAX_TIMELINE_EVENTS),
        "--format=%H|%gd|%gs|%ci",
        f"--since={since}",
        f"--until={until}",
    ]
    log_cmd = [
        "git",
        "-C",
        repo_path,
        "log",
        "--all",
        "-n",
        str(_MAX_TIMELINE_EVENTS),
        "--format=%H|%an|%s|%ci",
        f"--since={since}",
        f"--until={until}",
    ]
    stash_cmd = [
        "git",
        "-C",
        repo_path,
        "stash",
        "list",
        "-n",
        str(_MAX_TIMELINE_EVENTS),
        "--format=%H|%s|%ci",
        f"--since={since}",
        f"--until={until}",
    ]

    reflog_proc, log_proc, stash_proc = await asyncio.gather(
        asyncio.create_subprocess_exec(
            *reflog_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *log_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *stash_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
    )

    reflog_out, _ = await reflog_proc.communicate()
    log_out, _ = await log_proc.communicate()
    stash_out, _ = await stash_proc.communicate()

    events: list[dict[str, Any]] = []

    for line in reflog_out.decode(errors="replace").splitlines()[:_MAX_TIMELINE_EVENTS]:
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) == 4:
            events.append(
                {
                    "type": "reflog",
                    "sha": parts[0][:8],
                    "selector": parts[1],
                    "action": parts[2],
                    "date": _normalized_event_date(parts[3]),
                }
            )

    for line in log_out.decode(errors="replace").splitlines()[:_MAX_TIMELINE_EVENTS]:
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) == 4:
            events.append(
                {
                    "type": "commit",
                    "sha": parts[0][:8],
                    "author": parts[1],
                    "message": parts[2],
                    "date": _normalized_event_date(parts[3]),
                }
            )

    for line in stash_out.decode(errors="replace").splitlines()[:_MAX_TIMELINE_EVENTS]:
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) == 3 and _in_iso_window(parts[2], since, until):
            events.append(
                {
                    "type": "stash",
                    "sha": parts[0][:8],
                    "message": parts[1],
                    "date": _normalized_event_date(parts[2]),
                }
            )

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
        "range": {"since": since, "until": until},
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

    confidence = 1.0
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
