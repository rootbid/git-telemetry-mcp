"""Unit tests for privacy scrubber."""

from git_telemetry_mcp.privacy import scrub_data, scrub_paths, scrub_pii, scrub_text


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


def test_scrub_arbitrary_token_assignment():
    result = scrub_text("TOKEN=secret")
    assert "secret" not in result
    assert "[REDACTED_SECRET]" in result


def test_scrub_contextual_registry_assignments():
    azure_pat = "A" * 52
    text = f"API_TOKEN=short AZURE_DEVOPS_PAT={azure_pat}"
    result = scrub_text(text)
    assert "short" not in result
    assert azure_pat not in result


def test_scrub_structured_token_fields():
    data = {
        "token": "secret",
        "nested": {
            "client_secret": "another-secret",
            "accessToken": "camel-secret",
            "tokenValue": "value-secret",
        },
    }
    scrubbed = scrub_data(data)
    assert scrubbed["token"] == "[REDACTED_SECRET]"
    assert scrubbed["nested"]["client_secret"] == "[REDACTED_SECRET]"
    assert scrubbed["nested"]["tokenValue"] == "[REDACTED_SECRET]"
    assert scrubbed["nested"]["accessToken"] == "[REDACTED_SECRET]"


def test_scrub_common_registry_token_families():
    text = " ".join(
        (
            "glpat-" + "a" * 20,
            "npm_" + "b" * 36,
            "pypi-" + "c" * 20,
            "azdpat_" + "d" * 40,
        )
    )
    result = scrub_text(text)
    assert all(token not in result for token in ("glpat-", "npm_", "pypi-", "azdpat_"))


def test_invalid_custom_pattern_is_ignored(monkeypatch):
    monkeypatch.setenv("GIT_TELEMETRY_EXCLUDE_PATTERNS", "[unterminated,VALID_[0-9]+")
    result = scrub_text("VALID_123 remains hidden and this call must not fail")
    assert "VALID_123" not in result


def test_optional_pii_and_path_scrubbing_preserves_default_semantics():
    text = "Contact alice@example.com from /home/alice/project"
    assert scrub_text(text) == text
    assert "alice@example.com" not in scrub_pii(text)
    assert "/home/alice/project" not in scrub_paths(text)


def test_optional_scrubbing_flags_apply_recursively():
    data = {"message": "alice@example.com", "path": "/home/alice/project"}
    scrubbed = scrub_data(data, include_pii=True, include_paths=True)
    assert "alice@example.com" not in scrubbed["message"]
    assert "/home/alice/project" not in scrubbed["path"]
