"""Issue 03 恢复与回放测试：跨对象故障窗口、失联恢复、删除竞态与游标回放。

覆盖验收标准：

- 执行器失联后仍按既有上限领取恢复；达到上限后消息与运行收敛为一致的
  可重试失败，并保留部分正文；
- 覆盖消息终态写入后、事件持久化后、运行提交与队列确认附近的真实故障
  窗口：恢复不重新调用模型、不创建助手消息、不重复增加有效终态事件；
- 两个执行器竞争领取或修复同一运行不产生冲突终态，恢复不破坏在途模型锁；
- 从不同已保存游标重新订阅都能回放并结束，游标单调，完成/失败/停止各自
  保持原传输合同；
- 运行或会话被合法删除时，迟到恢复不复活数据、不跨账户读取，也不产生
  永久悬挂队列项。

故障注入在真实临时 SQLite 上完成：构造「执行器已领取后失联」的状态后，
在**同一数据库文件上重建仓库与服务**再验证恢复，而不是同一内存实例自证。
模型适配器全部是确定性脚本适配器，不访问网络。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import RateLimitError, StreamChunk
from bridges.chat.repository import ConversationRepository
from bridges.chat.run_executor import GenerationRunExecutor
from bridges.chat.service import ChatService
from bridges.config import get_settings
from bridges.contracts.chat import ChatMessageStatus
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _parse_sse,
    _register,
    _subscribe_all,
)

#: 终态事件的传输种类（完成/失败/停止的合同面）。
TERMINAL_KINDS = {"done", "error"}


class _ScriptedAdapter:
    """确定性脚本适配器：按脚本产块，可在指定位置阻塞或抛出连接错误。"""

    def __init__(
        self,
        chunks: tuple[str, ...] = ("块",),
        *,
        block_before: int | None = None,
        release: threading.Event | None = None,
        error: Exception | None = None,
        error_at: int = 0,
    ) -> None:
        self._chunks = chunks
        self._block_before = block_before
        self._release = release or threading.Event()
        self._error = error
        self._error_at = error_at
        self.stream_calls = 0
        self._lock = threading.Lock()

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ):
        with self._lock:
            self.stream_calls += 1
        for index, chunk in enumerate(self._chunks):
            if self._error is not None and index == self._error_at:
                raise self._error
            if self._block_before is not None and index == self._block_before:
                assert self._release.wait(timeout=15), "测试闸门未在超时前放行。"
            yield StreamChunk(kind="delta", delta=chunk)


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


# ---------------------------------------------------------------------------
# 助手：发送、故障注入、跨进程重建、观察面
# ---------------------------------------------------------------------------


def _register_other(sqlite_app: Any, tag: str) -> tuple[TestClient, dict[str, Any]]:
    """在独立客户端上注册另一个账户（同一客户端的会话会被注册覆盖）。"""
    other = TestClient(sqlite_app)
    return other, _register(other, tag)


def _send(client: TestClient, conversation_id: str) -> dict[str, Any]:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages", json={"content": "你好"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _past() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=1)


def _rebuild(
    db_path: Path, adapter: Any, *, worker_name: str = "recovered-executor"
) -> tuple[ChatService, BridgesDatabase, GenerationRunExecutor]:
    """在同一数据库文件上重建仓库、服务与执行器（模拟进程重启）。"""
    database = BridgesDatabase(db_path)
    database.initialize()
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=_gateway_with(adapter),
    )
    executor = GenerationRunExecutor(
        service,
        database,
        worker_name=worker_name,
        poll_interval=0.01,
    )
    return service, database, executor


def _expire_queue_lease(database: BridgesDatabase, run_id: str) -> None:
    """把生成队列行的领取租约拨到过去（worker 死后无人续租）。"""
    database.connection.execute(
        "UPDATE task_claims SET lease_expires_at = ? WHERE task_key = ?",
        (_past().isoformat(timespec="seconds"), f"generation:{run_id}"),
    )


def _claim_row(database: BridgesDatabase, run_id: str) -> str:
    row = database.connection.execute(
        "SELECT status FROM task_claims WHERE task_key = ?",
        (f"generation:{run_id}",),
    ).fetchone()
    return str(row["status"]) if row is not None else ""


def _events(repo: ConversationRepository, account_id: str, run_id: str) -> list[Any]:
    return repo.list_generation_events(account_id, run_id, 0)


def _terminal_events(
    repo: ConversationRepository, account_id: str, run_id: str
) -> list[Any]:
    return [event for event in _events(repo, account_id, run_id) if event.kind in TERMINAL_KINDS]


def _last_non_node(kinds: list[str]) -> str:
    """最后一条非 node 事件种类（persist_result 节点进度会排在终态之后）。"""
    tail = [kind for kind in kinds if kind != "node"]
    assert tail, "订阅回放不得为空"
    return tail[-1]


def _start_lost_run(
    sqlite_app: Any,
    client: TestClient,
    account: dict[str, Any],
    conversation_id: str,
    *,
    persist_events: bool,
) -> tuple[str, str]:
    """构造「执行器已领取后失联」的真实状态并返回 (run_id, message_id)。

    第一次尝试由本进程按真实领取契约写入（租约已过期 = 持有者随后死亡）。
    之后由真实图入口执行一轮：回合编排提交消息终态（正文、思考摘要与模型
    运行锁），运行进度节点回填锁。``persist_events=False`` 表示事件通道在
    收尾前中断——消息终态已提交而终态事件与运行尚未提交的故障窗口。
    """
    created = _send(client, conversation_id)
    run_id = created["run_id"]
    message_id = created["assistant_message"]["message_id"]
    service = sqlite_app.state.chat_service
    executor = sqlite_app.state.generation_executor
    repo = service._repo
    assert (
        repo.claim_generation_run(
            account["id"],
            run_id,
            owner="dead-worker",
            lease_expires_at=_past(),
            max_attempts=2,
        )
        > 0
    )
    run = repo.get_generation_run(account["id"], run_id)
    assert run is not None
    if persist_events:
        service.run_graph_turn(
            run,
            on_event=lambda event: executor._persist_event(  # noqa: SLF001 - 测试复用真实事件通道
                account["id"], run_id, message_id, event
            ),
            stop_event=None,
        )
    else:
        service.run_graph_turn(run, on_event=lambda event: None, stop_event=None)
    return run_id, message_id


# ---------------------------------------------------------------------------
# 故障矩阵：消息终态 / 事件 / 运行提交 / 队列确认
# ---------------------------------------------------------------------------


def test_message_commit_window_recovery_repairs_without_second_model_call(
    sqlite_app: Any, client: TestClient, tmp_path: Path
) -> None:
    """消息终态已提交、事件与运行未提交：恢复补齐终态且不重跑模型。

    故障窗口 = 回合编排已把消息（正文 + 运行锁 + 思考摘要）收敛到 done，
    事件通道与运行提交尚未发生，持有者对随后死亡。恢复必须：以消息为准
    补齐恰一条 done 终态事件、把运行收敛到 done、保留运行锁，并确认队列；
    绝不第二次调用模型，也不新建助手消息。
    """
    account = _register(client)
    adapter = _ScriptedAdapter(("完整", "正文"))
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    run_id, message_id = _start_lost_run(
        sqlite_app, client, account, conversation_id, persist_events=False
    )
    repo = sqlite_app.state.chat_service._repo  # noqa: SLF001

    # 故障窗口的中间状态：消息终态已提交，事件与运行未提交
    committed = repo.get_message(account["id"], message_id)
    assert committed is not None
    assert committed.status == ChatMessageStatus.DONE
    assert committed.content == "完整正文"
    assert committed.run_lock_id, "成功轮次必须携带真实模型运行锁"
    assert _terminal_events(repo, account["id"], run_id) == []
    intermediate = repo.get_generation_run(account["id"], run_id)
    assert intermediate is not None and intermediate.status == "running"
    assert adapter.stream_calls == 1
    lock_id = committed.run_lock_id

    # 进程重启：同一数据库文件上重建仓库/服务/执行器后恢复
    _service, restarted_db, executor = _rebuild(tmp_path / "bridges.db", adapter)
    _expire_queue_lease(restarted_db, run_id)
    executor.run_tick()

    assert adapter.stream_calls == 1, "恢复已提交结果不得再次调用模型"
    recovered = repo.get_message(account["id"], message_id)
    assert recovered is not None
    assert recovered.status == ChatMessageStatus.DONE
    assert recovered.content == "完整正文"
    assert recovered.run_lock_id == lock_id
    terminal = _terminal_events(repo, account["id"], run_id)
    assert [event.kind for event in terminal] == ["done"]
    run = repo.get_generation_run(account["id"], run_id)
    assert run is not None
    assert run.status == "done"
    assert run.error_code is None
    assert run.model_lock_id == lock_id, "恢复必须保留已提交的模型运行锁"
    assert _claim_row(restarted_db, run_id) == "completed"
    # 订阅回放：终态事件已可读，回放完即结束（节点进度可排在终态之后）
    subscribed = _subscribe_all(client, conversation_id, message_id)
    assert _last_non_node([name for name, _payload in subscribed]) == "done"
    assert len(repo.list_messages(account["id"], conversation_id)) == 2


def test_reap_of_committed_result_keeps_done_without_conflicting_event(
    sqlite_app: Any, client: TestClient, tmp_path: Path
) -> None:
    """恢复预算耗尽时收尸已提交结果的运行：不得追加矛盾的失败终态。

    中间状态 = 消息与 done 终态事件都已提交、运行仍 running（收尾中断），
    且两次尝试都已失联（达到恢复上限）。收尸只补齐运行终态，保持 done、
    恰一条终态事件、无错误码；队列行在领取后确认完成，不做永久悬挂。
    """
    account = _register(client)
    adapter = _ScriptedAdapter(("完成", "的回答"))
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    run_id, message_id = _start_lost_run(
        sqlite_app, client, account, conversation_id, persist_events=True
    )
    repo = sqlite_app.state.chat_service._repo  # noqa: SLF001

    committed = repo.get_message(account["id"], message_id)
    assert committed is not None and committed.status == ChatMessageStatus.DONE
    assert [event.kind for event in _terminal_events(repo, account["id"], run_id)] == ["done"]

    # 第二次尝试同样失联：恢复预算耗尽（租约过期，租约重建后仍为旧值）
    repo.renew_generation_lease(account["id"], run_id, _past())
    assert (
        repo.claim_generation_run(
            account["id"],
            run_id,
            owner="dead-worker-2",
            lease_expires_at=_past(),
            max_attempts=2,
        )
        > 0
    )
    _service, restarted_db, executor = _rebuild(tmp_path / "bridges.db", adapter)

    executor.run_tick()  # 收尸

    assert adapter.stream_calls == 1, "收尸不得调用模型"
    message = repo.get_message(account["id"], message_id)
    assert message is not None
    assert message.status == ChatMessageStatus.DONE
    assert message.content == "完成的回答"
    terminal = _terminal_events(repo, account["id"], run_id)
    assert [event.kind for event in terminal] == ["done"], "不得追加第二个矛盾终态"
    run = repo.get_generation_run(account["id"], run_id)
    assert run is not None
    assert run.status == "done"
    assert run.error_code is None
    # 收尸幂等：重复一轮不产生任何新写入
    executor.run_tick()
    assert [event.kind for event in _terminal_events(repo, account["id"], run_id)] == ["done"]
    assert repo.get_generation_run(account["id"], run_id).status == "done"  # type: ignore[union-attr]

    # 队列行仍会被领取一次并按「已终态」确认完成（不永久悬挂）
    _expire_queue_lease(restarted_db, run_id)
    executor.run_tick()
    assert _claim_row(restarted_db, run_id) == "completed"
    assert [event.kind for event in _terminal_events(repo, account["id"], run_id)] == ["done"]
    replayed = _terminal_events(repo, account["id"], run_id)
    assert [event.kind for event in replayed] == ["done"]
    subscribed = _subscribe_all(client, conversation_id, message_id)
    assert _last_non_node([name for name, _payload in subscribed]) == "done"


def test_lost_worker_recovers_within_cap_then_converges_with_partial_content(
    sqlite_app: Any, client: TestClient, tmp_path: Path
) -> None:
    """失联（消息仍在生成中）：按既有上限领取恢复，达上限后收敛为可重试失败。

    正文在失联前已落库，收尸后必须保留；消息与运行收敛为同一可重试错误、
    恰一条 error 终态事件；被释放的旧执行器迟到收尾不得改写终态。
    """
    account = _register(client)
    release = threading.Event()
    adapter = _ScriptedAdapter(("部分", "后续"), block_before=1, release=release)
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id)
    run_id = created["run_id"]
    message_id = created["assistant_message"]["message_id"]
    repo = sqlite_app.state.chat_service._repo  # noqa: SLF001
    database = sqlite_app.state.bridges_database

    first_stop = threading.Event()
    first = threading.Thread(
        target=sqlite_app.state.generation_executor.run_loop,
        kwargs={"stop": first_stop},
        daemon=True,
    )
    first.start()
    recovery_stop = threading.Event()
    recovery = None
    recovery_thread = None
    try:
        deadline = datetime.now(UTC) + timedelta(seconds=10)
        message = None
        while datetime.now(UTC) < deadline:
            message = repo.get_message(account["id"], message_id)
            if message is not None and message.content == "部分":
                break
            threading.Event().wait(0.02)
        assert message is not None and message.content == "部分", "失联前正文未落库"
        assert adapter.stream_calls == 1
        # 第一次尝试失联：租约过期（worker 被强制退出，无人续租）
        repo.renew_generation_lease(account["id"], run_id, _past())
        _expire_queue_lease(database, run_id)

        # 第二次尝试：新执行器按既有上限领取恢复（attempt 2），同样失联
        _service, recovery_db, recovery = _rebuild(
            tmp_path / "bridges.db", adapter, worker_name="recovery-executor"
        )
        recovery_thread = threading.Thread(
            target=recovery.run_loop, kwargs={"stop": recovery_stop}, daemon=True
        )
        recovery_thread.start()
        deadline = datetime.now(UTC) + timedelta(seconds=10)
        while datetime.now(UTC) < deadline:
            run = repo.get_generation_run(account["id"], run_id)
            if run is not None and run.attempt_count == 2 and adapter.stream_calls == 2:
                break
            threading.Event().wait(0.02)
        assert run is not None and run.attempt_count == 2, "失联后必须按上限领取恢复"
        assert adapter.stream_calls == 2

        # 第二次尝试也失联：达到上限，交由收尸执行器收敛
        repo.renew_generation_lease(account["id"], run_id, _past())
        _expire_queue_lease(recovery_db, run_id)
        _service, reaper_db, reaper = _rebuild(
            tmp_path / "bridges.db", adapter, worker_name="reaper-executor"
        )
        reaper.run_tick()

        assert adapter.stream_calls == 2, "收尸本身不再调用模型"
        reaped = repo.get_message(account["id"], message_id)
        assert reaped is not None
        assert reaped.status == ChatMessageStatus.ERROR
        assert reaped.error_code == "generation_worker_lost"
        assert "部分" in reaped.content, "收尸必须保留部分正文"
        run = repo.get_generation_run(account["id"], run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error_code == "generation_worker_lost"
        assert run.error_message == reaped.error_message
        terminal = _terminal_events(repo, account["id"], run_id)
        assert [event.kind for event in terminal] == ["error"]
        detail = terminal[0].payload["error"]
        assert detail["code"] == "generation_worker_lost"
        assert detail["retryable"] is True, "收尸错误与消息同源：可重试"
    finally:
        first_stop.set()
        recovery_stop.set()
        first.join(timeout=5)
        if recovery_thread is not None:
            recovery_thread.join(timeout=5)
        release.set()  # 释放失联适配器：旧执行器安全退出

    # 迟到收尾（被释放的旧执行器继续跑完）不得改写已收敛的终态
    deadline = datetime.now(UTC) + timedelta(seconds=10)
    while datetime.now(UTC) < deadline:
        if not repo.list_active_runs(account["id"], conversation_id):
            break
        threading.Event().wait(0.05)
    final = repo.get_message(account["id"], message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.ERROR
    assert final.error_code == "generation_worker_lost"
    assert [event.kind for event in _terminal_events(repo, account["id"], run_id)] == ["error"]
    assert repo.get_generation_run(account["id"], run_id).status == "failed"  # type: ignore[union-attr]
    _expire_queue_lease(reaper_db, run_id)
    reaper.run_tick()
    assert _claim_row(reaper_db, run_id) == "completed"


def test_reap_write_fault_keeps_loop_alive_and_repairs_on_next_tick(
    sqlite_app: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """收尸写入故障：执行器不退出，运行留在待收敛形态，下一轮重试即收敛。

    收尸跨越消息、终态事件与运行三个持久对象，任一步骤失败都不得让执行器
    停摆——否则其余运行永远无人收敛。故障轮以持久守卫的形态收尾：没有写
    入的步骤在下一轮由同一收尸入口补齐，已写入的步骤按判重原样保留。
    """
    account = _register(client)
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id)
    run_id = created["run_id"]
    message_id = created["assistant_message"]["message_id"]
    service = sqlite_app.state.chat_service
    executor = sqlite_app.state.generation_executor
    repo = service._repo
    database = sqlite_app.state.bridges_database
    adapter = _ScriptedAdapter(("不应输出",))
    service._gateway = _gateway_with(adapter)  # noqa: SLF001

    # 两次领取都失联（租约过期无人续租）：恢复预算耗尽、消息仍在生成中
    for _ in range(2):
        assert (
            repo.claim_generation_run(
                account["id"],
                run_id,
                owner="dead-worker",
                lease_expires_at=_past(),
                max_attempts=2,
            )
            > 0
        )
    # 队列行由仍在续租的执行器持有：本轮只有收尸一条路径
    database.connection.execute(
        "UPDATE task_claims SET status = 'claimed', lease_expires_at = ?"
        " WHERE task_key = ?",
        (
            (datetime.now(UTC) + timedelta(seconds=300)).isoformat(timespec="seconds"),
            f"generation:{run_id}",
        ),
    )

    def _unwritable(*args: Any, **kwargs: Any) -> int:
        raise StorageError("数据库当前不可写，请稍后重试或检查数据目录权限。")

    monkeypatch.setattr(repo, "finalize_generation_run", _unwritable)
    assert "收尸失败" in executor.run_tick()
    assert adapter.stream_calls == 0
    overdue = repo.list_overdue_runs(2, datetime.now(UTC))
    assert [run.run_id for run in overdue] == [run_id], "故障后的运行必须仍可被再次收尸"

    monkeypatch.undo()
    assert "收尸 1 个失联运行" in executor.run_tick()
    # 已写入的步骤按判重保留：恰一条终态事件，消息与运行同源同码
    message = repo.get_message(account["id"], message_id)
    assert message is not None and message.status == ChatMessageStatus.ERROR
    assert message.error_code == "generation_worker_lost"
    run = repo.get_generation_run(account["id"], run_id)
    assert run is not None and run.status == "failed"
    assert run.error_message == message.error_message
    assert [event.kind for event in _terminal_events(repo, account["id"], run_id)] == ["error"]
    # 队列行不被收尸步骤吞掉：租约到期后仍按「结果已提交」确认完成
    _expire_queue_lease(database, run_id)
    executor.run_tick()
    assert _claim_row(database, run_id) == "completed"


# ---------------------------------------------------------------------------
# 竞争：两个执行器领取 / 修复同一运行
# ---------------------------------------------------------------------------


def test_rival_executor_leaves_recovery_channel_intact(
    sqlite_app: Any, client: TestClient
) -> None:
    """竞争者跳过在途运行时不吞掉队列行：失联后仍可被领取恢复。

    队列行是失联运行唯一的恢复通道。领取者看到运行正被其他执行器持有
    时必须原样留下该行（既不完成也不改动运行），否则持有者死亡后运行
    既不能被重领也不能被收尸，永久停在 running。
    """
    account = _register(client)
    release = threading.Event()
    adapter = _ScriptedAdapter(("在途",), block_before=0, release=release)
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id)
    run_id = created["run_id"]
    service = sqlite_app.state.chat_service
    repo = service._repo  # noqa: SLF001
    database = sqlite_app.state.bridges_database

    stop_event = threading.Event()
    holder = threading.Thread(
        target=sqlite_app.state.generation_executor.run_loop,
        kwargs={"stop": stop_event},
        daemon=True,
    )
    holder.start()
    try:
        deadline = datetime.now(UTC) + timedelta(seconds=10)
        while datetime.now(UTC) < deadline:
            run = repo.get_generation_run(account["id"], run_id)
            if run is not None and run.status == "running" and run.attempt_count == 1:
                break
            threading.Event().wait(0.02)
        assert run is not None and run.attempt_count == 1

        # 竞争执行器领到同一队列行：运行租约未过期 → 只跳过，不动队列行
        _service, rival_db, rival = _rebuild(
            Path(database.path), adapter, worker_name="rival-executor"
        )
        _expire_queue_lease(rival_db, run_id)
        rival.run_tick()
        assert _claim_row(rival_db, run_id) == "claimed", (
            "竞争者不得完成仍可能失联的运行队列行"
        )
        held = repo.get_generation_run(account["id"], run_id)
        assert held is not None and held.status == "running" and held.attempt_count == 1

        # 持有者失联：租约过期后按既有上限恢复（恢复到第二次尝试）
        repo.renew_generation_lease(account["id"], run_id, _past())
        _expire_queue_lease(rival_db, run_id)
        assert (
            repo.claim_generation_run(
                account["id"],
                run_id,
                owner="recovery-executor",
                lease_expires_at=_past(),
                max_attempts=2,
            )
            > 0
        ), "失联运行必须仍可被领取（队列行未被竞争者吞掉）"
        recovered = repo.get_generation_run(account["id"], run_id)
        assert recovered is not None and recovered.attempt_count == 2
    finally:
        stop_event.set()
        holder.join(timeout=5)
        release.set()


def test_competing_recovery_of_same_run_yields_one_terminal(
    sqlite_app: Any, client: TestClient, tmp_path: Path
) -> None:
    """两个执行器同时收尸同一运行：终态唯一，其他运行与在途锁不受影响。"""
    account = _register(client)
    adapter_a = _ScriptedAdapter(("A",))
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter_a)  # noqa: SLF001
    conversation_a = _create_conversation(client)
    run_a, message_a = _start_lost_run(
        sqlite_app, client, account, conversation_a, persist_events=False
    )
    conversation_b = _create_conversation(client)
    created_b = _send(client, conversation_b)
    run_b = created_b["run_id"]
    message_b = created_b["assistant_message"]["message_id"]
    service = sqlite_app.state.chat_service
    repo = service._repo  # noqa: SLF001
    # 另一个会话的运行处于在途（另一账户的无关运行也必须原样）
    repo.claim_generation_run(
        account["id"],
        run_b,
        owner="in-flight-executor",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
        max_attempts=2,
    )
    other_client, other = _register_other(sqlite_app, "2")
    unrelated = _send(other_client, _create_conversation(other_client))
    repo.claim_generation_run(
        other["id"],
        unrelated["run_id"],
        owner="other-account-executor",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
        max_attempts=2,
    )
    in_flight_before = repo.get_generation_run(account["id"], run_b)
    unrelated_before = repo.get_generation_run(other["id"], unrelated["run_id"])
    locks_before = _lock_count(sqlite_app.state.bridges_database)

    # 两个执行器同时修复同一运行（同一进度的真实收尸入口）
    _service_a, db_a, first = _rebuild(
        tmp_path / "bridges.db", adapter_a, worker_name="reaper-1"
    )
    _service_b, db_b, second = _rebuild(
        tmp_path / "bridges.db", adapter_a, worker_name="reaper-2"
    )
    assert db_a.path == db_b.path
    threads = [
        threading.Thread(target=executor.run_tick, daemon=True)
        for executor in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    terminal = _terminal_events(repo, account["id"], run_a)
    assert [event.kind for event in terminal] == ["done"]
    assert db_a.connection.execute(
        "SELECT COUNT(*) AS n FROM generation_events WHERE run_id = ?"
        " AND kind IN ('done', 'error')",
        (run_a,),
    ).fetchone()["n"] == 1
    message = repo.get_message(account["id"], message_a)
    assert message is not None and message.status == ChatMessageStatus.DONE
    assert repo.get_generation_run(account["id"], run_a).status == "done"  # type: ignore[union-attr]
    # 在途运行与跨账户运行不受恢复影响（模型锁与终态字段逐字段不变）
    assert repo.get_generation_run(account["id"], run_b) == in_flight_before
    assert repo.get_generation_run(other["id"], unrelated["run_id"]) == unrelated_before
    assert _lock_count(sqlite_app.state.bridges_database) == locks_before, "恢复不产生模型锁"
    untouched = repo.get_message(account["id"], message_b)
    assert untouched is not None and untouched.status == ChatMessageStatus.STREAMING


def _lock_count(database: BridgesDatabase) -> int:
    row = database.connection.execute(
        "SELECT COUNT(*) AS n FROM model_run_locks"
    ).fetchone()
    return int(row["n"]) if row is not None else 0


# ---------------------------------------------------------------------------
# 删除竞态：运行/会话被合法删除后的迟到恢复
# ---------------------------------------------------------------------------


def test_late_recovery_after_run_deleted_completes_queue_without_revival(
    sqlite_app: Any, client: TestClient, tmp_path: Path
) -> None:
    """运行已被删除：迟到恢复不复活数据、不跨账户读取，队列行不悬挂。"""
    account = _register(client)
    adapter = _ScriptedAdapter(("不应", "生成"))
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id)
    run_id = created["run_id"]
    message_id = created["assistant_message"]["message_id"]
    repo = sqlite_app.state.chat_service._repo  # noqa: SLF001
    assert (
        repo.claim_generation_run(
            account["id"],
            run_id,
            owner="dead-worker",
            lease_expires_at=_past(),
            max_attempts=2,
        )
        > 0
    )
    # 合法删除：运维/测试清理路径删除运行与其事件（消息保留）
    assert repo.delete_run_by_message(account["id"], message_id) == 1

    _service, restarted_db, executor = _rebuild(tmp_path / "bridges.db", adapter)
    _expire_queue_lease(restarted_db, run_id)
    executor.run_tick()

    assert adapter.stream_calls == 0, "运行已删除：不得重新执行模型"
    assert repo.get_generation_run(account["id"], run_id) is None, "不得复活运行"
    assert _events(repo, account["id"], run_id) == [], "不得复活事件"
    assert _claim_row(restarted_db, run_id) == "completed", "队列行不得永久悬挂"
    # 消息仍是残留 streaming：读取路径按迁移前语义收敛为断流，不冒充回答
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [m for m in projection["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == "error"
    assert assistant["error_code"] == "stream_interrupted"


def test_late_recovery_after_conversation_deleted_converges_run_without_events(
    sqlite_app: Any, client: TestClient, tmp_path: Path
) -> None:
    """会话被删除（运行成为孤儿）：迟到恢复只收敛运行，不写事件、不跨账户。"""
    account = _register(client)
    adapter = _ScriptedAdapter(("孤儿",))
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id)
    run_id = created["run_id"]
    repo = sqlite_app.state.chat_service._repo  # noqa: SLF001
    assert (
        repo.claim_generation_run(
            account["id"],
            run_id,
            owner="dead-worker",
            lease_expires_at=_past(),
            max_attempts=2,
        )
        > 0
    )
    # 另一账户的在途运行必须完全不受影响
    other_client, other = _register_other(sqlite_app, "2")
    other_run = _send(other_client, _create_conversation(other_client))
    repo.claim_generation_run(
        other["id"],
        other_run["run_id"],
        owner="other-account-executor",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
        max_attempts=2,
    )
    unrelated_before = repo.get_generation_run(other["id"], other_run["run_id"])

    # 第二次尝试同样失联 → 恢复预算耗尽
    repo.renew_generation_lease(account["id"], run_id, _past())
    assert (
        repo.claim_generation_run(
            account["id"],
            run_id,
            owner="dead-worker-2",
            lease_expires_at=_past(),
            max_attempts=2,
        )
        > 0
    )
    # 合法删除：会话及其消息（删除编排的仓库入口；运行成为孤儿）
    assert repo.delete_conversation(account["id"], conversation_id) == 1
    assert repo.get_message(account["id"], created["assistant_message"]["message_id"]) is None

    events_before = _events(repo, account["id"], run_id)
    _service, restarted_db, executor = _rebuild(tmp_path / "bridges.db", adapter)
    executor.run_tick()  # 收尸孤儿运行

    assert adapter.stream_calls == 0, "消息已删除：不得重新执行模型"
    assert _events(repo, account["id"], run_id) == events_before, "没有消息投影就不写新事件"
    assert _terminal_events(repo, account["id"], run_id) == [], "不得补发终态事件"
    orphan = repo.get_generation_run(account["id"], run_id)
    assert orphan is not None and orphan.status == "failed", "孤儿运行必须收敛终态"
    assert repo.get_generation_run(other["id"], other_run["run_id"]) == unrelated_before
    _expire_queue_lease(restarted_db, run_id)
    executor.run_tick()
    assert _claim_row(restarted_db, run_id) == "completed"


# ---------------------------------------------------------------------------
# 游标回放：从不同已保存游标重新订阅
# ---------------------------------------------------------------------------


def _replay(
    client: TestClient, conversation_id: str, message_id: str, cursor: int
) -> list[tuple[str, dict[str, Any]]]:
    """从给定游标订阅一次；运行已终态时端点回放完即结束（响应结束即证明）。"""
    with client.stream(
        "GET",
        f"/chat/conversations/{conversation_id}/messages/{message_id}/events",
        params={"cursor": cursor},
    ) as response:
        assert response.status_code == 200, response.text
        body = "\n".join(response.iter_lines())
    return _parse_sse(body)


@pytest.mark.parametrize("outcome", ["done", "error", "stopped"])
def test_replay_from_saved_cursors_is_monotonic_and_ends(
    sqlite_app: Any, client: TestClient, outcome: str
) -> None:
    """完成/失败/停止三种终态：任意已保存游标都能回放、单调并结束。"""
    account = _register(client)
    if outcome == "error":
        adapter = _ScriptedAdapter(
            ("失败前", "不应输出"), error=RateLimitError(), error_at=1
        )
    else:
        adapter = _ScriptedAdapter(("正文",))
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id)
    run_id = created["run_id"]
    message_id = created["assistant_message"]["message_id"]
    service = sqlite_app.state.chat_service
    repo = service._repo  # noqa: SLF001

    if outcome == "stopped":
        # 排队期停止：经停止接口写入请求，再由执行器按停止收敛
        response = client.post(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
        )
        assert response.status_code == 200, response.text
    sqlite_app.state.generation_executor.run_tick()

    events = _events(repo, account["id"], run_id)
    seqs = [event.seq for event in events]
    assert seqs == sorted(set(seqs)), "持久化游标严格单调"
    terminal = [event for event in events if event.kind in TERMINAL_KINDS]
    assert len(terminal) == 1
    expected_kind = {"done": "done", "error": "error", "stopped": "error"}[outcome]
    assert terminal[0].kind == expected_kind
    if outcome == "error":
        assert terminal[0].payload["error"]["code"] == "rate_limit"
    if outcome == "stopped":
        assert terminal[0].payload["error"]["code"] == "stopped"

    # 从任意已保存游标订阅：只回放游标之后的事件，并以该终态种类结束
    for cursor in (0, seqs[len(seqs) // 2], seqs[-1]):
        replayed = _replay(client, conversation_id, message_id, cursor)
        expected = [event for event in events if event.seq > cursor]
        assert len(replayed) == len(expected)
        if not expected:
            continue
        assert _last_non_node([name for name, _payload in replayed]) == expected_kind
        for (name, payload), event in zip(replayed, expected, strict=True):
            assert name == event.kind
            if name == "delta":
                assert payload["delta"] == event.payload["delta"]
            if name == expected_kind:
                assert payload["message_id"] == message_id
