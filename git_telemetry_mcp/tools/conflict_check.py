"""conflict_prelim_check — dry-run merge to detect conflicts early."""

import asyncio

from git_telemetry_mcp.schema import serialize_telemetry_payload


async def conflict_prelim_check(arguments: dict) -> str:
    repo_path = arguments.get("repo_path", ".")
    target_branch = arguments.get("target_branch", "main")
    source_branch = arguments.get("source_branch")

    # Get current branch if source not specified
    if not source_branch:
        branch_cmd = ["git", "-C", repo_path, "branch", "--show-current"]
        proc = await asyncio.create_subprocess_exec(
            *branch_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, _ = await proc.communicate()
        source_branch = out.decode().strip()

    # Find merge base
    merge_base_cmd = [
        "git",
        "-C",
        repo_path,
        "merge-base",
        source_branch,
        target_branch,
    ]
    proc = await asyncio.create_subprocess_exec(
        *merge_base_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    base_out, base_err = await proc.communicate()

    if proc.returncode != 0:
        return serialize_telemetry_payload(
            {
                "error": f"Cannot find merge base between {source_branch} and {target_branch}",
                "detail": base_err.decode().strip(),
            },
            repo_path=repo_path,
            confidence_score=0.0,
        )

    merge_base = base_out.decode().strip()

    # Three-tree merge-tree emits conflict stages without touching the index
    # or writing a result tree.  Do not use --write-tree in this read-only tool.
    merge_tree_cmd = [
        "git",
        "-C",
        repo_path,
        "merge-tree",
        merge_base,
        source_branch,
        target_branch,
    ]
    proc = await asyncio.create_subprocess_exec(
        *merge_tree_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    tree_out, _tree_err = await proc.communicate()
    tree_text = tree_out.decode()
    conflict_markers = (
        "changed in both",
        "added in both",
        "removed in both",
        "deleted in both",
        "unmerged",
        "conflict",
    )
    has_conflicts = proc.returncode != 0 or any(
        marker in tree_text.lower() for marker in conflict_markers
    )

    # Get files that differ between branches
    diff_files_cmd = [
        "git",
        "-C",
        repo_path,
        "diff",
        "--name-only",
        f"{target_branch}...{source_branch}",
    ]
    diff_proc = await asyncio.create_subprocess_exec(
        *diff_files_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    diff_out, _ = await diff_proc.communicate()
    changed_files = diff_out.decode().strip().splitlines()

    # Parse conflict info from merge-tree output without exposing duplicate paths.
    conflicting_files = []
    if has_conflicts:
        for line in tree_text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("conflict") and " in " in stripped.lower():
                conflicting_files.append(stripped.rsplit(" in ", 1)[-1])
            elif stripped.startswith(("base ", "our ", "their ")):
                fields = stripped.split()
                if len(fields) >= 4:
                    conflicting_files.append(" ".join(fields[3:]))
        conflicting_files = list(dict.fromkeys(conflicting_files))

    # Commits ahead/behind
    ahead_cmd = [
        "git",
        "-C",
        repo_path,
        "rev-list",
        "--count",
        f"{target_branch}..{source_branch}",
    ]
    behind_cmd = [
        "git",
        "-C",
        repo_path,
        "rev-list",
        "--count",
        f"{source_branch}..{target_branch}",
    ]

    ahead_proc, behind_proc = await asyncio.gather(
        asyncio.create_subprocess_exec(
            *ahead_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *behind_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
    )
    ahead_out, _ = await ahead_proc.communicate()
    behind_out, _ = await behind_proc.communicate()

    try:
        ahead = int(ahead_out.decode().strip())
        behind = int(behind_out.decode().strip())
    except ValueError:
        ahead, behind = 0, 0

    result = {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "merge_base": merge_base[:8],
        "has_conflicts": has_conflicts,
        "conflicting_files": conflicting_files,
        "files_changed": changed_files[:30],
        "ahead": ahead,
        "behind": behind,
        "summary": (
            f"{'CONFLICTS DETECTED' if has_conflicts else 'Clean merge possible'}: "
            f"{source_branch} -> {target_branch} "
            f"({ahead} ahead, {behind} behind, {len(changed_files)} files changed)"
        ),
    }
    return serialize_telemetry_payload(result, repo_path=repo_path)
