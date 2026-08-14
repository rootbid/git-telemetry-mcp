"""Unit tests for schema module and telemetry envelope."""

import json

from git_telemetry_mcp.schema import (
    calculate_repo_checksum,
    get_timezone_offset,
    make_output_schema,
    serialize_telemetry_payload,
    wrap_telemetry_payload,
)


def test_calculate_repo_checksum(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()

    cs1 = calculate_repo_checksum(repo)
    cs2 = calculate_repo_checksum(str(repo))
    assert cs1 == cs2
    assert len(cs1) == 64  # SHA-256 hex digest length

    assert calculate_repo_checksum(None) == ""


def test_get_timezone_offset():
    tz = get_timezone_offset()
    assert tz.startswith(("+", "-"))
    assert len(tz) == 6
    assert tz[3] == ":"


def test_wrap_telemetry_payload(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    data = {"commits": [{"hash": "abc", "msg": "test"}]}

    payload = wrap_telemetry_payload(data, repo_path=repo, confidence_score=0.95)
    assert payload["confidence_score"] == 0.95
    assert payload["data"] == data
    assert len(payload["repo_checksum"]) == 64
    assert payload["timezone_offset"] == get_timezone_offset()


def test_serialize_telemetry_payload(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    data = {"secret_token": "ghp_1234567890abcdefghijklmnopqrstuvwxyz"}

    json_str = serialize_telemetry_payload(data, repo_path=repo)
    parsed = json.loads(json_str)

    assert "ghp_" not in json_str
    assert parsed["data"]["secret_token"] == "[REDACTED_SECRET]"
    assert "repo_checksum" in parsed
    assert "confidence_score" in parsed


def test_make_output_schema():
    data_schema = {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
        "required": ["count"],
    }
    schema = make_output_schema(data_schema)

    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert "timezone_offset" in schema["properties"]
    assert schema["properties"]["data"] == data_schema
