"""Issue 06：test 环境脚本化聊天流适配器的单元测试。

只在 ``BRIDGES_ENVIRONMENT=test`` 由组合根注册；此处验证匹配、分块顺序与
未命中时对确定性替身的委托，真实链路与浏览器生命周期由链路级测试与 e2e
覆盖。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from bridges.ai.adapters import StubQwenAdapter
from bridges.ai.stream_script_fixture import ScriptedChatStreamAdapter


class _Capability:
    name = "qwen_text_chat"
    version = "1"
    model_id = "qwen-test"


def _payload(content: str) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": content}]}


def _deltas(adapter: ScriptedChatStreamAdapter, content: str) -> list[str]:
    chunks = adapter.stream_call(
        _Capability(),  # type: ignore[arg-type]
        SimpleNamespace(run_id="run-fixture"),  # type: ignore[arg-type]
        _payload(content),
    )
    return [chunk.delta for chunk in chunks if chunk.kind == "delta"]


def test_unmatched_turn_falls_back_to_deterministic_stub() -> None:
    adapter = ScriptedChatStreamAdapter(StubQwenAdapter())
    deltas = _deltas(adapter, "你好")
    assert deltas == ["这是一条来自本地替身模式的确定性测试回答。"]


def test_configured_script_streams_chunks_in_order() -> None:
    adapter = ScriptedChatStreamAdapter(StubQwenAdapter())
    adapter.configure(
        match="请保留 `x = 1`", chunks=["答案：`x", " = 1`。"], delay_ms=0
    )
    assert _deltas(adapter, "请保留 `x = 1` 并解释。") == ["答案：`x", " = 1`。"]


def test_configure_replaces_same_match_and_rejects_empty_input() -> None:
    adapter = ScriptedChatStreamAdapter(StubQwenAdapter())
    adapter.configure(match="锚点", chunks=["旧"])
    adapter.configure(match="锚点", chunks=["新"])
    assert _deltas(adapter, "锚点在这里") == ["新"]

    for match, chunks in (("", ["x"]), ("锚点", [])):
        try:
            adapter.configure(match=match, chunks=chunks)
        except ValueError:
            continue
        raise AssertionError("空 match 或空 chunks 应被拒绝。")
