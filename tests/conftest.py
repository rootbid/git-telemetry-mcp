"""Pytest fixtures for git-telemetry-mcp test suite."""

import subprocess
from pathlib import Path
import pytest


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create an ephemeral git repository for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Git init
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)

    # Initial commit
    readme = repo / "README.md"
    readme.write_text("# Test Repo\nInitial content\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, check=True, capture_output=True)

    return repo


@pytest.fixture
def repo_with_history(temp_git_repo: Path) -> Path:
    """Create a git repository with commits, branches, reflog entries, and stashes."""
    repo = temp_git_repo

    # Add commit on main
    file1 = repo / "file1.txt"
    file1.write_text("Hello world\n")
    subprocess.run(["git", "add", "file1.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: add file1"], cwd=repo, check=True, capture_output=True)

    # Create feature branch and commit
    subprocess.run(["git", "checkout", "-b", "feature/test"], cwd=repo, check=True, capture_output=True)
    file2 = repo / "file2.py"
    file2.write_text("print('feature')\n")
    subprocess.run(["git", "add", "file2.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat(python): add feature module"], cwd=repo, check=True, capture_output=True)

    # Create uncommitted change and stash
    file3 = repo / "stashed.txt"
    file3.write_text("stash me\n")
    subprocess.run(["git", "add", "stashed.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "stash", "push", "-m", "wip stash"], cwd=repo, check=True, capture_output=True)

    # Return to main
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    # Dirty working directory change
    readme = repo / "README.md"
    readme.write_text("# Test Repo\nInitial content\nUncommitted line\n")

    return repo


@pytest.fixture
def fake_shell_history(tmp_path: Path) -> Path:
    """Create a temporary shell history file with zsh/bash style timestamps."""
    history_file = tmp_path / "shell_history"
    content = (
        ": 1700000000:0;git status\n"
        ": 1700000100:0;git checkout -b feature/test\n"
        ": 1700000200:0;pytest\n"
        ": 1700000300:0;git commit -m 'feat: test'\n"
    )
    history_file.write_text(content)
    return history_file
