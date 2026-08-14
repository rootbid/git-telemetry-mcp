"""generate_smart_commit — semantic commit message from staged diffs."""

import asyncio

from git_telemetry_mcp.schema import serialize_telemetry_payload

COMMIT_TYPES = {
    "feat": ["add", "new", "implement", "create"],
    "fix": ["fix", "bug", "patch", "resolve", "correct"],
    "refactor": ["refactor", "restructure", "reorganize", "move", "rename"],
    "docs": ["doc", "readme", "comment", "handbook"],
    "test": ["test", "spec", "assert"],
    "chore": ["config", "ci", "build", "deps", "dependency", "version"],
    "style": ["format", "lint", "whitespace", "style"],
    "perf": ["perf", "optim", "speed", "cache"],
}


def _infer_type(files: list[str], diff_text: str) -> str:
    diff_lower = diff_text.lower()
    file_str = " ".join(files).lower()

    for commit_type, keywords in COMMIT_TYPES.items():
        for kw in keywords:
            if kw in diff_lower or kw in file_str:
                return commit_type
    return "feat"


def _infer_scope(files: list[str]) -> str:
    if not files:
        return ""

    dirs = set()
    for f in files:
        parts = f.split("/")
        if len(parts) > 1:
            dirs.add(
                parts[0]
                if parts[0] not in ("src", "lib", "app")
                else parts[1]
                if len(parts) > 2
                else parts[0]
            )

    if len(dirs) == 1:
        return dirs.pop()
    return ""


async def generate_smart_commit(arguments: dict) -> str:
    repo_path = arguments.get("repo_path", ".")
    execute = arguments.get("execute", False)

    if not isinstance(execute, bool):
        return serialize_telemetry_payload(
            {"error": "execute must be a boolean", "committed": False},
            repo_path=repo_path,
            confidence_score=0.0,
        )
    if execute:
        return serialize_telemetry_payload(
            {
                "error": "execute=true requires an explicit confirmation flow, which is not available",
                "committed": False,
            },
            repo_path=repo_path,
            confidence_score=0.0,
        )

    # Get staged changes
    staged_files_cmd = ["git", "-C", repo_path, "diff", "--cached", "--name-only"]
    staged_diff_cmd = ["git", "-C", repo_path, "diff", "--cached", "--no-color"]
    staged_stat_cmd = ["git", "-C", repo_path, "diff", "--cached", "--stat"]

    procs = await asyncio.gather(
        asyncio.create_subprocess_exec(
            *staged_files_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ),
        asyncio.create_subprocess_exec(
            *staged_diff_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ),
        asyncio.create_subprocess_exec(
            *staged_stat_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ),
    )

    files_out, _ = await procs[0].communicate()
    diff_out, _ = await procs[1].communicate()
    stat_out, _ = await procs[2].communicate()

    files = [f for f in files_out.decode().strip().splitlines() if f]
    diff_text = diff_out.decode()

    if not files:
        return serialize_telemetry_payload(
            {"error": "No staged changes to commit"},
            repo_path=repo_path,
            confidence_score=0.0,
        )

    commit_type = _infer_type(files, diff_text)
    scope = _infer_scope(files)

    # Build description from diff analysis
    additions = sum(
        1
        for line in diff_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1
        for line in diff_text.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )

    # Extract new function/class names for the description
    new_symbols = []
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            stripped = line[1:].strip()
            if stripped.startswith(
                ("def ", "class ", "async def ", "func ", "fn ", "function ", "export ")
            ):
                sym = stripped.split("(")[0].split("{")[0].strip()
                for prefix in (
                    "def ",
                    "class ",
                    "async def ",
                    "func ",
                    "fn ",
                    "function ",
                    "export ",
                ):
                    sym = sym.replace(prefix, "")
                if sym:
                    new_symbols.append(sym)

    scope_str = f"({scope})" if scope else ""
    if new_symbols:
        description = f"add {', '.join(new_symbols[:3])}"
    elif deletions > additions * 2:
        description = (
            f"remove unused code from {', '.join(f.split('/')[-1] for f in files[:2])}"
        )
    elif additions > deletions:
        description = (
            f"add functionality to {', '.join(f.split('/')[-1] for f in files[:2])}"
        )
    else:
        description = f"update {', '.join(f.split('/')[-1] for f in files[:2])}"

    message = f"{commit_type}{scope_str}: {description}"

    result: dict = {
        "suggested_message": message,
        "type": commit_type,
        "scope": scope,
        "files": files,
        "stat": stat_out.decode().strip(),
        "additions": additions,
        "deletions": deletions,
    }

    return serialize_telemetry_payload(result, repo_path=repo_path)
