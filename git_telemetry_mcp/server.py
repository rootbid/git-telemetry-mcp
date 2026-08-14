"""MCP Server — Stateless HTTP JSON-RPC + SSE transport."""

import asyncio
import json
import uuid
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse

from git_telemetry_mcp.schema import serialize_telemetry_payload
from git_telemetry_mcp.tools import TOOLS_REGISTRY
from git_telemetry_mcp.tools.active_context_pack import get_active_context_pack
from git_telemetry_mcp.tools.session_timeline import get_session_timeline
from git_telemetry_mcp.tools.smart_commit import generate_smart_commit
from git_telemetry_mcp.tools.working_dir_delta import working_dir_delta

MCP_PROTOCOL_VERSION = "2026-07-28"
SERVER_INFO = {"name": "git-telemetry-mcp", "version": "0.1.1"}
TOOLS_LIST_TTL_MS = 600_000

# Resources are intentionally stable URIs; clients may select a repository using
# the optional ``repo_path`` request parameter, like existing tool arguments.
RESOURCE_DEFINITIONS = (
    {
        "uri": "telemetry://session/current",
        "name": "Current session",
        "description": "Recent repository activity and the active work session.",
        "mimeType": "application/json",
    },
    {
        "uri": "telemetry://history/standup",
        "name": "Standup history",
        "description": "A concise Markdown standup assembled from recent telemetry.",
        "mimeType": "text/markdown",
    },
    {
        "uri": "git://delta/latest",
        "name": "Latest commit delta",
        "description": "The unified diff for the repository's latest commit.",
        "mimeType": "text/plain",
    },
)

PROMPT_DEFINITIONS = (
    {
        "name": "review_debug_loop",
        "description": "Review current changes, reproduce a problem, and validate a focused fix.",
        "arguments": [
            {
                "name": "repo_path",
                "description": "Path to the Git repository (default: current directory).",
                "required": False,
            }
        ],
    },
    {
        "name": "generate_commit_message_context",
        "description": "Prepare staged-change context for a concise conventional commit message.",
        "arguments": [
            {
                "name": "repo_path",
                "description": "Path to the Git repository (default: current directory).",
                "required": False,
            }
        ],
    },
    {
        "name": "handover_notes",
        "description": "Assemble concise handover notes from current state and recent activity.",
        "arguments": [
            {
                "name": "repo_path",
                "description": "Path to the Git repository (default: current directory).",
                "required": False,
                }
        ],
    },
)

# SSE session store (in-memory, per-process)
_sse_sessions: dict[str, asyncio.Queue] = {}


