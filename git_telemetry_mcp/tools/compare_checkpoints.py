import asyncio
import json
import re
from datetime import datetime, timezone

async def _resolve_time_to_commit(repo_path: str, time_ref: str) -> str:
    """Resolves a time reference (e.g., '1.hour.ago', 'SHA', 'branch_name') to a commit SHA."""
    # First, try to interpret as a direct commit SHA or branch name
    cmd = ["git", "-C", repo_path, "rev-parse", "--short", time_ref]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        return stdout.decode().strip()

    # If not a direct ref, try as a time string
    cmd = ["git", "-C", repo_path, "rev-list", "-1", "--before", time_ref, "HEAD"]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0 and stdout.decode().strip():
        return stdout.decode().strip()
    
    raise ValueError(f"Could not resolve time reference '{time_ref}' to a commit.")


async def compare_workspace_checkpoints(arguments: dict) -> str:
    repo_path = arguments.get("repo_path", ".")
    ref1 = arguments["ref1"]
    ref2 = arguments["ref2"]
    include_diff_content = arguments.get("include_diff_content", False)

    try:
        commit1_sha = await _resolve_time_to_commit(repo_path, ref1)
        commit2_sha = await _resolve_time_to_commit(repo_path, ref2)
        head_sha_cmd = ["git", "-C", repo_path, "rev-parse", "--short", "HEAD"]
        head_proc = await asyncio.create_subprocess_exec(
            *head_sha_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        head_sha, _ = await head_proc.communicate()
        head_sha = head_sha.decode().strip()

    except ValueError as e:
        return json.dumps({"error": str(e)})

    # Get a common ancestor for more meaningful diffs
    merge_base_cmd = ["git", "-C", repo_path, "merge-base", commit1_sha, commit2_sha]
    merge_base_proc = await asyncio.create_subprocess_exec(
        *merge_base_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    merge_base_sha, _ = await merge_base_proc.communicate()
    merge_base_sha = merge_base_sha.decode().strip()

    diff_flags = ["--shortstat"] # Default to shortstat
    if include_diff_content:
        diff_flags = [] # If content is requested, remove shortstat
    diff_flags.append("--no-color")

    # Diff: Merge Base -> Commit 1
    diff_mb_c1_cmd = ["git", "-C", repo_path, "diff", merge_base_sha, commit1_sha, *diff_flags]
    # Diff: Merge Base -> Commit 2
    diff_mb_c2_cmd = ["git", "-C", repo_path, "diff", merge_base_sha, commit2_sha, *diff_flags]
    # Diff: Commit 2 -> HEAD (to see changes since ref2)
    diff_c2_head_cmd = ["git", "-C", repo_path, "diff", commit2_sha, head_sha, *diff_flags]
    
    procs = await asyncio.gather(
        asyncio.create_subprocess_exec(
            *diff_mb_c1_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *diff_mb_c2_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *diff_c2_head_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
    )

    diff_mb_c1_out, _ = await procs[0].communicate()
    diff_mb_c2_out, _ = await procs[1].communicate()
    diff_c2_head_out, _ = await procs[2].communicate()

    result = {
        "checkpoint1": {"ref": ref1, "sha": commit1_sha},
        "checkpoint2": {"ref": ref2, "sha": commit2_sha},
        "current_head": head_sha,
        "merge_base": merge_base_sha,
        "diff_from_base_to_ref1": diff_mb_c1_out.decode().strip(),
        "diff_from_base_to_ref2": diff_mb_c2_out.decode().strip(),
        "diff_from_ref2_to_head": diff_c2_head_out.decode().strip(),
        "summary": (
            f"Comparison between {ref1} ({commit1_sha[:8]}) and {ref2} ({commit2_sha[:8]}) "
            f"relative to current HEAD ({head_sha[:8]})."
        ),
    }

    return json.dumps(result, indent=2)
