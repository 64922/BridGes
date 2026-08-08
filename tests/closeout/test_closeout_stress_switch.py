"""Issue 11 收尾压力验收：生成期间 50 次随机会话切换/刷新，stream_interrupted 为 0。

验收标准（11-closeout-release-acceptance.md 第 3 条）：
「生成期间随机执行 50 次会话切换/刷新：除显式停止外，stream_interrupted 为 0。」

反馈环设计（复用 issue 02 的闸门慢适配器模式：真实 HTTP + 真实 SQLite +
真实后台执行器线程，模型输出由测试闸门逐段放行）：
- 50 轮生成，每轮用固定种子伪随机执行 1-3 次「切换/刷新」操作：
  · 切换 = 打开另一会话（新建会话 + 读取其投影）；
  · 刷新 = 重新 GET 原会话投影（模拟页面重开，从游标恢复）。
- 操作期间原运行必须保持 streaming/running，不得产生 stream_interrupted；
- 每轮放行末闸门后运行收敛为单一 done，模型只调用一次（不重复发送）；
- 对照用例：显式停止是唯一允许的中断来源（终态 stopped）。
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.config import get_settings
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _register,
)

#: 压力轮数（验收标准：50 次会话切换/刷新分布于生成期间）。
STRESS_ROUNDS = 50
#: 固定种子：切换/刷新序列确定可复现，不做真随机。
STRESS_SEED = 20260809


class _GatedSlowAdapter:
    """两段输出的可控慢模型：每块输出由测试闸门放行（真实流式调用计数）。"""

    def __init__(self, gates: list[threading.Event], chunks_per_gate: int = 1) -> None:
        self._gates = gates
        self._chunks_per_gate = chunks_per_gate
        self.stream_calls = 0
        self._lock = threading.Lock()

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ):
        with self._lock:
            self.stream_calls += 1
        for gate in self._gates:
            if not gate.wait(timeout=15):
                raise AssertionError("测试闸门未在超时前放行。")
            for _ in range(self._chunks_per_gate):
                yield StreamChunk(kind="delta", delta="块")


@pytest.fixture
def sqlite_app(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _send(
    client: TestClient, conversation_id: str, content: str
) -> dict[str, Any]:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _assistant(client: TestClient, conversation_id: str) -> dict[str, Any]:
    projection = client.get(
        f"/chat/conversations/{conversation_id}"
    ).json()
    assistant = [m for m in projection["messages"] if m["role"] == "assistant"]
    assert assistant, "应存在助手消息"
    return assistant[0]


def _wait_streaming(
    client: TestClient, conversation_id: str, *, timeout: float = 5.0
) -> None:
    """轮询直到运行进入 streaming（生成真正开始）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assistant = _assistant(client, conversation_id)
        if assistant["status"] == "streaming":
            return
        time.sleep(0.02)
    raise AssertionError("运行未在超时前进入 streaming。")


