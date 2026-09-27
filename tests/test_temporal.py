"""Unit tests for the fuzzy temporal parser (Phase 1)."""

from datetime import UTC, datetime, timedelta

import pytest

from git_telemetry_mcp.temporal import (
    GitTemporalProvider,
    parse_colloquial,
    parse_iso_range,
    parse_ordinal_ref,
    parse_relative_duration,
    resolve_time_bounds,
    resolve_time_range,
)

FIXED_NOW = datetime(2026, 8, 7, 15, 0, 0, tzinfo=UTC)


def test_parse_relative_duration_variants():
    assert parse_relative_duration("last 45m") == 45 * 60
    assert parse_relative_duration("past 2 days") == 2 * 86400
    assert parse_relative_duration("3h") == 3 * 3600
    assert parse_relative_duration("30 minutes") == 30 * 60
    assert parse_relative_duration("5 days ago") == 5 * 86400
    assert parse_relative_duration("not a duration") is None
    assert parse_relative_duration("42 lightyears") is None


def test_parse_ordinal_ref():
    assert parse_ordinal_ref("3 checkouts ago") == (3, "checkout")
    assert parse_ordinal_ref("2 commits ago") == (2, "commit")
    assert parse_ordinal_ref("1 push ago") == (1, "push")
    # time units are not ordinal actions
    assert parse_ordinal_ref("3 days ago") is None
    assert parse_ordinal_ref("last 45m") is None


def test_parse_iso_range():
    assert parse_iso_range("2026-08-01..2026-08-05") == ("2026-08-01", "2026-08-05")
    assert parse_iso_range("2026-08-01") == ("2026-08-01", "now")
    assert parse_iso_range("2026-08-01 12:30:00") == ("2026-08-01 12:30:00", "now")
    assert parse_iso_range("last week") is None
    assert parse_iso_range("2026-08-01T12:30:00+05:30..2026-08-01T06:00:00Z") is None
    assert parse_iso_range("2026-02-30") is None
    assert parse_iso_range("2026-08-02..2026-08-01") is None
    assert parse_iso_range("2026-08-01T12:30:00+05:30") == (
        "2026-08-01T12:30:00+05:30",
        "now",
    )


def test_temporal_grammars_are_bounded_and_exact():
    assert parse_relative_duration("999999999999999999999999d") is None
    assert parse_relative_duration("1 lightyear") is None
    assert parse_colloquial("this morning meeting", FIXED_NOW) is None
    assert parse_colloquial("yesterday-ish", FIXED_NOW) is None
    _, night_end = parse_colloquial("last night", FIXED_NOW)
    assert night_end.hour == 0 and night_end.minute == 0 and night_end.second == 0


@pytest.mark.asyncio
async def test_invalid_huge_duration_does_not_become_a_window():
    result = await resolve_time_range("999999999999999999999999d", now=FIXED_NOW)
    assert result["resolved_from"] == "fallback"
    invalid = await resolve_time_range("1.hour.ago trailing", now=FIXED_NOW)
    assert invalid["resolved_from"] == "fallback"


def test_parse_colloquial_morning():
    window = parse_colloquial("this morning", FIXED_NOW)
    assert window is not None
    start, end = window
    assert start.hour == 6
    assert end.hour == 12
    assert start.date() == FIXED_NOW.astimezone().date()


def test_parse_colloquial_before_lunch_and_yesterday():
    start, end = parse_colloquial("right before lunch", FIXED_NOW)
    assert (start.hour, end.hour) == (10, 12)

    y_start, _ = parse_colloquial("yesterday afternoon", FIXED_NOW)
    assert y_start.date() == (FIXED_NOW.astimezone() - timedelta(days=1)).date()


@pytest.mark.asyncio
async def test_resolve_relative_duration_bounded_window():
    res = await resolve_time_range("last 45m", now=FIXED_NOW)
    assert res["resolved_from"] == "relative_duration"
    assert res["confidence"] == 1.0
    since = datetime.fromisoformat(res["since"])
    until = datetime.fromisoformat(res["until"])
    assert (until - since) == timedelta(minutes=45)


@pytest.mark.asyncio
async def test_resolve_iso_absolute():
    res = await resolve_time_range("2026-08-01..2026-08-05", now=FIXED_NOW)
    assert res["resolved_from"] == "iso_absolute"
    assert res["since"] == "2026-08-01"
    assert res["until"] == "2026-08-05"
    assert res["confidence"] == 1.0


@pytest.mark.asyncio
async def test_resolve_colloquial_confidence():
    res = await resolve_time_range("this morning", now=FIXED_NOW)
    assert res["resolved_from"] == "colloquial"
    assert res["confidence"] == 0.6


@pytest.mark.asyncio
async def test_resolve_git_native_is_normalized():
    res = await resolve_time_range("1.hour.ago", now=FIXED_NOW)
    assert res["resolved_from"] == "git_native_normalized"
    assert datetime.fromisoformat(res["since"])
    assert datetime.fromisoformat(res["until"])


@pytest.mark.asyncio
async def test_resolve_fallback_ambiguous():
    res = await resolve_time_range("whenever", now=FIXED_NOW)
    assert res["resolved_from"] == "fallback"
    assert res["confidence"] < 0.5


@pytest.mark.asyncio
async def test_resolve_ordinal_against_repo(repo_with_history):
    # repo_with_history performs checkouts (main -> feature/test -> main).
    res = await resolve_time_range(
        "2 checkouts ago", repo_path=str(repo_with_history), now=FIXED_NOW
    )
    assert res["resolved_from"] == "reflog_ordinal"
    assert res["confidence"] == 0.85
    # 'since' is a concrete reflog timestamp, not the passthrough string.
    assert datetime.fromisoformat(res["since"])

@pytest.mark.asyncio
async def test_git_provider_indexes_repository_event_stream(repo_with_history):
    provider = GitTemporalProvider(str(repo_with_history))
    events = await provider.events()
    kinds = {event["type"] for event in events}

    assert {"reflog", "commit", "stash", "branch"} <= kinds
    assert events == await provider.events()
    assert all(datetime.fromisoformat(event["timestamp"]) for event in events)
    assert all(event.get("anchor") for event in events)


@pytest.mark.asyncio
async def test_ordinal_resolution_preserves_git_anchor(repo_with_history):
    result = await resolve_time_range(
        "2 checkouts ago", repo_path=str(repo_with_history), now=FIXED_NOW
    )
    assert result["resolved_from"] == "reflog_ordinal"
    assert result["anchor"]


@pytest.mark.asyncio
async def test_resolve_time_bounds_normalizes_explicit_iso_endpoint():
    result = await resolve_time_bounds(
        "last 45m",
        "2026-08-07T15:00:00+00:00",
        now=FIXED_NOW,
    )
    assert result["until"] == "2026-08-07T15:00:00+00:00"
    assert result["until"] != "now"


@pytest.mark.asyncio
async def test_resolve_ordinal_unresolved(temp_git_repo):
    # A fresh repo has no pushes recorded in the reflog.
    res = await resolve_time_range(
        "5 pushes ago", repo_path=str(temp_git_repo), now=FIXED_NOW
    )
    assert res["resolved_from"] == "reflog_ordinal_unresolved"
    assert res["confidence"] == 0.4
