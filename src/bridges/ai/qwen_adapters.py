"""Real Qwen adapters for text chat and structured output capabilities.

These adapters replace ``StubQwenAdapter`` for the three text/structured
capabilities registered in T009. They translate logical capability invocations
into Qwen OpenAI-compatible Chat Completions requests and return normalized
``AdapterResult`` objects so the model gateway can still own retry, fallback and
immutable run locks.
"""

from __future__ import annotations

import json
from typing import Any

from bridges.ai.adapters import (
    AdapterError,
    AdapterResult,
    CapabilityAdapter,
)
from bridges.ai.qwen_client import QwenApiClient
from bridges.contracts.ai import CapabilityRecord
from bridges.contracts.workflows import RunContextEnvelope


def _first_choice(response_body: dict[str, Any]) -> dict[str, Any]:
    choices = response_body.get("choices")
    if not choices or not isinstance(choices, list):
        raise AdapterError(
            code="empty_response",
            message="Qwen response contained no choices.",
            retryable=False,
        )
    choice = choices[0]
    if not isinstance(choice, dict):
        raise AdapterError(
            code="empty_response",
            message="Qwen response contained no choices.",
            retryable=False,
        )
    return choice


class QwenTextChatAdapter(CapabilityAdapter):
    """Adapter for ``qwen_text_chat`` and ``qwen_text_chat_fallback``.

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

        response_body = self._client.chat_completions(request_body)
        choice = _first_choice(response_body)
        content = choice.get("message", {}).get("content", "")
        return AdapterResult(
            actual_model_id=response_body.get("model") or capability.model_id,
            output={"content": content},
            usage=response_body.get("usage"),
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
        response_format = self._build_response_format(payload)
        request_body = {
            "model": capability.model_id,
            "messages": messages,
            "response_format": response_format,
            "temperature": payload.get("temperature", 0.7),
            "max_tokens": payload.get("max_tokens", 2048),
        }

        response_body = self._client.chat_completions(request_body)
        choice = _first_choice(response_body)
        content = choice.get("message", {}).get("content", "")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AdapterError(
                code="structured_output_parse_failed",
                message=f"Model output was not valid JSON: {exc}",
                retryable=False,
            ) from exc
        return AdapterResult(
            actual_model_id=response_body.get("model") or capability.model_id,
            output=parsed,
            usage=response_body.get("usage"),
        )

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

    def _build_response_format(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "response_format" in payload:
            response_format = payload["response_format"]
            if isinstance(response_format, dict):
                return response_format
            raise AdapterError(
                code="invalid_response_format",
                message="response_format must be a JSON object.",
                retryable=False,
            )
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
