"""explain_uncommitted_drift — summarize architectural direction of uncommitted changes."""

import asyncio
import json
from git_telemetry_mcp.schema import serialize_telemetry_payload



async def explain_uncommitted_drift(arguments: dict) -> str:
    repo_path = arguments.get("repo_path", ".")

    diff_stat_cmd = ["git", "-C", repo_path, "diff", "HEAD", "--stat"]
    diff_cmd = ["git", "-C", repo_path, "diff", "HEAD", "--no-color"]
    staged_cmd = ["git", "-C", repo_path, "diff", "--cached", "--stat"]

    stat_proc, diff_proc, staged_proc = await asyncio.gather(
        asyncio.create_subprocess_exec(
            *diff_stat_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *diff_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
        asyncio.create_subprocess_exec(
            *staged_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        ),
    )

    stat_out, _ = await stat_proc.communicate()
    diff_out, _ = await diff_proc.communicate()
    staged_out, _ = await staged_proc.communicate()

    diff_text = diff_out.decode()

    # Analyze the diff for patterns
    files_changed = set()
    additions = 0
    deletions = 0
    new_functions = []
    new_imports = []

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            parts = line.split(" b/")
            if len(parts) > 1:
                files_changed.add(parts[1])
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
            stripped = line[1:].strip()
            if stripped.startswith(("def ", "async def ", "func ", "fn ", "function ")):
                new_functions.append(stripped.split("(")[0].replace("def ", "").replace("async ", "").replace("func ", "").replace("fn ", "").replace("function ", "").strip())
            if stripped.startswith(("import ", "from ", "require(", "use ")):
                new_imports.append(stripped)
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

    # Categorize changes by directory
    dir_changes: dict[str, int] = {}
    for f in files_changed:
        d = f.rsplit("/", 1)[0] if "/" in f else "."
        dir_changes[d] = dir_changes.get(d, 0) + 1

    result = {
        "stat": stat_out.decode().strip(),
        "staged_stat": staged_out.decode().strip(),
        "analysis": {
            "files_changed": len(files_changed),
            "additions": additions,
            "deletions": deletions,
            "net_change": additions - deletions,
            "new_functions": new_functions[:20],
            "new_imports": new_imports[:20],
            "directories_touched": dir_changes,
        },
        "drift_summary": _summarize_drift(files_changed, new_functions, new_imports, additions, deletions),
    }
    return serialize_telemetry_payload(result, repo_path=repo_path)


def _summarize_drift(files: set, functions: list, imports: list, adds: int, dels: int) -> str:
    parts = []
    if adds > dels * 2:
        parts.append("Mostly additive (new code)")
    elif dels > adds * 2:
        parts.append("Mostly subtractive (removing/cleaning)")
    elif adds and dels:
        parts.append("Refactoring (balanced add/remove)")

    if functions:
        parts.append(f"New functions: {', '.join(functions[:5])}")
    if imports:
        parts.append(f"New dependencies being pulled in")

    return "; ".join(parts) if parts else "Minimal changes"
