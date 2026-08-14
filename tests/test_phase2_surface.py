"""Focused MCP resources and prompts surface tests."""

import json

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
    assert {"timezone_offset", "repo_checksum", "confidence_score", "data"} <= set(payload)




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
