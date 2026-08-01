"""Tests for the real Qwen OCR and vision adapters.

These tests use ``httpx.MockTransport`` so they do not require network access or
a real API key. They verify image request shaping, base64 data URL construction,
OCR task hints and vision response parsing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from bridges.ai import ModelGateway
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.qwen_client import QwenApiClient
from bridges.ai.qwen_vision_adapters import QwenOcrAdapter, QwenVisionAdapter
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    ModelCallStatus,
    RetryPolicy,
)
from bridges.contracts.workflows import RunContextEnvelope


def _context(run_id: str = "run-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="account-1",
        project_id="project-1",
        workflow_name="wf",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _ocr_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_ocr",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen-vl-ocr",
        input_schema_version="image-ocr-v1",
        output_schema_version="ocr-text-v1",
        supported_modalities=["text", "image"],
        retry_policy=RetryPolicy(max_attempts=1),
    )


def _vision_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_vision",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3-vl-plus",
        input_schema_version="image-vision-v1",
        output_schema_version="vision-text-v1",
        supported_modalities=["text", "image"],
        retry_policy=RetryPolicy(max_attempts=1),
    )


def _success_response(
    model: str = "qwen-vl-ocr", content: str = "extracted text"
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
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        },
    )


def test_ocr_adapter_request_shape_and_model_id() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _success_response(model="qwen-vl-ocr", content="OCR output")

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenOcrAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_ocr_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_ocr", "1", adapter)

    result = gateway.invoke(
        "qwen_ocr",
        "1",
        _context(),
        payload={
            "image_base64": "aGVsbG8=",
            "mime_type": "image/png",
            "task": "advanced_recognition",
        },
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen-vl-ocr"
    assert result.output == {"content": "OCR output", "task": "advanced_recognition"}

    body = captured["body"]
    assert body["model"] == "qwen-vl-ocr"
    assert body["temperature"] == 0.01
    assert body["max_tokens"] == 4096
    assert body["ocr_options"] == {"task": "advanced_recognition"}

    message = body["messages"][0]
    assert message["role"] == "user"
    image_part = message["content"][0]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"] == "data:image/png;base64,aGVsbG8="
    assert image_part["min_pixels"] == 3072
    assert image_part["max_pixels"] == 8388608


def test_ocr_adapter_uses_default_prompt_when_missing() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _success_response()

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenOcrAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_ocr_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_ocr", "1", adapter)

    gateway.invoke("qwen_ocr", "1", _context(), payload={"image_base64": "abcd"})

    body = captured["body"]
    assert "Extract all visible text" in body["messages"][0]["content"][1]["text"]


def test_vision_adapter_request_shape_and_model_id() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _success_response(model="qwen3-vl-plus", content="A scientific diagram.")

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenVisionAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_vision_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_vision", "1", adapter)

    result = gateway.invoke(
        "qwen_vision",
        "1",
        _context(),
        payload={
            "image_base64": "aGVsbG8=",
            "mime_type": "image/jpeg",
            "prompt": "Describe the figure.",
        },
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3-vl-plus"
    assert result.output == {"content": "A scientific diagram."}

    body = captured["body"]
    assert body["model"] == "qwen3-vl-plus"
    assert body["temperature"] == 0.7
    assert body["max_tokens"] == 2048
    message = body["messages"][0]
    assert message["content"][1]["text"] == "Describe the figure."


def test_ocr_adapter_missing_image_blocks() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    adapter = QwenOcrAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_ocr_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_ocr", "1", adapter)

    result = gateway.invoke("qwen_ocr", "1", _context(), payload={})

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "missing_image"
