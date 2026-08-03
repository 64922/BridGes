"""流式 Qwen 客户端、适配器与网关流式边界测试（Issue 11）。

不依赖网络与真实 Key：HTTP 层用 ``httpx.MockTransport`` 替身，验证
SSE 解析、错误分类、增量产出与运行锁；网关流式路径验证替身降级
（无 ``stream_call`` 的适配器一次性输出）与失败分类。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from bridges.ai import ModelGateway, StubQwenAdapter
from bridges.ai.adapters import AdapterError, AuthError, RateLimitError, TransientError
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.model_gateway import ModelGatewayError  # noqa: F401 - 保持导入面
from bridges.ai.qwen_adapters import QwenTextChatAdapter
from bridges.ai.qwen_client import QwenApiClient
from bridges.ai.streaming import StreamChunk
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    ModelCallStatus,
)
from bridges.contracts.workflows import RunContextEnvelope


def _context(run_id: str = "run-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="account-1",
        project_id="project-1",
        workflow_name="chat",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _chat_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_text_chat",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-completion-v1",
    )


def _sse_response(*data_lines: str, status: int = 200) -> httpx.Response:
    body = "".join(f"data: {line}\n\n" for line in data_lines)
    return httpx.Response(status, content=body.encode("utf-8"))


def _delta_line(delta: str, index: int = 0) -> str:
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "model": "qwen3.7-plus-2026-05-26",
            "choices": [{"index": index, "delta": {"content": delta}, "finish_reason": None}],
        },
        ensure_ascii=False,
    )


def _done_line() -> str:
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "model": "qwen3.7-plus-2026-05-26",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }
    )


class _ProgrammableStreamAdapter:
    """可编程流式适配器：预置块序列或连接阶段错误。"""

    def __init__(
        self,
        chunks: list[StreamChunk] | None = None,
        connect_error: AdapterError | None = None,
    ) -> None:
        self._chunks = chunks or []
        self._connect_error = connect_error

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> Any:
        from bridges.ai.adapters import AdapterResult

        return AdapterResult(
            actual_model_id=capability.model_id,
            output={
                "content": "".join(c.delta for c in self._chunks if c.kind == "delta")
            },
        )

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ):
        if self._connect_error is not None:
            raise self._connect_error
        yield from self._chunks


# ---------------------------------------------------------------------------
# QwenApiClient.chat_completions_stream
# ---------------------------------------------------------------------------


def test_stream_client_parses_sse_lines_and_consumes_done_sentinel() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: _sse_response(
                _delta_line("你"), _delta_line("好"), _done_line(), "[DONE]"
            )
        )
    )

    bodies = list(client.chat_completions_stream({"model": "m", "stream": True}))
    assert [
        b["choices"][0]["delta"].get("content")
        for b in bodies
        if b["choices"][0]["delta"].get("content")
    ] == ["你", "好"]
    assert len(bodies) == 3  # 两个增量 + 一个 usage 结束块


def test_stream_client_sends_stream_flag_and_auth_header() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return _sse_response("[DONE]")

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))

    list(client.chat_completions_stream({"model": "m", "stream": True}))
    assert captured["body"]["stream"] is True
    assert captured["auth"] is None


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(401, AuthError), (403, AuthError), (429, RateLimitError), (503, TransientError)],
)
def test_stream_client_classifies_http_errors(status_code: int, expected: type) -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code, text=""))
    )
    with pytest.raises(expected):
        list(client.chat_completions_stream({"model": "m", "stream": True}))


def test_stream_client_rejects_invalid_sse_json() -> None:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(
        transport=httpx.MockTransport(lambda request: _sse_response("not-json"))
    )
    with pytest.raises(TransientError):
        list(client.chat_completions_stream({"model": "m", "stream": True}))


# ---------------------------------------------------------------------------
# QwenTextChatAdapter.stream_call
# ---------------------------------------------------------------------------


def test_adapter_stream_call_empty_stream_yields_done_without_error() -> None:
    """立即 [DONE] 的空流：不引用未定义变量，正常产出 done 块。"""
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(
        transport=httpx.MockTransport(lambda request: _sse_response("[DONE]"))
    )
    adapter = QwenTextChatAdapter(client)
    chunks = list(
        adapter.stream_call(
            _chat_capability(),
            _context(),
            {"messages": [{"role": "user", "content": "你好"}]},
        )
    )
    assert len(chunks) == 1
    assert chunks[0].kind == "done"
    assert chunks[0].actual_model_id == "qwen3.7-plus-2026-05-26"
    assert chunks[0].usage is None


def test_adapter_stream_call_builds_request_and_yields_deltas() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _sse_response(
            _delta_line("这是"), _delta_line("流式回答"), _done_line(), "[DONE]"
        )

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = QwenTextChatAdapter(client)
    capability = _chat_capability()

    chunks = list(
        adapter.stream_call(
            capability,
            _context(),
            {"messages": [{"role": "user", "content": "你好"}]},
        )
    )
    assert [c.delta for c in chunks if c.kind == "delta"] == ["这是", "流式回答"]
    assert chunks[-1].kind == "done"
    assert chunks[-1].usage == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}
    assert chunks[-1].actual_model_id == "qwen3.7-plus-2026-05-26"
    assert captured["body"]["stream"] is True
    assert captured["body"]["model"] == "qwen3.7-plus-2026-05-26"
    assert captured["body"]["messages"][0] == {"role": "user", "content": "你好"}


# ---------------------------------------------------------------------------
# ModelGateway.stream
# ---------------------------------------------------------------------------


def _gateway_with(adapter: Any) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return gateway


def test_gateway_stream_deltas_then_done_with_lock() -> None:
    gateway = _gateway_with(
        _ProgrammableStreamAdapter(
            [StreamChunk(kind="delta", delta="你好"), StreamChunk(kind="delta", delta="世界")]
        )
    )
    events = list(gateway.stream("qwen_text_chat", "1", _context(), {"messages": []}))
    assert [e.kind for e in events] == ["delta", "delta", "done"]
    assert events[0].delta == "你好"
    done = events[-1]
    assert done.lock is not None
    assert done.lock.status == ModelCallStatus.SUCCESS
    assert done.lock.capability_name == "qwen_text_chat"
    assert done.lock.account_id == "account-1"
    assert done.lock.actual_model_id == "qwen3.7-plus-2026-05-26"


def test_gateway_stream_connection_error_classifies_lock() -> None:
    gateway = _gateway_with(
        _ProgrammableStreamAdapter(connect_error=AuthError("bad key"))
    )
    events = list(gateway.stream("qwen_text_chat", "1", _context(), {}))
    assert len(events) == 1
    assert events[0].kind == "error"
    assert events[0].error_code == "auth_error"
    assert events[0].lock is not None
    assert events[0].lock.status == ModelCallStatus.BLOCKED


def test_gateway_stream_rate_limit_lock_is_retryable() -> None:
    gateway = _gateway_with(
        _ProgrammableStreamAdapter(connect_error=RateLimitError("slow down"))
    )
    events = list(gateway.stream("qwen_text_chat", "1", _context(), {}))
    assert events[0].error_code == "rate_limit"
    assert events[0].lock is not None
    assert events[0].lock.status == ModelCallStatus.RETRYABLE_FAIL


def test_gateway_stream_mid_stream_error_chunk_preserves_deltas() -> None:
    gateway = _gateway_with(
        _ProgrammableStreamAdapter(
            [
                StreamChunk(kind="delta", delta="前半"),
                StreamChunk(kind="error", error_code="transient", error_message="boom"),
            ]
        )
    )
    events = list(gateway.stream("qwen_text_chat", "1", _context(), {}))
    assert [e.kind for e in events] == ["delta", "error"]
    assert events[1].lock is not None
    assert events[1].lock.status == ModelCallStatus.RETRYABLE_FAIL


def test_gateway_stream_unregistered_capability_blocks() -> None:
    registry = CapabilityRegistry()
    gateway = ModelGateway(registry)
    events = list(gateway.stream("qwen_text_chat", "1", _context(), {}))
    assert len(events) == 1
    assert events[0].kind == "error"
    assert events[0].error_code == "unregistered_capability"
    assert events[0].lock is not None
    assert events[0].lock.status == ModelCallStatus.BLOCKED


def test_gateway_stream_stub_adapter_falls_back_to_single_delta() -> None:
    """无 stream_call 的适配器（测试替身）降级为一次性完整回答。"""
    gateway = _gateway_with(StubQwenAdapter())
    events = list(
        gateway.stream("qwen_text_chat", "1", _context(), {"prompt": "你好"})
    )
    assert [e.kind for e in events] == ["delta", "done"]
    assert "确定性" in events[0].delta
    assert events[-1].lock is not None
    assert events[-1].lock.status == ModelCallStatus.SUCCESS


def test_gateway_stream_does_not_auto_retry_streaming_failures() -> None:
    """流式路径不做网关级自动重试：失败由用户级"新建尝试"重试承担。"""
    calls: list[str] = []

    class _CountingAdapter(_ProgrammableStreamAdapter):
        def stream_call(self, capability, run_context, payload):
            calls.append("called")
            raise TransientError("first attempt fails")

    gateway = _gateway_with(_CountingAdapter())
    events = list(gateway.stream("qwen_text_chat", "1", _context(), {}))
    assert len(calls) == 1
    assert events[0].kind == "error"
    assert events[0].error_code == "transient"
