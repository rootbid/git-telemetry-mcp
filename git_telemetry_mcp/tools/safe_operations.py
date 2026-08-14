"""safe_git_reset / safe_git_checkout — destructive commands with confirmation."""

import asyncio
import os
import secrets
import time
from typing import Any

from git_telemetry_mcp.privacy import scrub_data
from git_telemetry_mcp.schema import serialize_telemetry_payload

# Confirmation tokens are one-shot, short-lived, and bounded to avoid unbounded
# memory growth if a caller repeatedly requests previews without confirming.
_CONFIRMATION_TTL_SECONDS = 300.0
_MAX_PENDING_CONFIRMATIONS = 128
_pending_confirmations: dict[str, dict[str, Any]] = {}
_RESET_MODES = frozenset({"--hard", "--soft", "--mixed"})


def _prune_confirmations(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    expired = [
        token
        for token, pending in _pending_confirmations.items()
        if pending.get("expires_at", 0.0) <= now
    ]
    for token in expired:
        _pending_confirmations.pop(token, None)

    while len(_pending_confirmations) > _MAX_PENDING_CONFIRMATIONS:
        oldest_token = min(
            _pending_confirmations,
            key=lambda token: _pending_confirmations[token].get("created_at", 0.0),
        )
        _pending_confirmations.pop(oldest_token, None)


def _new_confirmation(
    *,
    operation: str,
    repo_path: str,
    target: str,
    state: dict[str, Any],
    command: list[str],
    description: str,
) -> str:
    now = time.monotonic()
    _prune_confirmations(now)
    while len(_pending_confirmations) >= _MAX_PENDING_CONFIRMATIONS:
        oldest_token = min(
            _pending_confirmations,
            key=lambda token: _pending_confirmations[token].get("created_at", 0.0),
        )
        _pending_confirmations.pop(oldest_token, None)

    token = secrets.token_urlsafe(32)
    while token in _pending_confirmations:
        token = secrets.token_urlsafe(32)
    _pending_confirmations[token] = {
        "operation": operation,
        "repo_path": _canonical_repo_path(repo_path),
        "target": target,
        "state": dict(state),
        "command": command,
        "description": description,
        "created_at": now,
        "expires_at": now + _CONFIRMATION_TTL_SECONDS,
    }
    return token


def _canonical_repo_path(repo_path: str) -> str:
    return os.path.realpath(os.path.abspath(os.fspath(repo_path)))


def _error(message: str, repo_path: str | None = None) -> str:
    return serialize_telemetry_payload(
        {"error": message, "executed": False},
        repo_path=repo_path,
        confidence_score=0.0,
    )


def _input_required(warning: str, token: str) -> dict[str, Any]:
    # input_required responses bypass serialize_telemetry_payload in the MCP
    # dispatcher, so scrub both the warning and nested values here explicitly.
    response = {
        "resultType": "input_required",
        "content": [{"type": "text", "text": warning}],
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirmation_id": {"type": "string", "const": token},
                "confirm": {"type": "boolean", "description": "Set to true to proceed"},
            },
            "required": ["confirmation_id", "confirm"],
        },
    }
    return scrub_data(response)


def _confirmation_argument(arguments: dict, key: str) -> tuple[bool, Any]:
    """Return whether a confirmation argument was caller-supplied.

    The registry records whether repo_path was present before it inserts its
    validated default. Direct callers simply use key membership.
    """
    if key == "repo_path" and "_repo_path_provided" in arguments:
        return bool(arguments["_repo_path_provided"]), arguments.get(key)
    return key in arguments, arguments.get(key)


