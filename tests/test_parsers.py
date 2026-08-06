"""Unit tests for pure parsing and metric calculation functions."""

from git_telemetry_mcp.tools.git_timeline import _parse_reflog_entry
from git_telemetry_mcp.tools.working_dir_delta import _calculate_change_entropy
from git_telemetry_mcp.tools.dev_activity import _parse_zsh_history, _parse_bash_history
from git_telemetry_mcp.tools.smart_commit import _infer_type, _infer_scope
from git_telemetry_mcp.tools.uncommitted_drift import _summarize_drift


def test_parse_reflog_entry():
    entry = "abc12345|HEAD@{0}|checkout: moving from main to feature/test|2026-08-06 12:00:00 +0000"
    parsed = _parse_reflog_entry(entry)
    assert parsed["sha"] == "abc12345"
    assert parsed["reflog_selector"] == "HEAD@{0}"
    assert parsed["action_type"] == "checkout_branch"

    assert parsed["branch_before"] == "main"
    assert parsed["branch_after"] == "feature/test"


def test_parse_reflog_entry_commit():
    entry = "def67890|HEAD@{1}|commit: feat: add new endpoint|2026-08-06 12:05:00 +0000"
    parsed = _parse_reflog_entry(entry)
    assert parsed["action_type"] == "commit"
    assert "feat: add new endpoint" in parsed["reason_detail"]


def test_calculate_change_entropy():
    diff_text = (
        "--- a/file1.py\n"
        "+++ b/file1.py\n"
        "@@ -1,3 +1,5 @@\n"
        "-old_line\n"
        "+new_line_1\n"
        "+new_line_2\n"
    )
    entropy = _calculate_change_entropy(diff_text)
    assert isinstance(entropy, float)
    assert entropy > 0.0

    # Empty diff -> 0.0 entropy
    assert _calculate_change_entropy("") == 0.0


def test_parse_zsh_history():
    lines = [
        ": 1700000000:0;git status",
        ": 1700000100:0;git commit -m 'test'",
        "invalid line",
    ]
    entries = _parse_zsh_history(lines, since_ts=1699999900, until_ts=1700000200)
    assert len(entries) == 2
    assert entries[0]["command"] == "git status"
    assert "2023-11-14" in entries[1]["timestamp"]


def test_parse_bash_history():
    lines = [
        "#1700000000",
        "git status",
        "#1700000100",
        "pytest",
    ]
    entries = _parse_bash_history(lines, since_ts=1699999900, until_ts=1700000200)
    assert len(entries) == 2
    assert entries[0]["command"] == "git status"
    assert entries[1]["command"] == "pytest"


def test_infer_type_and_scope():
    files = ["src/auth/login.py", "src/auth/utils.py"]
    diff = "+ def fix_login_bug(): pass"

    commit_type = _infer_type(files, diff)
    assert commit_type == "fix"

    scope = _infer_scope(files)
    assert scope == "auth"


def test_summarize_drift():
    summary = _summarize_drift(
        files={"src/a.py"},
        functions=["login"],
        imports=["import os"],
        adds=50,
        dels=5,
    )
    assert "Mostly additive" in summary
    assert "New functions: login" in summary
