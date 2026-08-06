"""git_timeline — reflog + commit analysis over a time range."""

import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from git_telemetry_mcp.schema import serialize_telemetry_payload
async def _git_timestamps(repo_path: str, since: str, until: str) -> tuple[float, float]:
    """Use git to resolve relative time expressions to unix timestamps."""
    # NOTE: This function is duplicated from dev_activity.py. Consider refactoring to a common utility.
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_path, "log", "--format=%ct",
        f"--since={since}", f"--until={until}", "-1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate() # We only care about the return code here, not the output

    # Use 'date' command to reliably parse relative time strings to epoch timestamps
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
        # Default to 1 hour ago if 'since' parsing fails
        since_ts = datetime.now(tz=timezone.utc).timestamp() - 3600

    try:
        until_ts = float(until_out.decode().strip())
    except ValueError:
        # Default to now if 'until' parsing fails
        until_ts = datetime.now(tz=timezone.utc).timestamp()

    return since_ts, until_ts

def _parse_reflog_entry(entry_line: str) -> dict:
    parts = entry_line.split("|", 3)
    if len(parts) != 4:
        return {}

    sha = parts[0][:8]
    reflog_selector = parts[1]
    raw_action_msg = parts[2]
    date = parts[3]

    action_type = "unknown"
    branch_before = None
    branch_after = None
    reason_detail = raw_action_msg

    # General pattern for action: message
    match = re.match(r"^(.*?):\s*(.*)$", raw_action_msg)
    if match:
        action_type = match.group(1).strip()
        reason_detail = match.group(2).strip()

        # Specific parsing for common actions
        if "checkout" in action_type:
            move_match = re.search(r"moving from (\S+) to (\S+)", reason_detail)
            if move_match:
                branch_before = move_match.group(1)
                branch_after = move_match.group(2)
                action_type = "checkout_branch"
            else:
                to_match = re.search(r"to (\S+)", reason_detail)
                if to_match:
                    branch_after = to_match.group(1)
                action_type = "checkout"
        elif "rebase" in action_type:
            action_type = "rebase"
        elif "commit" in action_type:
            action_type = "commit"
        elif "reset" in action_type:
            action_type = "reset"
        elif "merge" in action_type:
            action_type = "merge"
        elif "amend" in action_type:
            action_type = "amend"
        elif "stash" in action_type:
            action_type = "stash"

    return {
        "sha": sha,
        "reflog_selector": reflog_selector,
        "raw_action_message": raw_action_msg,
        "date": date,
        "action_type": action_type,
        "reason_detail": reason_detail,
        "branch_before": branch_before,
        "branch_after": branch_after,
    }


async def git_timeline(arguments: dict) -> str:
    since = arguments["since"]
    until = arguments.get("until", "now")
    repo_path = arguments.get("repo_path", ".")

    since_ts, until_ts = await _git_timestamps(repo_path, since, until) # Get numeric timestamps

    # Calculate reflog expiration threshold (default: 30 days)
    reflog_expiration_threshold = datetime.now(tz=timezone.utc) - timedelta(days=30)
    reflog_warning = None
    if since_ts < reflog_expiration_threshold.timestamp():
        reflog_warning = (
            f"The 'since' date ({since}) is older than the typical Git reflog "
            f"expiration period (30 days). Reflog data for this range may be incomplete; "
            f"relying primarily on git log for older history."
        )

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

    reflog_out, _ = await reflog_proc.communicate()
    log_out, _ = await log_proc.communicate()

    reflog_entries = []
    for line in reflog_out.decode().strip().splitlines():
        if not line:
            continue
        parsed_entry = _parse_reflog_entry(line)
        if parsed_entry:
            reflog_entries.append(parsed_entry)

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
    confidence = 1.0
    if reflog_warning:
        result["reflog_warning"] = reflog_warning
        confidence = 0.8
    return serialize_telemetry_payload(result, repo_path=repo_path, confidence_score=confidence)