async def safe_git_reset(arguments: dict) -> str | dict:
    repo_path = arguments.get("repo_path", ".")
    target = arguments.get("target", "HEAD")
    mode = arguments.get("mode", "--hard")
    has_confirmation_id, confirmation_id = _confirmation_argument(
        arguments, "confirmation_id"
    )

    if not isinstance(repo_path, (str, os.PathLike)):
        return _error("repo_path must be a string path")
    if not isinstance(target, str) or not target:
        return _error(
            "target must be a non-empty string", repo_path=os.fspath(repo_path)
        )
    if not isinstance(mode, str) or mode not in _RESET_MODES:
        return _error(
            "mode must be one of --hard, --soft, or --mixed",
            repo_path=os.fspath(repo_path),
        )

    if has_confirmation_id:
        if not isinstance(confirmation_id, str) or not confirmation_id:
            return _error(
                "confirmation_id must be a non-empty string",
                repo_path=os.fspath(repo_path),
            )
        return await _execute_confirmed(confirmation_id, "safe_git_reset", arguments)

    # Preview what will be lost.
    repo_string = os.fspath(repo_path)
    preview_cmd = ["git", "-C", repo_string, "diff", "--stat", target]
    proc = await asyncio.create_subprocess_exec(
        *preview_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    preview_out, _ = await proc.communicate()

    # Count commits that will be discarded.
    log_cmd = ["git", "-C", repo_string, "log", "--oneline", f"{target}..HEAD"]
    log_proc = await asyncio.create_subprocess_exec(
        *log_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    log_out, _ = await log_proc.communicate()
    commits_to_lose = log_out.decode().strip().splitlines()

    conf_id = _new_confirmation(
        operation="safe_git_reset",
        repo_path=repo_string,
        target=target,
        state={"mode": mode},
        command=["git", "-C", repo_string, "reset", mode, target],
        description=f"git reset {mode} {target}",
    )

    warning = (
        f"DESTRUCTIVE: git reset {mode} {target}\n"
        f"Will discard {len(commits_to_lose)} commits\n"
        f"Changes that will be lost:\n{preview_out.decode().strip()}"
    )
    return _input_required(warning, conf_id)


async def safe_git_checkout(arguments: dict) -> str | dict:
    repo_path = arguments.get("repo_path", ".")
    target = arguments.get("target")
    force = arguments.get("force", False)
    has_confirmation_id, confirmation_id = _confirmation_argument(
        arguments, "confirmation_id"
    )

    if not isinstance(repo_path, (str, os.PathLike)):
        return _error("repo_path must be a string path")
    repo_string = os.fspath(repo_path)
    if not isinstance(target, str) or not target:
        return _error("target must be a non-empty string", repo_path=repo_string)
    if not isinstance(force, bool):
        return _error("force must be a boolean", repo_path=repo_string)

    if has_confirmation_id:
        if not isinstance(confirmation_id, str) or not confirmation_id:
            return _error(
                "confirmation_id must be a non-empty string", repo_path=repo_string
            )
        return await _execute_confirmed(confirmation_id, "safe_git_checkout", arguments)

    if not force:
        # Non-destructive checkout, just do it.
        cmd = ["git", "-C", repo_string, "checkout", target]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        result = out.decode() + err.decode()
        return serialize_telemetry_payload(
            {
                "executed": proc.returncode == 0,
                "output": result.strip(),
                "returncode": proc.returncode,
            },
            repo_path=repo_string,
        )

    # Force checkout — preview what will be lost.
    status_cmd = ["git", "-C", repo_string, "status", "--porcelain"]
    proc = await asyncio.create_subprocess_exec(
        *status_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    status_out, _ = await proc.communicate()
    dirty_files = status_out.decode().strip().splitlines()

    conf_id = _new_confirmation(
        operation="safe_git_checkout",
        repo_path=repo_string,
        target=target,
        state={"force": True},
        command=["git", "-C", repo_string, "checkout", "--force", target],
        description=f"git checkout --force {target}",
    )

    warning = (
        f"DESTRUCTIVE: git checkout --force {target}\n"
        f"Will discard changes in {len(dirty_files)} files:\n"
        + "\n".join(dirty_files[:20])
    )
    return _input_required(warning, conf_id)


async def _execute_confirmed(
    confirmation_id: str,
    operation: str,
    arguments: dict,
) -> str:
    confirm = arguments.get("confirm")
    if confirm is not True:
        return _error("Explicit confirmation is required: confirm must be true")

    now = time.monotonic()
    _prune_confirmations(now)
    pending = _pending_confirmations.get(confirmation_id)
    if not pending:
        return _error("Confirmation expired or invalid")
    if pending.get("operation") != operation:
        return _error("Confirmation binding mismatch for operation")

    supplied_repo, requested_repo = _confirmation_argument(arguments, "repo_path")
    if supplied_repo and requested_repo is not None:
        try:
            requested_repo_path = _canonical_repo_path(os.fspath(requested_repo))
        except (TypeError, ValueError):
            return _error("Confirmation binding mismatch for repository")
        if requested_repo_path != pending.get("repo_path"):
            return _error("Confirmation binding mismatch for repository")

    for key in ("target", "mode", "force"):
        supplied, requested = _confirmation_argument(arguments, key)
        if key == "target" and supplied and requested != pending.get("target"):
            return _error("Confirmation binding mismatch for target")
        if (
            supplied
            and key in pending.get("state", {})
            and requested != pending["state"][key]
        ):
            return _error(f"Confirmation binding mismatch for {key}")

    # Consume only after all checks pass; a token can be retried after a bad
    # confirmation value or mismatched state, but never after execution.
    _pending_confirmations.pop(confirmation_id, None)
    proc = await asyncio.create_subprocess_exec(
        *pending["command"],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()

    return serialize_telemetry_payload(
        {
            "executed": proc.returncode == 0,
            "command": pending["description"],
            "output": (out.decode() + err.decode()).strip(),
            "returncode": proc.returncode,
        },
        repo_path=pending.get("repo_path"),
    )
