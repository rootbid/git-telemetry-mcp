"""Temporal parsing backed by one indexed Git chronology stream.

Python owns human-language interpretation. Git owns repository chronology:
reflogs, commits, stashes, branches, and revision ancestry. The public resolver
never returns Git's natural-language date syntax; consumers receive ISO-8601
bounds suitable for ``git --since``/``--until``.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from git_telemetry_mcp.process import safe_create_subprocess_exec

__all__ = [
    "GitTemporalProvider",
    "parse_colloquial",
    "parse_iso_range",
    "parse_ordinal_ref",
    "parse_relative_duration",
    "resolve_time_bounds",
    "resolve_time_range",
]

_MAX_DURATION_SECONDS = 10 * 365 * 24 * 60 * 60
_MAX_ORDINAL_COUNT = 1000
_MAX_EVENTS = 10_000

_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
}

_COLLOQUIAL: dict[str, tuple[int, int]] = {
    "before lunch": (10, 12),
    "lunch": (12, 13),
    "morning": (6, 12),
    "afternoon": (12, 17),
    "evening": (17, 21),
    "night": (21, 24),
}

_ACTIONS: frozenset[str] = frozenset(
    {
        "push",
        "checkout",
        "commit",
        "reset",
        "merge",
        "pull",
        "rebase",
        "clone",
        "cherry-pick",
        "revert",
        "stash",
        "branch",
    }
)

_DURATION_RE = re.compile(
    r"^(?:last|past|previous|within(?:\s+the)?(?:\s+last)?)?\s*([0-9]+)\s*([a-z]+)(?:\s+ago)?$"
)
_ORDINAL_RE = re.compile(r"^([0-9]+)\s+([a-z-]+?)(?:es|s)?\s+ago$")
_ISO_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:[ T][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?"
    r"(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?$"
)
_GIT_NATIVE_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)\."
    r"(second|seconds|minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\.ago$"
)
_GIT_NATIVE_UNIT_SECONDS = {
    "second": 1,
    "seconds": 1,
    "minute": 60,
    "minutes": 60,
    "hour": 3600,
    "hours": 3600,
    "day": 86400,
    "days": 86400,
    "week": 604800,
    "weeks": 604800,
    "month": 30 * 86400,
    "months": 30 * 86400,
    "year": 365 * 86400,
    "years": 365 * 86400,
}
_REVISION_RE = re.compile(r"^(HEAD(?:~[1-9][0-9]*|\^[1-9][0-9]*)?)$")


def parse_relative_duration(text: str) -> float | None:
    """Parse a bounded human duration into seconds."""
    if not isinstance(text, str):
        return None
    match = _DURATION_RE.fullmatch(text.strip().lower())
    if not match:
        return None
    unit = _UNIT_SECONDS.get(match.group(2))
    if unit is None:
        return None
    try:
        count = int(match.group(1))
    except (TypeError, ValueError):
        return None
    if count < 0 or count > _MAX_DURATION_SECONDS // unit:
        return None
    return count * unit


def parse_ordinal_ref(text: str) -> tuple[int, str] | None:
    """Parse ``3 pushes ago`` into a bounded ``(count, action)`` pair."""
    if not isinstance(text, str):
        return None
    match = _ORDINAL_RE.fullmatch(text.strip().lower())
    if not match:
        return None
    action = match.group(2)
    if action not in _ACTIONS:
        return None
    try:
        count = int(match.group(1))
    except (TypeError, ValueError):
        return None
    if not 1 <= count <= _MAX_ORDINAL_COUNT:
        return None
    return count, action


def _parse_iso_value(value: str) -> datetime | None:
    if not _ISO_RE.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None


def _comparable_datetime(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def parse_iso_range(text: str) -> tuple[str, str] | None:
    """Parse strict ISO dates/times and reject reversed ranges."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.count("..") > 1:
        return None
    if ".." in text:
        first, second = (part.strip() for part in text.split("..", 1))
        first_dt = _parse_iso_value(first)
        second_dt = _parse_iso_value(second)
        if first_dt is None or second_dt is None:
            return None
        try:
            if _comparable_datetime(first_dt) > _comparable_datetime(second_dt):
                return None
        except TypeError:
            return None
        return first, second
    if _parse_iso_value(text) is not None:
        return text, "now"
    return None


def parse_colloquial(text: str, now: datetime) -> tuple[datetime, datetime] | None:
    """Parse exact colloquial windows using local wall-clock boundaries."""
    if not isinstance(text, str) or not isinstance(now, datetime):
        return None
    normalized = " ".join(text.strip().lower().split())
    if normalized == "right before lunch":
        normalized = "before lunch"
    elif normalized.startswith("right "):
        return None

    day_offset = 0
    phrase = normalized
    parts = normalized.split(" ", 1)
    if len(parts) == 2 and parts[0] in {"this", "yesterday", "last"}:
        prefix, phrase = parts
        if prefix in {"yesterday", "last"}:
            day_offset = -1
    if phrase not in _COLLOQUIAL:
        return None

    local = now.astimezone()
    base = (local + timedelta(days=day_offset)).replace(minute=0, second=0, microsecond=0)
    start_hour, end_hour = _COLLOQUIAL[phrase]
    start = base.replace(hour=start_hour)
    end = (base + timedelta(days=1)).replace(hour=0) if end_hour >= 24 else base.replace(hour=end_hour)
    return start, end


