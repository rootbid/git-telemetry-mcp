"""Fuzzy temporal parser.

Resolves natural-language ranges ("last 45m", "right before lunch"), ordinal
git references ("3 checkouts ago", "2 commits ago") via reflog ordering, and
absolute ISO ranges ("2026-08-01..2026-08-05") into a normalized window::

    {"since": <str>, "until": <str>, "resolved_from": <str>,
     "confidence": <float>, "original": <str>}

`since`/`until` are emitted as ISO-8601 strings (or the literal ``"now"``),
both of which downstream ``git log --since/--until`` understand. Pure parsing
helpers are subprocess-free and unit-testable; only ordinal resolution touches
the reflog.
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta

from git_telemetry_mcp.process import safe_create_subprocess_exec

__all__ = [
    "parse_colloquial",
    "parse_iso_range",
    "parse_ordinal_ref",
    "parse_relative_duration",
    "resolve_time_range",
]

# Keep parsing bounded before constructing ``timedelta`` or passing a date to git.
_MAX_DURATION_SECONDS = 10 * 365 * 24 * 60 * 60
_MAX_ORDINAL_COUNT = 1000

# Duration unit -> seconds. Singular and plural forms are both accepted.
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

# Colloquial windows -> (start_hour, end_hour) in local time, [start, end).
_COLLOQUIAL: dict[str, tuple[int, int]] = {
    "before lunch": (10, 12),
    "lunch": (12, 13),
    "morning": (6, 12),
    "afternoon": (12, 17),
    "evening": (17, 21),
    "night": (21, 24),
}

# Reflog action verbs recognized for ordinal references.
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
    }
)

_DURATION_RE = re.compile(
    r"^(?:last|past|previous|within(?:\s+the)?(?:\s+last)?)?\s*([0-9]+)\s*([a-z]+)(?:\s+ago)?$"
)
_ORDINAL_RE = re.compile(r"^([0-9]+)\s+([a-z-]+?)(?:es|s)?\s+ago$")
# ``datetime.fromisoformat`` performs calendar and offset validation.  The
# regex is intentionally narrower so permissive parser extensions do not turn
# malformed user input into a git date expression.
_ISO_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:[ T][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?"
    r"(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?$"
)
_GIT_NATIVE_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)\."
    r"(?:second|seconds|minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\.ago$"
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


def parse_relative_duration(text: str) -> float | None:
    """Parse a bounded relative duration (``last 45m`` or ``2 days``)."""
    if not isinstance(text, str):
        return None
    m = _DURATION_RE.fullmatch(text.strip().lower())
    if not m:
        return None
    unit = _UNIT_SECONDS.get(m.group(2))
    if unit is None:
        return None
    try:
        count = int(m.group(1))
    except (TypeError, ValueError):
        return None
    if count < 0 or count > _MAX_DURATION_SECONDS // unit:
        return None
    return count * unit


def parse_ordinal_ref(text: str) -> tuple[int, str] | None:
    """Parse an ordinal git reference into a bounded ``(count, action)``."""
    if not isinstance(text, str):
        return None
    m = _ORDINAL_RE.fullmatch(text.strip().lower())
    if not m:
        return None
    action = m.group(2)
    if action not in _ACTIONS:
        return None
    try:
        count = int(m.group(1))
    except (TypeError, ValueError):
        return None
    if not 1 <= count <= _MAX_ORDINAL_COUNT:
        return None
    return count, action


def _parse_iso_value(value: str) -> datetime | None:
    if not _ISO_RE.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError:
        return None


def _comparable_datetime(value: datetime) -> datetime:
    """Treat a timezone-less ISO date as UTC for range ordering only."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def parse_iso_range(text: str) -> tuple[str, str] | None:
    """Parse strict ISO-8601 dates/times and reject invalid or reversed ranges."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.count("..") > 1:
        return None
    if ".." in text:
        a, b = (part.strip() for part in text.split("..", 1))
        a_dt = _parse_iso_value(a)
        b_dt = _parse_iso_value(b)
        if a_dt is None or b_dt is None:
            return None
        try:
            if _comparable_datetime(a_dt) > _comparable_datetime(b_dt):
                return None
        except TypeError:
            return None
        return a, b
    if _parse_iso_value(text) is not None:
        return text, "now"
    return None


def parse_colloquial(text: str, now: datetime) -> tuple[datetime, datetime] | None:
    """Parse one of the exact colloquial windows using half-open endpoints."""
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
    base = (local + timedelta(days=day_offset)).replace(
        minute=0, second=0, microsecond=0
    )
    start_hour, end_hour = _COLLOQUIAL[phrase]
    start = base.replace(hour=start_hour)
    end = (
        (base + timedelta(days=1)).replace(hour=0)
        if end_hour >= 24
        else base.replace(hour=end_hour)
    )
    return start, end


def _looks_git_native(text: str) -> bool:
    if not isinstance(text, str):
        return False
    match = _GIT_NATIVE_RE.fullmatch(text.strip().lower())
    if match is None:
        return False
    count_text, unit = text.strip().lower().rsplit(".", 2)[:2]
    try:
        count = int(count_text)
    except ValueError:
        return False
    return count <= _MAX_DURATION_SECONDS // _GIT_NATIVE_UNIT_SECONDS[unit]


def _result(
    since: str, until: str, resolved_from: str, confidence: float, original: str
) -> dict:
    return {
        "since": since,
        "until": until,
        "resolved_from": resolved_from,
        "confidence": confidence,
        "original": original,
    }


async def _resolve_ordinal(repo_path: str, count: int, action: str) -> str | None:
    """Return the ISO timestamp of the Nth-most-recent reflog action."""
    if not 1 <= count <= _MAX_ORDINAL_COUNT:
        return None
    try:
        proc = await safe_create_subprocess_exec(
            "git",
            "-C",
            repo_path,
            "reflog",
            "-n",
            str(_MAX_ORDINAL_COUNT),
            "--format=%gs|%cI",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
    except (OSError, ValueError):
        return None

    seen = 0
    for line in out.decode(errors="replace").splitlines()[:_MAX_ORDINAL_COUNT]:
        if "|" not in line:
            continue
        msg, iso = line.rsplit("|", 1)
        verb_match = re.match(r"^([a-z-]+)", msg.strip().lower())
        if verb_match and verb_match.group(1) == action:
            seen += 1
            if seen == count:
                return iso.strip()
    return None


async def resolve_time_range(
    expr: str,
    repo_path: str = ".",
    now: datetime | None = None,
) -> dict:
    """Resolve a fuzzy time expression into a normalized `{since, until, ...}` window.

    Resolution order: ISO absolute -> ordinal reflog ref -> relative duration ->
    colloquial window -> git-native passthrough -> 1-hour fallback. Ambiguity is
    reported through ``confidence``.
    """
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)
    text = expr.strip() if isinstance(expr, str) else ""
    lower = text.lower()

    iso = parse_iso_range(text)
    if iso:
        return _result(iso[0], iso[1], "iso_absolute", 1.0, expr)

    ordinal = parse_ordinal_ref(lower)
    if ordinal:
        count, action = ordinal
        ts = await _resolve_ordinal(repo_path, count, action)
        if ts:
            return _result(ts, now.isoformat(), "reflog_ordinal", 0.85, expr)
        return _result(
            (now - timedelta(hours=1)).isoformat(),
            now.isoformat(),
            "reflog_ordinal_unresolved",
            0.4,
            expr,
        )

    secs = parse_relative_duration(lower)
    if secs is not None:
        return _result(
            (now - timedelta(seconds=secs)).isoformat(),
            now.isoformat(),
            "relative_duration",
            1.0,
            expr,
        )

    colloq = parse_colloquial(lower, now)
    if colloq:
        return _result(
            colloq[0].isoformat(), colloq[1].isoformat(), "colloquial", 0.6, expr
        )

    if _looks_git_native(lower):
        return _result(text, "now", "git_native", 0.9, expr)

    return _result(
        (now - timedelta(hours=1)).isoformat(),
        now.isoformat(),
        "fallback",
        0.3,
        expr,
    )
