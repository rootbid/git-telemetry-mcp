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
from datetime import datetime, timedelta, timezone

__all__ = [
    "resolve_time_range",
    "parse_relative_duration",
    "parse_iso_range",
    "parse_colloquial",
    "parse_ordinal_ref",
]

# Duration unit -> seconds. Singular and plural forms are both accepted.
_UNIT_SECONDS: dict[str, int] = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
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
_ACTIONS: frozenset[str] = frozenset({
    "push", "checkout", "commit", "reset", "merge", "pull",
    "rebase", "clone", "cherry-pick", "revert", "stash",
})

_DURATION_RE = re.compile(
    r"^(?:last|past|previous|within(?:\s+the)?(?:\s+last)?)?\s*(\d+)\s*([a-z]+)(?:\s+ago)?$"
)
_ORDINAL_RE = re.compile(r"^(\d+)\s+([a-z-]+?)(?:es|s)?\s+ago$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
_GIT_NATIVE_RE = re.compile(r"\d+\.\w+\.ago")


def parse_relative_duration(text: str) -> float | None:
    """Parse a relative duration ("last 45m", "past 2 days") into seconds."""
    m = _DURATION_RE.match(text.strip())
    if not m:
        return None
    unit = _UNIT_SECONDS.get(m.group(2))
    if unit is None:
        return None
    return int(m.group(1)) * unit


def parse_ordinal_ref(text: str) -> tuple[int, str] | None:
    """Parse an ordinal git reference ("3 checkouts ago") into (count, action)."""
    m = _ORDINAL_RE.match(text.strip())
    if not m:
        return None
    action = m.group(2)
    if action not in _ACTIONS:
        return None
    return int(m.group(1)), action


def parse_iso_range(text: str) -> tuple[str, str] | None:
    """Parse an absolute ISO date or ``A..B`` range into (since, until)."""
    text = text.strip()
    if ".." in text:
        a, b = (part.strip() for part in text.split("..", 1))
        if _ISO_RE.match(a) and _ISO_RE.match(b):
            return a, b
        return None
    if _ISO_RE.match(text):
        return text, "now"
    return None


def parse_colloquial(text: str, now: datetime) -> tuple[datetime, datetime] | None:
    """Parse a colloquial window ("this morning", "right before lunch")."""
    text = text.strip().replace("right ", "")
    day_offset = -1 if "yesterday" in text else 0

    window: tuple[int, int] | None = None
    for name, hours in _COLLOQUIAL.items():
        if name in text:
            window = hours
            if name == "night" and "last" in text:
                day_offset = -1
            break
    if window is None:
        return None

    local = now.astimezone()
    base = (local + timedelta(days=day_offset)).replace(minute=0, second=0, microsecond=0)
    start_hour, end_hour = window
    start = base.replace(hour=start_hour)
    if end_hour >= 24:
        end = base.replace(hour=23, minute=59, second=59)
    else:
        end = base.replace(hour=end_hour)
    return start, end


def _looks_git_native(text: str) -> bool:
    return ".ago" in text or bool(_GIT_NATIVE_RE.search(text))


def _result(since: str, until: str, resolved_from: str, confidence: float, original: str) -> dict:
    return {
        "since": since,
        "until": until,
        "resolved_from": resolved_from,
        "confidence": confidence,
        "original": original,
    }


async def _resolve_ordinal(repo_path: str, count: int, action: str) -> str | None:
    """Return the ISO timestamp of the Nth-most-recent reflog `action`, or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo_path, "reflog", "--format=%gs|%cI",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
    except (OSError, ValueError):
        return None

    seen = 0
    for line in out.decode(errors="replace").strip().splitlines():
        if "|" not in line:
            continue
        msg, iso = line.rsplit("|", 1)
        verb_match = re.match(r"^([a-z-]+)", msg.strip().lower())
        if not verb_match:
            continue
        if verb_match.group(1) == action:
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
    now = now or datetime.now(timezone.utc)
    text = (expr or "").strip()
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
            (now - timedelta(hours=1)).isoformat(), now.isoformat(),
            "reflog_ordinal_unresolved", 0.4, expr,
        )

    secs = parse_relative_duration(lower)
    if secs is not None:
        return _result(
            (now - timedelta(seconds=secs)).isoformat(), now.isoformat(),
            "relative_duration", 1.0, expr,
        )

    colloq = parse_colloquial(lower, now)
    if colloq:
        return _result(colloq[0].isoformat(), colloq[1].isoformat(), "colloquial", 0.6, expr)

    if _looks_git_native(lower):
        return _result(text, "now", "git_native", 0.9, expr)

    return _result(
        (now - timedelta(hours=1)).isoformat(), now.isoformat(), "fallback", 0.3, expr,
    )
