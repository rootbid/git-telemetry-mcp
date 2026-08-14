"""Session boundary detection.

Segments a flat stream of git/telemetry events into logical work sessions by
inactivity gap (default >15 min), branch switch, or reflog discontinuity, then
numbers them most-recent-first so callers can request "Session #3" without
supplying timestamps.

Events are dicts carrying at least a ``date`` string (git ``%ci`` or ISO-8601).
Reflog events additionally carry an ``action`` string ("checkout: moving from
main to feature/x"), which triggers a branch-switch boundary.
"""

import re
from datetime import UTC, datetime

__all__ = ["segment_sessions", "select_session"]

_MAX_SESSION_EVENTS = 1000
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S %z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
)
_CHECKOUT_RE = re.compile(r"checkout:\s*moving from (\S+) to (\S+)")
_SESSION_SELECTOR_RE = re.compile(r"(?:session\s+#|#)?([1-9][0-9]*)", re.IGNORECASE)


def _parse_date(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    parsed: datetime | None = None
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(s)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed


def _checkout(event: dict) -> re.Match | None:
    action = event.get("action") or event.get("reason_detail") or ""
    return _CHECKOUT_RE.search(action) if isinstance(action, str) else None


def _branches_of(event: dict) -> list[str]:
    m = _checkout(event)
    if m:
        return [m.group(1), m.group(2)]
    return []


def segment_sessions(events: list[dict], gap_seconds: int = 900) -> list[dict]:
    """Group a bounded event stream into sessions numbered newest-first."""
    dated: list[tuple[datetime, dict]] = []
    for event in events[:_MAX_SESSION_EVENTS]:
        if not isinstance(event, dict):
            continue
        dt = _parse_date(event.get("date"))
        if dt is not None:
            normalized_event = dict(event)
            normalized_event["date"] = dt.isoformat()
            dated.append((dt, normalized_event))
    dated.sort(key=lambda pair: pair[0])

    raw: list[dict] = []
    current: dict | None = None
    prev_dt: datetime | None = None

    for dt, event in dated:
        reason: str | None = None
        if current is None:
            reason = "start"
        elif _checkout(event):
            reason = "branch_switch"
        elif prev_dt is not None and (dt - prev_dt).total_seconds() > gap_seconds:
            reason = "inactivity_gap"

        if reason is not None:
            current = {
                "start": dt,
                "end": dt,
                "boundary_reason": reason,
                "branches": [],
                "events": [],
            }
            raw.append(current)

        assert current is not None
        current["events"].append(event)
        current["end"] = dt
        for branch in _branches_of(event):
            if branch not in current["branches"]:
                current["branches"].append(branch)
        prev_dt = dt

    return _finalize(raw)


def _finalize(raw: list[dict]) -> list[dict]:
    """Number sessions #1 = most recent and freeze datetimes to ISO strings."""
    sessions: list[dict] = []
    # raw is chronological ascending; reverse so #1 is the newest session.
    for offset, sess in enumerate(reversed(raw)):
        start: datetime = sess["start"]
        end: datetime = sess["end"]
        sessions.append(
            {
                "session_id": offset + 1,
                "label": f"Session #{offset + 1}",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "duration_seconds": int((end - start).total_seconds()),
                "event_count": len(sess["events"]),
                "branches": sess["branches"],
                "boundary_reason": sess["boundary_reason"],
                "events": sess["events"],
            }
        )
    return sessions


def select_session(sessions: list[dict], requested) -> dict | None:
    """Return a session for an exact numeric selector."""
    if requested is None or isinstance(requested, bool):
        return None
    if isinstance(requested, str):
        match = _SESSION_SELECTOR_RE.fullmatch(requested.strip())
        if match is None:
            return None
        target = int(match.group(1))
    elif isinstance(requested, int) and requested > 0:
        target = requested
    else:
        return None
    for sess in sessions:
        if sess.get("session_id") == target:
            return sess
    return None
