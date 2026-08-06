"""Schema definitions and telemetry_payload envelope serializer."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from git_telemetry_mcp.privacy import scrub_data


def calculate_repo_checksum(repo_path: str | Path | None) -> str:
    """Calculate SHA-256 hash of canonical repository path."""
    if not repo_path:
        return ""
    try:
        canonical = str(Path(repo_path).resolve())
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def get_timezone_offset() -> str:
    """Get ISO 8601 formatted local timezone offset (e.g. '+00:00', '-05:00')."""
    if time.daylight and time.localtime().tm_isdst:
        offset_seconds = -time.altzone
    else:
        offset_seconds = -time.timezone

    hours, remainder = divmod(abs(offset_seconds), 3600)
    minutes, _ = divmod(remainder, 60)
    sign = "+" if offset_seconds >= 0 else "-"
    return f"{sign}{hours:02d}:{minutes:02d}"


def wrap_telemetry_payload(
    data: Any,
    repo_path: str | Path | None = None,
    confidence_score: float = 1.0,
    timezone_offset: str | None = None,
) -> dict[str, Any]:
    """Wrap tool result data in standard telemetry_payload envelope."""
    tz_offset = timezone_offset or get_timezone_offset()
    checksum = calculate_repo_checksum(repo_path)
    score = max(0.0, min(1.0, float(confidence_score)))

    return {
        "timezone_offset": tz_offset,
        "repo_checksum": checksum,
        "confidence_score": score,
        "data": data,
    }


def serialize_telemetry_payload(
    data: Any,
    repo_path: str | Path | None = None,
    confidence_score: float = 1.0,
    timezone_offset: str | None = None,
    indent: int | None = 2,
) -> str:
    """Wrap, privacy-scrub, and JSON serialize telemetry payload."""
    payload = wrap_telemetry_payload(
        data=data,
        repo_path=repo_path,
        confidence_score=confidence_score,
        timezone_offset=timezone_offset,
    )
    scrubbed_payload = scrub_data(payload)
    return json.dumps(scrubbed_payload, indent=indent)


def make_output_schema(data_schema: dict[str, Any]) -> dict[str, Any]:
    """Generate draft-07 JSON Schema for a tool's telemetry_payload wrapper."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "timezone_offset": {
                "type": "string",
                "pattern": r"^[+-]\d{2}:\d{2}$",
                "description": "ISO 8601 timezone offset (e.g. '+00:00')",
            },
            "repo_checksum": {
                "type": "string",
                "description": "SHA-256 hash of canonicalized repository path",
            },
            "confidence_score": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence score of the returned telemetry data (0.0 to 1.0)",
            },
            "data": data_schema,
        },
        "required": ["timezone_offset", "repo_checksum", "confidence_score", "data"],
        "additionalProperties": False,
    }
