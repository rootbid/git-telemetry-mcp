"""git_timeline — reflog + commit analysis over a time range."""

import re
from datetime import UTC, datetime, timedelta

from git_telemetry_mcp.schema import serialize_telemetry_payload
from git_telemetry_mcp.temporal import GitTemporalProvider, resolve_time_bounds


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
    since_input = arguments["since"]
    until_input = arguments.get("until")
    repo_path = arguments.get("repo_path", ".")
    resolved = await resolve_time_bounds(
        since_input, until_input, repo_path=repo_path
    )
    since = resolved["since"]
    until = resolved["until"]
    since_dt = datetime.fromisoformat(since)
    until_dt = datetime.fromisoformat(until)
    provider = GitTemporalProvider(repo_path)
    indexed_events = await provider.events()

    def in_window(event: dict) -> bool:
        event_dt = datetime.fromisoformat(event["timestamp"])
        return since_dt <= event_dt <= until_dt

    selected = [event for event in indexed_events if in_window(event)]
    reflog_entries = [
        {
            "sha": event.get("sha", "")[:8],
            "reflog_selector": event.get("selector"),
            "raw_action_message": event.get("message", ""),
            "date": event["date"],
            "action_type": event.get("action", "unknown"),
            "reason_detail": event.get("message", ""),
            "branch_before": None,
            "branch_after": event.get("branch"),
        }
        for event in selected
        if event["type"] == "reflog"
    ]
    commits = [
        {
            "sha": event.get("sha", "")[:8],
            "author": event.get("author", ""),
            "message": event.get("message", ""),
            "date": event["date"],
        }
        for event in selected
        if event["type"] == "commit"
    ]

    # Calculate reflog expiration threshold (default: 30 days)
    reflog_expiration_threshold = datetime.now(tz=UTC) - timedelta(days=30)
    reflog_warning = None
    if since_dt.timestamp() < reflog_expiration_threshold.timestamp():
        reflog_warning = (
            f"The 'since' date ({since}) is older than the typical Git reflog "
            "expiration period (30 days). Reflog data for this range may be incomplete; "
            "relying primarily on git log for older history."
        )

    result = {
        "range": {
            "since": since,
            "until": until,
            "resolved_from": resolved["resolved_from"],
            "confidence": resolved["confidence"],
            "anchor": resolved.get("anchor"),
        },
        "reflog": reflog_entries,
        "commits": commits,
        "summary": f"{len(reflog_entries)} reflog entries, {len(commits)} commits",
    }
    confidence = float(resolved["confidence"])
    if reflog_warning:
        result["reflog_warning"] = reflog_warning
        confidence = min(confidence, 0.8)
    return serialize_telemetry_payload(
        result, repo_path=repo_path, confidence_score=confidence
    )
