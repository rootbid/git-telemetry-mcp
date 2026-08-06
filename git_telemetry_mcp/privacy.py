"""Privacy scrubber for redacting PII, secrets, keys, and tokens."""

import os
import re
from typing import Any

# Default secret regex patterns
DEFAULT_SECRET_PATTERNS: list[str] = [
    # Private keys (PEM, RSA, OpenSSH, EC, PGP)
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
    r"-----BEGIN RSA PRIVATE KEY-----[\s\S]*?-----END RSA PRIVATE KEY-----",
    r"-----BEGIN OPENSSH PRIVATE KEY-----[\s\S]*?-----END OPENSSH PRIVATE KEY-----",
    r"-----BEGIN PGP PRIVATE KEY BLOCK-----[\s\S]*?-----END PGP PRIVATE KEY BLOCK-----",

    # Authorization Headers
    r"(?i)authorization\s*:\s*(?:Bearer|Basic|Digest|HOBA|Mutual|AWS4-HMAC-SHA256)\s+[A-Za-z0-9._\-~+/=]+",

    # URLs with embedded credentials (e.g. https://user:pass@host)
    r"(?i)[a-z0-9+\-.]+://[^:\s/]+:[^@\s/]+@[^\s/]+",

    # AWS Access Key & Secret
    r"(?<![A-Z0-9])(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}(?![A-Z0-9])",
    r"(?i)(?:aws_secret_access_key|aws_access_key_id)\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{16,64}['\"]?",

    # GitHub Tokens
    r"ghp_[A-Za-z0-9]{36}",
    r"gho_[A-Za-z0-9]{36}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"ghs_[A-Za-z0-9]{36}",
    r"ghr_[A-Za-z0-9]{36}",
    r"github_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59}",

    # OpenAI API Keys
    r"sk-proj-[A-Za-z0-9_\-]{20,}",
    r"sk-[a-zA-Z0-9]{32,}",
    r"sk-[a-zA-Z0-9T3BlbkFJ]{20,}",

    # Anthropic API Keys
    r"sk-ant-[a-zA-Z0-9_\-]{32,}",

    # Slack Tokens
    r"xox[baprs]-[0-9a-zA-Z]{10,48}",

    # Stripe Secret Keys
    r"sk_live_[0-9a-zA-Z]{24,}",
    r"rk_live_[0-9a-zA-Z]{24,}",

    # Generic Key/Secret assignments in text (e.g., api_key=..., password=...)
    r"(?i)(?:api_key|apikey|secret|password|passwd|auth_token|access_token|private_key)\s*[:=]\s*['\"]?[^\s'\",}{]{8,}['\"]?",

    # JWT Tokens (3 base64url segments separated by dots)
    r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
]

_COMPILED_PATTERNS: list[re.Pattern[str]] | None = None


def get_compiled_patterns(extra_patterns: list[str] | None = None) -> list[re.Pattern[str]]:
    """Get compiled regex patterns combining defaults, env config, and extra patterns."""
    raw_patterns = list(DEFAULT_SECRET_PATTERNS)

    env_patterns = os.getenv("GIT_TELEMETRY_EXCLUDE_PATTERNS", "")
    if env_patterns:
        # Split by comma or newline
        for pat in re.split(r"[,\n]", env_patterns):
            pat = pat.strip()
            if pat and pat not in raw_patterns:
                raw_patterns.append(pat)

    if extra_patterns:
        for pat in extra_patterns:
            pat = pat.strip()
            if pat and pat not in raw_patterns:
                raw_patterns.append(pat)

    return [re.compile(pat) for pat in raw_patterns]


def scrub_text(text: str, extra_patterns: list[str] | None = None) -> str:
    """Redact sensitive patterns in text with [REDACTED_SECRET]."""
    if not isinstance(text, str) or not text:
        return text

    patterns = get_compiled_patterns(extra_patterns)
    scrubbed = text
    for pattern in patterns:
        scrubbed = pattern.sub("[REDACTED_SECRET]", scrubbed)

    return scrubbed


def scrub_data(data: Any, extra_patterns: list[str] | None = None) -> Any:
    """Recursively scrub strings in dicts, lists, tuples, and sets."""
    if isinstance(data, str):
        return scrub_text(data, extra_patterns)
    elif isinstance(data, dict):
        return {
            scrub_text(str(k), extra_patterns): scrub_data(v, extra_patterns)
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [scrub_data(item, extra_patterns) for item in data]
    elif isinstance(data, tuple):
        return tuple(scrub_data(item, extra_patterns) for item in data)
    elif isinstance(data, set):
        return {scrub_data(item, extra_patterns) for item in data}
    else:
        return data
