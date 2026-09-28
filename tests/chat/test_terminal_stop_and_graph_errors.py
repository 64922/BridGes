"""停止与图异常的共同终态合同测试（.scratch/codebase/issues/02）。

覆盖票据验收标准：排队期停止不调用模型且消息/运行/持久终态事件一致；
流式停止保留已生成内容、迟到增量与迟到完成不能改写终态；重复停止不
新增有效终态事件；完成先提交时迟到停止与迟到异常不改写结果；图校验/
执行失败的中文原因、可重试性与思考摘要保持现行合同；停止接口兜底收
敛经共同 module 补齐终态事件与运行（订阅端不悬挂）。

竞争顺序由测试闸门与真实执行器线程控制（轮询等待终态，不依赖长时间
睡眠裁决胜负）；三种观察面（消息查询、运行查询、持久事件回放）都断言。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.chat.terminal import stopped_outcome
from bridges.chat.turn import done_thinking, finalize_message, initial_thinking
from bridges.config import get_settings
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _register,
    _send,
)


class _GatedAdapter:
    """闸门流式适配器：每块输出由测试闸门放行（真实流式调用计数）。"""

    def __init__(self, gates: list[threading.Event], chunks_per_gate: int = 2) -> None:
        self._gates = gates
        self._chunks_per_gate = chunks_per_gate
        self.stream_calls = 0
        self._lock = threading.Lock()

    def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]):
        with self._lock:
            self.stream_calls += 1
        for gate in self._gates:
            if not gate.wait(timeout=15):
                raise AssertionError("测试闸门未在超时前放行。")
            for _ in range(self._chunks_per_gate):
                yield StreamChunk(kind="delta", delta="块")


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _repo(app: Any) -> Any:
    return app.state.chat_service._repo  # noqa: SLF001 - 测试直读真实仓库


def _terminal_events(app: Any, account_id: str, run_id: str) -> list[Any]:
    return [
        event
        for event in _repo(app).list_generation_events(account_id, run_id, 0)
        if event.kind in {"done", "error"}
    ]


def _wait_run_terminal(app: Any, account_id: str, run_id: str) -> Any:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        run = _repo(app).get_generation_run(account_id, run_id)
        if run is not None and run.status in {"done", "failed", "stopped"}:
            return run
        time.sleep(0.05)
    raise AssertionError("运行未在超时前收敛到终态。")


# ---------------------------------------------------------------------------
# 排队期停止：不发起模型调用，订阅按现行事件合同结束
# ---------------------------------------------------------------------------


def test_queued_stop_never_calls_model_and_converges_shared_terminal(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """排队时停止：消息/运行/终态事件一次收敛，队列完成，模型零调用。"""
    account = _register(client)
    adapter = _GatedAdapter([threading.Event()])
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    # 正文带论文请求特征：若走到完成收尾，persist_result 会写入论文建议
    #（业务进度）；停止路径必须止步于停止，不额外推进。
    created = _send(client, conversation_id, "帮我找一篇强化学习的论文")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]

    _repo(sqlite_app).request_generation_stop(account["id"], run_id)
    generation_helpers["drive"](sqlite_app)

    assert adapter.stream_calls == 0, "排队期停止不得发起模型调用"
    message = _repo(sqlite_app).get_message(account["id"], message_id)
    assert message is not None and message.status == ChatMessageStatus.STOPPED
    assert message.error_code is None, "停止不是错误：消息不携带错误码"
    assert message.module_suggestion is None, "停止不得额外推进业务进度"
    run = _repo(sqlite_app).get_generation_run(account["id"], run_id)
    assert run is not None and run.status == "stopped"
    assert run.error_code is None

    events = _terminal_events(sqlite_app, account["id"], run_id)
    assert [event.kind for event in events] == ["error"]
    payload = events[0].payload
    assert payload["error"]["code"] == "stopped"
    assert payload["error"]["message"] == "生成已停止。"
    assert payload["error"]["retryable"] is True

    claim = sqlite_app.state.bridges_database.connection.execute(
        "SELECT status FROM task_claims WHERE task_key = ?", (f"generation:{run_id}",)
    ).fetchone()
    assert claim is not None and claim["status"] == "completed"

    events_replayed = generation_helpers["subscribe"](client, conversation_id, message_id)
    assert events_replayed[-1][0] == "error"
    assert events_replayed[-1][1]["error"]["code"] == "stopped"
    assert "done" not in [name for name, _ in events_replayed]


# ---------------------------------------------------------------------------
# 流式停止：保留已生成内容；迟到增量与迟到完成不能改写终态
# ---------------------------------------------------------------------------


def test_streaming_stop_keeps_content_and_rejects_late_writes(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """流式停止：正文保留；迟到增量、迟到完成、重复停止都改不了终态。"""
    account = _register(client)
    gates = [threading.Event(), threading.Event()]
    adapter = _GatedAdapter(gates)
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "生成到一半停止")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]

    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    try:
        gates[0].set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            probe = _repo(sqlite_app).get_message(account["id"], message_id)
            if probe is not None and probe.content:
                break
            time.sleep(0.05)
        stopped = client.post(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
        )
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["message"]["status"] == "stopped"
    finally:
        stop_exec.set()
        exec_thread.join(timeout=5)

    # 迟到增量：停止后放行剩余闸门，正文与终态事件不得被改写
    gates[1].set()
    message = _repo(sqlite_app).get_message(account["id"], message_id)
    assert message is not None and message.status == ChatMessageStatus.STOPPED
    assert message.content == "块块", "只保留停止前已收到的正文"
    run = _wait_run_terminal(sqlite_app, account["id"], run_id)
    assert run.status == "stopped"
    assert [event.kind for event in _terminal_events(sqlite_app, account["id"], run_id)] == [
        "error"
    ]

    # 迟到完成：走回合编排完成收尾的同一个真实函数，持久守卫必须拒绝
    rows = finalize_message(
        _repo(sqlite_app),
        account["id"],
        message_id,
        status=ChatMessageStatus.DONE,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id="qwen3.7-plus-2026-05-26",
        run_lock_id=None,
        started=time.monotonic(),
        now=datetime.now(UTC),
        thinking=done_thinking(initial_thinking(ChatMode.COMPANION)),
    )
    assert rows == 0, "停止终态后迟到完成不得改写消息"
    assert _repo(sqlite_app).get_message(account["id"], message_id).status == (
        ChatMessageStatus.STOPPED
    )
    rows = _repo(sqlite_app).finalize_generation_run(
        account["id"],
        run_id,
        status="done",
        error_code=None,
        error_message=None,
        duration_ms=1,
        now=datetime.now(UTC),
    )
    assert rows == 0, "停止终态后迟到完成不得改写运行"
    assert _terminal_events(sqlite_app, account["id"], run_id)[0].payload["error"][
        "code"
    ] == "stopped"

    events = generation_helpers["subscribe"](client, conversation_id, message_id)
    assert events[-1][0] == "error"
    assert events[-1][1]["error"]["code"] == "stopped"
    assert "done" not in [name for name, _ in events]


# ---------------------------------------------------------------------------
# 完成先提交：迟到停止与迟到异常不改写已获准的结果
# ---------------------------------------------------------------------------


def test_completion_first_survives_late_stop_and_late_exception(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完成先提交：迟到停止返回原状态；终态后的迟到异常不追加矛盾事件。"""
    account = _register(client)

    class _BoomSuggestion:
        """persist_result 内的确定性同步点：成功收尾后抛未知异常。"""

        def __call__(self, content: str) -> str | None:
            raise RuntimeError("保存结果阶段炸了")

    adapter_calls = 0
    lock = threading.Lock()

    class _Counting:
        def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]):
            nonlocal adapter_calls
            with lock:
                adapter_calls += 1
            yield StreamChunk(kind="delta", delta="完整正文")

    sqlite_app.state.chat_service._gateway = _gateway_with(_Counting())  # noqa: SLF001
    monkeypatch.setattr(
        "bridges.chat.graph.detect_paper_suggestion", _BoomSuggestion()
    )
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "先完成再迟到异常")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]
    generation_helpers["drive"](sqlite_app)

    run = _wait_run_terminal(sqlite_app, account["id"], run_id)
    assert run.status == "done", "消息已完成的轮次不因迟到异常改成失败"
    assert run.error_code is None
    message = _repo(sqlite_app).get_message(account["id"], message_id)
    assert message is not None and message.status == ChatMessageStatus.DONE
    assert message.content == "完整正文"
    assert [event.kind for event in _terminal_events(sqlite_app, account["id"], run_id)] == [
        "done"
    ], "迟到异常不得追加矛盾终态事件"
    assert adapter_calls == 1

    # 迟到停止：消息已终态 → 幂等返回当前投影，不新增任何写入
    before = _repo(sqlite_app).get_message(account["id"], message_id)
    stopped = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
    )
    assert stopped.status_code == 200
    assert stopped.json()["message"]["status"] == "done"
    after = _repo(sqlite_app).get_message(account["id"], message_id)
    assert after.status == ChatMessageStatus.DONE
    assert after.updated_at == before.updated_at
    assert [event.kind for event in _terminal_events(sqlite_app, account["id"], run_id)] == [
        "done"
    ]
    events = generation_helpers["subscribe"](client, conversation_id, message_id)
    assert "error" not in [name for name, _ in events], "订阅回放不含矛盾终态事件"
    assert "done" in [name for name, _ in events]


