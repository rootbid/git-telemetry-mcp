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
        _ev(
            "2026-08-06 09:02:00 +0000",
            type_="reflog",
            action="checkout: moving from main to feature/x",
        ),
        _ev("2026-08-06 09:03:00 +0000"),
    ]
    sessions = segment_sessions(events, gap_seconds=900)
    assert len(sessions) == 2
    switch_session = next(
        s for s in sessions if s["boundary_reason"] == "branch_switch"
    )
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


def test_session_dates_are_utc_aware_and_event_dates_normalized():
    sessions = segment_sessions(
        [
            _ev("2026-08-06 09:00:00"),
            _ev("2026-08-06T09:05:00+02:00"),
        ]
    )
    assert sessions[0]["start"].endswith("+00:00")
    assert sessions[0]["end"].endswith("+00:00")
    assert all(event["date"].endswith("+00:00") for event in sessions[0]["events"])


def test_session_selector_rejects_malformed_values():
    events = [_ev("2026-08-06 09:00:00 +0000")]
    sessions = segment_sessions(events)
    for selector in (True, 1.0, "Session #1 extra", "#1 trailing", "0", "-1", "one"):
        assert select_session(sessions, selector) is None


def test_session_event_processing_is_bounded():
    events = [
        _ev(f"2026-08-06 09:{minute % 60:02d}:00 +0000") for minute in range(1500)
    ]
    sessions = segment_sessions(events)
    assert sum(session["event_count"] for session in sessions) <= 1000
