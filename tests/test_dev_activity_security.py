"""Focused shell-history path and bounded-read tests."""

import json
import time
from pathlib import Path

import pytest

from git_telemetry_mcp.tools.dev_activity import dev_activity


def _payload(text: str) -> dict:
    return json.loads(text)


@pytest.mark.asyncio
async def test_history_path_outside_allowlist_returns_safe_partial_result(
    temp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    rejected = tmp_path / "private-history"
    rejected.write_text(f": {int(time.time())}:0;secret-command\n")
    monkeypatch.setenv("GIT_TELEMETRY_ALLOWED_HISTORY_ROOTS", str(allowed))

    payload = _payload(
        await dev_activity(
            {
                "since": "1.day.ago",
                "repo_path": str(temp_git_repo),
                "shell_history_path": str(rejected),
            }
        )
    )

    data = payload["data"]
    assert data["shell_commands"] == []
    assert data["history_error"] == "History path is not permitted"
    assert "history_file" not in data
    assert str(rejected) not in json.dumps(payload)


@pytest.mark.asyncio
async def test_history_path_must_be_regular_file_under_allowed_root(
    temp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GIT_TELEMETRY_ALLOWED_HISTORY_ROOTS", str(allowed))

    payload = _payload(
        await dev_activity(
            {
                "since": "1.day.ago",
                "repo_path": str(temp_git_repo),
                "shell_history_path": str(allowed),
            }
        )
    )

    data = payload["data"]
    assert data["shell_commands"] == []
    assert data["history_error"] == "History path is not a regular file"


@pytest.mark.asyncio
async def test_history_read_is_byte_bounded_and_reports_partial_result(
    temp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    history = allowed / ".zsh_history"
    now = int(time.time())
    history.write_text(f": {now}:0;first-command\n" + "x" * 200)
    monkeypatch.setenv("GIT_TELEMETRY_ALLOWED_HISTORY_ROOTS", str(allowed))
    monkeypatch.setenv("GIT_TELEMETRY_MAX_HISTORY_BYTES", "64")

    payload = _payload(
        await dev_activity(
            {
                "since": "1.day.ago",
                "repo_path": str(temp_git_repo),
                "shell_history_path": str(history),
            }
        )
    )

    data = payload["data"]
    assert data["history_bytes_truncated"] is True
    assert data["shell_commands"]
    assert data["shell_commands"][0]["command"] == "first-command"
    assert payload["confidence_score"] < 1.0
    assert str(history) not in json.dumps(payload)


@pytest.mark.asyncio
async def test_detected_home_history_file_is_allowed_and_path_scrubbed(
    temp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    home.mkdir()
    history = home / ".bash_history"
    history.write_text(f"#{int(time.time())}\nfirst-command\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HISTFILE", str(history))
    monkeypatch.delenv("GIT_TELEMETRY_ALLOWED_HISTORY_ROOTS", raising=False)

    payload = _payload(
        await dev_activity({"since": "1.day.ago", "repo_path": str(temp_git_repo)})
    )

    data = payload["data"]
    assert data["shell_commands"][0]["command"] == "first-command"
    assert str(history) not in json.dumps(payload)
    assert "[REDACTED_PATH]" in payload["data"]["history_file"]
