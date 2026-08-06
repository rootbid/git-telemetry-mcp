"""Contract tests for tool definitions, annotations, and outputSchema validation."""

import json
import pytest
import jsonschema

from git_telemetry_mcp.tools import TOOLS_REGISTRY
from git_telemetry_mcp.server import _handle_tools_list, _handle_tools_call


EXPECTED_TOOL_NAMES = {
    "git_timeline",
    "working_dir_delta",
    "dev_activity",
    "get_session_timeline",
    "explain_uncommitted_drift",
    "trace_file_evolution",
    "get_active_context_pack",
    "safe_git_reset",
    "safe_git_checkout",
    "stash_and_isolate",
    "detect_stale_branches",
    "generate_smart_commit",
    "get_developer_velocity",
    "conflict_prelim_check",
    "get_temporal_snapshot",
    "compare_workspace_checkpoints",
}


def test_registry_contains_all_16_tools():
    assert set(TOOLS_REGISTRY.keys()) == EXPECTED_TOOL_NAMES


def test_tool_definitions_and_annotations():
    for name, entry in TOOLS_REGISTRY.items():
        defn = entry["definition"]
        assert "name" in defn
        assert "description" in defn
        assert "inputSchema" in defn
        assert "outputSchema" in defn

        # Check annotations
        assert "readOnlyHint" in defn
        assert "destructiveHint" in defn
        assert "idempotentHint" in defn
        assert "annotations" in defn

        annotations = defn["annotations"]
        assert "readOnly" in annotations
        assert "destructive" in annotations
        assert "idempotent" in annotations


@pytest.mark.asyncio
async def test_tools_list_endpoint():
    res = await _handle_tools_list({})
    assert "tools" in res
    tools = res["tools"]
    assert len(tools) == 16
    tool_names = {t["name"] for t in tools}
    assert tool_names == EXPECTED_TOOL_NAMES


@pytest.mark.asyncio
async def test_tools_call_and_schema_validation(repo_with_history, fake_shell_history):
    repo_path = str(repo_with_history)
    history_path = str(fake_shell_history)

    # Arguments per tool to execute successfully against repo_with_history
    tool_args = {
        "git_timeline": {"since": "1.day.ago", "repo_path": repo_path},
        "working_dir_delta": {"repo_path": repo_path, "include_diff": True},
        "dev_activity": {"since": "1.day.ago", "repo_path": repo_path, "shell_history_path": history_path},
        "get_session_timeline": {"since": "1.day.ago", "repo_path": repo_path},
        "explain_uncommitted_drift": {"repo_path": repo_path},
        "trace_file_evolution": {"file_path": "README.md", "repo_path": repo_path},
        "get_active_context_pack": {"repo_path": repo_path},
        "safe_git_reset": {"target": "HEAD", "mode": "--soft", "repo_path": repo_path},
        "safe_git_checkout": {"target": "main", "force": False, "repo_path": repo_path},
        "stash_and_isolate": {"repo_path": repo_path, "message": "test stash"},
        "detect_stale_branches": {"repo_path": repo_path},
        "generate_smart_commit": {"repo_path": repo_path, "execute": False},
        "get_developer_velocity": {"since": "1.day.ago", "repo_path": repo_path},
        "conflict_prelim_check": {"target_branch": "main", "repo_path": repo_path},
        "get_temporal_snapshot": {"time_range": "1.day.ago", "repo_path": repo_path},
        "compare_workspace_checkpoints": {"ref1": "HEAD~1", "ref2": "HEAD", "repo_path": repo_path},
    }
    for name in EXPECTED_TOOL_NAMES:
        args = tool_args[name]
        entry = TOOLS_REGISTRY[name]
        schema = entry["definition"]["outputSchema"]
        # Call via server dispatch / handle_tools_call
        response = await _handle_tools_call({"name": name, "arguments": args})
        assert not response.get("isError"), f"Tool {name} returned error: {response}"

        if response.get("resultType") == "input_required":
            assert "inputSchema" in response
            assert "content" in response
            continue

        assert "content" in response, f"Failed for tool {name}"
        content_item = response["content"][0]
        text = content_item["text"]

        # Parse JSON
        payload = json.loads(text)

        # Assert envelope fields
        assert "timezone_offset" in payload, f"Missing timezone_offset in {name}"
        assert "repo_checksum" in payload, f"Missing repo_checksum in {name}"
        assert "confidence_score" in payload, f"Missing confidence_score in {name}"
        assert "data" in payload, f"Missing data in {name}"

        # Validate against tool's declared outputSchema
        jsonschema.validate(instance=payload, schema=schema)
@pytest.mark.asyncio
async def test_tool_call_secret_scrubbing(temp_git_repo):
    repo_path = str(temp_git_repo)
    # Commit a file containing a secret token
    secret_file = temp_git_repo / "secrets.txt"
    secret_file.write_text("API_KEY=ghp_1234567890abcdefghijklmnopqrstuvwxyz\n")

    import subprocess
    subprocess.run(["git", "add", "secrets.txt"], cwd=temp_git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add secret sk-proj-1234567890abcdef1234567890"], cwd=temp_git_repo, check=True, capture_output=True)

    # Call git_timeline
    response = await _handle_tools_call({
        "name": "git_timeline",
        "arguments": {"since": "1.hour.ago", "repo_path": repo_path},
    })
    text = response["content"][0]["text"]

    # Secret should be scrubbed
    assert "ghp_123456" not in text
    assert "sk-proj-" not in text
    assert "[REDACTED_SECRET]" in text
