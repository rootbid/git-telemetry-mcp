# Git Telemetry MCP

An MCP server that behaves as a **Development Time Machine**. Exposes tools to analyze git reflogs, working directory state, branch health, and bash history to give AI assistants instant context about what you've been doing.

## Getting started

```bash
# Install
uv sync

# Run
uv run git-telemetry-mcp
# Server starts on http://127.0.0.1:8787
```

## MCP Client Configuration

Add to your MCP client config (e.g. Claude Desktop, Cursor):

```json
{
  "mcpServers": {
    "git-telemetry": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

For SSE transport, connect to `http://127.0.0.1:8787/sse`.

## Architecture

```
git_telemetry_mcp/
├── __init__.py
├── server.py                 # Starlette app, JSON-RPC dispatch, SSE
└── tools/
    ├── __init__.py           # Tool registry (14 tools)
    ├── git_timeline.py       # Reflog + commit analysis
    ├── working_dir_delta.py  # Dirty tree summary
    ├── dev_activity.py       # Shell history correlation
    ├── session_timeline.py   # Unified session view
    ├── uncommitted_drift.py  # Architectural drift analysis
    ├── file_evolution.py     # Per-file diff history
    ├── active_context_pack.py # Prompt-optimized context blob
    ├── safe_operations.py    # safe_git_reset, safe_git_checkout
    ├── stash_isolate.py      # Stash checkpoint before risky ops
    ├── stale_branches.py     # Merged/inactive branch detection
    ├── smart_commit.py       # Conventional Commits generation
    ├── developer_velocity.py # Churn and frequency metrics
    └── conflict_check.py     # Dry-run merge conflict detection
```

## Development

```bash
# Run in dev mode with auto-reload
uv run uvicorn git_telemetry_mcp.server:app --reload --port 8787

# Test a tool call
curl -X POST http://127.0.0.1:8787/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_active_context_pack","arguments":{}}}'
```
