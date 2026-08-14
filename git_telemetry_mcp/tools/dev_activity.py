"""dev_activity — correlate shell history with git activity."""

import asyncio
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from git_telemetry_mcp.schema import serialize_telemetry_payload

DEFAULT_MAX_HISTORY_BYTES = 1_000_000
MAX_HISTORY_BYTES = DEFAULT_MAX_HISTORY_BYTES


def _history_max_bytes() -> int:
    """Return a bounded history read limit from trusted local configuration."""
    try:
        configured = int(
            os.getenv("GIT_TELEMETRY_MAX_HISTORY_BYTES", str(DEFAULT_MAX_HISTORY_BYTES))
        )
    except (TypeError, ValueError):
        configured = DEFAULT_MAX_HISTORY_BYTES
    return max(1, min(configured, 10_000_000))


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _configured_history_roots() -> list[Path]:
    roots: list[Path] = []
    for raw_root in os.getenv("GIT_TELEMETRY_ALLOWED_HISTORY_ROOTS", "").split(
        os.pathsep
    ):
        if not raw_root.strip():
            continue
        try:
            root = Path(raw_root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if root.is_dir():
            roots.append(root)
    return roots


def _home_history_files() -> set[Path]:
    """Return existing history files detected directly under the user's home."""
    try:
        home = Path.home().expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        return set()

    candidates = [str(home / ".zsh_history"), str(home / ".bash_history")]
    histfile = os.environ.get("HISTFILE", "")
    if histfile:
        candidates.insert(0, histfile)

    detected: set[Path] = set()
    for raw_path in candidates:
        try:
            path = Path(raw_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if path.is_file() and _path_is_under(path, home):
            detected.add(path)
    return detected


def _validate_history_path(raw_path: str | None) -> tuple[Path | None, str | None]:
    """Resolve a history path only when it is a regular file in an allowed area."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, "History path is invalid"
    try:
        path = Path(raw_path).expanduser().resolve(strict=True)
    except FileNotFoundError:
        return None, "History file is unavailable"
    except (OSError, RuntimeError, TypeError, ValueError):
        return None, "History path is invalid"

    if not path.is_file():
        return None, "History path is not a regular file"
    if any(_path_is_under(path, root) for root in _configured_history_roots()):
        return path, None
    if path in _home_history_files():
        return path, None
    return None, "History path is not permitted"


def _history_candidates() -> list[str]:
    """Return auto-detection candidates without treating arbitrary paths as safe."""
    candidates: list[str] = []
    histfile = os.environ.get("HISTFILE", "")
    if histfile:
        candidates.append(histfile)
    try:
        home = Path.home()
    except (OSError, RuntimeError):
        home = Path("~")
    candidates.extend([str(home / ".zsh_history"), str(home / ".bash_history")])
    return candidates


def _detect_history_file() -> str | None:
    for candidate in _history_candidates():
        path, _ = _validate_history_path(candidate)
        if path is not None:
            return str(path)
    return None


def _read_history(path: Path) -> tuple[list[str], bool]:
    """Read at most the configured byte budget, returning lines and truncation state."""
    limit = _history_max_bytes()
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    truncated = len(raw) > limit
    if truncated:
        raw = raw[:limit]
    return raw.decode(errors="replace").splitlines(), truncated


def _parse_zsh_history(
    lines: list[str], since_ts: float, until_ts: float
) -> list[dict]:
    entries = []
    for line in lines:
        match = re.match(r"^:\s*(\d+):\d+;(.+)", line)
        if match:
            ts = int(match.group(1))
            if since_ts <= ts <= until_ts:
                entries.append(
                    {
                        "timestamp": datetime.fromtimestamp(ts, tz=UTC).isoformat(),
                        "command": match.group(2).strip(),
                    }
                )
    return entries


def _parse_bash_history(
    lines: list[str], since_ts: float, until_ts: float
) -> list[dict]:
    entries = []
    current_ts = None
    for line in lines:
        if line.startswith("#") and line[1:].strip().isdigit():
            current_ts = int(line[1:].strip())
        elif current_ts and since_ts <= current_ts <= until_ts:
            entries.append(
                {
                    "timestamp": datetime.fromtimestamp(current_ts, tz=UTC).isoformat(),
                    "command": line.strip(),
                }
            )
            current_ts = None
    return entries


async def _git_timestamps(
    repo_path: str, since: str, until: str
) -> tuple[float, float]:
    """Use git to resolve relative time expressions to unix timestamps."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        repo_path,
        "log",
        "--format=%ct",
        f"--since={since}",
        f"--until={until}",
        "-1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    since_proc = await asyncio.create_subprocess_exec(
        "date",
        "--date",
        since.replace(".", " ").replace("ago", "ago"),
        "+%s",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    since_out, _ = await since_proc.communicate()

    until_proc = await asyncio.create_subprocess_exec(
        "date",
        "--date",
        until.replace(".", " ") if until != "now" else "now",
        "+%s",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    until_out, _ = await until_proc.communicate()

    try:
        since_ts = float(since_out.decode().strip())
    except ValueError:
        since_ts = datetime.now(tz=UTC).timestamp() - 3600

    try:
        until_ts = float(until_out.decode().strip())
    except ValueError:
        until_ts = datetime.now(tz=UTC).timestamp()

    return since_ts, until_ts


async def dev_activity(arguments: dict) -> str:
    since = arguments["since"]
    until = arguments.get("until", "now")
    repo_path = arguments.get("repo_path", ".")
    requested_history = "shell_history_path" in arguments

    history_file: Path | None = None
    history_error: str | None = None
    if requested_history:
        history_file, history_error = _validate_history_path(
            arguments.get("shell_history_path")
        )
    else:
        detected = _detect_history_file()
        if detected is None:
            history_error = "No permitted shell history file found"
        else:
            history_file, history_error = _validate_history_path(detected)

    since_ts, until_ts = await _git_timestamps(repo_path, since, until)

    shell_commands: list[dict] = []
    history_precision_warning: str | None = None
    history_bytes_truncated = False
    if history_file is not None:
        try:
            content, history_bytes_truncated = _read_history(history_file)
            if history_file.name == ".zsh_history":
                shell_commands = _parse_zsh_history(content, since_ts, until_ts)
            else:  # Assume bash history if not zsh.
                bash_has_timestamps = any(
                    line.startswith("#") and line[1:].strip().isdigit()
                    for line in content
                )
                if not bash_has_timestamps:
                    history_precision_warning = (
                        "Bash history may lack precise timestamps. "
                        "Consider setting HISTTIMEFORMAT in your .bashrc for better accuracy."
                    )
                shell_commands = _parse_bash_history(content, since_ts, until_ts)
        except (OSError, PermissionError):
            history_file = None
            history_error = "Unable to read history file"

    # Get git log for same period.
    log_cmd = [
        "git",
        "-C",
        repo_path,
        "log",
        "--all",
        "--format=%H|%s|%ci",
        f"--since={since}",
        f"--until={until}",
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
            git_events.append(
                {
                    "sha": parts[0][:8],
                    "message": parts[1],
                    "date": parts[2],
                }
            )

    result: dict[str, object] = {
        "range": {"since": since, "until": until},
        "shell_commands": shell_commands[-50:],  # cap to avoid huge payloads
        "git_events": git_events,
        "summary": (
            f"{len(shell_commands)} shell commands, {len(git_events)} git events "
            "in range"
        ),
    }
    # The serializer's path mode scrubs this field before returning it.  Keep
    # it only after a successful bounded read so rejected paths never echo.
    if history_file is not None:
        result["history_file"] = str(history_file)
    if history_error:
        result["history_error"] = history_error
    confidence = 1.0
    if history_precision_warning:
        result["history_precision_warning"] = history_precision_warning
        confidence = min(confidence, 0.8)
    if history_bytes_truncated:
        result["history_bytes_truncated"] = True
        confidence = min(confidence, 0.8)
    if history_error:
        confidence = min(confidence, 0.6)
    return serialize_telemetry_payload(
        result, repo_path=repo_path, confidence_score=confidence
    )
