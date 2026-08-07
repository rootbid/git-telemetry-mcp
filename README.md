# Git Telemetry MCP

An MCP server (target spec `2026-07-28`) providing a **Development Time Machine**. Analyzes git reflogs, commit history, working directory deltas, and shell history to give AI assistants temporal context about developer activity.

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
<!-- mcp-name: io.github.de391882/git-telemetry-mcp -->


## Key Features & Architecture

- **16 MCP Tools:** Context packs, reflog timelines, dirty diff summaries, shell correlation, dry-run merge checks, stale branch detection, and smart commit generation.
- **Privacy First:** Built-in serializer gate redacts PII, private keys, authorization tokens, API keys, and database credentials before payload return.
- **Contract Compliant:** Every tool emits a `telemetry_payload` envelope (`timezone_offset`, `repo_checksum`, `confidence_score`, `data`) validated against draft-07 `outputSchema` and annotated with `readOnlyHint`, `destructiveHint`, and `idempotentHint`.
- **Safe Destructive Operations:** Two-round-trip `input_required` confirmation flow for `safe_git_reset` and `safe_git_checkout`.

For complete documentation, tool reference, argument schemas, sample outputs, and protocol details, see [HANDBOOK.md](HANDBOOK.md).
