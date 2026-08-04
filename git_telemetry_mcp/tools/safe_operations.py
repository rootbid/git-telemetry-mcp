"""safe_git_reset / safe_git_checkout — destructive commands with confirmation."""

import asyncio
import json
import uuid

# In-memory pending confirmations
_pending_confirmations: dict[str, dict] = {}


async def safe_git_reset(arguments: dict) -> str | dict:
    repo_path = arguments.get("repo_path", ".")
    target = arguments.get("target", "HEAD")
    mode = arguments.get("mode", "--hard")
    confirmation_id = arguments.get("confirmation_id")

    if confirmation_id:
        return await _execute_confirmed(confirmation_id)

    # Preview what will be lost
    preview_cmd = ["git", "-C", repo_path, "diff", "--stat", target]
    proc = await asyncio.create_subprocess_exec(
        *preview_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    preview_out, _ = await proc.communicate()

    # Count commits that will be discarded
    log_cmd = ["git", "-C", repo_path, "log", "--oneline", f"{target}..HEAD"]
    log_proc = await asyncio.create_subprocess_exec(
        *log_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    log_out, _ = await log_proc.communicate()
    commits_to_lose = log_out.decode().strip().splitlines()

    conf_id = str(uuid.uuid4())[:8]
    _pending_confirmations[conf_id] = {
        "command": ["git", "-C", repo_path, "reset", mode, target],
        "description": f"git reset {mode} {target}",
    }

    warning = (
        f"⚠️ DESTRUCTIVE: git reset {mode} {target}\n"
        f"Will discard {len(commits_to_lose)} commits\n"
        f"Changes that will be lost:\n{preview_out.decode().strip()}"
    )

    return {
        "resultType": "input_required",
        "content": [{"type": "text", "text": warning}],
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirmation_id": {"type": "string", "const": conf_id},
                "confirm": {"type": "boolean", "description": "Set to true to proceed"},
            },
            "required": ["confirmation_id", "confirm"],
        },
    }


async def safe_git_checkout(arguments: dict) -> str | dict:
    repo_path = arguments.get("repo_path", ".")
    target = arguments["target"]
    force = arguments.get("force", False)
    confirmation_id = arguments.get("confirmation_id")

    if confirmation_id:
        return await _execute_confirmed(confirmation_id)

    if not force:
        # Non-destructive checkout, just do it
        cmd = ["git", "-C", repo_path, "checkout", target]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        result = out.decode() + err.decode()
        return json.dumps({"executed": True, "output": result.strip()})

    # Force checkout — preview what will be lost
    status_cmd = ["git", "-C", repo_path, "status", "--porcelain"]
    proc = await asyncio.create_subprocess_exec(
        *status_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    status_out, _ = await proc.communicate()
    dirty_files = status_out.decode().strip().splitlines()

    conf_id = str(uuid.uuid4())[:8]
    _pending_confirmations[conf_id] = {
        "command": ["git", "-C", repo_path, "checkout", "--force", target],
        "description": f"git checkout --force {target}",
    }

    warning = (
        f"⚠️ DESTRUCTIVE: git checkout --force {target}\n"
        f"Will discard changes in {len(dirty_files)} files:\n"
        + "\n".join(dirty_files[:20])
    )

    return {
        "resultType": "input_required",
        "content": [{"type": "text", "text": warning}],
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirmation_id": {"type": "string", "const": conf_id},
                "confirm": {"type": "boolean", "description": "Set to true to proceed"},
            },
            "required": ["confirmation_id", "confirm"],
        },
    }


async def _execute_confirmed(confirmation_id: str) -> str:
    pending = _pending_confirmations.pop(confirmation_id, None)
    if not pending:
        return json.dumps({"error": "Confirmation expired or invalid", "executed": False})

    proc = await asyncio.create_subprocess_exec(
        *pending["command"],
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()

    return json.dumps({
        "executed": True,
        "command": pending["description"],
        "output": (out.decode() + err.decode()).strip(),
        "returncode": proc.returncode,
    })
