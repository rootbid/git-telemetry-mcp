"""get_active_context_pack — prompt-optimized context blob."""

import asyncio
import json


async def get_active_context_pack(arguments: dict) -> str:
    repo_path = arguments.get("repo_path", ".")

    branch_cmd = ["git", "-C", repo_path, "branch", "--show-current"]
    log_cmd = ["git", "-C", repo_path, "log", "-5", "--format=%h %s (%cr)"]
    status_cmd = ["git", "-C", repo_path, "status", "--porcelain=v2"]
    remote_cmd = ["git", "-C", repo_path, "remote", "-v"]
    upstream_cmd = ["git", "-C", repo_path, "log", "@{u}..HEAD", "--oneline"]

    procs = await asyncio.gather(
        asyncio.create_subprocess_exec(
            *branch_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *log_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *status_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *remote_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *upstream_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
    )

    branch_out, _ = await procs[0].communicate()
    log_out, _ = await procs[1].communicate()
    status_out, _ = await procs[2].communicate()
    remote_out, _ = await procs[3].communicate()
    upstream_out, _ = await procs[4].communicate()

    # Parse modified files from status
    modified_files = []
    for line in status_out.decode().strip().splitlines():
        if line and not line.startswith("#"):
            parts = line.split()
            modified_files.append(parts[-1])

    # Detect if there's a merge in progress
    merge_head_cmd = ["git", "-C", repo_path, "rev-parse", "--verify", "MERGE_HEAD"]
    merge_proc = await asyncio.create_subprocess_exec(
        *merge_head_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    merge_out, merge_err = await merge_proc.communicate()
    in_merge = merge_proc.returncode == 0

    # Check for rebase in progress
    rebase_cmd = ["git", "-C", repo_path, "rev-parse", "--git-path", "rebase-merge"]
    rebase_proc = await asyncio.create_subprocess_exec(
        *rebase_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    rebase_out, _ = await rebase_proc.communicate()

    import os
    rebase_path = rebase_out.decode().strip()
    in_rebase = os.path.isdir(rebase_path) if rebase_path else False

    branch = branch_out.decode().strip()
    result = {
        "branch": branch,
        "recent_commits": log_out.decode().strip().splitlines(),
        "modified_files": modified_files[:20],
        "unpushed_commits": upstream_out.decode().strip().splitlines(),
        "remotes": remote_out.decode().strip().splitlines(),
        "state": {
            "in_merge": in_merge,
            "in_rebase": in_rebase,
        },
        "summary": (
            f"On '{branch}' with {len(modified_files)} modified files, "
            f"{len(upstream_out.decode().strip().splitlines())} unpushed commits"
        ),
    }
    return json.dumps(result, indent=2)
