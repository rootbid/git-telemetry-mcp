"""Focused safety tests for destructive and input-boundary tools."""

import json
import subprocess

import pytest

from git_telemetry_mcp.tools import TOOLS_REGISTRY, safe_operations
from git_telemetry_mcp.tools.safe_operations import safe_git_reset
from git_telemetry_mcp.tools.smart_commit import generate_smart_commit


def _payload(text: str) -> dict:
    return json.loads(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [False, 1, "true", None])
async def test_reset_requires_strict_true_confirmation(temp_git_repo, confirm):
    preview = await safe_git_reset({"repo_path": str(temp_git_repo), "mode": "--soft"})
    assert preview["resultType"] == "input_required"
    token = preview["inputSchema"]["properties"]["confirmation_id"]["const"]

    result = await safe_git_reset(
        {"repo_path": str(temp_git_repo), "confirmation_id": token, "confirm": confirm}
    )
    data = _payload(result)["data"]
    assert data["executed"] is False
    assert "confirmation" in data["error"].lower()


@pytest.mark.asyncio
async def test_confirmation_token_is_one_shot(temp_git_repo):
    preview = await safe_git_reset({"repo_path": str(temp_git_repo), "mode": "--soft"})
    token = preview["inputSchema"]["properties"]["confirmation_id"]["const"]

    first = await safe_git_reset(
        {"repo_path": str(temp_git_repo), "confirmation_id": token, "confirm": True}
    )
    assert _payload(first)["data"]["executed"] is True

    replay = await safe_git_reset(
        {"repo_path": str(temp_git_repo), "confirmation_id": token, "confirm": True}
    )
    replay_data = _payload(replay)["data"]
    assert replay_data["executed"] is False
    assert (
        "invalid" in replay_data["error"].lower()
        or "expired" in replay_data["error"].lower()
    )


@pytest.mark.asyncio
async def test_confirmation_token_expiry(temp_git_repo, monkeypatch):
    preview = await safe_git_reset({"repo_path": str(temp_git_repo), "mode": "--soft"})
    token = preview["inputSchema"]["properties"]["confirmation_id"]["const"]
    safe_operations._pending_confirmations[token]["expires_at"] = (
        safe_operations.time.monotonic() - 1
    )

    result = await safe_git_reset(
        {"repo_path": str(temp_git_repo), "confirmation_id": token, "confirm": True}
    )
    data = _payload(result)["data"]
    assert data["executed"] is False
    assert "expired" in data["error"].lower()


@pytest.mark.asyncio
async def test_confirmation_token_binds_operation_and_target(temp_git_repo):
    preview = await safe_git_reset(
        {"repo_path": str(temp_git_repo), "target": "HEAD", "mode": "--soft"}
    )
    token = preview["inputSchema"]["properties"]["confirmation_id"]["const"]

    wrong_target = await safe_git_reset(
        {
            "repo_path": str(temp_git_repo),
            "target": "HEAD~1",
            "mode": "--soft",
            "confirmation_id": token,
            "confirm": True,
        }
    )
    data = _payload(wrong_target)["data"]
    assert data["executed"] is False
    assert "binding" in data["error"].lower()
    assert token in safe_operations._pending_confirmations


@pytest.mark.asyncio
async def test_reset_rejects_invalid_mode_without_running_git(
    temp_git_repo, monkeypatch
):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("git should not run for an invalid reset mode")

    monkeypatch.setattr(
        safe_operations.asyncio, "create_subprocess_exec", fail_if_called
    )
    result = await safe_git_reset(
        {"repo_path": str(temp_git_repo), "mode": "--hard; rm -rf /"}
    )
    data = _payload(result)["data"]
    assert data["executed"] is False
    assert "mode" in data["error"].lower()


@pytest.mark.asyncio
async def test_smart_commit_rejects_non_boolean_execute(temp_git_repo):
    result = await generate_smart_commit(
        {"repo_path": str(temp_git_repo), "execute": 1}
    )
    data = _payload(result)["data"]
    assert data["committed"] is False
    assert "boolean" in data["error"].lower()


@pytest.mark.asyncio
async def test_smart_commit_rejects_execute_true_without_confirmation(temp_git_repo):
    path = temp_git_repo / "staged.txt"
    path.write_text("staged\n")
    subprocess.run(  # noqa: ASYNC221 — synchronous setup in async test
        ["git", "add", "staged.txt"], cwd=temp_git_repo, check=True, capture_output=True
    )

    result = await generate_smart_commit(
        {"repo_path": str(temp_git_repo), "execute": True}
    )
    data = _payload(result)["data"]
    assert data["committed"] is False
    assert "confirmation" in data["error"].lower()
    check = subprocess.run(  # noqa: ASYNC221 — synchronous assertion query
        ["git", "log", "-1", "--format=%s"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert check.stdout.strip() == "initial commit"


def test_destructive_annotations_and_conflict_command():
    assert TOOLS_REGISTRY["stash_and_isolate"]["definition"]["destructiveHint"] is True
    assert (
        TOOLS_REGISTRY["conflict_prelim_check"]["definition"]["destructiveHint"]
        is False
    )
    assert TOOLS_REGISTRY["conflict_prelim_check"]["definition"]["readOnlyHint"] is True
