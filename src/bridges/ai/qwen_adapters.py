"""Real Qwen adapters for text chat and structured output capabilities.

These adapters replace ``StubQwenAdapter`` for the three text/structured
capabilities registered in T009. They translate logical capability invocations
into Qwen OpenAI-compatible Chat Completions requests and return normalized
``AdapterResult`` objects so the model gateway can still own retry, fallback and
immutable run locks.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError

from bridges.ai.adapters import (
    REQUEST_TIMEOUT_SECONDS_KEY,
    AdapterError,
    AdapterResult,
    CapabilityAdapter,
    StreamChunk,
)
from bridges.ai.qwen_client import QwenApiClient, first_choice
from bridges.contracts.ai import CapabilityRecord, StructuredOutputFormat
from bridges.contracts.profile_extraction import (
    ProfileExtractionAction,
    ProfileExtractionOutput,
)
from bridges.contracts.profiles import FourDimension
from bridges.contracts.workflows import RunContextEnvelope


class QwenTextChatAdapter(CapabilityAdapter):
    """Adapter for the fixed ``qwen_text_chat`` binding (ADR-0009).

    Expects the payload to contain either ``messages`` or a ``prompt``/``node_id``
    from which a minimal message list can be built. Returns the assistant message
    content under the ``content`` key.
    """

    def __init__(self, client: QwenApiClient) -> None:
        self._client = client

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        messages = self._build_messages(payload)
        request_body = {
            "model": capability.model_id,
            "messages": messages,
            "temperature": payload.get("temperature", 0.7),
            "max_tokens": payload.get("max_tokens", 1024),
        }

        response_body = self._client.chat_completions(
            request_body,
            timeout=payload.get(REQUEST_TIMEOUT_SECONDS_KEY),
        )
        choice = first_choice(response_body)
        content = choice.get("message", {}).get("content", "")
        return AdapterResult(
            actual_model_id=response_body.get("model") or capability.model_id,
            output={"content": content},
            usage=response_body.get("usage"),
        )

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> Iterator[StreamChunk]:
        """流式调用 Chat Completions，逐块产出增量正文（Issue 11）。

        连接阶段失败抛 ``AdapterError`` 子类（网关据此分类）；已经开始
        输出后的失败以 ``StreamChunk(kind="error")`` 返回，保留已接收正文。
        """
        messages = self._build_messages(payload)
        request_body = {
            "model": capability.model_id,
            "messages": messages,
            "temperature": payload.get("temperature", 0.7),
            "max_tokens": payload.get("max_tokens", 1024),
            "stream": True,
        }
        last_body: dict[str, Any] | None = None
        for response_body in self._client.chat_completions_stream(request_body):
            last_body = response_body
            choices = response_body.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield StreamChunk(kind="delta", delta=content)
        # 空流（立即 [DONE]）时 last_body 为 None：不引用未定义变量
        yield StreamChunk(
            kind="done",
            usage=last_body.get("usage") if last_body is not None else None,
            actual_model_id=(
                last_body.get("model") if last_body is not None else capability.model_id
            ),
        )

    def _build_messages(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        if "messages" in payload:
            return [
                {"role": str(m["role"]), "content": str(m["content"])}
                for m in payload["messages"]
            ]
        user_content = str(payload.get("prompt") or payload.get("node_id") or "")
        if not user_content:
            user_content = "你好"
        return [
            {"role": "system", "content": "You are a helpful scientific assistant."},
            {"role": "user", "content": user_content},
        ]


class QwenStructuredOutputAdapter(CapabilityAdapter):
    """Adapter for ``qwen_structured_output``.

    The payload may contain ``messages`` and either ``response_format`` or
    ``json_schema``. The adapter forces ``response_format`` to ``json_object`` or
    the supplied schema, parses the assistant content as JSON, and returns the
    parsed object. JSON parse failures are non-retryable because they indicate a
    schema/contract mismatch rather than a transient vendor fault.
    """

    def __init__(self, client: QwenApiClient) -> None:
        self._client = client

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        messages = self._build_messages(payload)
        response_format = self._build_response_format(capability, payload)
        request_body = {
            "model": capability.model_id,
            "messages": messages,
            "response_format": response_format,
            "temperature": payload.get("temperature", 0.7),
            "max_tokens": payload.get("max_tokens", 2048),
        }

        response_body = self._client.chat_completions(
            request_body,
            timeout=payload.get(REQUEST_TIMEOUT_SECONDS_KEY),
        )
        choice = first_choice(response_body)
        message = choice.get("message")
        contract_error_code = (
            "profile_extraction_contract_invalid"
            if payload.get("output_contract")
            in {"profile-extraction-v1", "profile-extraction-v2"}
            else "structured_output_contract_invalid"
        )
        if choice.get("finish_reason") in {"content_filter", "safety"}:
            raise AdapterError(
                code="safety_refusal",
                message="Qwen refused the structured output request.",
                retryable=False,
            )
        if not isinstance(message, dict):
            raise AdapterError(
                code=contract_error_code,
                message="Structured output response did not contain a message object.",
                retryable=False,
            )
        if message.get("refusal") is not None:
            raise AdapterError(
                code="safety_refusal",
                message="Qwen refused the structured output request.",
                retryable=False,
            )
        content = message.get("content")
        if not isinstance(content, str):
            raise AdapterError(
                code=contract_error_code,
                message="Structured output content must be a string.",
                retryable=False,
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AdapterError(
                code="structured_output_parse_failed",
                message=f"Model output was not valid JSON: {exc}",
                retryable=False,
            ) from exc
        if not isinstance(parsed, dict):
            raise AdapterError(
                code=contract_error_code,
                message="Structured output must be a JSON object.",
                retryable=False,
            )
        if payload.get("output_contract") in {
            "profile-extraction-v1",
            "profile-extraction-v2",
        }:
            if "items" not in parsed:
                raise AdapterError(
                    code="profile_extraction_contract_invalid",
                    message="Profile extraction output must contain items.",
                    retryable=False,
                )
            try:
                self._validate_profile_extraction_json_types(parsed)
                ProfileExtractionOutput.model_validate(parsed)
            except ValidationError as exc:
                raise AdapterError(
                    code="profile_extraction_contract_invalid",
                    message="Profile extraction output did not match its contract.",
                    retryable=False,
                ) from exc
            except ValueError as exc:
                raise AdapterError(
                    code="profile_extraction_contract_invalid",
                    message="Profile extraction output did not match its contract.",
                    retryable=False,
                ) from exc
        return AdapterResult(
            actual_model_id=response_body.get("model") or capability.model_id,
            output=parsed,
            usage=response_body.get("usage"),
        )

    @staticmethod
    def _validate_profile_extraction_json_types(payload: dict[str, Any]) -> None:
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("items must be a JSON array")
        dimensions = {dimension.value for dimension in FourDimension}
        actions = {action.value for action in ProfileExtractionAction}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("items must contain JSON objects")
            if not isinstance(item.get("dimension"), str) or item["dimension"] not in dimensions:
                raise ValueError("dimension must be a known string enum")
            if not isinstance(item.get("normalized_value"), str):
                raise ValueError("normalized_value must be a string")
            if not isinstance(item.get("evidence_ref"), str):
                raise ValueError("evidence_ref must be a string")
            reliability = item.get("reliability")
            if isinstance(reliability, bool) or not isinstance(reliability, (int, float)):
                raise ValueError("reliability must be a number")
            if not isinstance(item.get("action"), str) or item["action"] not in actions:
                raise ValueError("action must be a known string enum")

    def _build_messages(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        if "messages" in payload:
            return [
                {"role": str(m["role"]), "content": str(m["content"])}
                for m in payload["messages"]
            ]
        user_content = str(payload.get("prompt") or payload.get("node_id") or "")
        if not user_content:
            user_content = "请输出 JSON。"
        return [
            {
                "role": "system",
                "content": "You are a helpful scientific assistant. Output valid JSON only.",
            },
            {"role": "user", "content": user_content},
        ]

    def _build_response_format(
        self, capability: CapabilityRecord, payload: dict[str, Any]
    ) -> dict[str, Any]:
        declared_format = capability.structured_output_format
        if "response_format" in payload:
            response_format = payload["response_format"]
            if isinstance(response_format, dict):
                if (
                    declared_format == StructuredOutputFormat.JSON_OBJECT
                    and response_format.get("type") == StructuredOutputFormat.JSON_SCHEMA
                ):
                    raise AdapterError(
                        code="unsupported_structured_output_format",
                        message="This capability only supports JSON object output.",
                        retryable=False,
                    )
                return response_format
            raise AdapterError(
                code="invalid_response_format",
                message="response_format must be a JSON object.",
                retryable=False,
            )
        if declared_format == StructuredOutputFormat.JSON_OBJECT:
            if "json_schema" in payload:
                raise AdapterError(
                    code="unsupported_structured_output_format",
                    message="This capability only supports JSON object output.",
                    retryable=False,
                )
            return {"type": "json_object"}
        if "json_schema" in payload:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "schema": payload["json_schema"],
                    "strict": True,
                },
            }
        return {"type": "json_object"}
