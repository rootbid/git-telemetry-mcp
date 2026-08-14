# Git Telemetry MCP

An MCP server implementing the documented, focused subset of the MCP `2026-07-28` protocol. It provides a **Development Time Machine** by analyzing git reflogs, commit history, working directory deltas, and permitted shell history to give AI assistants temporal context.

## Quick Start

```bash
# Install dependencies
uv sync

# Run the MCP server (HTTP JSON-RPC on http://127.0.0.1:8787/mcp, SSE on /sse)
uv run git-telemetry-mcp

# Run tests
uv run pytest
```

## MCP Client Configuration

Add to your MCP client config (e.g. Claude Desktop, Cursor, OpenCode):

```json
{
  "mcpServers": {
    "git-telemetry": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

## HTTP security boundaries

The HTTP JSON-RPC endpoint is `POST /mcp`; the server also exposes the SSE
stream at `GET /sse`. Set `GIT_TELEMETRY_AUTH_TOKEN` to require
`Authorization: Bearer <token>` on every HTTP/SSE request. When the variable is
unset, the server remains usable for local development but accepts requests
only from loopback clients and localhost origins. Requests must use
`Content-Type: application/json`, are size-bounded, and reject non-local
origins by default. Configure `GIT_TELEMETRY_ALLOWED_ROOTS` (path-separated)
to restrict repository arguments to approved roots.

### Protocol scope

The server implements `initialize`, `notifications/initialized`, `tools/list`,
`tools/call`, `resources/list`, `resources/read`, `prompts/list`, and
`prompts/get` over its HTTP JSON-RPC and SSE transports. Unsupported MCP
methods return JSON-RPC `-32601` (`Method not found`); this implementation does
not claim support for completion, sampling, logging, subscriptions, or other
methods not listed above. HTTP requests are stateless; SSE uses a bounded,
in-memory queue and is not a durable session store.

### Shell-history boundaries

`dev_activity` does not read arbitrary paths. An explicit
`shell_history_path` must be a regular file beneath a directory listed in
`GIT_TELEMETRY_ALLOWED_HISTORY_ROOTS` (path-separated), or a detected history
file under the server process user's home directory. History reads are capped
at one MiB by default; set `GIT_TELEMETRY_MAX_HISTORY_BYTES` to a smaller local
limit. Rejected paths produce a safe partial telemetry result without echoing
the rejected path. The returned `history_file` value is passed through path
scrubbing.

When `GIT_TELEMETRY_AUTH_TOKEN` is unset, the endpoint is intended for local
development only. Configure authentication and repository/history roots before
placing it behind a network-facing proxy.


## Key Features & Architecture

- **16 MCP Tools:** Context packs, reflog timelines, dirty diff summaries, shell correlation, dry-run merge checks, stale branch detection, and smart commit generation.
- **Privacy boundary:** The serializer scrubs known secret patterns and configured path patterns before telemetry or prompt return. It is defense in depth, not a guarantee that arbitrary command text or repository data is safe to disclose.
- **Telemetry envelope:** Successful tool results use a `telemetry_payload` envelope (`timezone_offset`, `repo_checksum`, `confidence_score`, `data`); tool arguments are validated against advertised `inputSchema` before execution.
- **Safe destructive operations:** `safe_git_reset` and `safe_git_checkout` require the documented two-round-trip `input_required` confirmation flow.

For complete documentation, tool reference, argument schemas, sample outputs, and protocol details, see [HANDBOOK.md](HANDBOOK.md).
