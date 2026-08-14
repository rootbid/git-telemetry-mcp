"""Focused security and protocol boundary tests for the remediation batch."""

import asyncio
import json
import sys

import pytest
from starlette.requests import Request

from git_telemetry_mcp.process import (
    MAX_OUTPUT_BYTES,
    SafeProcessTimeout,
    safe_create_subprocess_exec,
    validate_repo_path,
)
from git_telemetry_mcp.server import mcp_post


@pytest.mark.asyncio
async def test_repo_validator_rejects_non_worktree(tmp_path, monkeypatch):
    monkeypatch.delenv("GIT_TELEMETRY_ALLOWED_ROOTS", raising=False)
    with pytest.raises(ValueError, match="Git worktree"):
        await validate_repo_path(str(tmp_path))


@pytest.mark.asyncio
async def test_safe_process_caps_output():
    proc = await safe_create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; sys.stdout.write('x' * 2000000)",
    )
    stdout, _ = await proc.communicate()
    assert len(stdout) <= MAX_OUTPUT_BYTES


@pytest.mark.asyncio
async def test_safe_process_times_out():
    proc = await safe_create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(2)", timeout=0.01
    )
    with pytest.raises(SafeProcessTimeout):
        await proc.communicate()


async def _post(body, headers=None, query=b""):
    payload = body if isinstance(body, bytes) else body.encode()
    headers = headers or {}
    query = query.encode() if isinstance(query, str) else query
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": query,
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in headers.items()
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8787),
        "scheme": "http",
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return await mcp_post(Request(scope, receive))


def _response_json(response):
    return json.loads(response.body)


def _run(coro):
    return asyncio.run(coro)


async def _test_http_requires_json_and_rejects_bad_origin(monkeypatch):
    monkeypatch.setenv("GIT_TELEMETRY_AUTH_TOKEN", "test-token")
    response = await _post("{}", {"Authorization": "Bearer test-token"})
    assert response.status_code == 415
    response = await _post(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        {
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
            "Origin": "https://evil.example",
        },
    )
    assert response.status_code == 403


def test_http_requires_json_and_rejects_bad_origin(monkeypatch):
    _run(_test_http_requires_json_and_rejects_bad_origin(monkeypatch))


async def _test_http_jsonrpc_boundaries_and_notifications(monkeypatch):
    monkeypatch.delenv("GIT_TELEMETRY_AUTH_TOKEN", raising=False)
    headers = {"Content-Type": "application/json"}
    response = await _post(
        json.dumps({"jsonrpc": "1.0", "id": 1, "method": "initialize"}), headers
    )
    assert _response_json(response)["error"]["code"] == -32600
    response = await _post(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []}),
        headers,
    )
    assert _response_json(response)["error"]["code"] == -32602
    response = await _post(
        json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        ),
        headers,
    )
    assert response.status_code == 202
    assert not response.body


def test_http_jsonrpc_boundaries_and_notifications(monkeypatch):
    _run(_test_http_jsonrpc_boundaries_and_notifications(monkeypatch))


def test_sse_query_session_routes_responses(monkeypatch):
    from git_telemetry_mcp import server

    monkeypatch.delenv("GIT_TELEMETRY_AUTH_TOKEN", raising=False)
    queue = asyncio.Queue(maxsize=2)
    server._sse_sessions["route-test"] = queue
    try:
        response = _run(
            _post(
                json.dumps(
                    {"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {}}
                ),
                {"Content-Type": "application/json"},
                "session_id=route-test",
            )
        )
        assert response.status_code == 202
        routed = _run(queue.get())
        assert routed["id"] == 4
    finally:
        server._sse_sessions.pop("route-test", None)


def test_request_size_boundary(monkeypatch):
    monkeypatch.delenv("GIT_TELEMETRY_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("GIT_TELEMETRY_MAX_REQUEST_BYTES", "16")
    response = _run(
        _post(b"{" + b"x" * 32 + b"}", {"Content-Type": "application/json"})
    )
    assert response.status_code == 413


def test_tools_call_includes_privacy_safe_structured_content(temp_git_repo):
    from git_telemetry_mcp.server import _handle_tools_call

    async def call():
        return await _handle_tools_call(
            {
                "name": "get_active_context_pack",
                "arguments": {"repo_path": str(temp_git_repo)},
            }
        )

    result = asyncio.run(call())
    assert "structuredContent" in result
    assert result["structuredContent"]["repo_checksum"]
    assert result["structuredContent"]["data"]
