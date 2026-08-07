"""MCP Server — Stateless HTTP JSON-RPC + SSE transport."""

import json
import asyncio
import uuid
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse

from git_telemetry_mcp.tools import TOOLS_REGISTRY

MCP_PROTOCOL_VERSION = "2026-07-28"
SERVER_INFO = {"name": "git-telemetry-mcp", "version": "0.1.1"}
TOOLS_LIST_TTL_MS = 600_000

# SSE session store (in-memory, per-process)
_sse_sessions: dict[str, asyncio.Queue] = {}


def _jsonrpc_response(id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _jsonrpc_error(id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def _mcp_headers() -> dict[str, str]:
    return {"MCP-Protocol-Version": MCP_PROTOCOL_VERSION}


async def _handle_initialize(params: dict) -> dict:
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
    }


async def _handle_tools_list(params: dict) -> dict:
    tools = [entry["definition"] for entry in TOOLS_REGISTRY.values()]
    return {"tools": tools, "_meta": {"ttlMs": TOOLS_LIST_TTL_MS, "cacheScope": "global"}}


async def _handle_tools_call(params: dict) -> dict:
    name = params.get("name", "")
    arguments = params.get("arguments", {})

    if name not in TOOLS_REGISTRY:
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
            "isError": True,
        }

    try:
        result = await TOOLS_REGISTRY[name]["handler"](arguments)
        if isinstance(result, dict) and result.get("resultType") == "input_required":
            return result
        return {"content": [{"type": "text", "text": result}]}
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "isError": True,
        }


METHOD_HANDLERS = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
}


async def _dispatch(body: dict) -> dict:
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    if method == "notifications/initialized":
        return _jsonrpc_response(req_id, {})

    handler = METHOD_HANDLERS.get(method)
    if not handler:
        return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")

    result = await handler(params)
    return _jsonrpc_response(req_id, result)


# --- HTTP Transport ---

async def mcp_post(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            _jsonrpc_error(None, -32700, "Parse error"),
            headers=_mcp_headers(),
        )

    # Check if this is an SSE session message
    session_id = request.headers.get("mcp-session-id")
    if session_id and session_id in _sse_sessions:
        response = await _dispatch(body)
        await _sse_sessions[session_id].put(response)
        return Response(status_code=202, headers=_mcp_headers())

    response = await _dispatch(body)

    resp_headers = _mcp_headers()
    resp_headers["Mcp-Method"] = body.get("method", "")
    if body.get("method") == "tools/call":
        resp_headers["Mcp-Name"] = body.get("params", {}).get("name", "")
    if body.get("method") == "initialize":
        new_session = str(uuid.uuid4())
        resp_headers["Mcp-Session-Id"] = new_session

    return JSONResponse(response, headers=resp_headers)


# --- SSE Transport ---

async def mcp_sse(request: Request) -> Response:
    session_id = request.query_params.get("session_id") or str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _sse_sessions[session_id] = queue

    async def event_generator():
        yield {"event": "endpoint", "data": f"/mcp?session_id={session_id}"}
        try:
            while True:
                msg = await queue.get()
                yield {"event": "message", "data": json.dumps(msg)}
        except asyncio.CancelledError:
            pass
        finally:
            _sse_sessions.pop(session_id, None)

    return EventSourceResponse(event_generator(), headers=_mcp_headers())


# --- Health ---

async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": SERVER_INFO})


# --- App ---

app = Starlette(
    routes=[
        Route("/mcp", mcp_post, methods=["POST"]),
        Route("/sse", mcp_sse, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
    ],
)


def main():
    uvicorn.run(
        "git_telemetry_mcp.server:app",
        host="127.0.0.1",
        port=8787,
        log_level="info",
    )


if __name__ == "__main__":
    main()
