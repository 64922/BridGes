"""Tests for the real Qwen TTS adapter.

These tests use ``httpx.MockTransport`` so they do not require network access or
a real API key. They verify TTS request shaping, input-limit enforcement,
response parsing, pronunciation pre-processing and error classification.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from science_companion.ai import ModelGateway
from science_companion.ai.capability_registry import CapabilityRegistry
from science_companion.ai.qwen_client import QwenApiClient
from science_companion.ai.qwen_tts_adapter import QwenTtsAdapter
from science_companion.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    ModelCallStatus,
    RetryPolicy,
)
from science_companion.contracts.workflows import RunContextEnvelope


def _context(run_id: str = "run-tts-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="account-1",
        project_id="project-1",
        workflow_name="accessibility_narration",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _tts_capability(*, retry: RetryPolicy | None = None) -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_tts",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3-tts-flash",
        input_schema_version="tts-text-v1",
        output_schema_version="tts-audio-v1",
        supported_modalities=["text", "audio"],
        retry_policy=retry or RetryPolicy(max_attempts=1),
    )


def _tts_instruct_capability(*, retry: RetryPolicy | None = None) -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_tts_instruct",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3-tts-instruct-flash",
        input_schema_version="tts-instruct-v1",
        output_schema_version="tts-audio-v1",
        supported_modalities=["text", "audio"],
        retry_policy=retry or RetryPolicy(max_attempts=1),
    )


def _tts_success_response(
    *,
    model: str = "qwen3-tts-flash",
    url: str = "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/audio.wav",
    expires_at: int = 1766113409,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status_code": 200,
            "request_id": "req-test-001",
            "code": "",
            "message": "",
            "output": {
                "text": None,
                "finish_reason": "stop",
                "choices": None,
                "audio": {
                    "data": "",
                    "url": url,
                    "id": "audio_req-test-001",
                    "expires_at": expires_at,
                },
            },
            "usage": {"input_tokens": 0, "output_tokens": 0, "characters": 195},
        },
    )


def _tts_payload(
    *,
    text: str = "重力加速度约为 9.8 m/s²。",
    voice: str | None = None,
    language_type: str | None = None,
    pronunciation_notes: list[dict[str, Any]] | None = None,
    instructions: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"text": text}
    if voice is not None:
        payload["voice"] = voice
    if language_type is not None:
        payload["language_type"] = language_type
    if pronunciation_notes is not None:
        payload["pronunciation_notes"] = pronunciation_notes
    if instructions is not None:
        payload["instructions"] = instructions
    return payload


def test_tts_request_shape_and_lock() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["url"] = str(request.url)
        return _tts_success_response()

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)

    result = gateway.invoke(
        "qwen_tts",
        "1",
        _context(),
        payload=_tts_payload(text="自由落体加速度为 9.8 m/s²。"),
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3-tts-flash"
    assert result.lock.region == "cn-beijing"
    assert result.lock.capability_name == "qwen_tts"

    output = result.output
    assert output is not None
    assert output["audio_url"].startswith("http://")
    assert output["audio_id"] == "audio_req-test-001"
    assert output["expires_at"] == 1766113409
    assert output["mime_type"] == "audio/wav"

    body = captured["body"]
    assert body["model"] == "qwen3-tts-flash"
    assert "input" in body
    assert body["input"]["text"] == "自由落体加速度为 9.8米每二次方秒。"
    assert body["input"]["voice"] == "Cherry"

    # The TTS endpoint should be the DashScope native endpoint.
    assert "/services/aigc/multimodal-generation/generation" in captured["url"]


def test_tts_instruct_routes_to_instruct_model() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _tts_success_response(model="qwen3-tts-instruct-flash")

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_instruct_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts_instruct", "1", adapter)

    result = gateway.invoke(
        "qwen_tts_instruct",
        "1",
        _context(),
        payload=_tts_payload(
            text="请以正常语速朗读。",
            instructions="以温和的语气朗读",
            pronunciation_notes=None,
        ),
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3-tts-instruct-flash"
    body = captured["body"]
    assert body["model"] == "qwen3-tts-instruct-flash"
    assert body["input"]["instructions"] == "以温和的语气朗读"


def test_missing_text_blocks() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)

    result = gateway.invoke("qwen_tts", "1", _context(), payload={})

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "missing_text"
    assert result.lock is not None


def test_empty_text_blocks() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)

    result = gateway.invoke(
        "qwen_tts", "1", _context(), payload={"text": "   "}
    )

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "missing_text"


def test_text_too_long_blocks() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)

    long_text = "A" * 601
    result = gateway.invoke(
        "qwen_tts", "1", _context(), payload={"text": long_text}
    )

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "text_too_long"


def test_429_is_retried_within_budget() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")

    def handler(request: httpx.Request) -> httpx.Response:
        if not hasattr(handler, "count"):
            handler.count = 0  # type: ignore[attr-defined]
        handler.count += 1  # type: ignore[attr-defined]
        if handler.count == 1:  # type: ignore[attr-defined]
            return httpx.Response(429, json={"error": "rate limit"})
        return _tts_success_response()

    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(
        _tts_capability(retry=RetryPolicy(max_attempts=3, backoff_seconds=0))
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)

    result = gateway.invoke(
        "qwen_tts", "1", _context(), payload=_tts_payload()
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.retry_count == 1


def test_401_blocks_immediately() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client._client = httpx.Client(transport=httpx.MockTransport(_handler))
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)

    result = gateway.invoke("qwen_tts", "1", _context(), payload=_tts_payload())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "auth_error"


def test_body_status_error_blocks() -> None:
    """DashScope may return HTTP 200 with an error status_code in the body."""

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status_code": 400,
                "request_id": "req-err",
                "code": "InvalidParameter",
                "message": "Text is empty.",
                "output": {},
            },
        )

    client._client = httpx.Client(transport=httpx.MockTransport(_handler))
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)

    result = gateway.invoke("qwen_tts", "1", _context(), payload=_tts_payload())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "tts_error_400"


def test_unit_expansion_in_processed_text() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _tts_success_response()

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)

    result = gateway.invoke(
        "qwen_tts",
        "1",
        _context(),
        payload=_tts_payload(text="速度为 10 m/s，温度为 25 °C。"),
    )

    assert result.status == ModelCallStatus.SUCCESS
    body = captured["body"]
    # Units should be expanded to spoken forms.
    assert "10米每秒" in body["input"]["text"]
    assert "25摄氏度" in body["input"]["text"]
    # Original symbols should not remain.
    assert "m/s" not in body["input"]["text"]
    assert "°C" not in body["input"]["text"]


def test_degraded_pronunciation_notes_passed_through() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")

    def handler(_req: httpx.Request) -> httpx.Response:
        return _tts_success_response()

    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)

    notes = [
        {"token": "E=mc²", "kind": "formula", "spoken_form": None, "degraded": True},
        {"token": "DNA", "kind": "abbreviation", "spoken_form": None, "degraded": True},
        {"token": "9.8", "kind": "number", "spoken_form": "9.8", "degraded": False},
    ]
    result = gateway.invoke(
        "qwen_tts",
        "1",
        _context(),
        payload=_tts_payload(
            text="质能方程 E=mc² 和 DNA 结构。",
            pronunciation_notes=notes,
        ),
    )

    assert result.status == ModelCallStatus.SUCCESS
    output = result.output
    assert output is not None
    degraded = output["degraded_pronunciation_notes"]
    assert len(degraded) == 2
    degraded_tokens = {n["token"] for n in degraded}
    assert "E=mc²" in degraded_tokens
    assert "DNA" in degraded_tokens


def test_voice_override_in_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _tts_success_response()

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)

    result = gateway.invoke(
        "qwen_tts",
        "1",
        _context(),
        payload=_tts_payload(text="测试语音。", voice="Ethan"),
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert captured["body"]["input"]["voice"] == "Ethan"


def test_language_type_passed_through() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _tts_success_response()

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTtsAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_tts_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_tts", "1", adapter)

    result = gateway.invoke(
        "qwen_tts",
        "1",
        _context(),
        payload=_tts_payload(text="测试。", language_type="Chinese"),
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert captured["body"]["input"]["language_type"] == "Chinese"
