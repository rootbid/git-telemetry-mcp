"""git_timeline — reflog + commit analysis over a time range."""

import asyncio
import json


async def git_timeline(arguments: dict) -> str:
    since = arguments["since"]
    until = arguments.get("until", "now")
    repo_path = arguments.get("repo_path", ".")

    reflog_cmd = [
        "git", "-C", repo_path, "reflog", "--format=%H|%gd|%gs|%ci",
        f"--since={since}", f"--until={until}",
    ]
    log_cmd = [
        "git", "-C", repo_path, "log", "--all",
        "--format=%H|%an|%s|%ci",
        f"--since={since}", f"--until={until}",
    ]

    reflog_proc, log_proc = await asyncio.gather(
        asyncio.create_subprocess_exec(
            *reflog_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *log_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
    )

    reflog_out, reflog_err = await reflog_proc.communicate()
    log_out, log_err = await log_proc.communicate()

    reflog_entries = []
    for line in reflog_out.decode().strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) == 4:
            reflog_entries.append({
                "sha": parts[0][:8],
                "selector": parts[1],
                "action": parts[2],
                "date": parts[3],
            })

    commits = []
    for line in log_out.decode().strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({
                "sha": parts[0][:8],
                "author": parts[1],
                "message": parts[2],
                "date": parts[3],
            })

    result = {
        "range": {"since": since, "until": until},
        "reflog": reflog_entries,
        "commits": commits,
        "summary": f"{len(reflog_entries)} reflog entries, {len(commits)} commits",
    }
    return json.dumps(result, indent=2)
