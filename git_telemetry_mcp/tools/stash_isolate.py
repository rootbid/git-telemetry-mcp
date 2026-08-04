"""stash_and_isolate — stash uncommitted changes before risky operations."""

import asyncio
import json
from datetime import datetime, timezone


async def stash_and_isolate(arguments: dict) -> str:
    repo_path = arguments.get("repo_path", ".")
    message = arguments.get("message")
    operation = arguments.get("operation", "manual")

    # Check if there are changes to stash
    status_cmd = ["git", "-C", repo_path, "status", "--porcelain"]
    proc = await asyncio.create_subprocess_exec(
        *status_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    status_out, _ = await proc.communicate()
    dirty_files = status_out.decode().strip().splitlines()

    if not dirty_files:
        return json.dumps({
            "stashed": False,
            "reason": "Working directory is clean, nothing to stash",
        })

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    stash_msg = message or f"auto-isolate/{operation}/{ts}"

    # Stash including untracked files
    stash_cmd = [
        "git", "-C", repo_path, "stash", "push",
        "--include-untracked", "-m", stash_msg,
    ]
    proc = await asyncio.create_subprocess_exec(
        *stash_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()

    if proc.returncode != 0:
        return json.dumps({
            "stashed": False,
            "error": err.decode().strip(),
        })

    # Get the stash ref
    ref_cmd = ["git", "-C", repo_path, "stash", "list", "-1", "--format=%H %gd"]
    ref_proc = await asyncio.create_subprocess_exec(
        *ref_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    ref_out, _ = await ref_proc.communicate()

    return json.dumps({
        "stashed": True,
        "message": stash_msg,
        "files_stashed": len(dirty_files),
        "ref": ref_out.decode().strip(),
        "restore_command": f"git stash pop",
    })
