"""Focused protocol metadata and registry metadata checks."""

import json
from pathlib import Path

import jsonschema
import pytest

from git_telemetry_mcp.server import _dispatch, _handle_resources_list

ROOT = Path(__file__).parents[1]


def test_server_json_matches_published_schema():
    metadata = json.loads((ROOT / "server.json").read_text())
    schema = json.loads((ROOT / "schema.json").read_text())
    jsonschema.validate(metadata, schema)
    package = metadata["packages"][0]
    assert package["transport"] == {
        "type": "sse",
        "url": "http://127.0.0.1:8787/sse",
    }


@pytest.mark.asyncio
async def test_resources_declare_json_for_telemetry_envelopes():
    listed = await _handle_resources_list({})
    assert listed["resources"]
    assert all(
        resource["mimeType"] == "application/json" for resource in listed["resources"]
    )


@pytest.mark.asyncio
async def test_unsupported_protocol_method_is_explicit_error():
    response = await _dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "completion/complete", "params": {}}
    )
    assert response["error"] == {
        "code": -32601,
        "message": "Method not found: completion/complete",
    }
