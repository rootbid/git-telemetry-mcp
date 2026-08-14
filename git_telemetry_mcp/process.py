"""Bounded, isolated subprocesses and centralized repository validation."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REAL_CREATE_SUBPROCESS_EXEC = asyncio.create_subprocess_exec

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
MAX_OUTPUT_BYTES = DEFAULT_MAX_OUTPUT_BYTES


class SafeProcessTimeout(TimeoutError):
    """Raised when a child process exceeds its execution deadline."""


@dataclass(slots=True)
class CommandResult:
    """Small subprocess result with bounded byte streams."""

    returncode: int
    stdout: bytes
    stderr: bytes


class SafeProcess:
    """Wrap an asyncio process with a deadline and output budget."""

    def __init__(
        self, process: asyncio.subprocess.Process, timeout: float, max_output: int
    ):
        self._process = process
        self._timeout = timeout
        self._max_output = max(1, max_output)

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        try:
            stdout, stderr = await asyncio.wait_for(
                self._process.communicate(input), timeout=self._timeout
            )
        except TimeoutError as exc:
            if self._process.returncode is None:
                self._process.kill()
            await self._process.communicate()
            raise SafeProcessTimeout(
                f"subprocess timed out after {self._timeout:.3g}s"
            ) from exc
        return (stdout or b"")[: self._max_output], (stderr or b"")[: self._max_output]


def _env_for_subprocess(env: Mapping[str, str] | None) -> dict[str, str]:
    merged = dict(os.environ)
    if env:
        merged.update({str(key): str(value) for key, value in env.items()})
    # Do not allow a repository's system/global config to select arbitrary
    # helpers, aliases, credentials, or commands for telemetry inspection.
    merged.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    merged.setdefault("GIT_CONFIG_SYSTEM", os.devnull)
    merged.setdefault("GIT_CONFIG_GLOBAL", os.devnull)
    merged.setdefault("GIT_OPTIONAL_LOCKS", "0")
    merged.setdefault("GIT_TERMINAL_PROMPT", "0")
    return merged


def _harden_git_command(command: list[Any]) -> list[Any]:
    if not command or os.path.basename(str(command[0])) != "git":
        return command
    # Keep Git's executable and arguments as separate argv entries. Config
    # options are placed before -C so they apply to the selected worktree.
    options = [
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "diff.external=",
        "-c",
        "diff.trustExitCode=false",
    ]
    if all(
        str(item) not in {"core.hooksPath=/dev/null", "diff.external="}
        for item in command
    ):
        command = [command[0], *options, *command[1:]]

    # Disable configured diff drivers for commands that render diffs. Unlike
    # environment-based disabling this cannot be replaced by a repository hook.
    subcommand = ""
    index = 1
    while index < len(command):
        item = str(command[index])
        if item in {"-c", "-C"}:
            index += 2
            continue
        if item.startswith("-"):
            index += 1
            continue
        subcommand = item
        break
    if subcommand in {"diff", "show"}:
        index = command.index(subcommand) + 1
        if "--no-ext-diff" not in command:
            command.insert(index, "--no-ext-diff")
            index += 1
        if "--no-textconv" not in command:
            command.insert(index, "--no-textconv")
    return command


async def safe_create_subprocess_exec(
    *command: Any,
    timeout: float | None = None,
    max_output: int | None = None,
    env: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> SafeProcess:
    """Start a process with bounded output, deadline, and safe Git config."""
    timeout = (
        float(timeout)
        if timeout is not None
        else float(
            os.getenv("GIT_TELEMETRY_GIT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        )
    )
    max_output = (
        int(max_output)
        if max_output is not None
        else int(os.getenv("GIT_TELEMETRY_MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES))
    )
    argv = _harden_git_command(list(command))
    if "env" not in kwargs:
        kwargs["env"] = _env_for_subprocess(env)
    process = await _REAL_CREATE_SUBPROCESS_EXEC(*argv, **kwargs)
    return SafeProcess(process, timeout, max_output)


async def run_git(
    repo_path: str | os.PathLike[str],
    args: Sequence[str],
    *,
    timeout: float | None = None,
    max_output: int | None = None,
) -> CommandResult:
    """Run a Git command in a validated path using isolated configuration."""
    process = await safe_create_subprocess_exec(
        "git",
        "-C",
        os.fspath(repo_path),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        timeout=timeout,
        max_output=max_output,
    )
    try:
        stdout, stderr = await process.communicate()
    except SafeProcessTimeout as exc:
        return CommandResult(-9, b"", str(exc).encode())
    return CommandResult(process.returncode or 0, stdout, stderr)


async def validate_repo_path(repo_path: Any) -> str:
    """Resolve and validate an existing Git worktree under optional allow roots."""
    if not isinstance(repo_path, (str, os.PathLike)) or not str(repo_path).strip():
        raise ValueError(
            "Invalid repo_path: expected a non-empty path to a Git worktree"
        )
    try:
        canonical = Path(repo_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as exc:
        raise ValueError(f"Invalid repo_path {repo_path!r}: {exc}") from exc
    if not canonical.is_dir():
        raise ValueError(f"Invalid repo_path {repo_path!r}: not a directory")

    raw_roots = os.getenv("GIT_TELEMETRY_ALLOWED_ROOTS", "")
    if raw_roots.strip():
        roots: list[Path] = []
        for raw_root in raw_roots.split(os.pathsep):
            if not raw_root.strip():
                continue
            try:
                roots.append(Path(raw_root).expanduser().resolve(strict=True))
            except (OSError, RuntimeError):
                continue
        if not any(canonical == root or root in canonical.parents for root in roots):
            raise ValueError(f"Invalid repo_path {repo_path!r}: outside allowed roots")

    result = await run_git(canonical, ["rev-parse", "--is-inside-work-tree"])
    if (
        result.returncode != 0
        or result.stdout.decode(errors="replace").strip().lower() != "true"
    ):
        detail = result.stderr.decode(errors="replace").strip()
        suffix = f" ({detail})" if detail else ""
        raise ValueError(
            f"Invalid repo_path {repo_path!r}: not an existing Git worktree{suffix}"
        )
    return str(canonical)


def install_safe_subprocess() -> None:
    """Route legacy tool subprocess calls through the bounded launcher."""
    if asyncio.create_subprocess_exec is not safe_create_subprocess_exec:
        asyncio.create_subprocess_exec = safe_create_subprocess_exec


__all__ = [
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_OUTPUT_BYTES",
    "CommandResult",
    "SafeProcess",
    "SafeProcessTimeout",
    "install_safe_subprocess",
    "run_git",
    "safe_create_subprocess_exec",
    "validate_repo_path",
]
