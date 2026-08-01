"""Tests for the real Qwen text and structured output adapters.

These tests use ``httpx.MockTransport`` so they do not require network access or
a real API key. They verify request shaping, response parsing, error
classification and structured-output failure handling.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from bridges.ai import ModelGateway
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.qwen_adapters import QwenStructuredOutputAdapter, QwenTextChatAdapter
from bridges.ai.qwen_client import QwenApiClient
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    FallbackPolicy,
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


def _text_capability(*, retry: RetryPolicy | None = None) -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_text_chat",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus",
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-completion-v1",
        retry_policy=retry or RetryPolicy(max_attempts=1),
    )


def _structured_capability(*, retry: RetryPolicy | None = None) -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_structured_output",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.6-flash",
        input_schema_version="structured-messages-v1",
        output_schema_version="json-schema-v1",
        retry_policy=retry or RetryPolicy(max_attempts=1),
    )


def _transport_for(*responses: httpx.Response) -> httpx.MockTransport:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return queue.pop(0)

    return httpx.MockTransport(handler)


def _success_response(model: str = "qwen3.7-plus", content: str = "你好！") -> httpx.Response:
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
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def test_text_chat_request_shape_and_lock() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return _success_response()

    client = QwenApiClient(
        api_key=None,
        workspace_id=None,
        region="cn-beijing",
    )
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTextChatAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_text_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)

    result = gateway.invoke(
        "qwen_text_chat", "1", _context(), payload={"node_id": "compile_context"}
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3.7-plus"
    assert result.lock.region == "cn-beijing"
    assert result.output == {"content": "你好！"}
    assert captured["body"]["model"] == "qwen3.7-plus"
    assert captured["body"]["temperature"] == 0.7
    assert captured["body"]["max_tokens"] == 1024
    assert captured["body"]["messages"][-1]["content"] == "compile_context"
    assert captured["auth"] is None


def test_text_chat_uses_prompt_when_provided() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _success_response()

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTextChatAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_text_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)

    gateway.invoke(
        "qwen_text_chat",
        "1",
        _context(),
        payload={"prompt": "解释量子纠缠", "temperature": 0.5, "max_tokens": 512},
    )

    assert captured["body"]["messages"][-1]["content"] == "解释量子纠缠"
    assert captured["body"]["temperature"] == 0.5
    assert captured["body"]["max_tokens"] == 512


def test_structured_output_parses_json_and_records_lock() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1234567890,
                "model": "qwen3.6-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"claim": "E=mc^2", "confidence": "high"}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            },
        )

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenStructuredOutputAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_structured_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_structured_output", "1", adapter)

    result = gateway.invoke(
        "qwen_structured_output",
        "1",
        _context(),
        payload={"node_id": "produce_output"},
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3.6-flash"
    assert result.output == {"claim": "E=mc^2", "confidence": "high"}
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_structured_output_invalid_json_blocks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": "qwen3.6-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "not valid json"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
        )

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenStructuredOutputAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_structured_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_structured_output", "1", adapter)

    result = gateway.invoke(
        "qwen_structured_output",
        "1",
        _context(),
        payload={"node_id": "produce_output"},
    )

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "structured_output_parse_failed"
    assert result.lock is not None
    assert result.lock.retry_count == 0


def test_429_is_retried_within_budget() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(
        transport=_transport_for(
            httpx.Response(429, json={"error": "rate limit"}),
            _success_response(),
        )
    )
    adapter = QwenTextChatAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_text_capability(retry=RetryPolicy(max_attempts=3, backoff_seconds=0)))
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)

    result = gateway.invoke("qwen_text_chat", "1", _context())

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.retry_count == 1


def test_401_blocks_immediately() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(
        transport=_transport_for(httpx.Response(401, json={"error": "unauthorized"}))
    )
    adapter = QwenTextChatAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_text_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)

    result = gateway.invoke("qwen_text_chat", "1", _context())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "auth_error"
    assert result.lock is not None
    assert result.lock.retry_count == 0


def test_403_blocks_immediately() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(
        transport=_transport_for(httpx.Response(403, json={"error": "forbidden"}))
    )
    adapter = QwenTextChatAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_text_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)

    result = gateway.invoke("qwen_text_chat", "1", _context())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "auth_error"


def test_5xx_is_retried_then_retryable_fail() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(
        transport=_transport_for(
            httpx.Response(503, json={"error": "overloaded"}),
            httpx.Response(503, json={"error": "overloaded"}),
        )
    )
    adapter = QwenTextChatAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_text_capability(retry=RetryPolicy(max_attempts=2, backoff_seconds=0)))
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)

    result = gateway.invoke("qwen_text_chat", "1", _context())

    assert result.status == ModelCallStatus.RETRYABLE_FAIL
    assert result.error_code == "transient"
    assert result.lock is not None
    assert result.lock.retry_count == 1


def test_connect_error_is_region_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTextChatAdapter(client)

    registry = CapabilityRegistry()
    registry.register(
        _text_capability().model_copy(
            update={
                "fallback_policy": FallbackPolicy(
                    fallback_capability_name="fallback", fallback_capability_version="1"
                )
            }
        )
    )
    registry.register(
        CapabilityRecord(
            name="fallback",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.6-flash",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
        )
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    gateway.register_adapter("fallback", "1", adapter)

    result = gateway.invoke("qwen_text_chat", "1", _context())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "region_error"
    assert result.lock is not None
    assert result.lock.retry_count == 0


def test_api_key_is_not_logged_or_exposed() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return _success_response()

    from pydantic import SecretStr

    client = QwenApiClient(
        api_key=SecretStr("super-secret-key"),
        workspace_id=None,
        region="cn-beijing",
    )
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTextChatAdapter(client)

    registry = CapabilityRegistry()
    registry.register(_text_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)

    result = gateway.invoke("qwen_text_chat", "1", _context())

    assert result.status == ModelCallStatus.SUCCESS
    assert captured["auth"] == "Bearer super-secret-key"
    # Ensure the result/lock never carries the raw key.
    assert "super-secret-key" not in str(result.lock)


def test_base_url_uses_workspace_and_region() -> None:
    client = QwenApiClient(
        api_key=None,
        workspace_id="my-workspace",
        region="ap-southeast-1",
    )
    assert client.base_url == (
        "https://my-workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )


def test_base_url_falls_back_to_dashscope_when_no_workspace() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    assert client.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
