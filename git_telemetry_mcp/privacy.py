"""Privacy scrubber for redacting secrets, keys, tokens, and optional PII."""

import os
import re
from functools import lru_cache
from typing import Any

REDACTED_SECRET = "[REDACTED_SECRET]"
REDACTED_PII = "[REDACTED_PII]"
REDACTED_PATH = "[REDACTED_PATH]"

# Default secret regex patterns.  These patterns intentionally target token
# shapes and credential assignments rather than arbitrary high-entropy text.
DEFAULT_SECRET_PATTERNS: list[str] = [
    # Private keys (PEM, RSA, OpenSSH, EC, PGP)
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
    r"-----BEGIN RSA PRIVATE KEY-----[\s\S]*?-----END RSA PRIVATE KEY-----",
    r"-----BEGIN OPENSSH PRIVATE KEY-----[\s\S]*?-----END OPENSSH PRIVATE KEY-----",
    r"-----BEGIN PGP PRIVATE KEY BLOCK-----[\s\S]*?-----END PGP PRIVATE KEY BLOCK-----",
    # Authorization headers
    r"(?i)authorization\s*:\s*(?:Bearer|Basic|Digest|HOBA|Mutual|AWS4-HMAC-SHA256)\s+[A-Za-z0-9._\-~+/=]+",
    # URLs with embedded credentials (e.g. https://user:pass@host)
    r"(?i)[a-z0-9+\-.]+://[^:\s/]+:[^@\s/]+@[^\s/]+",
    # AWS access keys and secrets
    r"(?<![A-Z0-9])(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}(?![A-Z0-9])",
    r"(?i)(?:aws_secret_access_key|aws_access_key_id)\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{16,64}['\"]?",
    # GitHub tokens
    r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{36}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])gho_[A-Za-z0-9]{36}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])ghs_[A-Za-z0-9]{36}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])ghr_[A-Za-z0-9]{36}(?![A-Za-z0-9])",
    # OpenAI and Anthropic API keys
    r"(?<![A-Za-z0-9])sk-proj-[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])sk-[a-zA-Z0-9T3BlbkFJ]{20,}(?![A-Za-z0-9])",
    # Slack and Stripe tokens
    r"(?<![A-Za-z0-9])xox[baprs]-[0-9a-zA-Z]{10,48}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])sk_live_[0-9a-zA-Z]{24,}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])rk_live_[0-9a-zA-Z]{24,}(?![A-Za-z0-9])",
    # GitLab personal/project/deploy, pipeline, and runner tokens
    r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])glptt-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])glrt-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])gldt-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])",
    # npm and PyPI API tokens
    r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{20,}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])pypi-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9])",
    # Azure DevOps PATs and common prefixed forms.  Unprefixed PATs are
    # redacted when assigned to a contextual PAT/token field below.
    r"(?<![A-Za-z0-9])azdpat_[A-Za-z0-9]{20,}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])(?:azdo|azure[_-]?devops)[_-]?(?:pat|token)[_-]?[A-Za-z0-9]{20,84}(?![A-Za-z0-9])",
    r"(?i)(?:account[_-]?key|shared[_-]?access[_-]?signature)\s*=\s*['\"]?[^;\s'\"]+['\"]?",
    r"(?i)(?<![a-z0-9_-])['\"]?(?:api[_-]?key|apikey|(?:[a-z0-9]+[_-])*(?:token|tokens|secret|secrets|password|passwd|passphrase|credential|credentials|pat)|auth(?:entication)?[_-]?token|access[_-]?token|refresh[_-]?token|id[_-]?token|bearer[_-]?token|client[_-]?secret|private[_-]?key|encryption[_-]?key|signing[_-]?key|webhook[_-]?secret|azure[_-]?devops[_-]?(?:pat|token))['\"]?\s*[:=]\s*['\"]?[^\s'\",}{\]]+['\"]?",
    # TOKEN=secret is still a credential and must not leak.  Quotes around
    # JSON/YAML keys and values are consumed as part of the redacted span.
    # JWT tokens (three base64url segments separated by dots)
    r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
]

# Optional scrubbing is opt-in so the serializer gate retains its historical
# default: secret redaction only.  These patterns are deliberately narrower
# than a catch-all path/email expression to avoid changing normal telemetry.
PII_PATTERNS: tuple[str, ...] = (
    r"(?i)\b[\w.!#$%&'*+/=?^`{|}~-]+@(?:[a-z0-9-]+\.)+[a-z]{2,}\b",
    r"(?<!\w)(?:\+?\d[\d(). -]{7,}\d)(?!\w)",
    r"(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?!\w)",
)

PATH_PATTERNS: tuple[str, ...] = (
    r"""(?<![\w])(?:~[\\/]|[A-Za-z]:[\\/]|/(?:home|Users|user|root|tmp|var|opt|srv|mnt|workspace|workspaces|private|Volumes)/)[^\s"'`,;)}\]]+""",
)

_PATTERN_CACHE_SIZE = 32


