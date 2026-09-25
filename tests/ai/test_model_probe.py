"""V2 Issue 09：主模型真实能力探测（文本/图片/工具调用/结构化输出）。"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from bridges.ai.model_probe import (
    PROBE_ERR_CREDENTIAL_INVALID,
    PROBE_ERR_MODEL_NOT_FOUND,
    PROBE_ERR_OUTPUT_CONTRACT,
    PROBE_ERR_UNAVAILABLE,
    PROBE_TOOL_NAME,
    ModelCapabilityProbe,
)
from bridges.ai.qwen_client import QwenApiClient
from bridges.contracts.ai import ModelCapabilities

_MODEL_ID = "qwen-probe-model"


def _client(handler: object) -> QwenApiClient:
    return QwenApiClient(
        api_key=SecretStr("sk-probe-secret"),
        workspace_id=None,
        region="cn-beijing",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
    )


def _chat_response(message: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": _MODEL_ID,
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        },
    )


def _capable_handler(requests: list[dict[str, object]]):
    """按请求体判定能力：完整实现四项探测的供应商替身。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        requests.append(body)
        if body.get("response_format") == {"type": "json_object"}:
            return _chat_response({"role": "assistant", "content": '{"status": "ok"}'})
        if body.get("tools"):
            return _chat_response(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": PROBE_TOOL_NAME, "arguments": '{"value":"ok"}'},
                        }
                    ],
                }
            )
        if isinstance(body["messages"][0].get("content"), list):
            return _chat_response({"role": "assistant", "content": "白色"})
        return _chat_response({"role": "assistant", "content": "ok"})

    return handler


def _all_capabilities() -> ModelCapabilities:
    return ModelCapabilities(
        text=True, image=True, tool_calling=True, structured_output=True
    )


def test_all_four_probes_pass_on_a_capable_model() -> None:
    requests: list[dict[str, object]] = []
    outcomes = ModelCapabilityProbe(_client(_capable_handler(requests))).run(
        model_id=_MODEL_ID, capabilities=_all_capabilities()
    )

    assert [outcome.capability for outcome in outcomes] == [
        "text",
        "image",
        "tool_calling",
        "structured_output",
    ]
    assert all(outcome.ok for outcome in outcomes)
    assert len(requests) == 4
    # 图片探测确实发送了图片输入，且模型 ID 一律取自候选值。
    image_body = requests[1]
    content = image_body["messages"][0]["content"]  # type: ignore[index]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert {body["model"] for body in requests} == {_MODEL_ID}


@pytest.mark.parametrize(
    ("failure", "expected_capability"),
    [
        ("empty_text", "text"),
        ("empty_image_text", "image"),
        ("no_tool_calls", "tool_calling"),
        ("invalid_json", "structured_output"),
    ],
)
def test_each_capability_failure_is_reported_with_a_reason(
    failure: str, expected_capability: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if body.get("response_format") == {"type": "json_object"}:
            content = "not-json" if failure == "invalid_json" else '{"status": "ok"}'
            return _chat_response({"role": "assistant", "content": content})
        if body.get("tools"):
            message: dict[str, object] = {"role": "assistant", "content": ""}
            if failure != "no_tool_calls":
                message["tool_calls"] = [{"id": "call-1", "type": "function"}]
            return _chat_response(message)
        if isinstance(body["messages"][0].get("content"), list):
            content_text = "" if failure == "empty_image_text" else "白色"
            return _chat_response({"role": "assistant", "content": content_text})
        return _chat_response(
            {"role": "assistant", "content": "" if failure == "empty_text" else "ok"}
        )

    outcomes = ModelCapabilityProbe(_client(handler)).run(
        model_id=_MODEL_ID, capabilities=_all_capabilities()
    )

    failed = [outcome for outcome in outcomes if not outcome.ok]
    assert [outcome.capability for outcome in failed] == [expected_capability]
    assert failed[0].error_code == PROBE_ERR_OUTPUT_CONTRACT
    assert failed[0].message is not None
    assert failed[0].label  # 中文标签随结论返回，供设置页展示


def test_rejected_key_and_unknown_model_are_classified_without_leaking() -> None:
    def auth_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid sk-probe-secret"})

    def missing_model_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"message": "model qwen-probe-model not exist"}},
        )

    def unavailable_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    probe = ModelCapabilityProbe(_client(auth_handler))
    auth_outcomes = probe.run(
        model_id=_MODEL_ID, capabilities=ModelCapabilities(text=True)
    )
    assert auth_outcomes[0].error_code == PROBE_ERR_CREDENTIAL_INVALID
    assert "sk-probe-secret" not in (auth_outcomes[0].message or "")
    assert "先更换 Qwen 密钥" in (auth_outcomes[0].message or "")

    missing_outcomes = ModelCapabilityProbe(_client(missing_model_handler)).run(
        model_id=_MODEL_ID, capabilities=ModelCapabilities(text=True)
    )
    assert missing_outcomes[0].error_code == PROBE_ERR_MODEL_NOT_FOUND

    unavailable_outcomes = ModelCapabilityProbe(_client(unavailable_handler)).run(
        model_id=_MODEL_ID, capabilities=ModelCapabilities(text=True)
    )
    assert unavailable_outcomes[0].error_code == PROBE_ERR_UNAVAILABLE


def test_probe_only_covers_the_declared_capabilities() -> None:
    requests: list[dict[str, object]] = []
    outcomes = ModelCapabilityProbe(_client(_capable_handler(requests))).run(
        model_id=_MODEL_ID,
        capabilities=ModelCapabilities(text=True, image=True),
    )

    assert [outcome.capability for outcome in outcomes] == ["text", "image"]
    assert len(requests) == 2