def test_50_random_switch_refresh_during_generation_no_stream_interrupted(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """50 轮生成，期间随机切换/刷新，全部收敛 done 且无 stream_interrupted。

    修复前（旧实现，issue 02 前）：断开/切换会话稳定得到
    stream_interrupted；修复后运行与页面/SSE 生命周期解耦，本压力循环
    必须每轮得到单一 done。
    """
    _register(client)
    rng = random.Random(STRESS_SEED)
    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    try:
        switch_ops = 0
        for round_no in range(STRESS_ROUNDS):
            gates = [threading.Event(), threading.Event()]
            adapter = _GatedSlowAdapter(gates, chunks_per_gate=2)
            sqlite_app.state.chat_service._gateway = _gateway_with(adapter)

            conversation_id = _create_conversation(client)
            created = _send(client, conversation_id, f"压力轮次 {round_no}")
            message_id = created["assistant_message"]["message_id"]

            # 放行首批输出，等生成真正开始
            gates[0].set()
            _wait_streaming(client, conversation_id)

            # 生成期间随机 1-3 次切换/刷新，每次操作后原运行仍为 streaming
            for _ in range(rng.randint(1, 3)):
                switch_ops += 1
                if rng.random() < 0.5:
                    # 切换：打开另一会话（新建 + 读取投影），原运行不受影响
                    other = _create_conversation(client)
                    assert client.get(
                        f"/chat/conversations/{other}"
                    ).status_code == 200
                else:
                    # 刷新：重新 GET 原会话投影（模拟页面重开）
                    pass
                assistant = _assistant(client, conversation_id)
                assert assistant["status"] == "streaming", (
                    f"轮次 {round_no} 第 {switch_ops} 次操作后运行不得中断"
                )

            # 放行末闸门：运行收敛为单一 done
            gates[1].set()
            events = generation_helpers["subscribe"](
                client, conversation_id, message_id
            )
            kinds = [name for name, _ in events]
            assert kinds[-1] == "done", f"轮次 {round_no} 未收敛 done: {kinds}"
            errors = [
                payload["error"]["code"]
                for name, payload in events
                if name == "error"
            ]
            assert not errors, f"轮次 {round_no} 出现错误事件: {errors}"

            final = _assistant(client, conversation_id)
            assert final["status"] == "done"
            assert final["active_run"] is None
            # 模型只调用一次：切换/刷新不产生重复发送或重复调用
            assert adapter.stream_calls == 1, (
                f"轮次 {round_no} 模型调用 {adapter.stream_calls} 次"
            )
    finally:
        stop_exec.set()
        exec_thread.join(timeout=5)

    assert switch_ops >= STRESS_ROUNDS, (
        f"切换/刷新操作 {switch_ops} 次（每轮至少 1 次）"
    )


def test_explicit_stop_is_the_only_allowed_interruption(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """对照：显式停止是唯一允许的中断来源（终态 error(stopped)）。

    验证「除显式停止外」边界：用户点击停止后运行收敛为 stopped，
    随后不会因迟到的生成事件被改写成 done 或 stream_interrupted。
    订阅与停止控制使用独立 TestClient（同一 httpx 客户端并发请求
    会互相干扰，导致订阅被误判为断开——见 test_chat_api 既有约定）。
    """
    from tests.chat.test_chat_api import _ProgrammableStreamAdapter

    _register(client)
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter(
            [StreamChunk(kind="delta", delta=f"块{i}") for i in range(30)],
            slow=True,
        )
    )
    control_client = TestClient(sqlite_app)
    control_client.cookies.set(
        "bridges_session", client.cookies.get("bridges_session")
    )
    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    try:
        conversation_id = _create_conversation(client)
        created = _send(client, conversation_id, "显式停止的对照")
        message_id = created["assistant_message"]["message_id"]

        # 等待生成真正产出内容（避免停止抢在执行器领取前），再显式停止
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            assistant = _assistant(control_client, conversation_id)
            if assistant["content"]:
                break
            time.sleep(0.05)

        response = control_client.post(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
        )
        assert response.status_code == 200, response.text
        # 只有显式停止才产生 stopped；停止在 2 秒内可见
        assert response.json()["message"]["status"] == "stopped"

        events = generation_helpers["subscribe"](
            control_client, conversation_id, message_id, timeout=10.0
        )
        kinds = [name for name, _ in events]
        # 订阅流以 error(stopped) 终态结束，绝不会被迟到事件改写成 done
        assert any(
            name == "error" and payload["error"]["code"] == "stopped"
            for name, payload in events
        ), f"显式停止应收敛 error(stopped): {kinds}"
        assert "done" not in kinds, "停止后不得被改写成 done"
        assert not any(
            name == "error" and payload["error"]["code"] != "stopped"
            for name, payload in events
        ), f"停止路径不得出现其他错误: {kinds}"

        final = _assistant(control_client, conversation_id)
        assert final["status"] == "stopped"
        assert final["active_run"] is None
        assert final["content"], "已接收正文保留"
    finally:
        stop_exec.set()
        exec_thread.join(timeout=5)