@lru_cache(maxsize=_PATTERN_CACHE_SIZE)
def _compile_patterns(raw_patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """Compile a bounded pattern set, ignoring malformed user patterns."""
    compiled: list[re.Pattern[str]] = []
    for raw_pattern in raw_patterns:
        try:
            compiled.append(re.compile(raw_pattern))
        except (re.error, TypeError, ValueError, OverflowError):
            # Configuration is user supplied.  One malformed custom pattern
            # must not disable the serializer gate or crash a tool response.
            continue
    return tuple(compiled)


def _normalise_patterns(extra_patterns: list[str] | None) -> tuple[str, ...]:
    raw_patterns = list(DEFAULT_SECRET_PATTERNS)
    seen = set(raw_patterns)

    env_patterns = os.getenv("GIT_TELEMETRY_EXCLUDE_PATTERNS", "")
    configured_patterns = re.split(r"[,\n]", env_patterns) if env_patterns else []
    if extra_patterns:
        configured_patterns.extend(extra_patterns)

    for pattern in configured_patterns:
        if not isinstance(pattern, str):
            continue
        pattern = pattern.strip()
        if pattern and pattern not in seen:
            raw_patterns.append(pattern)
            seen.add(pattern)
    return tuple(raw_patterns)


def get_compiled_patterns(
    extra_patterns: list[str] | None = None,
) -> list[re.Pattern[str]]:
    """Return safely compiled defaults plus configured patterns.

    The cache is bounded and callers receive a fresh list, so mutating the
    returned collection cannot corrupt cached state.
    """
    return list(_compile_patterns(_normalise_patterns(extra_patterns)))


def _apply_patterns(
    text: str,
    patterns: list[re.Pattern[str]] | tuple[re.Pattern[str], ...],
    replacement: str,
) -> str:
    scrubbed = text
    for pattern in patterns:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


@lru_cache(maxsize=1)
def _compiled_pii_patterns() -> tuple[re.Pattern[str], ...]:
    return _compile_patterns(PII_PATTERNS)


@lru_cache(maxsize=1)
def _compiled_path_patterns() -> tuple[re.Pattern[str], ...]:
    return _compile_patterns(PATH_PATTERNS)


def scrub_pii(text: str) -> str:
    """Opt-in redaction of common email, phone-number, and IPv4 PII."""
    if not isinstance(text, str) or not text:
        return text
    return _apply_patterns(text, _compiled_pii_patterns(), REDACTED_PII)


def scrub_paths(text: str) -> str:
    """Opt-in redaction of common Unix, home, and Windows filesystem paths."""
    if not isinstance(text, str) or not text:
        return text
    return _apply_patterns(text, _compiled_path_patterns(), REDACTED_PATH)


def scrub_text(
    text: str,
    extra_patterns: list[str] | None = None,
    *,
    include_pii: bool = False,
    include_paths: bool = False,
) -> str:
    """Redact secrets, with optional explicit PII and path scrubbing."""
    if not isinstance(text, str) or not text:
        return text

    scrubbed = _apply_patterns(
        text, get_compiled_patterns(extra_patterns), REDACTED_SECRET
    )
    if include_pii:
        scrubbed = _apply_patterns(scrubbed, _compiled_pii_patterns(), REDACTED_PII)
    if include_paths:
        scrubbed = _apply_patterns(scrubbed, _compiled_path_patterns(), REDACTED_PATH)
    return scrubbed


_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "token",
        "tokens",
        "secret",
        "secrets",
        "password",
        "passwd",
        "passphrase",
        "credential",
        "credentials",
        "apikey",
        "auth_token",
        "access_token",
        "refresh_token",
        "id_token",
        "bearer_token",
        "client_secret",
        "private_key",
        "encryption_key",
        "signing_key",
        "webhook_secret",
        "pat",
        "azure_devops_pat",
    }
)


def _is_sensitive_field(name: str) -> bool:
    camel_case = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name)
    normalised = re.sub(r"[-. ]", "_", camel_case).lower()
    if normalised in _SENSITIVE_FIELD_NAMES:
        return True

    if re.search(
        r"(?:^|_)(?:token|tokens|secret|secrets|password|passwd|passphrase|credential|credentials|pat)"
        r"(?:$|_(?:value|string|data|raw))",
        normalised,
    ):
        return True

    compact = normalised.replace("_", "")
    return compact.endswith(
        (
            "token",
            "secret",
            "password",
            "passwd",
            "passphrase",
            "credential",
            "credentials",
            "apikey",
            "privatekey",
            "encryptionkey",
            "signingkey",
            "pat",
        )
    )


def scrub_data(
    data: Any,
    extra_patterns: list[str] | None = None,
    *,
    include_pii: bool = False,
    include_paths: bool = False,
) -> Any:
    """Recursively scrub strings and redact values of sensitive fields."""
    if isinstance(data, str):
        return scrub_text(
            data,
            extra_patterns,
            include_pii=include_pii,
            include_paths=include_paths,
        )
    if isinstance(data, dict):
        scrubbed_data = {}
        for key, value in data.items():
            scrubbed_key = scrub_text(
                str(key),
                extra_patterns,
                include_pii=include_pii,
                include_paths=include_paths,
            )
            if isinstance(key, str) and _is_sensitive_field(key) and value is not None:
                scrubbed_data[scrubbed_key] = REDACTED_SECRET
            else:
                scrubbed_data[scrubbed_key] = scrub_data(
                    value,
                    extra_patterns,
                    include_pii=include_pii,
                    include_paths=include_paths,
                )
        return scrubbed_data
    if isinstance(data, list):
        return [
            scrub_data(
                item,
                extra_patterns,
                include_pii=include_pii,
                include_paths=include_paths,
            )
            for item in data
        ]
    if isinstance(data, tuple):
        return tuple(
            scrub_data(
                item,
                extra_patterns,
                include_pii=include_pii,
                include_paths=include_paths,
            )
            for item in data
        )
    if isinstance(data, set):
        return {
            scrub_data(
                item,
                extra_patterns,
                include_pii=include_pii,
                include_paths=include_paths,
            )
            for item in data
        }
    return data
