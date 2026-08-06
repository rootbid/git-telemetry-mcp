"""dev_activity — correlate shell history with git activity."""

import asyncio
import json
import os
import re
from git_telemetry_mcp.schema import serialize_telemetry_payload

from datetime import datetime, timezone
from pathlib import Path


def _detect_history_file() -> str:
    for candidate in [
        os.environ.get("HISTFILE", ""),
        str(Path.home() / ".zsh_history"),
        str(Path.home() / ".bash_history"),
    ]:
        if candidate and Path(candidate).exists():
            return candidate
    return str(Path.home() / ".bash_history")


def _parse_zsh_history(lines: list[str], since_ts: float, until_ts: float) -> list[dict]:
    entries = []
    for line in lines:
        match = re.match(r"^:\s*(\d+):\d+;(.+)", line)
        if match:
            ts = int(match.group(1))
            if since_ts <= ts <= until_ts:
                entries.append({
                    "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "command": match.group(2).strip(),
                })
    return entries


def _parse_bash_history(lines: list[str], since_ts: float, until_ts: float) -> list[dict]:
    entries = []
    current_ts = None
    for line in lines:
        if line.startswith("#") and line[1:].strip().isdigit():
            current_ts = int(line[1:].strip())
        elif current_ts and since_ts <= current_ts <= until_ts:
            entries.append({
                "timestamp": datetime.fromtimestamp(current_ts, tz=timezone.utc).isoformat(),
                "command": line.strip(),
            })
            current_ts = None
    return entries


async def _git_timestamps(repo_path: str, since: str, until: str) -> tuple[float, float]:
    """Use git to resolve relative time expressions to unix timestamps."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_path, "log", "--format=%ct",
        f"--since={since}", f"--until={until}", "-1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    since_proc = await asyncio.create_subprocess_exec(
        "date", "--date", since.replace(".", " ").replace("ago", "ago"), "+%s",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    since_out, _ = await since_proc.communicate()

    until_proc = await asyncio.create_subprocess_exec(
        "date", "--date", until.replace(".", " ") if until != "now" else "now", "+%s",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    until_out, _ = await until_proc.communicate()

    try:
        since_ts = float(since_out.decode().strip())
    except ValueError:
        since_ts = datetime.now(tz=timezone.utc).timestamp() - 3600

    try:
        until_ts = float(until_out.decode().strip())
    except ValueError:
        until_ts = datetime.now(tz=timezone.utc).timestamp()

    return since_ts, until_ts


async def dev_activity(arguments: dict) -> str:
    since = arguments["since"]
    until = arguments.get("until", "now")
    repo_path = arguments.get("repo_path", ".")
    history_path = arguments.get("shell_history_path") or _detect_history_file()

    since_ts, until_ts = await _git_timestamps(repo_path, since, until)

    # Read shell history
    shell_commands: list[dict] = []
    history_precision_warning = None
    try:
        history_file = Path(history_path)
        if history_file.exists():
            content = history_file.read_text(errors="replace").splitlines()
            if ".zsh_history" in history_path:
                shell_commands = _parse_zsh_history(content, since_ts, until_ts)
            else:  # Assume bash history if not zsh
                # Check for bash history timestamp precision
                bash_has_timestamps = any(line.startswith("#") and line[1:].strip().isdigit() for line in content)
                if not bash_has_timestamps:
                    history_precision_warning = (
                        "Bash history may lack precise timestamps. "
                        "Consider setting HISTTIMEFORMAT in your .bashrc for better accuracy."
                    )
                shell_commands = _parse_bash_history(content, since_ts, until_ts)
    except (OSError, PermissionError):
        pass

    # Get git log for same period
    log_cmd = [
        "git", "-C", repo_path, "log", "--all",
        "--format=%H|%s|%ci", f"--since={since}", f"--until={until}",
    ]
    proc = await asyncio.create_subprocess_exec(
        *log_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    log_out, _ = await proc.communicate()

    git_events = []
    for line in log_out.decode().strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            git_events.append({
                "sha": parts[0][:8],
                "message": parts[1],
                "date": parts[2],
            })

    result = {
        "range": {"since": since, "until": until},
        "shell_commands": shell_commands[-50:],  # cap to avoid huge payloads
        "git_events": git_events,
        "summary": (
            f"{len(shell_commands)} shell commands, {len(git_events)} git events "
            f"in range"
        ),
        "history_file": history_path,
    }
    confidence = 1.0
    if history_precision_warning:
        result["history_precision_warning"] = history_precision_warning
        confidence = 0.8
    return serialize_telemetry_payload(result, repo_path=repo_path, confidence_score=confidence)
