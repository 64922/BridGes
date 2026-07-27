"""Privacy scrubber for telemetry, metrics, logs and audit events.

Ensures that observability data does not copy private body, full prompts,
secrets/keys, or unnecessary raw model output. Callers declare what they intend
to emit; the scrubber removes or hashes forbidden fields and returns a privacy
manifest.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from science_companion.contracts.observability import PrivacyManifest


_FORBIDDEN_KEYS = frozenset(
    {
        "private_body",
        "full_prompt",
        "prompt_text",
        "prompt",
        "message_text",
        "secret",
        "api_key",
        "password",
        "token",
        "credential",
        "private_key",
        "vault_payload",
        "memory_slice_content",
        # Additional privacy-sensitive fields (T010 review)
        "raw_output",
        "model_output",
        "completion",
        "authorization",
        "client_secret",
        "secret_key",
        "session_token",
        "refresh_token",
        "access_token",
    }
)

_SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key[:=\s]+)[^\s&]+", re.IGNORECASE),
    re.compile(r"(token[:=\s]+)[^\s&]+", re.IGNORECASE),
    re.compile(r"(secret[:=\s]+)[^\s&]+", re.IGNORECASE),
    re.compile(r"(password[:=\s]+)[^\s&]+", re.IGNORECASE),
]

_SENSITIVE_SUBSTRINGS = [
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "sk-",
    "AKIA",
]


def _contains_secret(value: str) -> bool:
    """Heuristic check for common secret material in a string."""
    for substring in _SENSITIVE_SUBSTRINGS:
        if substring in value:
            return True
    return False


def _scrub_string(value: str) -> str:
    """Remove known secret patterns from a string."""
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(r"\1<redacted>", result)
    if _contains_secret(result):
        return "<redacted>"
    return result


def scrub_value(value: Any, *, keep_keys: set[str] | None = None) -> Any:
    """Recursively scrub a telemetry value.

    Dict keys in the forbidden set are replaced with ``<redacted>``. Strings are
    scanned for secret patterns. Lists and dicts are recursively scrubbed.
    """
    keep_keys = keep_keys or set()
    if isinstance(value, dict):
        scrubbed: dict[str, Any] = {}
        for key, val in value.items():
            if key in keep_keys:
                scrubbed[key] = val
            elif key in _FORBIDDEN_KEYS:
                scrubbed[key] = "<redacted>"
            else:
                scrubbed[key] = scrub_value(val, keep_keys=keep_keys)
        return scrubbed
    if isinstance(value, list):
        return [scrub_value(item, keep_keys=keep_keys) for item in value]
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def scrub_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], PrivacyManifest]:
    """Scrub a payload and produce a privacy manifest.

    The payload is deep-copied before scrubbing so callers cannot accidentally
    use the original afterwards.
    """
    working = copy.deepcopy(payload)
    manifest = PrivacyManifest(
        includes_private_body=False,
        includes_full_prompt=False,
        includes_secret=False,
        includes_model_output=False,
        scrubbed_fields=[],
    )

    def _inspect(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, val in value.items():
                current_path = f"{path}.{key}" if path else key
                if key in _FORBIDDEN_KEYS:
                    manifest.scrubbed_fields.append(current_path)
                    if key == "private_body":
                        manifest.includes_private_body = True
                    if key in {"full_prompt", "prompt_text", "prompt"}:
                        manifest.includes_full_prompt = True
                    if key in {"secret", "api_key", "token", "credential", "private_key"}:
                        manifest.includes_secret = True
                    if key in {"message_text", "memory_slice_content"}:
                        manifest.includes_private_body = True
                _inspect(val, current_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                _inspect(item, f"{path}[{index}]")

    _inspect(working)
    scrubbed = scrub_value(working)
    return scrubbed, manifest
