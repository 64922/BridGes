"""Issue 10 tracer bullet：聊天路径经 ModelRunLockRecorder 持久化运行锁。

覆盖验收标准中的聊天回归面：success、error/retryable fail、重复消费同一
完成事件（幂等）、业务提交前中断的原子回滚，以及非模型本地操作（停止）
不产生模型锁。锁通过 recorder 端口落库，不经过旧 ``insert_run_lock``
直接 INSERT 路径。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import RateLimitError, StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord, CapabilityStatus
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import BridgesDatabase


def _context(run_id: str = "run-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="alice",
        project_id="conversation-1",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
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


class _ProgrammableStreamAdapter:
    """预置块序列或连接阶段错误的可编程流式适配器。"""

    def __init__(
        self,
        chunks: list[StreamChunk] | None = None,
        connect_error: Exception | None = None,
    ) -> None:
        self._chunks = chunks or []
        self._connect_error = connect_error
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        if self._connect_error is not None:
            raise self._connect_error
        yield from self._chunks


@pytest.fixture
def app(tmp_path: Path) -> tuple[ChatService, ConversationRepository, BridgesDatabase]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        "qwen_text_chat", "1", _ProgrammableStreamAdapter()
    )
    service = ChatService(repository=repository, gateway=gateway)
    return service, repository, database


def _with_chunks(
    service: ChatService, chunks: list[StreamChunk] | None = None, **kwargs: Any
) -> ModelGateway:
    adapter = _ProgrammableStreamAdapter(chunks=chunks, **kwargs)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return gateway


def _start(service: ChatService, conversation_id: str, content: str = "你好"):
    return service.start_generation("alice", conversation_id, content)


def _lock_rows(database: BridgesDatabase, account_id: str = "alice") -> list[dict[str, Any]]:
    rows = database.scoped(account_id).execute(
        "SELECT * FROM model_run_locks WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _link_rows(database: BridgesDatabase, account_id: str = "alice") -> list[dict[str, Any]]:
    rows = database.scoped(account_id).execute(
        "SELECT * FROM model_run_lock_links WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def test_done_persists_full_lock_and_message_link(
    app: tuple[ChatService, ConversationRepository, BridgesDatabase],
) -> None:
    service, _, database = app
    service._gateway = _with_chunks(
        service, [StreamChunk(kind="delta", delta="成功回答")]
    )
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    events = list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context("run-tracer")
        )
    )
    done = next(event for event in events if event.kind == "done")
    assert done.lock is not None

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE
    assert final.run_lock_id == done.lock.lock_id

    locks = _lock_rows(database)
    assert len(locks) == 1
    lock = locks[0]
    assert lock["lock_id"] == done.lock.lock_id
    assert lock["account_id"] == "alice"
    assert lock["run_id"] == "run-tracer"
    assert lock["project_id"] == "conversation-1"
    assert lock["capability_name"] == "qwen_text_chat"
    assert lock["actual_model_id"] == "qwen3.7-plus-2026-05-26"
    assert lock["status"] == "success"
    import json as _json

    assert lock["parameters"] is not None
    captured = _json.loads(lock["parameters"])
    assert captured["temperature"] == 0.7
    assert "max_tokens" in captured
    # 白名单：正文/提示词/凭据字段绝不进入参数快照。
    assert not any(
        key in captured
        for key in ("prompt", "messages", "content", "api_key", "authorization", "image", "ocr")
    )
    assert lock["prompt_version"] == "1"
    assert lock["input_output_contract"] == "qwen_text_chat:chat-messages-v1->chat-completion-v1"
    assert lock["retry_count"] == 0
    assert lock["canonical_hash"] and not str(lock["canonical_hash"]).startswith("legacy-")

    links = _link_rows(database)
    assert len(links) == 1
    link = links[0]
    assert link["lock_id"] == done.lock.lock_id
    assert link["object_type"] == "message"
    assert link["object_id"] == assistant.message_id
    assert link["operation"] == "generate"
    assert link["attempt_ordinal"] == 1
    assert link["is_primary"] == 1

    # 通过 recorder 查询接口可读回：业务对象与 run 两个视角。
    recorder = service._repo._run_lock_recorder  # noqa: SLF001 - 测试直读
    by_object = recorder.list_locks_by_business_ref("alice", "message", assistant.message_id)
    assert [lock.lock_id for lock in by_object] == [done.lock.lock_id]
    by_run = recorder.list_locks_by_run("alice", "run-tracer")
    assert [lock.lock_id for lock in by_run] == [done.lock.lock_id]
    assert by_run[0].business_refs[0].object_id == assistant.message_id


def test_error_persists_lock_with_failure_status(
    app: tuple[ChatService, ConversationRepository, BridgesDatabase],
) -> None:
    service, _, database = app
    service._gateway = _with_chunks(
        service,
        [
            StreamChunk(kind="delta", delta="部分内容"),
            StreamChunk(kind="error", error_code="rate_limit", error_message="vendor msg"),
        ],
    )
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    events = list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context("run-error")
        )
    )
    error_event = next(event for event in events if event.kind == "error")
    assert error_event.lock is not None

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.ERROR
    assert final.error_code == "rate_limit"
    assert final.run_lock_id == error_event.lock.lock_id

    locks = _lock_rows(database)
    assert len(locks) == 1
    assert locks[0]["lock_id"] == error_event.lock.lock_id
    assert locks[0]["status"] == error_event.lock.status.value
    assert locks[0]["error_code"] == "rate_limit"
    assert locks[0]["run_id"] == "run-error"


def test_duplicate_done_consumption_is_idempotent(
    app: tuple[ChatService, ConversationRepository, BridgesDatabase],
) -> None:
    service, repository, database = app
    service._gateway = _with_chunks(
        service, [StreamChunk(kind="delta", delta="回答")]
    )
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    events = list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context("run-idem")
        )
    )
    done = next(event for event in events if event.kind == "done")
    assert done.lock is not None

    # 同一完成事件被重复消费（如执行器恢复后重放终态）：锁与关联幂等，
    # 消息终态不被改写。
    repository.finalize_message(
        "alice",
        assistant.message_id,
        status=ChatMessageStatus.DONE,
        error_code=None,
        error_message=None,
        duration_ms=100,
        model_id=done.lock.actual_model_id,
        lock=done.lock,
        updated_at=datetime.now(UTC),
    )

    assert len(_lock_rows(database)) == 1
    assert len(_link_rows(database)) == 1
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE
    assert final.content == "回答"


def test_fault_between_lock_write_and_business_commit_rolls_back_everything(
    app: tuple[ChatService, ConversationRepository, BridgesDatabase],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """锁已写入但业务提交前中断：整个事务回滚，收敛为明确失败，无孤儿锁。"""
    service, repository, database = app
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)

    real_record = repository._run_lock_recorder.record  # noqa: SLF001 - 测试直读

    def _record_then_explode(*args: object, **kwargs: object) -> Any:
        real_record(*args, **kwargs)
        raise RuntimeError("模拟业务提交前进程中断")

    monkeypatch.setattr(repository._run_lock_recorder, "record", _record_then_explode)

    events = list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context("run-fault")
        )
    )

    # 锁行与关联全部回滚：既无孤儿锁，也无"业务成功、锁缺失"——消息收敛为
    # 明确的内部错误（fail-closed），绝不静默缺锁。
    assert _lock_rows(database) == []
    assert _link_rows(database) == []
    error_events = [e for e in events if e.kind == "error"]
    assert error_events, "故障注入后必须产出明确错误事件"
    assert error_events[0].error_code == "internal_error"
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.ERROR
    assert final.error_code == "internal_error"


def test_stopped_local_action_produces_no_model_lock(
    app: tuple[ChatService, ConversationRepository, BridgesDatabase],
) -> None:
    """非模型本地操作（停止生成）不产生任何模型锁。"""
    service, _, database = app
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)

    service.stop_generation("alice", created.conversation_id, assistant.message_id)

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.STOPPED
    assert _lock_rows(database) == []
    assert _link_rows(database) == []


def test_retry_attempts_produce_distinct_locks(
    app: tuple[ChatService, ConversationRepository, BridgesDatabase],
) -> None:
    """重试后两次真实模型动作各自独立成锁，按调用顺序可查询。"""
    service, _, database = app
    service._gateway = _with_chunks(
        service, connect_error=RateLimitError("slow")
    )
    created = service.create_conversation("alice")
    _, failed = _start(service, created.conversation_id, "帮我分析论文")
    list(
        service.stream_generation(
            "alice", created.conversation_id, failed.message_id, _context("run-first")
        )
    )

    service._gateway = _with_chunks(service, [StreamChunk(kind="delta", delta="成功回答")])
    _, retried = service.retry_generation(
        "alice", created.conversation_id, failed.message_id
    )
    list(
        service.stream_generation(
            "alice", created.conversation_id, retried.message_id, _context("run-second")
        )
    )

    locks = _lock_rows(database)
    assert len(locks) == 2
    assert {lock["run_id"] for lock in locks} == {"run-first", "run-second"}
    first_run_lock = next(lock for lock in locks if lock["run_id"] == "run-first")
    second_run_lock = next(lock for lock in locks if lock["run_id"] == "run-second")
    # 两次尝试各自独立成锁，可分别按各自消息对象查询（不折叠成汇总锁）。
    recorder = service._repo._run_lock_recorder  # noqa: SLF001 - 测试直读
    first_attempt = recorder.list_locks_by_business_ref("alice", "message", failed.message_id)
    second_attempt = recorder.list_locks_by_business_ref("alice", "message", retried.message_id)
    assert [lock.lock_id for lock in first_attempt] == [first_run_lock["lock_id"]]
    assert [lock.lock_id for lock in second_attempt] == [second_run_lock["lock_id"]]
    by_run = recorder.list_locks_by_run("alice", "run-first") + recorder.list_locks_by_run(
        "alice", "run-second"
    )
    assert len(by_run) == 2
    assert first_attempt[0].status.value in {"error", "retryable_fail"}
    assert second_attempt[0].status.value == "success"
    # 重试尝试按消息 attempt_number 稳定编号，run 级查询按调用序号排序。
    assert first_attempt[0].business_refs[0].attempt_ordinal == 1
    assert second_attempt[0].business_refs[0].attempt_ordinal == 2
    run_ordered = recorder.list_locks_by_run("alice", "run-first") + recorder.list_locks_by_run(
        "alice", "run-second"
    )
    assert [lock.business_refs[0].attempt_ordinal for lock in run_ordered] == [1, 2]


def test_blocked_capability_persists_lock_with_blocked_status(
    app: tuple[ChatService, ConversationRepository, BridgesDatabase],
) -> None:
    """能力未验证（blocked）也是真实模型动作：锁以 blocked 状态经 recorder 落库。"""
    service, _, database = app
    registry = CapabilityRegistry()
    unverified = _chat_capability().model_copy(
        update={"status": CapabilityStatus.DEPRECATED}
    )
    registry.register(unverified)
    gateway = ModelGateway(registry)
    service._gateway = gateway

    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    events = list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context("run-blocked")
        )
    )
    error_event = next(event for event in events if event.kind == "error")
    assert error_event.lock is not None

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.ERROR
    assert final.run_lock_id == error_event.lock.lock_id

    locks = _lock_rows(database)
    assert len(locks) == 1
    assert locks[0]["status"] == "blocked"
    assert locks[0]["error_code"] == "capability_not_verified"
    assert locks[0]["run_id"] == "run-blocked"


def test_interrupted_stream_converges_without_orphan_lock(
    app: tuple[ChatService, ConversationRepository, BridgesDatabase],
) -> None:
    """模型流中断（timeout/断连语义）：消息收敛为明确错误，无孤儿锁。"""
    service, _, database = app
    service._gateway = _with_chunks(
        service,
        [StreamChunk(kind="delta", delta="部分内容") for _ in range(200)],
    )
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    gen = service.stream_generation(
        "alice", created.conversation_id, assistant.message_id, _context("run-interrupted")
    )
    for event in gen:
        if event.kind == "delta":
            break
    gen.close()  # 模拟客户端断开/超时中止

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.ERROR
    assert final.error_code == "stream_interrupted"
    # 无终态模型事件 → 无锁行、无关联行、无孤儿数据。
    assert _lock_rows(database) == []
    assert _link_rows(database) == []
