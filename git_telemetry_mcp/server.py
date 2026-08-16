"""MCP Server — Stateless HTTP JSON-RPC + SSE transport."""

import asyncio
import hmac
import json
import os
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jsonschema
import uvicorn
from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from git_telemetry_mcp.privacy import scrub_data, scrub_text
from git_telemetry_mcp.process import run_git, validate_repo_path
from git_telemetry_mcp.schema import serialize_telemetry_payload
from git_telemetry_mcp.tools import TOOLS_REGISTRY
from git_telemetry_mcp.tools.active_context_pack import get_active_context_pack
from git_telemetry_mcp.tools.session_timeline import get_session_timeline
from git_telemetry_mcp.tools.smart_commit import generate_smart_commit
from git_telemetry_mcp.tools.working_dir_delta import working_dir_delta

MCP_PROTOCOL_VERSION = "2026-07-28"
SERVER_INFO = {"name": "git-telemetry-mcp", "version": "0.1.2"}
TOOLS_LIST_TTL_MS = 600_000
RESOURCE_MAX_BYTES = 100_000
DEFAULT_REQUEST_MAX_BYTES = 1_000_000
MAX_SSE_SESSIONS = 128
MAX_SSE_QUEUE_ITEMS = 128

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
        "description": "A concise JSON standup assembled from recent telemetry.",
        "mimeType": "application/json",
    },
    {
        "uri": "git://delta/latest",
        "name": "Latest commit delta",
        "description": "A JSON telemetry envelope containing the latest commit diff.",
        "mimeType": "application/json",
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

# SSE session store (in-memory, per-process). Queues are bounded so a client
# that disconnects cannot retain unbounded response state.
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


def _validate_tool_arguments(name: str, arguments: dict) -> bool:
    """Validate caller arguments without exposing schema or value details."""
    try:
        jsonschema.validate(
            arguments, TOOLS_REGISTRY[name]["definition"]["inputSchema"]
        )
    except (jsonschema.ValidationError, jsonschema.SchemaError, TypeError):
        return False
    return True


def _input_required_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize confirmation responses to one scrubbed MCP result shape."""
    scrubbed = scrub_data(result, include_paths=True)
    if not isinstance(scrubbed, dict):
        return {"resultType": "input_required", "content": [], "structuredContent": {}}
    structured = scrubbed.get("structuredContent")
    if not isinstance(structured, dict):
        structured = {
            "resultType": "input_required",
            "inputSchema": scrubbed.get("inputSchema", {}),
        }
    scrubbed["structuredContent"] = scrub_data(structured, include_paths=True)
    return scrubbed


def _untrusted_git_data(label: str, text: str) -> str:
    """Delimit and scrub Git-controlled text before placing it in a prompt."""
    safe = scrub_text(text, include_paths=True)
    return f"<<<BEGIN UNTRUSTED GIT DATA: {label}>>>\n{safe}\n<<<END UNTRUSTED GIT DATA: {label}>>>"


def _serialized_size(text: str) -> int:
    return len(text.encode("utf-8"))


def _bounded_resource_text(serialized: str, repo_path: str) -> str:
    """Keep a serialized resource within the MCP response size budget."""
    original_bytes = _serialized_size(serialized)
    if original_bytes <= RESOURCE_MAX_BYTES:
        return serialized

    data, confidence = _payload_parts(serialized)
    preview = json.dumps(data, ensure_ascii=False, indent=2)

    def render(preview_length: int) -> str:
        return serialize_telemetry_payload(
            {
                "truncated": True,
                "preview": preview[:preview_length],
                "original_bytes": original_bytes,
            },
            repo_path=repo_path,
            confidence_score=confidence,
        )

    # Find the largest UTF-8-safe preview that still fits after serialization.
    low, high = 0, len(preview)
    bounded = render(0)
    while low <= high:
        midpoint = (low + high) // 2
        candidate = render(midpoint)
        if _serialized_size(candidate) <= RESOURCE_MAX_BYTES:
            bounded = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return bounded


def _resource_content(
    uri: str, text: str, mime_type: str, repo_path: str = "."
) -> dict:
    """Build the MCP resources/read ``contents`` item for a text resource."""
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": mime_type,
                "text": _bounded_resource_text(text, repo_path),
            }
        ]
    }


async def _validate_repo_path(repo_path: Any) -> str | None:
    """Return a stable error without echoing caller filesystem identity."""
    try:
        await validate_repo_path(repo_path)
    except (TypeError, ValueError, OSError):
        return "Invalid repo_path"
    return None


def _resource_uri_parts(uri: Any) -> tuple[str, str | None]:
    """Return a listed base URI and an optional query-selected repository."""
    if not isinstance(uri, str):
        return "", None
    try:
        parsed = urlsplit(uri)
        base_uri = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        query = parse_qs(parsed.query, keep_blank_values=True)
    except ValueError:
        return uri, None
    repo_values = query.get("repo_path")
    return base_uri, repo_values[0] if repo_values else None


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
    return serialize_telemetry_payload(
        data, repo_path=repo_path, confidence_score=confidence
    )


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
    return {
        "tools": tools,
        "_meta": {"ttlMs": TOOLS_LIST_TTL_MS, "cacheScope": "global"},
    }


async def _handle_resources_list(params: dict) -> dict:
    return {"resources": [dict(resource) for resource in RESOURCE_DEFINITIONS]}


async def _latest_commit_delta(repo_path: str) -> dict:
    result = await run_git(repo_path, ["show", "--format=", "--unified=3"])
    if result.returncode != 0:
        return {
            "error": result.stderr.decode(errors="replace").strip()
            or "Unable to read latest commit"
        }
    return {"diff": result.stdout.decode(errors="replace")}


async def _handle_resources_read(params: dict) -> dict:
    params = params or {}
    requested_uri = params.get("uri", "")
    base_uri, query_repo_path = _resource_uri_parts(requested_uri)
    resource = next(
        (entry for entry in RESOURCE_DEFINITIONS if entry["uri"] == base_uri), None
    )
    if resource is None:
        return _method_error(f"Unknown resource: {requested_uri}")

    repo_path = (
        query_repo_path if query_repo_path is not None else params.get("repo_path", ".")
    )
    validation_error = await _validate_repo_path(repo_path)
    if validation_error:
        return _method_error(validation_error)

    if base_uri == "telemetry://session/current":
        serialized = await get_session_timeline(
            {"since": "4 hours ago", "until": "now", "repo_path": repo_path}
        )
        return _resource_content(
            requested_uri,
            _repack_payload(serialized, repo_path),
            resource["mimeType"],
            repo_path,
        )

    if base_uri == "telemetry://history/standup":
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
        data = {
            "format": "markdown",
            "content": markdown,
            "source": "get_session_timeline",
        }
        return _resource_content(
            requested_uri,
            serialize_telemetry_payload(
                data, repo_path=repo_path, confidence_score=confidence
            ),
            resource["mimeType"],
            repo_path,
        )

    delta = await _latest_commit_delta(repo_path)
    return _resource_content(
        requested_uri,
        serialize_telemetry_payload(delta, repo_path=repo_path),
        resource["mimeType"],
        repo_path,
    )


async def _handle_prompts_list(params: dict) -> dict:
    return {"prompts": [dict(prompt) for prompt in PROMPT_DEFINITIONS]}


async def _handle_prompts_get(params: dict) -> dict:
    params = params or {}
    name = params.get("name", "")
    definition = next(
        (prompt for prompt in PROMPT_DEFINITIONS if prompt["name"] == name), None
    )
    if definition is None:
        return _method_error(f"Unknown prompt: {name}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _method_error("Invalid prompt arguments: expected an object")
    repo_path = arguments.get("repo_path", ".")
    validation_error = await _validate_repo_path(repo_path)
    if validation_error:
        return _method_error(validation_error)

    if name == "review_debug_loop":
        context = _repack_payload(
            await get_active_context_pack({"repo_path": repo_path}), repo_path
        )
        delta = _repack_payload(
            await working_dir_delta({"repo_path": repo_path, "include_diff": True}),
            repo_path,
        )
        text = (
            "Review/debug loop: inspect the active context, review the working-tree delta, "
            "reproduce the issue, then validate a focused fix.\n\n"
            f"Active context:\n{_untrusted_git_data('active-context', _compact(context))}\n\n"
            f"Working-tree delta:\n{_untrusted_git_data('working-tree-delta', _compact(delta))}"
        )
    elif name == "generate_commit_message_context":
        staged = _repack_payload(
            await generate_smart_commit({"repo_path": repo_path}), repo_path
        )
        text = (
            "Generate a concise conventional commit message from the staged changes. "
            "Preserve the intent and avoid inventing scope.\n\nStaged-change context:\n"
            f"{_untrusted_git_data('staged-change', _compact(staged))}"
        )
    else:
        context = _repack_payload(
            await get_active_context_pack({"repo_path": repo_path}), repo_path
        )
        timeline = _repack_payload(
            await get_session_timeline(
                {"since": "24 hours ago", "until": "now", "repo_path": repo_path}
            ),
            repo_path,
        )
        text = (
            "Prepare concise handover notes: state, recent work, outstanding changes, "
            "and the next useful action.\n\nActive context:\n"
            f"{_untrusted_git_data('active-context', _compact(context))}\n\n"
            f"Recent activity:\n{_untrusted_git_data('recent-activity', _compact(timeline))}"
        )

    return {
        "description": definition["description"],
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }


async def _handle_tools_call(params: dict) -> dict:
    if not isinstance(params, dict):
        return {
            "content": [{"type": "text", "text": "Invalid tools/call params"}],
            "isError": True,
        }
    name = params.get("name", "")
    arguments = params.get("arguments", {})

    if not isinstance(name, str) or not name:
        return {
            "content": [{"type": "text", "text": "Invalid tool name"}],
            "isError": True,
        }
    if not isinstance(arguments, dict):
        return {
            "content": [{"type": "text", "text": "Invalid tool arguments"}],
            "isError": True,
        }
    if name not in TOOLS_REGISTRY:
        return {"content": [{"type": "text", "text": "Unknown tool"}], "isError": True}
    if not _validate_tool_arguments(name, arguments):
        return {
            "content": [{"type": "text", "text": "Invalid tool arguments"}],
            "isError": True,
        }

    try:
        result = await TOOLS_REGISTRY[name]["handler"](arguments)
        if isinstance(result, dict) and result.get("resultType") == "input_required":
            return _input_required_result(result)
        text = (
            result
            if isinstance(result, str)
            else json.dumps(result, ensure_ascii=False)
        )
        response = {"content": [{"type": "text", "text": text}]}
        if os.getenv("GIT_TELEMETRY_STRUCTURED_CONTENT", "1").lower() not in {
            "0",
            "false",
            "no",
        }:
            try:
                parsed = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, dict):
                response["structuredContent"] = scrub_data(parsed, include_paths=True)
        return response
    except Exception:  # noqa: BLE001 — transport boundary must contain tool failures
        return {
            "content": [{"type": "text", "text": "Tool execution failed"}],
            "isError": True,
        }


def _validate_rpc_body(body: Any) -> tuple[dict | None, dict | None]:
    if not isinstance(body, dict):
        return None, _jsonrpc_error(None, -32600, "Invalid Request")
    req_id = body.get("id")
    if (
        "jsonrpc" not in body
        or body.get("jsonrpc") != "2.0"
        or not isinstance(body.get("method"), str)
        or not body["method"]
    ):
        return None, _jsonrpc_error(req_id, -32600, "Invalid Request")
    if "id" in body and not (
        req_id is None
        or isinstance(req_id, (str, int, float))
        and not isinstance(req_id, bool)
    ):
        return None, _jsonrpc_error(None, -32600, "Invalid Request")
    if "params" in body and not isinstance(body["params"], dict):
        return None, _jsonrpc_error(req_id, -32602, "Invalid params")
    if body["method"] == "tools/call":
        params = body.get("params")
        if (
            not isinstance(params, dict)
            or not isinstance(params.get("name"), str)
            or not params["name"]
        ):
            return None, _jsonrpc_error(req_id, -32602, "Invalid params")
        if "arguments" in params and not isinstance(params["arguments"], dict):
            return None, _jsonrpc_error(req_id, -32602, "Invalid params")
        if params.get("name") in TOOLS_REGISTRY and not _validate_tool_arguments(
            params["name"], params.get("arguments", {})
        ):
            return None, _jsonrpc_error(req_id, -32602, "Invalid tool arguments")
    return body, None

async def _dispatch(body: dict) -> dict | None:
    is_notification = isinstance(body, dict) and "id" not in body
    validated_body, validation_error = _validate_rpc_body(body)
    if validation_error:
        return None if is_notification else validation_error
    assert validated_body is not None
    body = validated_body
    method = body["method"]
    params = body.get("params", {})
    req_id = body.get("id")
    is_notification = "id" not in body

    if method == "notifications/initialized":
        return None if is_notification else _jsonrpc_response(req_id, {})

    handler = METHOD_HANDLERS.get(method)
    if not handler:
        return (
            None
            if is_notification
            else _jsonrpc_error(req_id, -32601, f"Method not found: {method}")
        )

    result = await handler(params)
    return None if is_notification else _jsonrpc_response(req_id, result)


METHOD_HANDLERS = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
    "resources/list": _handle_resources_list,
    "resources/read": _handle_resources_read,
    "prompts/list": _handle_prompts_list,
    "prompts/get": _handle_prompts_get,
}


# --- HTTP Transport ---


def _transport_error(
    status: int, message: str, *, authenticate: bool = False
) -> Response:
    headers = _mcp_headers()
    if authenticate:
        headers["WWW-Authenticate"] = 'Bearer realm="git-telemetry-mcp"'
    return JSONResponse({"error": message}, status_code=status, headers=headers)


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    configured = {
        item.strip().rstrip("/")
        for item in os.getenv("GIT_TELEMETRY_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    if configured:
        return origin.rstrip("/") in configured
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


def _request_boundary(request: Request, *, json_body: bool = False) -> Response | None:
    token = os.getenv("GIT_TELEMETRY_AUTH_TOKEN")
    if token:
        authorization = request.headers.get("authorization", "")
        supplied = (
            authorization[7:] if authorization.lower().startswith("bearer ") else ""
        )
        if not supplied or not hmac.compare_digest(supplied, token):
            return _transport_error(401, "Unauthorized", authenticate=True)
    else:
        client_host = request.client.host if request.client else None
        if client_host and client_host not in {"localhost", "127.0.0.1", "::1"}:
            return _transport_error(
                403, "Localhost access required when GIT_TELEMETRY_AUTH_TOKEN is unset"
            )
    if not _origin_allowed(request):
        return _transport_error(403, "Origin not allowed")
    if json_body:
        content_type = (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        if content_type != "application/json":
            return _transport_error(415, "Content-Type must be application/json")
    return None


def _request_max_bytes() -> int:
    try:
        return max(
            1,
            int(
                os.getenv("GIT_TELEMETRY_MAX_REQUEST_BYTES", DEFAULT_REQUEST_MAX_BYTES)
            ),
        )
    except ValueError:
        return DEFAULT_REQUEST_MAX_BYTES


async def mcp_post(request: Request) -> Response:
    boundary_error = _request_boundary(request, json_body=True)
    if boundary_error:
        return boundary_error
    maximum = _request_max_bytes()
    try:
        declared_length = int(request.headers.get("content-length", "0"))
    except ValueError:
        declared_length = 0
    if declared_length > maximum:
        return _transport_error(413, "Request body too large")
    raw_body = await request.body()
    if len(raw_body) > maximum:
        return _transport_error(413, "Request body too large")
    try:
        body = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return JSONResponse(
            _jsonrpc_error(None, -32700, "Parse error"), headers=_mcp_headers()
        )

    session_id = request.headers.get("mcp-session-id") or request.query_params.get(
        "session_id"
    )
    response = await _dispatch(body)
    if session_id and session_id in _sse_sessions:
        if response is not None:
            queue = _sse_sessions[session_id]
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(response)
        return Response(status_code=202, headers=_mcp_headers())
    if response is None:
        return Response(status_code=202, headers=_mcp_headers())

    resp_headers = _mcp_headers()
    if isinstance(body, dict):
        resp_headers["Mcp-Method"] = body.get("method", "")
        if body.get("method") == "tools/call":
            params = body.get("params") or {}
            if isinstance(params, dict):
                resp_headers["Mcp-Name"] = params.get("name", "")
        if body.get("method") == "initialize":
            resp_headers["Mcp-Session-Id"] = str(uuid.uuid4())
    return JSONResponse(response, headers=resp_headers)


# --- SSE Transport ---
async def mcp_sse(request: Request) -> Response:
    boundary_error = _request_boundary(request)
    if boundary_error:
        return boundary_error
    session_id = (
        request.headers.get("mcp-session-id")
        or request.query_params.get("session_id")
        or str(uuid.uuid4())
    )
    if len(session_id) > 256:
        return _transport_error(400, "Invalid session id")
    queue = _sse_sessions.get(session_id)
    if queue is None:
        if len(_sse_sessions) >= MAX_SSE_SESSIONS:
            return _transport_error(429, "Too many SSE sessions")
        queue = asyncio.Queue(maxsize=MAX_SSE_QUEUE_ITEMS)
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
            if _sse_sessions.get(session_id) is queue:
                _sse_sessions.pop(session_id, None)

    return EventSourceResponse(event_generator(), headers=_mcp_headers())


# --- Health ---
async def health(request: Request) -> JSONResponse | Response:
    boundary_error = _request_boundary(request)
    if boundary_error:
        return boundary_error
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
