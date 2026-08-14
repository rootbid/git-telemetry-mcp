"""Tools registry with annotations and output schemas for MCP tools/list and tools/call."""

from git_telemetry_mcp.process import install_safe_subprocess, validate_repo_path
from git_telemetry_mcp.schema import make_output_schema
from git_telemetry_mcp.tools.active_context_pack import get_active_context_pack
from git_telemetry_mcp.tools.compare_checkpoints import compare_workspace_checkpoints
from git_telemetry_mcp.tools.conflict_check import conflict_prelim_check
from git_telemetry_mcp.tools.dev_activity import dev_activity
from git_telemetry_mcp.tools.developer_velocity import get_developer_velocity
from git_telemetry_mcp.tools.file_evolution import trace_file_evolution
from git_telemetry_mcp.tools.git_timeline import git_timeline
from git_telemetry_mcp.tools.safe_operations import safe_git_checkout, safe_git_reset
from git_telemetry_mcp.tools.session_timeline import get_session_timeline
from git_telemetry_mcp.tools.smart_commit import generate_smart_commit
from git_telemetry_mcp.tools.stale_branches import detect_stale_branches
from git_telemetry_mcp.tools.stash_isolate import stash_and_isolate
from git_telemetry_mcp.tools.temporal_snapshot import get_temporal_snapshot
from git_telemetry_mcp.tools.uncommitted_drift import explain_uncommitted_drift
from git_telemetry_mcp.tools.working_dir_delta import working_dir_delta

install_safe_subprocess()


def _tool_entry(
    handler,
    name: str,
    description: str,
    input_schema: dict,
    data_schema: dict,
    read_only: bool = True,
    destructive: bool = False,
    idempotent: bool = True,
) -> dict:
    async def validated_handler(arguments: dict):
        if not isinstance(arguments, dict):
            raise ValueError("Invalid tool arguments: expected an object")
        checked = dict(arguments)
        checked["_repo_path_provided"] = "repo_path" in arguments
        checked["repo_path"] = await validate_repo_path(checked.get("repo_path", "."))
        return await handler(checked)

    annotations = {
        "readOnly": read_only,
        "destructive": destructive,
        "idempotent": idempotent,
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": idempotent,
    }
    definition = {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "outputSchema": make_output_schema(data_schema),
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": idempotent,
        "annotations": annotations,
    }
    return {
        "handler": validated_handler,
        "definition": definition,
    }


