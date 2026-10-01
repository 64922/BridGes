"""test 环境专用：E2E 可配置的聊天流式脚本适配器（Issue 06）。

浏览器生命周期验收需要以确定性内容与故障注入（chunk 边界、延迟、停止、
断线重连）驱动正式 API、后台执行器与 SSE 链路，而不是在页面侧模拟拼接。
本适配器只在 ``BRIDGES_ENVIRONMENT=test`` 时由 API 组合根替换聊天能力的
确定性替身，通过 ``POST /_test/chat-stream-script`` 注册脚本；未命中脚本的
回合完全沿用确定性替身。生产环境不会实例化，也不影响任何真实模型路径。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

from bridges.ai.adapters import AdapterResult, CapabilityAdapter, StreamChunk
from bridges.contracts.ai import CapabilityRecord
from bridges.contracts.workflows import RunContextEnvelope


class ScriptedChatStreamAdapter:
    """按注册脚本分块流式返回聊天正文；未命中时委托确定性替身。"""

    def __init__(self, fallback: CapabilityAdapter) -> None:
        self._fallback = fallback
        self._lock = threading.Lock()
        self._scripts: list[tuple[str, tuple[str, ...], float]] = []

    def configure(
        self, *, match: str, chunks: list[str], delay_ms: int = 0
    ) -> None:
        """注册/替换脚本：用户原文包含 ``match`` 的回合按 ``chunks`` 流式返回。

        多个脚本按注册顺序倒序匹配（后注册优先）；``match`` 必须唯一标识
        一个场景，避免并行测试互相命中。``delay_ms`` 为每块前的等待，用于
        在真实浏览器里制造可停止、可重连的进行中窗口。
        """
        if not match:
            raise ValueError("match 不能为空。")
        if not chunks:
            raise ValueError("chunks 不能为空。")
        with self._lock:
            self._scripts = [
                script for script in self._scripts if script[0] != match
            ]
            self._scripts.append((match, tuple(chunks), max(0, delay_ms) / 1000.0))

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        return self._fallback.call(capability, run_context, payload)

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> Iterator[StreamChunk]:
        chunks, delay = self._match(payload)
        if chunks is None:
            result = self._fallback.call(capability, run_context, payload)
            content = ""
            if isinstance(result.output, dict):
                content = str(result.output.get("content") or "")
            if content:
                yield StreamChunk(kind="delta", delta=content)
        else:
            for chunk in chunks:
                if delay:
                    time.sleep(delay)
                yield StreamChunk(kind="delta", delta=chunk)
        yield StreamChunk(kind="done", actual_model_id=capability.model_id)

    def _match(
        self, payload: dict[str, Any]
    ) -> tuple[tuple[str, ...] | None, float]:
        material = "\n".join(
            str(message.get("content", ""))
            for message in payload.get("messages", [])
            if isinstance(message, dict)
        )
        with self._lock:
            scripts = list(self._scripts)
        for match, chunks, delay in reversed(scripts):
            if match in material:
                return chunks, delay
        return None, 0.0


__all__ = ["ScriptedChatStreamAdapter"]
