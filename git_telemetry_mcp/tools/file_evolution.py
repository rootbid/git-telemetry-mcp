"""trace_file_evolution — line-by-line diff history for a file."""

import asyncio
import json
from git_telemetry_mcp.schema import serialize_telemetry_payload



async def trace_file_evolution(arguments: dict) -> str:
    file_path = arguments["file_path"]
    repo_path = arguments.get("repo_path", ".")
    max_commits = arguments.get("max_commits", 10)

    # Get recent commits touching this file
    log_cmd = [
        "git", "-C", repo_path, "log",
        f"-{max_commits}", "--format=%H|%an|%s|%ci", "--follow",
        "--", file_path,
    ]
    log_proc = await asyncio.create_subprocess_exec(
        *log_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    log_out, _ = await log_proc.communicate()

    commits = []
    for line in log_out.decode().strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({
                "sha": parts[0],
                "author": parts[1],
                "message": parts[2],
                "date": parts[3],
            })

    # Get diffs for each commit pair
    evolutions = []
    for i, commit in enumerate(commits):
        diff_cmd = [
            "git", "-C", repo_path, "show",
            "--format=", "--stat", "--no-color", commit["sha"],
            "--", file_path,
        ]
        diff_proc = await asyncio.create_subprocess_exec(
            *diff_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        diff_out, _ = await diff_proc.communicate()

        evolutions.append({
            **commit,
            "sha": commit["sha"][:8],
            "diff_stat": diff_out.decode().strip(),
        })

    # Check if file exists in stashes
    stash_hits = []
    stash_list_cmd = ["git", "-C", repo_path, "stash", "list", "--format=%gd|%s"]
    stash_proc = await asyncio.create_subprocess_exec(
        *stash_list_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stash_out, _ = await stash_proc.communicate()

    for line in stash_out.decode().strip().splitlines()[:5]:
        if not line:
            continue
        parts = line.split("|", 1)
        if len(parts) == 2:
            check_cmd = [
                "git", "-C", repo_path, "stash", "show", parts[0], "--", file_path,
            ]
            check_proc = await asyncio.create_subprocess_exec(
                *check_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            check_out, _ = await check_proc.communicate()
            if check_out.decode().strip():
                stash_hits.append({"ref": parts[0], "message": parts[1]})

    # Blame summary (top contributors to current state)
    blame_cmd = [
        "git", "-C", repo_path, "blame", "--line-porcelain", file_path,
    ]
    blame_proc = await asyncio.create_subprocess_exec(
        *blame_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    blame_out, _ = await blame_proc.communicate()

    author_lines: dict[str, int] = {}
    for line in blame_out.decode().splitlines():
        if line.startswith("author "):
            author = line[7:]
            author_lines[author] = author_lines.get(author, 0) + 1

    result = {
        "file": file_path,
        "commit_history": evolutions,
        "stash_appearances": stash_hits,
        "blame_summary": dict(sorted(author_lines.items(), key=lambda x: -x[1])[:5]),
        "summary": f"{len(evolutions)} commits, {len(stash_hits)} stash appearances",
    }
    return serialize_telemetry_payload(result, repo_path=repo_path)
