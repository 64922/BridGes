"""Tests for the real Qwen ASR adapter.

These tests use ``httpx.MockTransport`` so they do not require network access or
a real API key. They verify audio request shaping, input-limit enforcement,
response parsing and error classification.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from science_companion.ai import ModelGateway
from science_companion.ai.capability_registry import CapabilityRegistry
from science_companion.ai.qwen_asr_adapter import QwenAsrAdapter
from science_companion.ai.qwen_client import QwenApiClient
from science_companion.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    ModelCallStatus,
    RetryPolicy,
)
from science_companion.contracts.workflows import RunContextEnvelope


def _context(run_id: str = "run-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="account-1",
        project_id="project-1",
        workflow_name="media_ingestion",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _short_capability(*, retry: RetryPolicy | None = None) -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_asr_short",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3-asr-flash",
        input_schema_version="audio-upload-v1",
        output_schema_version="transcript-v1",
        retry_policy=retry or RetryPolicy(max_attempts=1),
    )


def _long_capability(*, retry: RetryPolicy | None = None) -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_asr_long",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3-asr-flash-filetrans",
        input_schema_version="audio-file-v1",
        output_schema_version="transcript-v1",
        retry_policy=retry or RetryPolicy(max_attempts=1),
    )


def _success_response(
    model: str = "qwen3-asr-flash", transcript: str = "Hello world."
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1234567890,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": transcript},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        },
    )


def _audio_payload(
    *, duration: float = 10.0, size_bytes: int = 1024, language: str | None = None
) -> dict[str, Any]:
    audio_bytes = b"\x00" * size_bytes
    payload: dict[str, Any] = {
        "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
        "mime_type": "audio/mpeg",
        "duration_seconds": duration,
    }
    if language is not None:
        payload["language"] = language
    return payload


def test_short_asr_request_shape_and_lock() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _success_response(transcript="欢迎收听科学讲座。")

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenAsrAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_short_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_asr_short", "1", adapter)

    result = gateway.invoke(
        "qwen_asr_short",
        "1",
        _context(),
        payload=_audio_payload(duration=30.0, language="zh"),
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3-asr-flash"
    assert result.lock.region == "cn-beijing"
    assert result.lock.capability_name == "qwen_asr_short"
    assert result.output == {"transcript": "欢迎收听科学讲座。", "language": "zh"}

    body = captured["body"]
    assert body["model"] == "qwen3-asr-flash"
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 4096
    message = body["messages"][0]
    assert message["role"] == "user"
    audio_part = message["content"][0]
    assert audio_part["type"] == "audio_url"
    assert audio_part["audio_url"]["url"].startswith("data:audio/mpeg;base64,")


def test_long_asr_routes_to_filetrans_model() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _success_response(
            model="qwen3-asr-flash-filetrans", transcript="Long recording."
        )

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenAsrAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_long_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_asr_long", "1", adapter)

    result = gateway.invoke(
        "qwen_asr_long",
        "1",
        _context(),
        payload=_audio_payload(duration=600.0),
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3-asr-flash-filetrans"
    assert captured["body"]["model"] == "qwen3-asr-flash-filetrans"


def test_missing_audio_blocks() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    adapter = QwenAsrAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_short_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_asr_short", "1", adapter)

    result = gateway.invoke("qwen_asr_short", "1", _context(), payload={})

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "missing_audio"
    assert result.lock is not None


def test_short_audio_duration_limit_blocks() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    adapter = QwenAsrAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_short_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_asr_short", "1", adapter)

    result = gateway.invoke(
        "qwen_asr_short",
        "1",
        _context(),
        payload=_audio_payload(duration=400.0),
    )

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "audio_too_long"


def test_short_audio_size_limit_blocks() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    adapter = QwenAsrAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_short_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_asr_short", "1", adapter)

    result = gateway.invoke(
        "qwen_asr_short",
        "1",
        _context(),
        payload=_audio_payload(duration=10.0, size_bytes=11 * 1024 * 1024),
    )

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "audio_too_large"


def test_429_is_retried_within_budget() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")

    def handler(request: httpx.Request) -> httpx.Response:
        # First request rate-limited, second succeeds.
        if not hasattr(handler, "count"):
            handler.count = 0  # type: ignore[attr-defined]
        handler.count += 1  # type: ignore[attr-defined]
        if handler.count == 1:  # type: ignore[attr-defined]
            return httpx.Response(429, json={"error": "rate limit"})
        return _success_response()

    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenAsrAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_short_capability(retry=RetryPolicy(max_attempts=3, backoff_seconds=0)))
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_asr_short", "1", adapter)

    result = gateway.invoke(
        "qwen_asr_short", "1", _context(), payload=_audio_payload()
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.retry_count == 1


def test_401_blocks_immediately() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client._client = httpx.Client(transport=httpx.MockTransport(_handler))
    adapter = QwenAsrAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_short_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_asr_short", "1", adapter)

    result = gateway.invoke("qwen_asr_short", "1", _context(), payload=_audio_payload())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "auth_error"


def test_unsupported_mime_type_blocks() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    adapter = QwenAsrAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_short_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_asr_short", "1", adapter)

    payload = _audio_payload()
    payload["mime_type"] = "video/mp4"
    result = gateway.invoke("qwen_asr_short", "1", _context(), payload=payload)

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "unsupported_mime_type"
