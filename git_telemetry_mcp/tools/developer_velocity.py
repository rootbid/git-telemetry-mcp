"""get_developer_velocity — churn, top files, commit frequency."""

import asyncio
import json
from git_telemetry_mcp.schema import serialize_telemetry_payload



async def get_developer_velocity(arguments: dict) -> str:
    since = arguments["since"]
    until = arguments.get("until", "now")
    repo_path = arguments.get("repo_path", ".")
    author = arguments.get("author")

    # Commit count and frequency
    log_args = [
        "git", "-C", repo_path, "log",
        f"--since={since}", f"--until={until}",
        "--format=%H|%ci|%s",
    ]
    if author:
        log_args.extend(["--author", author])

    # Shortstat per commit for churn
    numstat_args = [
        "git", "-C", repo_path, "log",
        f"--since={since}", f"--until={until}",
        "--numstat", "--format=COMMIT|%H",
    ]
    if author:
        numstat_args.extend(["--author", author])

    log_proc, numstat_proc = await asyncio.gather(
        asyncio.create_subprocess_exec(
            *log_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *numstat_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
    )

    log_out, _ = await log_proc.communicate()
    numstat_out, _ = await numstat_proc.communicate()

    # Parse commits
    commits = []
    for line in log_out.decode().strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0][:8], "date": parts[1], "message": parts[2]})

    # Parse numstat for file-level churn
    file_churn: dict[str, dict] = {}
    total_additions = 0
    total_deletions = 0

    for line in numstat_out.decode().splitlines():
        if line.startswith("COMMIT|") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) == 3:
            try:
                adds = int(parts[0]) if parts[0] != "-" else 0
                dels = int(parts[1]) if parts[1] != "-" else 0
                fname = parts[2]
                total_additions += adds
                total_deletions += dels
                if fname not in file_churn:
                    file_churn[fname] = {"additions": 0, "deletions": 0, "touches": 0}
                file_churn[fname]["additions"] += adds
                file_churn[fname]["deletions"] += dels
                file_churn[fname]["touches"] += 1
            except ValueError:
                pass

    # Top files by total churn
    top_files = sorted(
        file_churn.items(),
        key=lambda x: x[1]["additions"] + x[1]["deletions"],
        reverse=True,
    )[:10]

    # Commit frequency by day
    day_counts: dict[str, int] = {}
    for c in commits:
        day = c["date"].split(" ")[0] if " " in c["date"] else c["date"][:10]
        day_counts[day] = day_counts.get(day, 0) + 1

    result = {
        "range": {"since": since, "until": until},
        "total_commits": len(commits),
        "total_additions": total_additions,
        "total_deletions": total_deletions,
        "net_lines": total_additions - total_deletions,
        "top_files": [{"file": f, **stats} for f, stats in top_files],
        "commits_by_day": day_counts,
        "avg_commits_per_day": round(len(commits) / max(len(day_counts), 1), 1),
        "summary": (
            f"{len(commits)} commits, +{total_additions}/-{total_deletions} lines, "
            f"top file: {top_files[0][0] if top_files else 'none'}"
        ),
    }
    return serialize_telemetry_payload(result, repo_path=repo_path)
