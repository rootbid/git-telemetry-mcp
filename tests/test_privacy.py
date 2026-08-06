"""Unit tests for privacy scrubber."""

from git_telemetry_mcp.privacy import scrub_text, scrub_data


def test_scrub_private_key():
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC3\n-----END PRIVATE KEY-----"
    result = scrub_text(pem)
    assert "-----BEGIN PRIVATE KEY-----" not in result
    assert result == "[REDACTED_SECRET]"


def test_scrub_authorization_header():
    header = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThis"
    result = scrub_text(header)
    assert "Bearer" not in result
    assert "[REDACTED_SECRET]" in result


def test_scrub_url_credentials():
    url = "https://admin:secretPass123@db.example.com/production"
    result = scrub_text(url)
    assert "secretPass123" not in result
    assert "[REDACTED_SECRET]" in result


def test_scrub_aws_keys():
    text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\naws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    result = scrub_text(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in result
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in result
    assert "[REDACTED_SECRET]" in result


def test_scrub_github_tokens():
    tokens = "ghp_1234567890abcdefghijklmnopqrstuvwxyz gho_abcdefghijklmnopqrstuvwxyz1234567890 github_pat_11AAAAAAA0123456789012_abcdefghijklmnopqrstuvwxyz012345678901234567890123456789"
    result = scrub_text(tokens)
    assert "ghp_" not in result
    assert "gho_" not in result
    assert "github_pat_" not in result
    assert result.count("[REDACTED_SECRET]") == 3


def test_scrub_openai_and_anthropic_keys():
    text = "openai = 'sk-proj-1234567890abcdefghijklmnopqrstuvwxyz123456' anthropic = 'sk-ant-1234567890abcdefghijklmnopqrstuvwxyz'"
    result = scrub_text(text)
    assert "sk-proj-" not in result
    assert "sk-ant-" not in result


def test_scrub_data_structure():
    data = {
        "status": "ok",
        "nested": {
            "token": "api_key='secret_key_123456789'",
            "items": [123, "sk-proj-abcdef12345678901234567890", True],
        },
    }
    scrubbed = scrub_data(data)
    assert scrubbed["status"] == "ok"
    assert scrubbed["nested"]["items"][0] == 123
    assert scrubbed["nested"]["items"][2] is True
    assert "secret_key_123456789" not in scrubbed["nested"]["token"]
    assert "sk-proj-" not in scrubbed["nested"]["items"][1]


def test_custom_env_pattern(monkeypatch):
    monkeypatch.setenv("GIT_TELEMETRY_EXCLUDE_PATTERNS", r"INTERNAL_TOKEN_\d+")
    text = "User token is INTERNAL_TOKEN_99887766 and it should be hidden."
    result = scrub_text(text)
    assert "INTERNAL_TOKEN_99887766" not in result
    assert "[REDACTED_SECRET]" in result