def _parse_git_native_duration(text: str) -> float | None:
    if not isinstance(text, str):
        return None
    match = _GIT_NATIVE_RE.fullmatch(text.strip().lower())
    if match is None:
        return None
    unit = match.group(1)
    try:
        count = int(text.strip().lower().split(".", 1)[0])
    except ValueError:
        return None
    seconds = _GIT_NATIVE_UNIT_SECONDS[unit]
    if count > _MAX_DURATION_SECONDS // seconds:
        return None
    return count * seconds


async def _run_git(repo_path: str, *args: str) -> bytes:
    try:
        process = await safe_create_subprocess_exec(
            "git",
            "-C",
            repo_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, _ = await process.communicate()
    except (OSError, ValueError, TimeoutError):
        return b""
    return output


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _event(
    event_type: str,
    timestamp: datetime,
    *,
    anchor: str | None = None,
    action: str | None = None,
    sha: str | None = None,
    message: str | None = None,
    branch: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": event_type,
        "timestamp": _iso(timestamp),
        "date": _iso(timestamp),
    }
    if anchor:
        result["anchor"] = anchor
    if action:
        result["action"] = action
    if sha:
        result["sha"] = sha
    if message:
        result["message"] = message
    if branch:
        result["branch"] = branch
    result.update(extra)
    return result


def _reflog_action(message: str) -> str:
    first = message.split(":", 1)[0].strip().lower()
    for action in sorted(_ACTIONS, key=len, reverse=True):
        if first.startswith(action):
            return action
    return first.split()[0] if first else "unknown"


class GitTemporalProvider:
    """Load and index repository chronology once per provider instance."""

    def __init__(self, repo_path: str = ".") -> None:
        self.repo_path = repo_path
        self._events: list[dict[str, Any]] | None = None
        self._load_task: asyncio.Task[list[dict[str, Any]]] | None = None

    async def events(self) -> list[dict[str, Any]]:
        if self._events is not None:
            return self._events
        if self._load_task is None:
            self._load_task = asyncio.create_task(self._load_events())
        self._events = await self._load_task
        return self._events

    async def _load_events(self) -> list[dict[str, Any]]:
        reflog_raw, commits_raw, stashes_raw, branches_raw = await asyncio.gather(
            _run_git(
                self.repo_path,
                "reflog",
                "--all",
                "--date=iso-strict",
                "--format=%H%x00%gd%x00%gs%x00%cI",
            ),
            _run_git(
                self.repo_path,
                "log",
                "--all",
                "--date=iso-strict",
                "--format=%H%x00%ct%x00%cI%x00%an%x00%s",
            ),
            _run_git(
                self.repo_path,
                "stash",
                "list",
                "--date=iso-strict",
                "--format=%H%x00%ct%x00%cI%x00%gs",
            ),
            _run_git(
                self.repo_path,
                "for-each-ref",
                "refs/heads",
                "--format=%(refname:short)%00%(objectname)%00%(committerdate:iso-strict)",
            ),
        )
        events: list[dict[str, Any]] = []

        for line in reflog_raw.decode(errors="replace").splitlines()[:_MAX_EVENTS]:
            parts = line.split("\x00", 3)
            if len(parts) != 4:
                continue
            sha, selector, message, iso = parts
            timestamp = _parse_timestamp(iso)
            if timestamp is None:
                continue
            events.append(
                _event(
                    "reflog",
                    timestamp,
                    anchor=selector,
                    action=_reflog_action(message),
                    sha=sha[:12],
                    message=message,
                    selector=selector,
                )
            )

        for line in commits_raw.decode(errors="replace").splitlines()[:_MAX_EVENTS]:
            parts = line.split("\x00", 4)
            if len(parts) != 5:
                continue
            sha, _epoch, iso, author, message = parts
            timestamp = _parse_timestamp(iso)
            if timestamp is not None:
                events.append(
                    _event(
                        "commit",
                        timestamp,
                        anchor=sha,
                        sha=sha[:12],
                        message=message,
                        author=author,
                    )
                )

        for line in stashes_raw.decode(errors="replace").splitlines()[:_MAX_EVENTS]:
            parts = line.split("\x00", 3)
            if len(parts) != 4:
                continue
            sha, _epoch, iso, message = parts
            timestamp = _parse_timestamp(iso)
            if timestamp is not None:
                events.append(_event("stash", timestamp, anchor=sha, sha=sha[:12], message=message))

        for line in branches_raw.decode(errors="replace").splitlines()[:_MAX_EVENTS]:
            parts = line.split("\x00", 2)
            if len(parts) != 3:
                continue
            branch, sha, iso = parts
            timestamp = _parse_timestamp(iso)
            if timestamp is not None:
                events.append(_event("branch", timestamp, anchor=branch, sha=sha[:12], branch=branch))

        events.sort(key=lambda item: item["timestamp"], reverse=True)
        return events[:_MAX_EVENTS]

    async def revision_timestamp(self, revision: str) -> tuple[str, str] | None:
        """Resolve ``HEAD~N``/``HEAD^N`` using Git's ancestry timestamps."""
        if not _REVISION_RE.fullmatch(revision):
            return None
        raw = await _run_git(self.repo_path, "rev-list", "--timestamp", "-n", "1", revision)
        line = raw.decode(errors="replace").strip().splitlines()
        if not line:
            return None
        parts = line[0].split()
        if len(parts) < 2:
            return None
        try:
            timestamp = datetime.fromtimestamp(int(parts[0]), tz=UTC)
        except (TypeError, ValueError, OSError):
            return None
        return _iso(timestamp), parts[1][:12]

    async def ordinal(self, count: int, action: str) -> dict[str, Any] | None:
        events = await self.events()
        if action == "commit":
            matches = [event for event in events if event["type"] == "commit"]
        else:
            matches = [
                event
                for event in events
                if event["type"] == "reflog" and event.get("action") == action
            ]
        return matches[count - 1] if len(matches) >= count else None


async def _resolve_ordinal(
    repo_path: str, count: int, action: str, provider: GitTemporalProvider | None = None
) -> dict[str, Any] | None:
    """Compatibility wrapper returning the selected indexed event."""
    if not 1 <= count <= _MAX_ORDINAL_COUNT:
        return None
    return await (provider or GitTemporalProvider(repo_path)).ordinal(count, action)


def _result(
    since: str,
    until: str,
    resolved_from: str,
    confidence: float,
    original: str,
    anchor: str | None = None,
) -> dict[str, Any]:
    return {
        "since": since,
        "until": until,
        "resolved_from": resolved_from,
        "confidence": confidence,
        "original": original,
        "anchor": anchor,
    }


async def resolve_time_range(
    expr: str,
    repo_path: str = ".",
    now: datetime | None = None,
    provider: GitTemporalProvider | None = None,
) -> dict[str, Any]:
    """Resolve human time or repository-relative references to ISO bounds."""
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)
    now_iso = _iso(now)
    text = expr.strip() if isinstance(expr, str) else ""
    lower = text.lower()

    iso_range = parse_iso_range(text)
    if iso_range:
        since, until = iso_range
        return _result(since, now_iso if until == "now" else until, "iso_absolute", 1.0, expr)

    if lower == "now":
        return _result(now_iso, now_iso, "absolute_now", 1.0, expr)

    revision_provider = provider or GitTemporalProvider(repo_path)
    revision = await revision_provider.revision_timestamp(text)
    if revision:
        timestamp, anchor = revision
        return _result(timestamp, now_iso, "git_revision", 0.95, expr, anchor)

    ordinal = parse_ordinal_ref(lower)
    if ordinal:
        count, action = ordinal
        event = await _resolve_ordinal(repo_path, count, action, revision_provider)
        if event:
            return _result(
                event["timestamp"],
                now_iso,
                "reflog_ordinal" if event["type"] == "reflog" else "commit_ordinal",
                0.85,
                expr,
                event.get("anchor"),
            )
        return _result(_iso(now - timedelta(hours=1)), now_iso, "reflog_ordinal_unresolved", 0.4, expr)

    seconds = parse_relative_duration(lower)
    if seconds is None:
        seconds = _parse_git_native_duration(lower)
        resolved_from = "git_native_normalized" if seconds is not None else "fallback"
    else:
        resolved_from = "relative_duration"
    if seconds is not None:
        return _result(_iso(now - timedelta(seconds=seconds)), now_iso, resolved_from, 1.0, expr)

    colloquial = parse_colloquial(lower, now)
    if colloquial:
        return _result(_iso(colloquial[0]), _iso(colloquial[1]), "colloquial", 0.6, expr)

    return _result(_iso(now - timedelta(hours=1)), now_iso, "fallback", 0.3, expr)


async def resolve_time_bounds(
    since: str,
    until: str | None = None,
    repo_path: str = ".",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve a lower/upper query bound without passing raw input to Git."""
    resolved = await resolve_time_range(since, repo_path=repo_path, now=now)
    if until is None or str(until).strip().lower() == "now":
        return resolved
    upper = parse_iso_range(str(until).strip())
    if upper:
        resolved["until"] = upper[0] if upper[1] == "now" else upper[1]
        return resolved
    upper_resolved = await resolve_time_range(str(until), repo_path=repo_path, now=now)
    resolved["until"] = upper_resolved["since"]
    resolved["confidence"] = min(resolved["confidence"], upper_resolved["confidence"])
    return resolved
