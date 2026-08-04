from git_telemetry_mcp.tools.git_timeline import git_timeline
from git_telemetry_mcp.tools.working_dir_delta import working_dir_delta
from git_telemetry_mcp.tools.dev_activity import dev_activity
from git_telemetry_mcp.tools.session_timeline import get_session_timeline
from git_telemetry_mcp.tools.uncommitted_drift import explain_uncommitted_drift
from git_telemetry_mcp.tools.file_evolution import trace_file_evolution
from git_telemetry_mcp.tools.active_context_pack import get_active_context_pack
from git_telemetry_mcp.tools.safe_operations import safe_git_reset, safe_git_checkout
from git_telemetry_mcp.tools.stash_isolate import stash_and_isolate
from git_telemetry_mcp.tools.stale_branches import detect_stale_branches
from git_telemetry_mcp.tools.smart_commit import generate_smart_commit
from git_telemetry_mcp.tools.developer_velocity import get_developer_velocity
from git_telemetry_mcp.tools.conflict_check import conflict_prelim_check

TOOLS_REGISTRY: dict[str, dict] = {
    "git_timeline": {
        "handler": git_timeline,
        "definition": {
            "name": "git_timeline",
            "description": (
                "Analyze git reflog and commit history over a time range. "
                "Returns commits, branch switches, rebases, and resets."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "Start of time range (git date format, e.g. '3.hours.ago')."},
                    "until": {"type": "string", "description": "End of time range (default: now)."},
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                },
                "required": ["since"],
            },
        },
    },
    "working_dir_delta": {
        "handler": working_dir_delta,
        "definition": {
            "name": "working_dir_delta",
            "description": "Summarize the current dirty working directory: staged, unstaged, and untracked changes with diff stats.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                    "include_diff": {"type": "boolean", "description": "Include full diff content (default: false)."},
                },
            },
        },
    },
    "dev_activity": {
        "handler": dev_activity,
        "definition": {
            "name": "dev_activity",
            "description": "Correlate bash/shell history with git activity over a time range to reconstruct developer workflow.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "Start of time range (e.g. '2.hours.ago')."},
                    "until": {"type": "string", "description": "End of time range (default: now)."},
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                    "shell_history_path": {"type": "string", "description": "Path to shell history file (auto-detected if omitted)."},
                },
                "required": ["since"],
            },
        },
    },
    "get_session_timeline": {
        "handler": get_session_timeline,
        "definition": {
            "name": "get_session_timeline",
            "description": (
                "Combines git reflog, recent commits, stashes, and file modification timestamps "
                "to answer: 'What was I working on for the last N hours?'"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "Start of time range (e.g. '3.hours.ago')."},
                    "until": {"type": "string", "description": "End of time range (default: now)."},
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                },
                "required": ["since"],
            },
        },
    },
    "explain_uncommitted_drift": {
        "handler": explain_uncommitted_drift,
        "definition": {
            "name": "explain_uncommitted_drift",
            "description": "Compares current unstaged/staged changes against HEAD and summarizes the architectural direction of current edits.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                },
            },
        },
    },
    "trace_file_evolution": {
        "handler": trace_file_evolution,
        "definition": {
            "name": "trace_file_evolution",
            "description": "Pull diff histories across recent commits, branches, and stashes for a specific file to identify where changes were introduced.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file to trace (relative to repo root)."},
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                    "max_commits": {"type": "integer", "description": "Maximum commits to analyze (default: 10)."},
                },
                "required": ["file_path"],
            },
        },
    },
    "get_active_context_pack": {
        "handler": get_active_context_pack,
        "definition": {
            "name": "get_active_context_pack",
            "description": "Aggregates active branch, recent commits, modified files, and repo state into a single prompt-optimized context blob.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                },
            },
        },
    },
    "safe_git_reset": {
        "handler": safe_git_reset,
        "definition": {
            "name": "safe_git_reset",
            "description": "Intercepts git reset --hard and requires explicit confirmation before executing. Shows what will be lost.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Reset target (default: HEAD)."},
                    "mode": {"type": "string", "enum": ["--hard", "--soft", "--mixed"], "description": "Reset mode (default: --hard)."},
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                    "confirmation_id": {"type": "string", "description": "Confirmation ID from a previous input_required response."},
                },
            },
        },
    },
    "safe_git_checkout": {
        "handler": safe_git_checkout,
        "definition": {
            "name": "safe_git_checkout",
            "description": "Safe checkout that requires confirmation for --force operations. Non-force checkouts execute immediately.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Branch or commit to checkout."},
                    "force": {"type": "boolean", "description": "Whether to force checkout (triggers confirmation)."},
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                    "confirmation_id": {"type": "string", "description": "Confirmation ID from a previous input_required response."},
                },
                "required": ["target"],
            },
        },
    },
    "stash_and_isolate": {
        "handler": stash_and_isolate,
        "definition": {
            "name": "stash_and_isolate",
            "description": "Automatically stashes uncommitted changes into a tagged checkpoint before risky operations.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                    "message": {"type": "string", "description": "Custom stash message (auto-generated if omitted)."},
                    "operation": {"type": "string", "description": "Name of the operation triggering the stash (for labeling)."},
                },
            },
        },
    },
    "detect_stale_branches": {
        "handler": detect_stale_branches,
        "definition": {
            "name": "detect_stale_branches",
            "description": "Scans local and remote branches to identify merged or inactive branches eligible for cleanup.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                    "days_inactive": {"type": "integer", "description": "Days of inactivity threshold (default: 30)."},
                    "include_remote": {"type": "boolean", "description": "Include remote tracking branches (default: true)."},
                },
            },
        },
    },
    "generate_smart_commit": {
        "handler": generate_smart_commit,
        "definition": {
            "name": "generate_smart_commit",
            "description": "Analyzes staged diffs and generates a semantic commit message following Conventional Commits. Optionally executes the commit.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                    "execute": {"type": "boolean", "description": "Actually create the commit (default: false, just suggests)."},
                },
            },
        },
    },
    "get_developer_velocity": {
        "handler": get_developer_velocity,
        "definition": {
            "name": "get_developer_velocity",
            "description": "Provides insights into churn (lines added/deleted), top touched files, and commit frequencies over a time window.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "Start of time range (e.g. '7.days.ago')."},
                    "until": {"type": "string", "description": "End of time range (default: now)."},
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                    "author": {"type": "string", "description": "Filter by author name/email (optional)."},
                },
                "required": ["since"],
            },
        },
    },
    "conflict_prelim_check": {
        "handler": conflict_prelim_check,
        "definition": {
            "name": "conflict_prelim_check",
            "description": "Simulates a dry-run merge between branches to warn about merge conflicts early without modifying the working tree.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_branch": {"type": "string", "description": "Branch to merge into (default: main)."},
                    "source_branch": {"type": "string", "description": "Branch to merge from (default: current branch)."},
                    "repo_path": {"type": "string", "description": "Path to git repository (default: cwd)."},
                },
            },
        },
    },
}