def _jsonrpc_response(id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _jsonrpc_error(id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def _mcp_headers() -> dict[str, str]:
    return {"MCP-Protocol-Version": MCP_PROTOCOL_VERSION}


def _method_error(message: str) -> dict:
    """Return the same in-band error shape used by ``tools/call``."""
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _resource_content(uri: str, text: str, mime_type: str) -> dict:
    """Build the MCP resources/read ``contents`` item for a text resource."""
    return {"contents": [{"uri": uri, "mimeType": mime_type, "text": text}]}


def _payload_parts(serialized: str) -> tuple[Any, float]:
    """Extract data/confidence from an existing serialized tool result."""
    try:
        payload = json.loads(serialized)
    except (TypeError, json.JSONDecodeError):
        return serialized, 1.0
    if isinstance(payload, dict) and "data" in payload:
        try:
            confidence = float(payload.get("confidence_score", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        return payload["data"], confidence
    return payload, 1.0


def _repack_payload(serialized: str, repo_path: str) -> str:
    """Re-run the serializer gate before exposing a tool result as a resource."""
    data, confidence = _payload_parts(serialized)
    return serialize_telemetry_payload(data, repo_path=repo_path, confidence_score=confidence)


def _compact(text: str, limit: int = 8_000) -> str:
    """Keep prompt context useful without allowing an unbounded diff to dominate."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[context truncated]"


async def _handle_initialize(params: dict) -> dict:
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"listChanged": False},
            "prompts": {"listChanged": False},
        },
        "serverInfo": SERVER_INFO,
    }


async def _handle_tools_list(params: dict) -> dict:
    tools = [entry["definition"] for entry in TOOLS_REGISTRY.values()]
    return {"tools": tools, "_meta": {"ttlMs": TOOLS_LIST_TTL_MS, "cacheScope": "global"}}


async def _handle_resources_list(params: dict) -> dict:
    return {"resources": [dict(resource) for resource in RESOURCE_DEFINITIONS]}


async def _latest_commit_delta(repo_path: str) -> dict:
    command = [
        "git", "-C", repo_path, "show", "--format=", "--no-ext-diff", "--unified=3", "HEAD"
    ]
    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return {"error": stderr.decode(errors="replace").strip() or "Unable to read latest commit"}
    return {"diff": stdout.decode(errors="replace")}


async def _handle_resources_read(params: dict) -> dict:
    params = params or {}
    uri = params.get("uri", "")
    repo_path = params.get("repo_path", ".")
    resource = next((entry for entry in RESOURCE_DEFINITIONS if entry["uri"] == uri), None)
    if resource is None:
        return _method_error(f"Unknown resource: {uri}")

    if uri == "telemetry://session/current":
        serialized = await get_session_timeline(
            {"since": "4 hours ago", "until": "now", "repo_path": repo_path}
        )
        return _resource_content(uri, _repack_payload(serialized, repo_path), resource["mimeType"])

    if uri == "telemetry://history/standup":
        serialized = await get_session_timeline(
            {"since": "24 hours ago", "until": "now", "repo_path": repo_path}
        )
        timeline_data, confidence = _payload_parts(serialized)
        if isinstance(timeline_data, dict):
            summary = timeline_data.get("summary", "No recent activity")
            events = timeline_data.get("events", [])
        else:
            summary, events = str(timeline_data), []
        event_lines = [
            f"- {event.get('type', 'activity')}: {event.get('message') or event.get('action') or event.get('sha', '')}"
            for event in events[:10]
            if isinstance(event, dict)
        ]
        markdown = "# Standup\n\n## Summary\n" + str(summary)
        if event_lines:
            markdown += "\n\n## Recent activity\n" + "\n".join(event_lines)
        data = {"format": "markdown", "content": markdown, "source": "get_session_timeline"}
        return _resource_content(
            uri,
            serialize_telemetry_payload(data, repo_path=repo_path, confidence_score=confidence),
            resource["mimeType"],
        )

    delta = await _latest_commit_delta(repo_path)
    return _resource_content(uri, serialize_telemetry_payload(delta, repo_path=repo_path), resource["mimeType"])


async def _handle_prompts_list(params: dict) -> dict:
    return {"prompts": [dict(prompt) for prompt in PROMPT_DEFINITIONS]}


async def _handle_prompts_get(params: dict) -> dict:
    params = params or {}
    name = params.get("name", "")
    definition = next((prompt for prompt in PROMPT_DEFINITIONS if prompt["name"] == name), None)
    if definition is None:
        return _method_error(f"Unknown prompt: {name}")
    arguments = params.get("arguments") or {}
    repo_path = arguments.get("repo_path", ".")

    if name == "review_debug_loop":
        context = _repack_payload(await get_active_context_pack({"repo_path": repo_path}), repo_path)
        delta = _repack_payload(
            await working_dir_delta({"repo_path": repo_path, "include_diff": True}), repo_path
        )
        text = (
            "Review/debug loop: inspect the active context, review the working-tree delta, "
            "reproduce the issue, then validate a focused fix.\n\n"
            f"Active context:\n{_compact(context)}\n\nWorking-tree delta:\n{_compact(delta)}"
        )
    elif name == "generate_commit_message_context":
        staged = _repack_payload(await generate_smart_commit({"repo_path": repo_path}), repo_path)
        text = (
            "Generate a concise conventional commit message from the staged changes. "
            "Preserve the intent and avoid inventing scope.\n\nStaged-change context:\n"
            f"{_compact(staged)}"
        )
    else:
        context = _repack_payload(await get_active_context_pack({"repo_path": repo_path}), repo_path)
        timeline = _repack_payload(
            await get_session_timeline(
                {"since": "24 hours ago", "until": "now", "repo_path": repo_path}
            ),
            repo_path,
        )
        text = (
            "Prepare concise handover notes: state, recent work, outstanding changes, "
            "and the next useful action.\n\nActive context:\n"
            f"{_compact(context)}\n\nRecent activity:\n{_compact(timeline)}"
        )

    return {
        "description": definition["description"],
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }


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
    "resources/list": _handle_resources_list,
    "resources/read": _handle_resources_read,
    "prompts/list": _handle_prompts_list,
    "prompts/get": _handle_prompts_get,
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
