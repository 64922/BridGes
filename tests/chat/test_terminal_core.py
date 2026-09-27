"""生成终态 module 合同测试（.scratch/codebase/issues/01）。

覆盖票据验收标准：普通成功/失败轮次的消息、运行与持久终态事件一致；
同一收尾请求重复执行不改写已提交结果、不重复追加有效终态；对真实
SQLite 路径注入提交故障后不暴露互相矛盾的终态，且重放同一收尾操作
可恢复；同账户下不同运行与跨账户之间不串写；既有失败合同（稳定错误码、
中文原因、可重试性）与订阅结束语义不变。

全部使用真实临时 SQLite 与确定性模型适配器，不访问外部网络。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import RateLimitError, StreamChunk
from bridges.chat.terminal import GenerationTerminal, TerminalConvergeError
from bridges.chat.turn import done_thinking, finalize_message, initial_thinking
from bridges.config import get_settings
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from bridges.storage.errors import StorageError
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _register,
    _send,
)


class _CountingAdapter:
    """确定性适配器 + 模型调用计数（验证刷新不重复调用模型）。"""

    def __init__(self, chunks: list[str] | None = None) -> None:
        self._chunks = chunks or ["真实", "正文"]
        self.stream_calls = 0
        self._lock = threading.Lock()

    def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]):
        with self._lock:
            self.stream_calls += 1
        for chunk in self._chunks:
            yield StreamChunk(kind="delta", delta=chunk)


class _FailAfterChunkAdapter:
    """先输出正文再失败：失败轮次必须保留已生成内容。"""

    def __init__(self) -> None:
        self.stream_calls = 0

    def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]):
        self.stream_calls += 1
        yield StreamChunk(kind="delta", delta="已生成的正文")
        raise RateLimitError("slow down")


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


def _terminal(app: Any) -> GenerationTerminal:
    service = app.state.chat_service
    return GenerationTerminal(service._repo, projection=service.message_projection)


def _events(app: Any, account_id: str, run_id: str) -> list[Any]:
    return _repo(app).list_generation_events(account_id, run_id, 0)


def _terminal_kinds(app: Any, account_id: str, run_id: str) -> list[str]:
    return [
        event.kind
        for event in _events(app, account_id, run_id)
        if event.kind in {"done", "error"}
    ]


def _commit_success_message(repo: Any, account_id: str, message_id: str) -> None:
    """模拟回合编排已提交成功消息（与成功路径调用同一收尾函数）。"""
    finalize_message(
        repo,
        account_id,
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


# ---------------------------------------------------------------------------
# 普通成功与普通失败：消息、运行与持久终态事件一致
# ---------------------------------------------------------------------------


def test_success_round_keeps_content_lock_and_single_terminal_event(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """普通成功轮次：正文、运行锁、消息/运行/事件三者一致，刷新不重复生成。"""
    account = _register(client, "61")
    adapter = _CountingAdapter(["完整", "正文"])
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "普通成功轮次")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]

    generation_helpers["drive"](sqlite_app)

    message = _repo(sqlite_app).get_message(account["id"], message_id)
    assert message is not None
    assert message.status == ChatMessageStatus.DONE
    assert message.content == "完整正文"
    assert message.model_id == "qwen3.7-plus-2026-05-26"
    assert message.run_lock_id, "成功轮次必须保留真实模型运行锁"
    run = _repo(sqlite_app).get_generation_run(account["id"], run_id)
    assert run is not None
    assert run.status == "done"
    assert run.error_code is None
    assert run.duration_ms is not None and run.duration_ms >= 1
    assert _terminal_kinds(sqlite_app, account["id"], run_id) == ["done"]

    events = generation_helpers["subscribe"](client, conversation_id, message_id)
    content_events = [name for name, _ in events if name not in ("stage", "node")]
    assert content_events[-1] == "done"
    assert "error" not in content_events
    done = [payload for name, payload in events if name == "done"][-1]
    assert done["message"]["run_lock_id"] == message.run_lock_id

    # 刷新页面：不新增消息、不重复调用模型
    for _ in range(2):
        history = client.get(f"/chat/conversations/{conversation_id}").json()
    assert [(m["role"], m["status"]) for m in history["messages"]] == [
        ("user", "done"),
        ("assistant", "done"),
    ]
    assert adapter.stream_calls == 1


def test_failure_round_keeps_content_and_terminal_contract(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """普通失败轮次：保留已生成内容，消息/运行/事件共享同一稳定错误合同。"""
    account = _register(client, "62")
    adapter = _FailAfterChunkAdapter()
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "普通失败轮次")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]

    generation_helpers["drive"](sqlite_app)

    message = _repo(sqlite_app).get_message(account["id"], message_id)
    assert message is not None
    assert message.status == ChatMessageStatus.ERROR
    assert message.content == "已生成的正文"
    assert message.error_code == "rate_limit"
    run = _repo(sqlite_app).get_generation_run(account["id"], run_id)
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == message.error_code
    assert run.error_message == message.error_message

    # 订阅能结束，且终态事件与消息合同一致（稳定码/中文原因/可重试性）
    events = generation_helpers["subscribe"](client, conversation_id, message_id)
    content_events = [name for name, _ in events if name not in ("stage", "node")]
    assert content_events[-1] == "error"
    error = [payload for name, payload in events if name == "error"][-1]["error"]
    assert error["code"] == "rate_limit"
    assert "限流" in error["message"]
    assert error["retryable"] is True
    assert _terminal_kinds(sqlite_app, account["id"], run_id) == ["error"]


# ---------------------------------------------------------------------------
# 相同收尾请求重复执行：不改写已提交结果、不重复追加有效终态
# ---------------------------------------------------------------------------


def test_repeated_converge_replays_committed_result(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """同一收尾请求执行两次：第二次为幂等重放，结果与事件数不变。"""
    account = _register(client, "63")
    adapter = _CountingAdapter(["提交", "一次"])
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "重复收尾")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]
    generation_helpers["drive"](sqlite_app)

    before = _repo(sqlite_app).get_message(account["id"], message_id)
    events_before = _events(sqlite_app, account["id"], run_id)
    terminal = _terminal(sqlite_app)
    first = terminal.converge(
        account["id"], run_id, message_id, run_duration_ms=before.duration_ms
    )
    second = terminal.converge(
        account["id"], run_id, message_id, run_duration_ms=before.duration_ms
    )
    for commit in (first, second):
        assert commit.replayed is True
        assert commit.message_committed is False
        assert commit.event_seq is None
        assert commit.run_committed is False

    after = _repo(sqlite_app).get_message(account["id"], message_id)
    assert after.status == before.status
    assert after.content == before.content
    assert after.run_lock_id == before.run_lock_id
    assert after.model_id == before.model_id
    assert after.duration_ms == before.duration_ms
    run = _repo(sqlite_app).get_generation_run(account["id"], run_id)
    assert run.status == "done"
    assert [event.seq for event in _events(sqlite_app, account["id"], run_id)] == [
        event.seq for event in events_before
    ]
    assert adapter.stream_calls == 1


# ---------------------------------------------------------------------------
# 提交故障注入：不暴露矛盾终态，重放同一收尾可恢复
# ---------------------------------------------------------------------------


def test_run_commit_fault_leaves_recoverable_state_and_replays(
    sqlite_app: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """运行提交步骤故障：成功轮次不会变成失败，重放收尾补齐运行终态。"""
    account = _register(client, "64")
    adapter = _CountingAdapter(["故障", "之前"])
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "运行提交故障")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]
    repo = _repo(sqlite_app)

    def _unwritable(*args: Any, **kwargs: Any) -> int:
        raise StorageError("数据库当前不可写，请稍后重试或检查数据目录权限。")

    monkeypatch.setattr(repo, "finalize_generation_run", _unwritable)
    sqlite_app.state.generation_executor.run_tick()

    # 残留形态：消息与终态事件已提交，运行仍未收敛——不矛盾、可修复
    message = repo.get_message(account["id"], message_id)
    assert message is not None and message.status == ChatMessageStatus.DONE
    assert message.run_lock_id
    assert _terminal_kinds(sqlite_app, account["id"], run_id) == ["done"]
    run = repo.get_generation_run(account["id"], run_id)
    assert run is not None and run.status == "running", "运行不得被改成失败"
    claim_status = sqlite_app.state.bridges_database.connection.execute(
        "SELECT status FROM task_claims WHERE task_key = ?",
        (f"generation:{run_id}",),
    ).fetchone()["status"]
    assert claim_status == "claimed", "结果不可恢复前不得确认队列完成"

    # 重放同一收尾：补齐运行终态，不改写已提交结果、不追加第二条终态事件
    monkeypatch.undo()
    commit = _terminal(sqlite_app).converge(
        account["id"], run_id, message_id, run_duration_ms=message.duration_ms
    )
    assert commit.message_committed is False
    assert commit.event_seq is None
    assert commit.run_committed is True
    assert commit.outcome.status == ChatMessageStatus.DONE
    run = repo.get_generation_run(account["id"], run_id)
    assert run.status == "done"
    assert run.error_code is None
    assert _terminal_kinds(sqlite_app, account["id"], run_id) == ["done"]
    assert repo.get_message(account["id"], message_id).content == "故障之前"


def test_event_commit_fault_leaves_recoverable_state_and_replays(
    sqlite_app: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """终态事件写入故障：消息已终态但订阅无终态事件，重放补齐且只补一次。"""
    account = _register(client, "65")
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "事件提交故障")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]
    repo = _repo(sqlite_app)
    # 回合编排已提交成功消息（真实路径的同一收尾函数），随后终态事件写入失败
    _commit_success_message(repo, account["id"], message_id)

    def _unwritable(*args: Any, **kwargs: Any) -> int:
        raise StorageError("数据库当前不可写，请稍后重试或检查数据目录权限。")

    monkeypatch.setattr(repo, "append_generation_event", _unwritable)
    with pytest.raises(StorageError):
        _terminal(sqlite_app).converge(account["id"], run_id, message_id)

    assert _terminal_kinds(sqlite_app, account["id"], run_id) == []
    run = repo.get_generation_run(account["id"], run_id)
    assert run is not None and run.status == "queued"

    monkeypatch.undo()
    commit = _terminal(sqlite_app).converge(account["id"], run_id, message_id)
    assert commit.message_committed is False
    assert commit.event_seq is not None
    assert commit.run_committed is True
    assert commit.outcome.status == ChatMessageStatus.DONE

    events = _events(sqlite_app, account["id"], run_id)
    terminal_events = [event for event in events if event.kind in {"done", "error"}]
    assert [event.kind for event in terminal_events] == ["done"]
    assert terminal_events[0].payload["message"]["status"] == "done"
    assert repo.get_generation_run(account["id"], run_id).status == "done"
    # 再次收尾：结果与事件数都不变（幂等重放）
    replay = _terminal(sqlite_app).converge(account["id"], run_id, message_id)
    assert replay.replayed is True
    assert _terminal_kinds(sqlite_app, account["id"], run_id) == ["done"]


# ---------------------------------------------------------------------------
# 收尾输入与隔离：不猜测结果，不跨运行、跨账户串写
# ---------------------------------------------------------------------------


def test_converge_refuses_to_guess_for_streaming_message(
    sqlite_app: Any, client: TestClient
) -> None:
    """消息仍在生成且没有兜底结果时拒绝收尾：不得凭空发明终态。"""
    account = _register(client, "66")
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "未结束的轮次")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]

    with pytest.raises(TerminalConvergeError):
        _terminal(sqlite_app).converge(account["id"], run_id, message_id)

    repo = _repo(sqlite_app)
    assert repo.get_message(account["id"], message_id).status == ChatMessageStatus.STREAMING
    assert repo.get_generation_run(account["id"], run_id).status == "queued"
    assert _terminal_kinds(sqlite_app, account["id"], run_id) == []


def test_converge_isolates_runs_and_accounts(
    sqlite_app: Any, client: TestClient
) -> None:
    """同一账户的不同运行与不同账户的运行都不得被本次收尾改写。"""
    account = _register(client, "67")
    conversation_id = _create_conversation(client)
    first = _send(client, conversation_id, "被收尾的运行")
    other_conversation_id = _create_conversation(client)
    untouched = _send(client, other_conversation_id, "同账户另一运行")

    other_client = TestClient(sqlite_app)
    other_account = _register(other_client, "68")
    other_conversation = _create_conversation(other_client)
    other = _send(other_client, other_conversation, "另一账户的运行")

    repo = _repo(sqlite_app)
    _commit_success_message(repo, account["id"], first["assistant_message"]["message_id"])
    commit = _terminal(sqlite_app).converge(
        account["id"], first["run_id"], first["assistant_message"]["message_id"]
    )
    assert commit.run_committed is True
    assert repo.get_generation_run(account["id"], first["run_id"]).status == "done"

    # 同账户另一运行：消息、运行与事件都不受影响
    assert (
        repo.get_message(account["id"], untouched["assistant_message"]["message_id"]).status
        == ChatMessageStatus.STREAMING
    )
    assert repo.get_generation_run(account["id"], untouched["run_id"]).status == "queued"
    assert _terminal_kinds(sqlite_app, account["id"], untouched["run_id"]) == []
    # 另一账户的运行：同样不受影响
    assert (
        repo.get_message(
            other_account["id"], other["assistant_message"]["message_id"]
        ).status
        == ChatMessageStatus.STREAMING
    )
    assert repo.get_generation_run(other_account["id"], other["run_id"]).status == "queued"
    assert _terminal_kinds(sqlite_app, other_account["id"], other["run_id"]) == []


def test_stopped_message_derives_stopped_terminal(
    sqlite_app: Any, client: TestClient
) -> None:
    """停止消息派生停止运行：错误事件沿用既有 error 种类与文案，运行不记错误码。"""
    account = _register(client, "69")
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "停止的轮次")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]
    repo = _repo(sqlite_app)
    finalize_message(
        repo,
        account["id"],
        message_id,
        status=ChatMessageStatus.STOPPED,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id=None,
        run_lock_id=None,
        started=time.monotonic(),
        now=datetime.now(UTC),
        thinking=done_thinking(initial_thinking(ChatMode.COMPANION)),
    )

    commit = _terminal(sqlite_app).converge(account["id"], run_id, message_id)

    assert commit.outcome.status == ChatMessageStatus.STOPPED
    run = repo.get_generation_run(account["id"], run_id)
    assert run.status == "stopped"
    assert run.error_code is None
    events = [
        event
        for event in _events(sqlite_app, account["id"], run_id)
        if event.kind in {"done", "error"}
    ]
    assert [event.kind for event in events] == ["error"]
    assert events[0].payload["error"]["code"] == "stopped"
    assert events[0].payload["error"]["message"] == "生成已停止。"
    assert events[0].payload["error"]["retryable"] is True