# ---------------------------------------------------------------------------
# 图执行异常：节点位置、稳定码、中文原因与思考摘要保持现行合同
# ---------------------------------------------------------------------------


def test_graph_unknown_exception_converges_with_location_and_contract(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未知异常：中文原因带节点位置，事件载荷走统一派生规则，三面一致。"""
    account = _register(client, "2")

    class _Counting:
        stream_calls = 0

        def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]):
            type(self).stream_calls += 1  # pragma: no cover - 编译前即失败
            yield StreamChunk(kind="delta", delta="不应出现")

    sqlite_app.state.chat_service._gateway = _gateway_with(_Counting())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "编译阶段炸掉")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]

    def _boom(run: Any) -> tuple[list[dict[str, str]], dict[str, object]]:
        raise RuntimeError("上下文编译不可用")

    monkeypatch.setattr(sqlite_app.state.chat_service, "compile_turn_context", _boom)
    generation_helpers["drive"](sqlite_app)

    node_message = (
        "在「编译上下文」步骤失败：生成过程出现内部错误（RuntimeError），请重试。可点击重试。"
    )
    message = _repo(sqlite_app).get_message(account["id"], message_id)
    assert message is not None and message.status == ChatMessageStatus.ERROR
    assert message.error_code == "internal_error"
    assert message.error_message == node_message
    assert message.thinking is not None
    assert message.thinking["quality"] == ["生成过程出现内部错误，请重试。"]

    run = _wait_run_terminal(sqlite_app, account["id"], run_id)
    assert run.status == "failed"
    assert run.error_code == message.error_code
    assert run.error_message == message.error_message
    assert run.current_node == "compile_context"
    assert run.duration_ms is not None and run.duration_ms >= 1

    events = _terminal_events(sqlite_app, account["id"], run_id)
    assert [event.kind for event in events] == ["error"]
    payload = events[0].payload
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["message"] == "生成过程出现内部错误，请重试。"
    assert payload["error"]["retryable"] is True

    # 刷新后与即时结果一致（同一稳定码与中文原因）
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [m for m in projection["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == "error"
    assert assistant["error_code"] == "internal_error"
    assert assistant["error_message"] == node_message
    events_replayed = generation_helpers["subscribe"](client, conversation_id, message_id)
    assert events_replayed[-1][0] == "error"


# ---------------------------------------------------------------------------
# 停止接口兜底收敛：经共同 module 补齐终态事件与运行，订阅端不悬挂
# ---------------------------------------------------------------------------


def test_stop_endpoint_fallback_converges_run_and_event_without_executor(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """无执行器时停止：兜底收敛一次补齐消息/终态事件/运行，订阅能结束。"""
    account = _register(client, "3")
    adapter = _GatedAdapter([])
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "执行器不在场")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]

    stopped = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
    )
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["message"]["status"] == "stopped"

    assert adapter.stream_calls == 0
    message = _repo(sqlite_app).get_message(account["id"], message_id)
    assert message is not None and message.status == ChatMessageStatus.STOPPED
    run = _repo(sqlite_app).get_generation_run(account["id"], run_id)
    assert run is not None and run.status == "stopped"
    events = _terminal_events(sqlite_app, account["id"], run_id)
    assert [event.kind for event in events] == ["error"]
    assert events[0].payload["error"]["code"] == "stopped"

    events_replayed = generation_helpers["subscribe"](client, conversation_id, message_id)
    assert events_replayed[-1][0] == "error"
    assert events_replayed[-1][1]["error"]["code"] == "stopped"


def test_stop_endpoint_fallback_cancels_inflight_search_projection(
    sqlite_app: Any, client: TestClient
) -> None:
    """兜底收敛的停止投影：已触发的公网搜索收敛为取消，不残留 loading。"""
    from bridges.web_search.contracts import WebSearchProjection, WebSearchStatus

    account = _register(client, "4")
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "带搜索的停止")
    message_id = created["assistant_message"]["message_id"]
    repo = _repo(sqlite_app)
    inflight = WebSearchProjection(
        status=WebSearchStatus.LOADING,
        trigger_reason="问题需要联网核实。",
        query_summary="带搜索的停止",
    )
    repo.update_message_web_search(
        account["id"],
        message_id,
        inflight.model_dump(mode="json"),
        datetime.now(UTC),
    )

    projection = sqlite_app.state.chat_service.stop_generation(
        account["id"], conversation_id, message_id
    )
    assert projection.status == ChatMessageStatus.STOPPED
    record = repo.get_message(account["id"], message_id)
    assert record is not None
    assert record.web_search is not None
    assert record.web_search["status"] == WebSearchStatus.CANCELLED.value
    assert record.web_search["error_code"] == "web_search_cancelled"


# ---------------------------------------------------------------------------
# module 层：停止收尾幂等，重复停止不新增有效终态事件
# ---------------------------------------------------------------------------


def test_repeated_stopped_converge_replays_without_new_terminal_event(
    sqlite_app: Any, client: TestClient
) -> None:
    """同一停止收尾执行两次：第二次为幂等重放，事件数与结果不变。"""
    account = _register(client, "5")
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "重复停止收尾")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]
    terminal = sqlite_app.state.chat_service.terminal

    first = terminal.converge(account["id"], run_id, message_id, fallback=stopped_outcome())
    second = terminal.converge(account["id"], run_id, message_id, fallback=stopped_outcome())

    assert first.message_committed is True
    assert first.event_seq is not None
    assert first.run_committed is True
    assert second.replayed is True
    message = _repo(sqlite_app).get_message(account["id"], message_id)
    assert message is not None and message.status == ChatMessageStatus.STOPPED
    assert message.error_code is None
    events = _terminal_events(sqlite_app, account["id"], run_id)
    assert [event.kind for event in events] == ["error"]
    assert events[0].payload["error"]["code"] == "stopped"
    run = _repo(sqlite_app).get_generation_run(account["id"], run_id)
    assert run is not None and run.status == "stopped"
