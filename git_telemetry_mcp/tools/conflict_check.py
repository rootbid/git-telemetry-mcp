"""conflict_prelim_check — dry-run merge to detect conflicts early."""

import asyncio
import json


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
    merge_base_cmd = ["git", "-C", repo_path, "merge-base", source_branch, target_branch]
    proc = await asyncio.create_subprocess_exec(
        *merge_base_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    base_out, base_err = await proc.communicate()

    if proc.returncode != 0:
        return json.dumps({
            "error": f"Cannot find merge base between {source_branch} and {target_branch}",
            "detail": base_err.decode().strip(),
        })

    merge_base = base_out.decode().strip()

    # Try merge without committing (dry-run via merge-tree)
    merge_tree_cmd = [
        "git", "-C", repo_path, "merge-tree", "--write-tree",
        "--no-messages", merge_base, source_branch, target_branch,
    ]
    proc = await asyncio.create_subprocess_exec(
        *merge_tree_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    tree_out, tree_err = await proc.communicate()
    has_conflicts = proc.returncode != 0

    # Get files that differ between branches
    diff_files_cmd = [
        "git", "-C", repo_path, "diff", "--name-only",
        f"{target_branch}...{source_branch}",
    ]
    diff_proc = await asyncio.create_subprocess_exec(
        *diff_files_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    diff_out, _ = await diff_proc.communicate()
    changed_files = diff_out.decode().strip().splitlines()

    # Parse conflict info from merge-tree output
    conflicting_files = []
    if has_conflicts:
        for line in tree_out.decode().splitlines():
            if line and not line.startswith(" ") and "/" in line:
                conflicting_files.append(line.strip())

    # Commits ahead/behind
    ahead_cmd = ["git", "-C", repo_path, "rev-list", "--count", f"{target_branch}..{source_branch}"]
    behind_cmd = ["git", "-C", repo_path, "rev-list", "--count", f"{source_branch}..{target_branch}"]

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
    return json.dumps(result, indent=2)
