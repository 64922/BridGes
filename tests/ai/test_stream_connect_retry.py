"""流式建连阶段自动重试测试（Issue 03）。

验证：尚未下发任何 delta 的 ``RegionError`` 自动重试 1 次（短退避，
重试次数计入运行锁遥测）；重试再败按细分码呈现；已下发 delta 后的
中断不重试；开关关闭后等价旧行为；非 RegionError 建连失败不重试。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

import bridges.ai.model_gateway as model_gateway
from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import RegionError, StreamChunk, TransientError
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


class _ScriptedStreamAdapter:
    """每次 ``stream_call`` 按脚本执行：异常立即抛出或产出预置块。"""

    def __init__(self, script: list[list[StreamChunk] | Exception]) -> None:
        self._script = list(script)
        self.call_count = 0

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ):
        self.call_count += 1
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        yield from step


class _MidStreamFailAdapter:
    """产出首个 delta 后在迭代中途抛出异常（模拟已开始输出后的断流）。"""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.call_count = 0

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ):
        self.call_count += 1
        yield StreamChunk(kind="delta", delta="前半")
        raise self._error


def _gateway_with(adapter: Any) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return gateway


def test_connect_region_error_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """建连失败 → 重试成功：用户无感知（无 error 事件），重试计入锁遥测。"""
    monkeypatch.setattr(model_gateway, "STREAM_CONNECT_RETRY_BACKOFF_SECONDS", 0)
    adapter = _ScriptedStreamAdapter(
        [
            RegionError("region unavailable"),
            [
                StreamChunk(kind="delta", delta="你好"),
                StreamChunk(kind="done"),
            ],
        ]
    )
    gateway = _gateway_with(adapter)

    events = list(gateway.stream("qwen_text_chat", "1", _context(), {"messages": []}))

    assert [e.kind for e in events] == ["delta", "done"]
    assert adapter.call_count == 2
    done = events[-1]
    assert done.lock is not None
    assert done.lock.status == ModelCallStatus.SUCCESS
    assert done.lock.retry_count == 1


def test_connect_region_error_retries_once_then_fails_with_subcode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试再败：呈现细分后错误码，运行锁如实记录 1 次重试。"""
    monkeypatch.setattr(model_gateway, "STREAM_CONNECT_RETRY_BACKOFF_SECONDS", 0)
    adapter = _ScriptedStreamAdapter(
        [
            RegionError("dns fail", sub_code="dns"),
            RegionError("dns fail", sub_code="dns"),
        ]
    )
    gateway = _gateway_with(adapter)

    events = list(gateway.stream("qwen_text_chat", "1", _context(), {}))

    assert len(events) == 1
    assert events[0].kind == "error"
    assert events[0].error_code == "region_dns"
    assert events[0].lock is not None
    assert events[0].lock.status == ModelCallStatus.BLOCKED
    assert events[0].lock.retry_count == 1
    assert adapter.call_count == 2


def test_mid_stream_region_error_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """delta 已下发后的中断不重试：一次调用即收尾，保留已接收内容。"""
    monkeypatch.setattr(model_gateway, "STREAM_CONNECT_RETRY_BACKOFF_SECONDS", 0)
    adapter = _MidStreamFailAdapter(RegionError("connection reset mid-stream"))
    gateway = _gateway_with(adapter)

    events = list(gateway.stream("qwen_text_chat", "1", _context(), {}))

    assert [e.kind for e in events] == ["delta", "error"]
    assert events[1].error_code == "region_error"
    assert events[1].lock is not None
    assert events[1].lock.retry_count == 0
    assert adapter.call_count == 1


def test_connect_retry_respects_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """重试前执行一次约 1 秒的短退避（只 sleep 一次）。"""
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(model_gateway.time, "sleep", fake_sleep)
    adapter = _ScriptedStreamAdapter(
        [
            RegionError("region unavailable"),
            [StreamChunk(kind="done")],
        ]
    )
    gateway = _gateway_with(adapter)

    list(gateway.stream("qwen_text_chat", "1", _context(), {}))

    assert sleeps == [model_gateway.STREAM_CONNECT_RETRY_BACKOFF_SECONDS]
    assert adapter.call_count == 2


def test_connect_retry_disabled_switch_behaves_like_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开关关闭（回滚形态）：建连失败立即 error 事件，不重试。"""
    monkeypatch.setattr(model_gateway, "STREAM_CONNECT_RETRY_ENABLED", False)
    monkeypatch.setattr(model_gateway, "STREAM_CONNECT_RETRY_BACKOFF_SECONDS", 0)
    adapter = _ScriptedStreamAdapter(
        [
            RegionError("region unavailable"),
            [StreamChunk(kind="done")],
        ]
    )
    gateway = _gateway_with(adapter)

    events = list(gateway.stream("qwen_text_chat", "1", _context(), {}))

    assert [e.kind for e in events] == ["error"]
    assert events[0].error_code == "region_error"
    assert events[0].lock is not None
    assert events[0].lock.retry_count == 0
    assert adapter.call_count == 1


def test_connect_non_region_error_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 RegionError 建连失败（如瞬时故障）保持不重试的既有语义。"""
    monkeypatch.setattr(model_gateway, "STREAM_CONNECT_RETRY_BACKOFF_SECONDS", 0)
    adapter = _ScriptedStreamAdapter(
        [
            TransientError("transient boom"),
            [StreamChunk(kind="done")],
        ]
    )
    gateway = _gateway_with(adapter)

    events = list(gateway.stream("qwen_text_chat", "1", _context(), {}))

    assert [e.kind for e in events] == ["error"]
    assert events[0].error_code == "transient"
    assert adapter.call_count == 1


def test_connect_retry_success_lock_records_retry_in_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试成功路径：运行锁 retry_count=1（遥测可见一次建连重试）。"""
    monkeypatch.setattr(model_gateway, "STREAM_CONNECT_RETRY_BACKOFF_SECONDS", 0)
    adapter = _ScriptedStreamAdapter(
        [
            RegionError("region unavailable", sub_code="tls"),
            [StreamChunk(kind="done")],
        ]
    )
    gateway = _gateway_with(adapter)

    events = list(gateway.stream("qwen_text_chat", "1", _context(), {}))

    assert [e.kind for e in events] == ["done"]
    assert events[0].lock is not None
    assert events[0].lock.retry_count == 1
