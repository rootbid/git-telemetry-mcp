# Git Telemetry MCP — Complete User & Developer Handbook

Welcome to the **Git Telemetry MCP Server** handbook. This server provides a **Development Time Machine** for AI assistants, exposing 16 tools that analyze git reflogs, commit histories, working directory state, branch health, and shell commands to provide rich temporal context.

---

## Table of Contents

1. [Protocol Target & Architecture](#1-protocol-target--architecture)
2. [Quick Start & Transport Endpoints](#2-quick-start--transport-endpoints)
3. [MCP Client Integration](#3-mcp-client-integration)
4. [Data Privacy & Forensic Hygiene](#4-data-privacy--forensic-hygiene)
5. [Telemetry Envelope & Schema Standard](#5-telemetry-envelope--schema-standard)
6. [Interactive Confirmation Flow (`input_required`)](#6-interactive-confirmation-flow-input_required)
7. [Comprehensive Tool Reference](#7-tool-reference)
   - [Context & Timeline Tools](#1-context--timeline-tools)
   - [Analysis & Insights Tools](#2-analysis--insights-tools)
   - [Branch & Merge Safety Tools](#3-branch--merge-safety-tools)
   - [Safe Operations Tools](#4-safe-operations-tools)
   - [Commit Generation Tools](#5-commit-generation-tools)
8. [Testing & Verification](#8-testing--verification)

---

## 1. Protocol Target & Architecture

- **Protocol Version:** MCP `2026-07-28`
- **Transport:** Stateless HTTP JSON-RPC + Server-Sent Events (SSE)
- **Runtime:** Python `>= 3.13` (managed via `uv`)

```
git_telemetry_mcp/
├── __init__.py
├── server.py                 # Starlette server, JSON-RPC dispatch, SSE handler
├── privacy.py                # Secret scrubber (PII/key redaction serializer gate)
├── schema.py                 # telemetry_payload wrapper & outputSchema generator
└── tools/
    ├── __init__.py           # Registry of 16 tools with draft-07 schemas & annotations
    ├── git_timeline.py       # Reflog + commit analysis
    ├── working_dir_delta.py  # Dirty working tree diff stats + entropy
    ├── dev_activity.py       # Shell history correlation
    ├── session_timeline.py   # Unified session timeline
    ├── uncommitted_drift.py  # Architectural direction summary
    ├── file_evolution.py     # File diff history across commits/stashes
    ├── active_context_pack.py # Prompt-optimized context pack
    ├── safe_operations.py    # safe_git_reset, safe_git_checkout (confirmation flows)
    ├── stash_isolate.py      # Pre-operation stash isolation
    ├── stale_branches.py     # Merged and inactive branch scanner
    ├── smart_commit.py       # Conventional Commits inference
    ├── developer_velocity.py # Code churn and commit frequency
    ├── conflict_check.py     # Dry-run merge conflict detection
    ├── temporal_snapshot.py  # Aggregated temporal snapshot
    └── compare_checkpoints.py# Multi-checkpoint diff comparison
```

---

## 2. Quick Start & Transport Endpoints

### Installation & Execution

```bash
# Clone & install dependencies
uv sync

# Run server (default host: 127.0.0.1, port: 8787)
uv run git-telemetry-mcp

# Run dev mode with auto-reload
uv run uvicorn git_telemetry_mcp.server:app --reload --port 8787
```

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/mcp` | POST | Stateless HTTP JSON-RPC 2.0 endpoint |
| `/sse` | GET | Server-Sent Events (SSE) streaming session connection |
| `/health` | GET | Health check returning server version and status |

---

## 3. MCP Client Integration

### Claude Desktop / Cursor / OpenCode Config

Add `git-telemetry` to your MCP settings file:

```json
{
  "mcpServers": {
    "git-telemetry": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

For SSE connection, use `http://127.0.0.1:8787/sse`.

---

## 4. Data Privacy & Forensic Hygiene

The server enforces zero-trust data privacy at the serialization gate. Before any tool payload is returned, `serialize_telemetry_payload()` passes the complete payload through the `git_telemetry_mcp/privacy.py` scrubber.

### Scrubbed Secret Patterns
- **Private Keys:** PEM, RSA, OpenSSH, EC, PGP blocks
- **Authorization Headers:** `Authorization: Bearer ...`, `Authorization: Basic ...`
- **Embedded Credentials in URLs:** `https://user:pass@domain.com`, `postgres://user:pass@host/db`
- **AWS Keys:** `AKIA...`, `ASIA...`, `aws_secret_access_key`
- **GitHub Tokens:** `ghp_...`, `gho_...`, `github_pat_...`
- **AI Service Keys:** OpenAI `sk-proj-...`, Anthropic `sk-ant-...`
- **SaaS Tokens:** Slack `xoxb-...`, Stripe `sk_live_...`, JWTs

### Custom Secret Patterns
You can supply additional custom regex patterns via the `GIT_TELEMETRY_EXCLUDE_PATTERNS` environment variable (comma or newline separated):

```bash
export GIT_TELEMETRY_EXCLUDE_PATTERNS="COMPANY_TOKEN_[A-Z0-9]+,INTERNAL_KEY_.*"
```

---

## 5. Telemetry Envelope & Schema Standard

Every tool handler returns a standardized JSON envelope called `telemetry_payload`.

```json
{
  "timezone_offset": "+00:00",
  "repo_checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "confidence_score": 1.0,
  "data": { ... }
}
```

### Envelope Fields

| Field | Type | Description |
|-------|------|-------------|
| `timezone_offset` | string | Local ISO 8601 offset (e.g. `"+00:00"` or `"-05:00"`) |
| `repo_checksum` | string | SHA-256 hash of the resolved absolute repository path |
| `confidence_score` | float | 0.0 to 1.0 indicator of data accuracy/completeness |
| `data` | object/array | Tool-specific structured result |

### Tool Annotations
Each tool in `tools/list` contains explicit annotations indicating its operational behavior:
- `readOnlyHint` (`boolean`): True if tool only queries state without modifying disk or refs.
- `destructiveHint` (`boolean`): True if tool performs reset/checkout/commit operations that alter git history or working tree.
- `idempotentHint` (`boolean`): True if repeated calls with identical parameters return identical side effects.

---

## 6. Interactive Confirmation Flow (`input_required`)

Destructive operations (`safe_git_reset`, `safe_git_checkout`) implement a two-round-trip confirmation flow:

1. **Initial Call:** Tool previews affected files/commits, generates a random `confirmation_id`, and returns `resultType: "input_required"`.
2. **User Confirmation:** Client sends a second call providing `confirmation_id`. The operation executes and returns the final `telemetry_payload`.

---

## 7. Tool Reference

### 1. Context & Timeline Tools

#### `git_timeline`
Analyzes reflogs and commit history over a specified time window to identify branch switches, resets, rebases, and commits.

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `since` | string | Yes | Start time (e.g. `"3.hours.ago"`, `"2026-08-01"`) |
  | `until` | string | No | End time (default: `"now"`) |
  | `repo_path` | string | No | Path to git repository (default: cwd) |

- **Sample Request:**
  ```json
  {
    "method": "tools/call",
    "params": {
      "name": "git_timeline",
      "arguments": { "since": "2.hours.ago" }
    }
  }
  ```

#### `get_session_timeline`
Combines reflog events, commit logs, stashes, and file `mtime` attributes to summarize activity during a work session.

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `since` | string | Yes | Start time (e.g. `"4.hours.ago"`) |
  | `until` | string | No | End time (default: `"now"`) |
  | `repo_path` | string | No | Path to repository (default: cwd) |

#### `get_active_context_pack`
Aggregates active branch, recent commits, uncommitted modified files, remotes, and merge/rebase state into a prompt-optimized context pack.

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `repo_path` | string | No | Path to repository (default: cwd) |

#### `dev_activity`
Correlates zsh/bash shell history with git commits and reflog entries to reconstruct developer commands and workflow.

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `since` | string | Yes | Start time (e.g. `"1.day.ago"`) |
  | `until` | string | No | End time (default: `"now"`) |
  | `repo_path` | string | No | Repository path (default: cwd) |
  | `shell_history_path` | string | No | Path to history file (auto-detected if omitted) |

#### `get_temporal_snapshot`
The Time Machine entry point. Aggregates `git_timeline`, `working_dir_delta`, and `dev_activity` into a unified temporal window.

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `time_range` | string | Yes | Time range string (e.g. `"last 45m"`, `"1.hour.ago"`) |
  | `repo_path` | string | No | Path to repository |
  | `granularity` | string | No | Detail level (default: `"raw"`) |

---

### 2. Analysis & Insights Tools

#### `working_dir_delta`
Summarizes dirty working directory status: staged, unstaged, and untracked changes alongside diff stats and a calculated change entropy score.

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `repo_path` | string | No | Path to repository |
  | `include_diff` | boolean | No | Include full diff hunk text (default: `false`) |

#### `explain_uncommitted_drift`
Compares unstaged and staged changes against `HEAD` to summarize architectural trends (new function definitions, dependencies pulled in, refactoring balance).

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `repo_path` | string | No | Path to repository |

#### `trace_file_evolution`
Traces commit history, stashes, and line blame for a specific file to pinpoint where changes or bugs were introduced.

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `file_path` | string | Yes | Target file path relative to repo root |
  | `repo_path` | string | No | Repository path |
  | `max_commits` | integer | No | Maximum commits to trace (default: `10`) |

#### `get_developer_velocity`
Calculates code churn (lines added/deleted), top touched files, and commit frequency per day over a time range.

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `since` | string | Yes | Start time (e.g. `"7.days.ago"`) |
  | `until` | string | No | End time |
  | `repo_path` | string | No | Repository path |
  | `author` | string | No | Author name/email filter |

#### `compare_workspace_checkpoints`
Compares two historical git checkpoints (commit SHAs, branches, or date strings) against each other and current `HEAD`.

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `ref1` | string | Yes | First reference (e.g. `"HEAD~1"`, `"1.day.ago"`) |
  | `ref2` | string | Yes | Second reference (e.g. `"HEAD"`, `"main"`) |
  | `repo_path` | string | No | Repository path |
  | `include_diff_content` | boolean | No | Include full unified diff content |

---

### 3. Branch & Merge Safety Tools

#### `conflict_prelim_check`
Performs a dry-run merge check via `git merge-tree` to identify potential merge conflicts before modifying the working directory.

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `target_branch` | string | No | Target branch (default: `"main"`) |
  | `source_branch` | string | No | Source branch (default: current branch) |
  | `repo_path` | string | No | Repository path |

#### `detect_stale_branches`
Scans local and remote tracking branches to identify merged or inactive branches eligible for deletion.

- **Annotations:** `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `repo_path` | string | No | Repository path |
  | `days_inactive` | integer | No | Days threshold (default: `30`) |
  | `include_remote` | boolean | No | Check remote tracking branches (default: `true`) |

---

### 4. Safe Operations Tools

#### `safe_git_reset`
Safely resets workspace state with confirmation prompts for hard resets.

- **Annotations:** `readOnlyHint: false`, `destructiveHint: true`, `idempotentHint: false`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `target` | string | No | Target ref (default: `"HEAD"`) |
  | `mode` | string | No | Reset mode (`"--hard"`, `"--soft"`, `"--mixed"`) |
  | `repo_path` | string | No | Repository path |
  | `confirmation_id` | string | No | Confirmation token for second round-trip |

#### `safe_git_checkout`
Safely switches branches or checkouts commits, requiring explicit confirmation if `--force` is requested.

- **Annotations:** `readOnlyHint: false`, `destructiveHint: true`, `idempotentHint: false`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `target` | string | Yes | Target branch/commit |
  | `force` | boolean | No | Force checkout flag |
  | `repo_path` | string | No | Repository path |
  | `confirmation_id` | string | No | Confirmation token |

#### `stash_and_isolate`
Stashes uncommitted changes (including untracked files) into a named isolation checkpoint before executing risky commands.

- **Annotations:** `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: false`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `repo_path` | string | No | Repository path |
  | `message` | string | No | Custom stash description |
  | `operation` | string | No | Operation name tag |

---

### 5. Commit Generation Tools

#### `generate_smart_commit`
Analyzes staged diffs and infers a Conventional Commits message (`feat`, `fix`, `refactor`, `docs`, `test`, `chore`). Optionally creates the commit.

- **Annotations:** `readOnlyHint: false`, `destructiveHint: true`, `idempotentHint: false`
- **Arguments:**
  | Argument | Type | Required | Description |
  |----------|------|----------|-------------|
  | `repo_path` | string | No | Repository path |
  | `execute` | boolean | No | Create commit if true (default: `false`) |

---

### 6. Resources & Prompts

The server advertises `resources` and `prompts` capabilities during `initialize`.

#### Resources

`resources/list` exposes these stable resources:

| URI | MIME type | Purpose |
|-----|-----------|---------|
| `telemetry://session/current` | `application/json` | Recent activity and current session |
| `telemetry://history/standup` | `text/markdown` | Standup-style recent activity summary |
| `git://delta/latest` | `text/plain` | Unified diff for the latest commit |

Call `resources/read` with the URI. A repository may be selected with the optional
`repo_path` parameter, or with a URI query such as
`telemetry://session/current?repo_path=/workspace/project`. The path must resolve to
an existing Git working tree. Dynamic resource data passes through the privacy
serializer and oversized payloads are returned as bounded, explicitly truncated
previews.

#### Prompts

`prompts/list` and `prompts/get` expose reusable context assembly:

- `review_debug_loop` — active context plus working-tree delta.
- `generate_commit_message_context` — staged-change context for commit wording.
- `handover_notes` — current state plus recent activity for a handoff.

Prompt arguments accept an optional `repo_path`, which is validated as a Git
working tree before telemetry is collected.

---

## 8. Testing & Verification

Run the comprehensive unit and contract test suite using `uv`:

```bash
# Execute unit, privacy, schema, and contract tests
uv run pytest

# Run with verbose output
uv run pytest -v
```

All tool handlers and endpoints are validated against JSON Schema contracts and secret redaction rules during automated test runs.
