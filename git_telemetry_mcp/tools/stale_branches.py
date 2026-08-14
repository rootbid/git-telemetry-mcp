"""detect_stale_branches — find merged or inactive branches."""

import asyncio

from git_telemetry_mcp.schema import serialize_telemetry_payload


async def detect_stale_branches(arguments: dict) -> str:
    repo_path = arguments.get("repo_path", ".")
    days_inactive = arguments.get("days_inactive", 30)
    include_remote = arguments.get("include_remote", True)

    # Merged branches
    merged_cmd = [
        "git",
        "-C",
        repo_path,
        "branch",
        "--merged",
        "HEAD",
        "--format=%(refname:short)|%(committerdate:iso)",
    ]
    merged_proc = await asyncio.create_subprocess_exec(
        *merged_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    merged_out, _ = await merged_proc.communicate()

    # All branches with last commit date
    all_cmd = [
        "git",
        "-C",
        repo_path,
        "for-each-ref",
        "--sort=-committerdate",
        "--format=%(refname:short)|%(committerdate:relative)|%(committerdate:unix)|%(upstream:track)",
        "refs/heads/",
    ]
    all_proc = await asyncio.create_subprocess_exec(
        *all_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    all_out, _ = await all_proc.communicate()

    # Remote branches if requested
    remote_stale = []
    if include_remote:
        remote_cmd = [
            "git",
            "-C",
            repo_path,
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)|%(committerdate:relative)|%(committerdate:unix)",
            "refs/remotes/",
        ]
        remote_proc = await asyncio.create_subprocess_exec(
            *remote_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        remote_out, _ = await remote_proc.communicate()

        import time

        cutoff = time.time() - (days_inactive * 86400)
        for line in remote_out.decode().strip().splitlines():
            if not line or "HEAD" in line:
                continue
            parts = line.split("|", 2)
            if len(parts) >= 3:
                try:
                    ts = int(parts[2])
                    if ts < cutoff:
                        remote_stale.append(
                            {
                                "branch": parts[0],
                                "last_activity": parts[1],
                            }
                        )
                except ValueError:
                    pass

    # Parse merged
    merged_branches = []
    current_cmd = ["git", "-C", repo_path, "branch", "--show-current"]
    current_proc = await asyncio.create_subprocess_exec(
        *current_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    current_out, _ = await current_proc.communicate()
    current_branch = current_out.decode().strip()

    for line in merged_out.decode().strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 1)
        name = parts[0].strip()
        if name in (current_branch, "main", "master", "develop"):
            continue
        merged_branches.append(
            {
                "branch": name,
                "last_commit_date": parts[1] if len(parts) > 1 else "unknown",
            }
        )

    # Inactive local branches
    import time

    cutoff = time.time() - (days_inactive * 86400)
    inactive = []
    for line in all_out.decode().strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) >= 3:
            name = parts[0].strip()
            if name in (current_branch, "main", "master", "develop"):
                continue
            try:
                ts = int(parts[2])
                if ts < cutoff:
                    inactive.append(
                        {
                            "branch": name,
                            "last_activity": parts[1],
                            "tracking": parts[3] if len(parts) > 3 else "",
                        }
                    )
            except ValueError:
                pass

    result = {
        "current_branch": current_branch,
        "merged_branches": merged_branches,
        "inactive_local": inactive,
        "inactive_remote": remote_stale[:20],
        "summary": (
            f"{len(merged_branches)} merged, {len(inactive)} inactive local, "
            f"{len(remote_stale)} inactive remote (>{days_inactive} days)"
        ),
    }
    return serialize_telemetry_payload(result, repo_path=repo_path)
