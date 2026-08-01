"""Tests for the observability privacy scrubber.

The seam: telemetry and audit payloads never contain private body, full prompts,
secrets or keys. The scrubber removes or hashes forbidden fields and produces a
privacy manifest.
"""

from bridges.observability.scrubber import scrub_payload, scrub_value


def test_scrubber_removes_private_body() -> None:
    payload = {"private_body": "this is secret content", "public_meta": "ok"}
    scrubbed, manifest = scrub_payload(payload)
    assert scrubbed["private_body"] == "<redacted>"
    assert scrubbed["public_meta"] == "ok"
    assert manifest.includes_private_body is True
    assert "private_body" in manifest.scrubbed_fields


def test_scrubber_removes_full_prompt() -> None:
    payload = {"full_prompt": "system: you are helpful", "prompt_version": "v1"}
    scrubbed, manifest = scrub_payload(payload)
    assert scrubbed["full_prompt"] == "<redacted>"
    assert scrubbed["prompt_version"] == "v1"
    assert manifest.includes_full_prompt is True


def test_scrubber_removes_secrets_and_keys() -> None:
    payload = {
        "api_key": "sk-1234567890abcdef",
        "secret": "hunter2",
        "token": "bearer-token",
        "public": "visible",
    }
    scrubbed, manifest = scrub_payload(payload)
    assert scrubbed["api_key"] == "<redacted>"
    assert scrubbed["secret"] == "<redacted>"
    assert scrubbed["token"] == "<redacted>"
    assert scrubbed["public"] == "visible"
    assert manifest.includes_secret is True


def test_scrubber_redacts_secret_patterns_in_strings() -> None:
    payload = {
        "log_line": "request api_key=supersecret token=anothertoken secret=hidden ok=true",
    }
    scrubbed, _ = scrub_payload(payload)
    assert "supersecret" not in scrubbed["log_line"]
    assert "anothertoken" not in scrubbed["log_line"]
    assert "hidden" not in scrubbed["log_line"]
    assert "<redacted>" in scrubbed["log_line"]


def test_scrubber_recursively_handles_nested_structures() -> None:
    payload = {
        "outer": {
            "inner": {
                "private_body": "nested secret",
            },
            "list": [{"token": "item-secret"}],
        }
    }
    scrubbed, _ = scrub_payload(payload)
    assert scrubbed["outer"]["inner"]["private_body"] == "<redacted>"
    assert scrubbed["outer"]["list"][0]["token"] == "<redacted>"


def test_scrubber_preserves_allowed_structures() -> None:
    payload = {
        "run_id": "run-1",
        "status": "success",
        "metadata": {"version": 3, "tags": ["a", "b"]},
    }
    scrubbed, manifest = scrub_payload(payload)
    assert scrubbed["run_id"] == "run-1"
    assert scrubbed["status"] == "success"
    assert scrubbed["metadata"]["version"] == 3
    assert manifest.includes_private_body is False
    assert manifest.includes_secret is False
    assert manifest.includes_full_prompt is False
