"""Unit tests for the fuzzy temporal parser (Phase 1)."""

from datetime import datetime, timedelta, timezone

import pytest

from git_telemetry_mcp.temporal import (
    parse_relative_duration,
    parse_ordinal_ref,
    parse_iso_range,
    parse_colloquial,
    resolve_time_range,
)

FIXED_NOW = datetime(2026, 8, 7, 15, 0, 0, tzinfo=timezone.utc)


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
async def test_resolve_git_native_passthrough():
    res = await resolve_time_range("1.hour.ago", now=FIXED_NOW)
    assert res["resolved_from"] == "git_native"
    assert res["since"] == "1.hour.ago"
    assert res["until"] == "now"


@pytest.mark.asyncio
async def test_resolve_fallback_ambiguous():
    res = await resolve_time_range("whenever", now=FIXED_NOW)
    assert res["resolved_from"] == "fallback"
    assert res["confidence"] < 0.5


@pytest.mark.asyncio
async def test_resolve_ordinal_against_repo(repo_with_history):
    # repo_with_history performs checkouts (main -> feature/test -> main).
    res = await resolve_time_range("2 checkouts ago", repo_path=str(repo_with_history), now=FIXED_NOW)
    assert res["resolved_from"] == "reflog_ordinal"
    assert res["confidence"] == 0.85
    # 'since' is a concrete reflog timestamp, not the passthrough string.
    assert datetime.fromisoformat(res["since"])


@pytest.mark.asyncio
async def test_resolve_ordinal_unresolved(temp_git_repo):
    # A fresh repo has no pushes recorded in the reflog.
    res = await resolve_time_range("5 pushes ago", repo_path=str(temp_git_repo), now=FIXED_NOW)
    assert res["resolved_from"] == "reflog_ordinal_unresolved"
    assert res["confidence"] == 0.4
