"""工单 42：冻结额度内的结构化正文与轻量问候搜索边界。"""

import json
from datetime import UTC, datetime

import httpx
import pytest

from bridges.ai.adapters import AdapterError
from bridges.ai.qwen_adapters import QwenStructuredOutputAdapter
from bridges.ai.qwen_client import QwenApiClient
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.workflows import RunContextEnvelope
from bridges.web_search.service import LocalQueryPlanner


@pytest.mark.parametrize("truncated", [False, True])
@pytest.mark.parametrize("model", ["qwen3.5-plus", "qwen3.6-flash", "qwen3.7-plus"])
def test_structured_response_reserves_frozen_allowance_for_json(
    model: str, truncated: bool
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["max_completion_tokens"] == 1024
        thinking = body.get("enable_thinking", True)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length" if thinking or truncated else "stop",
                        "message": {"content": "" if thinking else '{"ok":true}'},
                    }
                ]
            },
        )

    capability = CapabilityRecord(
        name="qwen_structured_output",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn",
        model_id=model,
        input_schema_version="structured-messages-v1",
        output_schema_version="json-schema-v1",
    )
    context = RunContextEnvelope(
        run_id="run-1",
        account_id="account-1",
        project_id="project-1",
        workflow_name="wf",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = QwenApiClient(api_key=None, workspace_id=None, region="cn", http_client=http)
        if truncated:
            with pytest.raises(AdapterError) as error:
                QwenStructuredOutputAdapter(client).call(
                    capability,
                    context,
                    {"prompt": "只输出JSON", "max_tokens": 1024},
                )
            assert error.value.code == "output_budget_exceeded"
            assert not error.value.retryable
            return
        result = QwenStructuredOutputAdapter(client).call(
            capability,
            context,
            {"prompt": "只输出JSON", "max_tokens": 1024},
        )
    assert result.output == {"ok": True}


@pytest.mark.parametrize(
    "content",
    [
        "你好，今天想随便聊两句，你最近怎么样？",
        "你近期还好吗？",
        "你好，最近怎么样？",
    ],
)
def test_social_check_in_does_not_plan_search(content: str) -> None:
    plan = LocalQueryPlanner().plan(content)
    assert not plan.should_search
    assert plan.query == ""


@pytest.mark.parametrize(
    "content",
    [
        "股价最近怎么样？",
        "你最近怎么样？顺便查一下天气。",
        "你最近怎么样？量子计算最新进展是什么？",
    ],
)
def test_recent_external_requests_still_plan_search(content: str) -> None:
    assert LocalQueryPlanner().plan(content).should_search


def test_learning_force_overrides_social_check_in() -> None:
    assert LocalQueryPlanner().plan("你最近怎么样？", force=True).should_search


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("model", "tokens", "explicit", "expected"),
    [
        ("qwen3.7-plus", 1024, None, False),
        ("qwen3.7-plus", 2048, None, None),
        ("qwen3.7-plus", 1024, True, True),
        ("qwen3.7-max", 1024, None, None),
        ("qwen3.8-plus", 1024, None, None),
        ("qwen-legacy", 1024, None, None),
    ],
)
def test_text_small_allowance_policy_preserves_explicit_and_large_requests(
    stream: bool, model: str, tokens: int, explicit: bool | None, expected: bool | None
) -> None:
    from bridges.ai.qwen_adapters import QwenTextChatAdapter

    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if stream:
            return httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"delta":{"content":"你好"},"finish_reason":"stop"}]}\n\n'
                    "data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "你好"},
                    }
                ]
            },
        )

    capability = CapabilityRecord(
        name="qwen_text_chat",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn",
        model_id=model,
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-completion-v1",
    )
    context = RunContextEnvelope(
        run_id="run-1",
        account_id="account-1",
        project_id="project-1",
        workflow_name="wf",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )
    payload = {"prompt": "你好", "max_tokens": tokens}
    if explicit is not None:
        payload["enable_thinking"] = explicit
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = QwenApiClient(api_key=None, workspace_id=None, region="cn", http_client=http)
        adapter = QwenTextChatAdapter(client)
        if stream:
            chunks = list(adapter.stream_call(capability, context, payload))
            assert chunks[-1].kind == "done"
        else:
            assert adapter.call(capability, context, payload).output == {"content": "你好"}
    assert len(captured) == 1
    body = captured[0]
    assert body.get("max_completion_tokens", body.get("max_tokens")) == tokens
    assert body.get("enable_thinking") is expected
