"""Real Qwen adapters for text chat and structured output capabilities.

These adapters replace ``StubQwenAdapter`` for the three text/structured
capabilities registered in T009. They translate logical capability invocations
into Qwen OpenAI-compatible Chat Completions requests and return normalized
``AdapterResult`` objects so the model gateway can still own retry, fallback and
immutable run locks.
"""

from __future__ import annotations

import json
import re
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


def _output_limit(model_id: str | None, tokens: int) -> dict[str, int]:
    """已支持的新 Qwen 型号按思考与正文合计额度限流，其余保留兼容参数。"""
    match = re.match(r"^qwen3\.(\d+)-(plus|flash|max)(?:-|$)", model_id or "")
    if match and (7 if match[2] == "max" else 5) <= int(match[1]) <= 8:
        return {"max_completion_tokens": tokens}
    return {"max_tokens": tokens}


def _thinking_policy(
    model_id: str | None, payload: dict[str, Any], *, structured: bool = False
) -> dict[str, bool]:
    """已核实混合型号的小额度/结构化请求优先交付正文，额度保持不变。

    Qwen 3.5–3.7 Plus/Flash 与 3.6 Max 支持 enable_thinking；3.7 Max
    只支持思考，不能关闭。明确调用策略优先；其他型号保持原有厂商默认。
    """
    match = re.match(r"^qwen3\.([567])-(plus|flash|max)(?:-|$)", model_id or "")
    if match is None or (match[2] == "max" and match[1] != "6"):
        return {}
    explicit = payload.get("enable_thinking")
    if isinstance(explicit, bool):
        return {"enable_thinking": explicit}
    tokens = payload.get("max_tokens", 2048 if structured else 1024)
    if structured or (isinstance(tokens, int) and tokens <= 1024):
        return {"enable_thinking": False}
    return {}


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
            **_output_limit(capability.model_id, payload.get("max_tokens", 1024)),
            **_thinking_policy(capability.model_id, payload),
        }

        response_body = self._client.chat_completions(
            request_body,
            timeout=payload.get(REQUEST_TIMEOUT_SECONDS_KEY),
        )
        choice = first_choice(response_body)
        if choice.get("finish_reason") == "length":
            raise AdapterError(code="output_budget_exceeded",
                               message="模型输出额度已耗尽，请缩小问题范围后重试。",
                               retryable=False)
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
            **_output_limit(capability.model_id, payload.get("max_tokens", 1024)),
            **_thinking_policy(capability.model_id, payload),
            "stream": True,
        }
        last_body: dict[str, Any] | None = None
        finish_reason: str | None = None
        for response_body in self._client.chat_completions_stream(request_body):
            last_body = response_body
            choices = response_body.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield StreamChunk(kind="delta", delta=content)
        # 空流（立即 [DONE]）时 last_body 为 None：不引用未定义变量
        yield StreamChunk(
            kind="error" if finish_reason == "length" else "done",
            error_code="output_budget_exceeded" if finish_reason == "length" else None,
            error_message="模型输出额度已耗尽，请缩小问题范围后重试。"
            if finish_reason == "length" else None,
            usage=last_body.get("usage") if last_body is not None else None,
            actual_model_id=(
                last_body.get("model") if last_body is not None else capability.model_id
            ),
        )

    def _build_messages(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if "messages" in payload:
            # content 原样透传：多模态轮次携带 OpenAI 兼容的内容部件列表
            # （image_url/text，V2 Issue 05），纯文本轮次仍是字符串。
            return [
                {"role": str(m["role"]), "content": m["content"]}
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
            **_output_limit(capability.model_id, payload.get("max_tokens", 2048)),
            **_thinking_policy(capability.model_id, payload, structured=True),
        }

        response_body = self._client.chat_completions(
            request_body,
            timeout=payload.get(REQUEST_TIMEOUT_SECONDS_KEY),
        )
        choice = first_choice(response_body)
        if choice.get("finish_reason") == "length":
            raise AdapterError(
                code="output_budget_exceeded",
                message="模型输出额度已耗尽，请缩小问题范围后重试。",
                retryable=False,
            )
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