TOOLS_REGISTRY: dict[str, dict] = {
    "git_timeline": _tool_entry(
        handler=git_timeline,
        name="git_timeline",
        description=(
            "Analyze git reflog and commit history over a time range. "
            "Returns commits, branch switches, rebases, resets, and semantic details."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "description": "Start of time range (e.g. '3.hours.ago').",
                },
                "until": {
                    "type": "string",
                    "description": "End of time range (default: now).",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
            },
            "required": ["since"],
        },
        data_schema={
            "type": "object",
            "properties": {
                "range": {"type": "object"},
                "reflog": {"type": "array"},
                "commits": {"type": "array"},
                "summary": {"type": "string"},
                "reflog_warning": {"type": "string"},
            },
            "required": ["range", "reflog", "commits", "summary"],
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    "working_dir_delta": _tool_entry(
        handler=working_dir_delta,
        name="working_dir_delta",
        description="Summarize the current dirty working directory: staged, unstaged, and untracked changes with diff stats and entropy score.",
        input_schema={
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "include_diff": {
                    "type": "boolean",
                    "description": "Include full diff content (default: false).",
                },
            },
        },
        data_schema={
            "type": "object",
            "properties": {
                "staged": {"type": "object"},
                "unstaged": {"type": "object"},
                "untracked": {"type": "array"},
                "summary": {"type": "string"},
                "change_entropy": {"type": "number"},
                "hunk_diffs": {"type": "object"},
            },
            "required": [
                "staged",
                "unstaged",
                "untracked",
                "summary",
                "change_entropy",
            ],
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    "dev_activity": _tool_entry(
        handler=dev_activity,
        name="dev_activity",
        description=(
            "Correlate permitted shell history with git activity over a time range. "
            "Explicit history paths must be regular files under "
            "GIT_TELEMETRY_ALLOWED_HISTORY_ROOTS or detected home history files."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "description": "Start of time range (e.g. '2.hours.ago').",
                },
                "until": {
                    "type": "string",
                    "description": "End of time range (default: now).",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "shell_history_path": {
                    "type": "string",
                    "description": (
                        "Optional regular history file path. It must be under a configured "
                        "GIT_TELEMETRY_ALLOWED_HISTORY_ROOTS root or be a detected home history file."
                    ),
                },
            },
            "required": ["since"],
        },
        data_schema={
            "type": "object",
            "properties": {
                "range": {"type": "object"},
                "shell_commands": {"type": "array"},
                "git_events": {"type": "array"},
                "summary": {"type": "string"},
                "history_file": {"type": "string"},
                "history_error": {"type": "string"},
                "history_bytes_truncated": {"type": "boolean"},
                "history_precision_warning": {"type": "string"},
            },
            "required": ["range", "shell_commands", "git_events", "summary"],
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    "get_session_timeline": _tool_entry(
        handler=get_session_timeline,
        name="get_session_timeline",
        description="Combines git reflog, recent commits, stashes, and file modification timestamps to answer what was worked on.",
        input_schema={
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "description": "Start of time range (e.g. '3.hours.ago').",
                },
                "until": {
                    "type": "string",
                    "description": "End of time range (default: now).",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "session": {
                    "type": ["string", "integer"],
                    "description": "Optional session selector (e.g. 3, '#3', 'Session #3') to focus events on one detected session.",
                },
            },
            "required": ["since"],
        },
        data_schema={
            "type": "object",
            "properties": {
                "range": {"type": "object"},
                "events": {"type": "array"},
                "recently_modified_files": {"type": "array"},
                "sessions": {"type": "array"},
                "summary": {"type": "string"},
            },
            "required": ["range", "events", "recently_modified_files", "summary"],
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    "explain_uncommitted_drift": _tool_entry(
        handler=explain_uncommitted_drift,
        name="explain_uncommitted_drift",
        description="Compares current unstaged/staged changes against HEAD and summarizes architectural direction.",
        input_schema={
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
            },
        },
        data_schema={
            "type": "object",
            "properties": {
                "stat": {"type": "string"},
                "staged_stat": {"type": "string"},
                "analysis": {"type": "object"},
                "drift_summary": {"type": "string"},
            },
            "required": ["stat", "staged_stat", "analysis", "drift_summary"],
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    "trace_file_evolution": _tool_entry(
        handler=trace_file_evolution,
        name="trace_file_evolution",
        description="Pull diff histories across recent commits, branches, and stashes for a specific file to identify change origins.",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to trace (relative to repo root).",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "max_commits": {
                    "type": "integer",
                    "description": "Maximum commits to analyze (default: 10).",
                },
            },
            "required": ["file_path"],
        },
        data_schema={
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "commit_history": {"type": "array"},
                "stash_appearances": {"type": "array"},
                "blame_summary": {"type": "object"},
                "summary": {"type": "string"},
            },
            "required": [
                "file",
                "commit_history",
                "stash_appearances",
                "blame_summary",
                "summary",
            ],
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    "get_active_context_pack": _tool_entry(
        handler=get_active_context_pack,
        name="get_active_context_pack",
        description="Aggregates active branch, recent commits, modified files, and repo state into a prompt-optimized context blob.",
        input_schema={
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
            },
        },
        data_schema={
            "type": "object",
            "properties": {
                "branch": {"type": "string"},
                "recent_commits": {"type": "array"},
                "modified_files": {"type": "array"},
                "unpushed_commits": {"type": "array"},
                "remotes": {"type": "array"},
                "state": {"type": "object"},
                "summary": {"type": "string"},
            },
            "required": [
                "branch",
                "recent_commits",
                "modified_files",
                "unpushed_commits",
                "remotes",
                "state",
                "summary",
            ],
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    "safe_git_reset": _tool_entry(
        handler=safe_git_reset,
        name="safe_git_reset",
        description="Intercepts git reset --hard and requires explicit confirmation before executing.",
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Reset target (default: HEAD).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["--hard", "--soft", "--mixed"],
                    "description": "Reset mode.",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "confirmation_id": {
                    "type": "string",
                    "description": "Confirmation ID from previous input_required response.",
                },
            },
        },
        data_schema={
            "type": "object",
            "properties": {
                "executed": {"type": "boolean"},
                "command": {"type": "string"},
                "output": {"type": "string"},
                "returncode": {"type": "integer"},
                "error": {"type": "string"},
            },
        },
        read_only=False,
        destructive=True,
        idempotent=False,
    ),
    "safe_git_checkout": _tool_entry(
        handler=safe_git_checkout,
        name="safe_git_checkout",
        description="Safe checkout that requires confirmation for --force operations.",
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Branch or commit to checkout.",
                },
                "force": {
                    "type": "boolean",
                    "description": "Whether to force checkout.",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "confirmation_id": {
                    "type": "string",
                    "description": "Confirmation ID from previous input_required response.",
                },
            },
            "required": ["target"],
        },
        data_schema={
            "type": "object",
            "properties": {
                "executed": {"type": "boolean"},
                "command": {"type": "string"},
                "output": {"type": "string"},
                "returncode": {"type": "integer"},
                "error": {"type": "string"},
            },
        },
        read_only=False,
        destructive=True,
        idempotent=False,
    ),
    "stash_and_isolate": _tool_entry(
        handler=stash_and_isolate,
        name="stash_and_isolate",
        description="Automatically stashes uncommitted changes into a tagged checkpoint before risky operations.",
        input_schema={
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "message": {"type": "string", "description": "Custom stash message."},
                "operation": {
                    "type": "string",
                    "description": "Name of operation triggering stash.",
                },
            },
        },
        data_schema={
            "type": "object",
            "properties": {
                "stashed": {"type": "boolean"},
                "message": {"type": "string"},
                "files_stashed": {"type": "integer"},
                "ref": {"type": "string"},
                "restore_command": {"type": "string"},
                "reason": {"type": "string"},
                "error": {"type": "string"},
            },
            "required": ["stashed"],
        },
        read_only=False,
        destructive=True,
        idempotent=False,
    ),
    "detect_stale_branches": _tool_entry(
        handler=detect_stale_branches,
        name="detect_stale_branches",
        description="Scans local and remote branches to identify merged or inactive branches eligible for cleanup.",
        input_schema={
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "days_inactive": {
                    "type": "integer",
                    "description": "Days of inactivity threshold (default: 30).",
                },
                "include_remote": {
                    "type": "boolean",
                    "description": "Include remote tracking branches.",
                },
            },
        },
        data_schema={
            "type": "object",
            "properties": {
                "current_branch": {"type": "string"},
                "merged_branches": {"type": "array"},
                "inactive_local": {"type": "array"},
                "inactive_remote": {"type": "array"},
                "summary": {"type": "string"},
            },
            "required": [
                "current_branch",
                "merged_branches",
                "inactive_local",
                "inactive_remote",
                "summary",
            ],
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    "generate_smart_commit": _tool_entry(
        handler=generate_smart_commit,
        name="generate_smart_commit",
        description="Analyzes staged diffs and generates Conventional Commits message. Optionally executes commit.",
        input_schema={
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "execute": {
                    "type": "boolean",
                    "description": "Actually create commit (default: false).",
                },
            },
        },
        data_schema={
            "type": "object",
            "properties": {
                "suggested_message": {"type": "string"},
                "type": {"type": "string"},
                "scope": {"type": "string"},
                "files": {"type": "array"},
                "stat": {"type": "string"},
                "additions": {"type": "integer"},
                "deletions": {"type": "integer"},
                "committed": {"type": "boolean"},
                "output": {"type": "string"},
                "error": {"type": "string"},
            },
        },
        read_only=False,
        destructive=True,
        idempotent=False,
    ),
    "get_developer_velocity": _tool_entry(
        handler=get_developer_velocity,
        name="get_developer_velocity",
        description="Provides insights into churn (lines added/deleted), top touched files, and commit frequencies.",
        input_schema={
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "description": "Start of time range (e.g. '7.days.ago').",
                },
                "until": {
                    "type": "string",
                    "description": "End of time range (default: now).",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "author": {
                    "type": "string",
                    "description": "Filter by author name/email.",
                },
            },
            "required": ["since"],
        },
        data_schema={
            "type": "object",
            "properties": {
                "range": {"type": "object"},
                "total_commits": {"type": "integer"},
                "total_additions": {"type": "integer"},
                "total_deletions": {"type": "integer"},
                "net_lines": {"type": "integer"},
                "top_files": {"type": "array"},
                "commits_by_day": {"type": "object"},
                "avg_commits_per_day": {"type": "number"},
                "summary": {"type": "string"},
            },
            "required": [
                "range",
                "total_commits",
                "total_additions",
                "total_deletions",
                "net_lines",
                "top_files",
                "commits_by_day",
                "avg_commits_per_day",
                "summary",
            ],
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    "conflict_prelim_check": _tool_entry(
        handler=conflict_prelim_check,
        name="conflict_prelim_check",
        description="Simulates a dry-run merge between branches to warn about merge conflicts early.",
        input_schema={
            "type": "object",
            "properties": {
                "target_branch": {
                    "type": "string",
                    "description": "Branch to merge into (default: main).",
                },
                "source_branch": {
                    "type": "string",
                    "description": "Branch to merge from.",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
            },
        },
        data_schema={
            "type": "object",
            "properties": {
                "source_branch": {"type": "string"},
                "target_branch": {"type": "string"},
                "merge_base": {"type": "string"},
                "has_conflicts": {"type": "boolean"},
                "conflicting_files": {"type": "array"},
                "files_changed": {"type": "array"},
                "ahead": {"type": "integer"},
                "behind": {"type": "integer"},
                "summary": {"type": "string"},
                "error": {"type": "string"},
                "detail": {"type": "string"},
            },
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    "get_temporal_snapshot": _tool_entry(
        handler=get_temporal_snapshot,
        name="get_temporal_snapshot",
        description="Returns a unified context window containing recent commits, reflog movements, dirty deltas, and shell commands.",
        input_schema={
            "type": "object",
            "properties": {
                "time_range": {
                    "type": "string",
                    "description": "Time range for snapshot (e.g. 'last 45m', '1.hour.ago').",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "granularity": {
                    "type": "string",
                    "description": "Level of detail for snapshot.",
                },
            },
            "required": ["time_range"],
        },
        data_schema={
            "type": "object",
            "properties": {
                "range": {"type": "object"},
                "repo_path": {"type": "string"},
                "granularity": {"type": "string"},
                "git_timeline": {"type": "object"},
                "working_dir_delta": {"type": "object"},
                "dev_activity": {"type": "object"},
                "summary": {"type": "string"},
                "_meta": {"type": "object"},
            },
            "required": [
                "range",
                "repo_path",
                "granularity",
                "git_timeline",
                "working_dir_delta",
                "dev_activity",
                "summary",
            ],
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
    "compare_workspace_checkpoints": _tool_entry(
        handler=compare_workspace_checkpoints,
        name="compare_workspace_checkpoints",
        description="Compares two historical checkpoints of the workspace against each other and HEAD.",
        input_schema={
            "type": "object",
            "properties": {
                "ref1": {"type": "string", "description": "First reference point."},
                "ref2": {"type": "string", "description": "Second reference point."},
                "repo_path": {
                    "type": "string",
                    "description": "Path to git repository (default: cwd).",
                },
                "include_diff_content": {
                    "type": "boolean",
                    "description": "Include full diff content.",
                },
            },
            "required": ["ref1", "ref2"],
        },
        data_schema={
            "type": "object",
            "properties": {
                "checkpoint1": {"type": "object"},
                "checkpoint2": {"type": "object"},
                "current_head": {"type": "string"},
                "merge_base": {"type": "string"},
                "diff_from_base_to_ref1": {"type": "string"},
                "diff_from_base_to_ref2": {"type": "string"},
                "diff_from_ref2_to_head": {"type": "string"},
                "summary": {"type": "string"},
                "error": {"type": "string"},
            },
        },
        read_only=True,
        destructive=False,
        idempotent=True,
    ),
}
