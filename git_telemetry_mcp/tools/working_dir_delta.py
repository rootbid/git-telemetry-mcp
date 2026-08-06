"""working_dir_delta — dirty working directory summary."""

import asyncio
import json
import re
from git_telemetry_mcp.schema import serialize_telemetry_payload



def _calculate_change_entropy(diff_text: str) -> float:
    lines = diff_text.splitlines()
    # Simple entropy: ratio of changed lines to total lines, weighted by hunks
    total_lines = len(lines)
    if total_lines == 0:  # Avoid division by zero
        return 0.0

    changed_lines = 0
    hunk_starts = 0
    for line in lines:
        if line.startswith(('+', '-')) and not line.startswith(('+++', '---')):
            changed_lines += 1
        if line.startswith('@@ -'):
            hunk_starts += 1

    # Normalize by number of hunks to give a sense of spread vs concentrated changes
    hunk_factor = hunk_starts if hunk_starts > 0 else 1
    entropy = (changed_lines / total_lines) * (hunk_starts / hunk_factor) # Simplified
    return round(entropy, 4)


async def working_dir_delta(arguments: dict) -> str:
    repo_path = arguments.get("repo_path", ".")
    include_diff = arguments.get("include_diff", False)
    exclude_patterns = arguments.get("exclude_patterns", [])  # New argument

    exclude_args = []
    if exclude_patterns:
        # Git's pathspec needs to be after the command, separated by --
        # and each pattern needs to be prefixed with ':(exclude)'
        for pattern in exclude_patterns:
            exclude_args.extend(["--", f":(exclude){pattern}"])

    status_cmd = ["git", "-C", repo_path, "status", "--porcelain=v2"]
    stat_cmd = ["git", "-C", repo_path, "diff", "--stat"]
    staged_stat_cmd = ["git", "-C", repo_path, "diff", "--cached", "--stat"]
    diff_cmd = ["git", "-C", repo_path, "diff", "--no-color", *exclude_args]  # Modified
    staged_diff_cmd = ["git", "-C", repo_path, "diff", "--cached", "--no-color", *exclude_args]  # Modified

    procs = await asyncio.gather(
        asyncio.create_subprocess_exec(
            *status_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *stat_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *staged_stat_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *diff_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *staged_diff_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
    )

    status_out, _ = await procs[0].communicate()
    stat_out, _ = await procs[1].communicate()
    staged_stat_out, _ = await procs[2].communicate()
    diff_out, _ = await procs[3].communicate()
    staged_diff_out, _ = await procs[4].communicate()

    staged, unstaged, untracked = [], [], []
    for line in status_out.decode().strip().splitlines():
        if not line:
            continue
        if line.startswith("?"):
            # Untracked files, check for preliminary syntax detection (simple file extension)
            file_path = line.split()[-1]
            file_type = file_path.split('.')[-1] if '.' in file_path else 'unknown'
            untracked.append({"file": file_path, "type": file_type})
        elif line.startswith("1") or line.startswith("2"):
            xy = line.split()[1] if len(line.split()) > 1 else ".."
            path = line.split()[-1]
            if xy[0] != ".":
                staged.append(path)
            if xy[1] != ".":
                unstaged.append(path)

    total_diff_text = diff_out.decode() + staged_diff_out.decode()
    change_entropy = _calculate_change_entropy(total_diff_text)

    result: dict = {
        "staged": {"files": staged, "stat": staged_stat_out.decode().strip()},
        "unstaged": {"files": unstaged, "stat": stat_out.decode().strip()},
        "untracked": untracked,
        "summary": f"{len(staged)} staged, {len(unstaged)} unstaged, {len(untracked)} untracked",
        "change_entropy": change_entropy,
    }

    if include_diff:
        result["hunk_diffs"] = {
            "unstaged": diff_out.decode(),
            "staged": staged_diff_out.decode(),
        }

    return serialize_telemetry_payload(result, repo_path=repo_path)
