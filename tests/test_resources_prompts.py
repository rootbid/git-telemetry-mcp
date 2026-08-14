"""Focused MCP resources and prompts surface tests."""

import json
from urllib.parse import quote

import pytest

from git_telemetry_mcp.server import (
    _dispatch,
    _handle_initialize,
    _handle_prompts_get,
    _handle_prompts_list,
    _handle_resources_list,
    _handle_resources_read,
)


@pytest.mark.asyncio
async def test_initialize_advertises_resources_and_prompts():
    result = await _handle_initialize({})
    assert result["capabilities"]["resources"] == {"listChanged": False}
    assert result["capabilities"]["prompts"] == {"listChanged": False}


@pytest.mark.asyncio
async def test_resources_list_exposes_phase_two_uris():
    result = await _handle_resources_list({})
    assert {resource["uri"] for resource in result["resources"]} == {
        "telemetry://session/current",
        "telemetry://history/standup",
        "git://delta/latest",
    }
    assert all(resource["name"] for resource in result["resources"])
    assert all(resource["mimeType"] for resource in result["resources"])


@pytest.mark.asyncio
async def test_resources_read_returns_mcp_contents_and_scrubs_payload(
    temp_git_repo, monkeypatch
):
    from git_telemetry_mcp import server

    async def fake_timeline(arguments):
        return server.serialize_telemetry_payload(
            {"secret": "ghp_1234567890abcdefghijklmnopqrstuvwxyz"},
            repo_path=arguments.get("repo_path", "."),
        )

    monkeypatch.setattr(server, "get_session_timeline", fake_timeline)
    result = await _handle_resources_read(
        {"uri": "telemetry://session/current", "repo_path": str(temp_git_repo)}
    )
    assert list(result) == ["contents"]
    content = result["contents"][0]
    assert content["uri"] == "telemetry://session/current"
    assert content["mimeType"] == "application/json"
    payload = json.loads(content["text"])
    assert payload["data"]["secret"] == "[REDACTED_SECRET]"
    assert "ghp_" not in content["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    [
        "telemetry://session/current",
        "telemetry://history/standup",
        "git://delta/latest",
    ],
)
async def test_each_resource_read_has_serialized_mcp_content(uri, temp_git_repo):
    result = await _handle_resources_read({"uri": uri, "repo_path": str(temp_git_repo)})
    assert "contents" in result and len(result["contents"]) == 1
    content = result["contents"][0]
    assert content["uri"] == uri
    payload = json.loads(content["text"])
    assert {"timezone_offset", "repo_checksum", "confidence_score", "data"} <= set(
        payload
    )


@pytest.mark.asyncio
async def test_resources_read_unknown_uri_is_error():
    result = await _handle_resources_read({"uri": "telemetry://unknown"})
    assert result["isError"] is True
    assert "Unknown resource" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_prompts_list_and_get():
    listed = await _handle_prompts_list({})
    names = {prompt["name"] for prompt in listed["prompts"]}
    assert names == {
        "review_debug_loop",
        "generate_commit_message_context",
        "handover_notes",
    }
    for name in names:
        prompt = await _handle_prompts_get(
            {"name": name, "arguments": {"repo_path": "."}}
        )
        assert prompt["messages"]
        assert prompt["messages"][0]["role"] == "user"
        assert prompt["messages"][0]["content"]["type"] == "text"
        assert prompt["messages"][0]["content"]["text"]


@pytest.mark.asyncio
async def test_prompts_get_unknown_name_is_error():
    result = await _handle_prompts_get({"name": "not-a-prompt"})
    assert result["isError"] is True
    assert "Unknown prompt" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_dispatch_routes_resources_and_prompts():
    resource_response = await _dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}}
    )
    prompt_response = await _dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "prompts/list", "params": {}}
    )
    assert resource_response["result"]["resources"]
    assert prompt_response["result"]["prompts"]


@pytest.mark.asyncio
async def test_resources_read_rejects_non_git_worktree(tmp_path):
    result = await _handle_resources_read(
        {"uri": "telemetry://session/current", "repo_path": str(tmp_path)}
    )
    assert result["isError"] is True
    assert "repo_path" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_prompts_get_rejects_non_git_worktree(tmp_path):
    result = await _handle_prompts_get(
        {
            "name": "handover_notes",
            "arguments": {"repo_path": str(tmp_path)},
        }
    )
    assert result["isError"] is True
    assert "repo_path" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_resource_uri_query_selects_repo_path(temp_git_repo, monkeypatch):
    from git_telemetry_mcp import server

    seen = {}

    async def fake_timeline(arguments):
        seen.update(arguments)
        return server.serialize_telemetry_payload(
            {"selected": True}, repo_path=arguments["repo_path"]
        )

    monkeypatch.setattr(server, "get_session_timeline", fake_timeline)
    uri = "telemetry://session/current?repo_path=" + quote(str(temp_git_repo))
    result = await _handle_resources_read({"uri": uri})

    assert "contents" in result
    assert seen["repo_path"] == str(temp_git_repo)
    assert result["contents"][0]["uri"] == uri


@pytest.mark.asyncio
async def test_oversized_resource_is_bounded_with_truncated_preview(
    temp_git_repo, monkeypatch
):
    from git_telemetry_mcp import server

    async def huge_timeline(arguments):
        return server.serialize_telemetry_payload(
            {"output": "é" * 100_000}, repo_path=arguments["repo_path"]
        )

    monkeypatch.setattr(server, "get_session_timeline", huge_timeline)
    result = await _handle_resources_read(
        {"uri": "telemetry://session/current", "repo_path": str(temp_git_repo)}
    )

    text = result["contents"][0]["text"]
    assert len(text.encode("utf-8")) <= 100_000
    payload = json.loads(text)
    assert payload["data"]["truncated"] is True
    assert isinstance(payload["data"]["preview"], str)
    assert payload["data"]["original_bytes"] > 100_000


@pytest.mark.asyncio
async def test_prompt_delimits_git_data_and_scrubs_paths(temp_git_repo):
    result = await _handle_prompts_get(
        {"name": "handover_notes", "arguments": {"repo_path": str(temp_git_repo)}}
    )
    text = result["messages"][0]["content"]["text"]
    assert "<<<BEGIN UNTRUSTED GIT DATA:" in text
    assert "<<<END UNTRUSTED GIT DATA:" in text
    assert str(temp_git_repo) not in text


@pytest.mark.asyncio
async def test_input_required_has_scrubbed_structured_result(temp_git_repo):
    from git_telemetry_mcp.server import _handle_tools_call

    result = await _handle_tools_call(
        {
            "name": "safe_git_reset",
            "arguments": {"repo_path": str(temp_git_repo), "mode": "--soft"},
        }
    )
    assert result["resultType"] == "input_required"
    assert result["structuredContent"]["resultType"] == "input_required"
    assert "inputSchema" in result["structuredContent"]
    assert str(temp_git_repo) not in json.dumps(result)
