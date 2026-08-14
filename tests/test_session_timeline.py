"""Integration tests: session-aware get_session_timeline (Phase 1)."""

import json

import pytest

from git_telemetry_mcp.tools.session_timeline import get_session_timeline


@pytest.mark.asyncio
async def test_session_timeline_exposes_sessions(repo_with_history):
    payload = json.loads(
        await get_session_timeline(
            {"since": "30.days.ago", "repo_path": str(repo_with_history)}
        )
    )
    data = payload["data"]
    assert "sessions" in data
    assert isinstance(data["sessions"], list)
    assert len(data["sessions"]) >= 1
    top = data["sessions"][0]
    assert top["session_id"] == 1
    assert top["label"] == "Session #1"
    assert "start" in top and "end" in top and "event_count" in top


@pytest.mark.asyncio
async def test_session_timeline_select_by_id(repo_with_history):
    full = json.loads(
        await get_session_timeline(
            {"since": "30.days.ago", "repo_path": str(repo_with_history)}
        )
    )
    n_sessions = len(full["data"]["sessions"])

    focused = json.loads(
        await get_session_timeline(
            {"since": "30.days.ago", "repo_path": str(repo_with_history), "session": 1}
        )
    )
    sel = focused["data"]["selected_session"]
    assert sel is not None
    assert sel["session_id"] == 1
    assert len(focused["data"]["events"]) == sel["event_count"]

    missing = json.loads(
        await get_session_timeline(
            {
                "since": "30.days.ago",
                "repo_path": str(repo_with_history),
                "session": n_sessions + 5,
            }
        )
    )
    assert missing["data"]["selected_session"] is None
    assert "session_error" in missing["data"]
    assert missing["confidence_score"] == 0.5


@pytest.mark.asyncio
async def test_session_timeline_filters_stashes_outside_window(repo_with_history):
    payload = json.loads(
        await get_session_timeline(
            {
                "since": "2999-01-01",
                "until": "2999-01-02",
                "repo_path": str(repo_with_history),
            }
        )
    )
    assert payload["data"]["events"] == []
