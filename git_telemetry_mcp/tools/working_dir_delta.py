"""working_dir_delta — dirty working directory summary."""

import asyncio
import json


async def working_dir_delta(arguments: dict) -> str:
    repo_path = arguments.get("repo_path", ".")
    include_diff = arguments.get("include_diff", False)

    status_cmd = ["git", "-C", repo_path, "status", "--porcelain=v2"]
    stat_cmd = ["git", "-C", repo_path, "diff", "--stat"]
    staged_stat_cmd = ["git", "-C", repo_path, "diff", "--cached", "--stat"]

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
    )

    status_out, _ = await procs[0].communicate()
    stat_out, _ = await procs[1].communicate()
    staged_stat_out, _ = await procs[2].communicate()

    staged, unstaged, untracked = [], [], []
    for line in status_out.decode().strip().splitlines():
        if not line:
            continue
        if line.startswith("?"):
            untracked.append(line.split()[-1])
        elif line.startswith("1") or line.startswith("2"):
            xy = line.split()[1] if len(line.split()) > 1 else ".."
            path = line.split()[-1]
            if xy[0] != ".":
                staged.append(path)
            if xy[1] != ".":
                unstaged.append(path)

    result: dict = {
        "staged": {"files": staged, "stat": staged_stat_out.decode().strip()},
        "unstaged": {"files": unstaged, "stat": stat_out.decode().strip()},
        "untracked": untracked,
        "summary": f"{len(staged)} staged, {len(unstaged)} unstaged, {len(untracked)} untracked",
    }

    if include_diff:
        diff_cmd = ["git", "-C", repo_path, "diff"]
        diff_proc = await asyncio.create_subprocess_exec(
            *diff_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        diff_out, _ = await diff_proc.communicate()
        result["diff"] = diff_out.decode()

    return json.dumps(result, indent=2)
