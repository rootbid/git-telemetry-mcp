"""Unit tests for session boundary detection (Phase 1)."""

from git_telemetry_mcp.sessions import segment_sessions, select_session


def _ev(date, type_="commit", **extra):
    return {"type": type_, "date": date, **extra}


def test_gap_splits_sessions():
    events = [
        _ev("2026-08-06 09:00:00 +0000"),
        _ev("2026-08-06 09:05:00 +0000"),
        # 3h gap -> new session
        _ev("2026-08-06 12:05:00 +0000"),
    ]
    sessions = segment_sessions(events, gap_seconds=900)
    assert len(sessions) == 2
    # Newest session is #1.
    assert sessions[0]["session_id"] == 1
    assert sessions[0]["boundary_reason"] == "inactivity_gap"
    assert sessions[1]["boundary_reason"] == "start"


def test_no_gap_single_session():
    events = [
        _ev("2026-08-06 09:00:00 +0000"),
        _ev("2026-08-06 09:05:00 +0000"),
        _ev("2026-08-06 09:12:00 +0000"),
    ]
    sessions = segment_sessions(events, gap_seconds=900)
    assert len(sessions) == 1
    assert sessions[0]["event_count"] == 3
    assert sessions[0]["duration_seconds"] == 12 * 60


def test_branch_switch_boundary():
    events = [
        _ev("2026-08-06 09:00:00 +0000"),
        _ev("2026-08-06 09:02:00 +0000", type_="reflog",
            action="checkout: moving from main to feature/x"),
        _ev("2026-08-06 09:03:00 +0000"),
    ]
    sessions = segment_sessions(events, gap_seconds=900)
    assert len(sessions) == 2
    switch_session = next(s for s in sessions if s["boundary_reason"] == "branch_switch")
    assert "main" in switch_session["branches"]
    assert "feature/x" in switch_session["branches"]


def test_undated_events_ignored():
    events = [_ev(None), _ev("not a date"), _ev("2026-08-06 09:00:00 +0000")]
    sessions = segment_sessions(events)
    assert len(sessions) == 1
    assert sessions[0]["event_count"] == 1


def test_select_session():
    events = [
        _ev("2026-08-06 09:00:00 +0000"),
        _ev("2026-08-06 12:05:00 +0000"),
    ]
    sessions = segment_sessions(events)
    assert select_session(sessions, 1)["session_id"] == 1
    assert select_session(sessions, "#2")["session_id"] == 2
    assert select_session(sessions, "Session #1")["session_id"] == 1
    assert select_session(sessions, 99) is None
    assert select_session(sessions, "none") is None
