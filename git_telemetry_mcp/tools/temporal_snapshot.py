import asyncio
import json
from datetime import datetime, timedelta, timezone
from git_telemetry_mcp.schema import serialize_telemetry_payload


from git_telemetry_mcp.tools.git_timeline import git_timeline
from git_telemetry_mcp.tools.working_dir_delta import working_dir_delta
from git_telemetry_mcp.tools.dev_activity import dev_activity


async def get_temporal_snapshot(arguments: dict) -> str:
    time_range_str = arguments["time_range"]
    repo_path = arguments.get("repo_path", ".")
    granularity = arguments.get("granularity", "raw")  # Currently just returns raw

    # Convert time_range_str to 'since' argument for git commands
    # This is a simplification; a full implementation would parse more flexibly
    since = time_range_str # Assuming time_range_str is already a git-compatible format like '1.hour.ago'

    # Fetch data concurrently
    git_timeline_task = git_timeline({"since": since, "repo_path": repo_path})
    working_dir_delta_task = working_dir_delta({"include_diff": True, "repo_path": repo_path})
    dev_activity_task = dev_activity({"since": since, "repo_path": repo_path})

    git_timeline_result, working_dir_delta_result, dev_activity_result = await asyncio.gather(
        git_timeline_task,
        working_dir_delta_task,
        dev_activity_task,
    )

    gt_parsed = json.loads(git_timeline_result)
    wd_parsed = json.loads(working_dir_delta_result)
    da_parsed = json.loads(dev_activity_result)

    snapshot = {
        "range": {"time_range_str": time_range_str, "since": since},
        "repo_path": repo_path,
        "granularity": granularity,
        "git_timeline": gt_parsed.get("data", gt_parsed),
        "working_dir_delta": wd_parsed.get("data", wd_parsed),
        "dev_activity": da_parsed.get("data", da_parsed),
        "summary": f"Temporal snapshot for {time_range_str} in {repo_path}",
    }

    return serialize_telemetry_payload(snapshot, repo_path=repo_path)
