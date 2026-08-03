"""聊天服务测试：持久化状态机、停止/重试、断流收敛与账户隔离（Issue 11）。

服务层直接使用临时 sqlite 数据库与可编程流式适配器，不经过网络；
生成器通过 ``list(...)`` 消费以模拟流式推进。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterError, AuthError, RateLimitError
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.streaming import StreamChunk
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import (
    STREAM_INTERRUPTED_MESSAGE,
    ChatDomainError,
    ChatService,
)
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus, ChatMode
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
    """可编程流式适配器：预置块序列或连接阶段错误。"""

    def __init__(
        self,
        chunks: list[StreamChunk] | None = None,
        connect_error: AdapterError | None = None,
        slow: bool = False,
    ) -> None:
        self._chunks = chunks or []
        self._connect_error = connect_error
        self._slow = slow

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
        for chunk in self._chunks:
            if self._slow:
                time.sleep(0.02)
            yield chunk


@pytest.fixture
def service(tmp_path: Path) -> ChatService:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        "qwen_text_chat", "1", _ProgrammableStreamAdapter()
    )
    return ChatService(repository=repository, gateway=gateway)


def _start(service: ChatService, conversation_id: str, content: str = "你好"):
    return service.start_generation("alice", conversation_id, content)


# ---------------------------------------------------------------------------
# 对话生命周期
# ---------------------------------------------------------------------------


def test_create_and_list_conversation(service: ChatService) -> None:
    created = service.create_conversation("alice")
    listing = service.list_conversations("alice")
    assert len(listing.conversations) == 1
    assert listing.conversations[0].conversation_id == created.conversation_id
    assert listing.conversations[0].title == ""
    assert listing.conversations[0].message_count == 0
    assert service.list_conversations("bob").conversations == []


def test_get_conversation_account_isolation(service: ChatService) -> None:
    created = service.create_conversation("alice")
    assert service.get_conversation("bob", created.conversation_id) is None
    assert service.get_conversation("alice", created.conversation_id) is not None


def test_first_message_derives_title(service: ChatService) -> None:
    created = service.create_conversation("alice")
    _start(service, created.conversation_id, "请帮我写一篇关于量子计算的短文")
    projection = service.get_conversation("alice", created.conversation_id)
    assert projection is not None
    assert projection.title.startswith("请帮我写一篇关于量子计算的短文")
    assert projection.title.endswith("…") or len(projection.title) <= 24


# ---------------------------------------------------------------------------
# 生成状态机
# ---------------------------------------------------------------------------


def test_start_generation_creates_user_and_streaming_assistant(
    service: ChatService,
) -> None:
    created = service.create_conversation("alice")
    user_msg, assistant_msg = _start(service, created.conversation_id)
    assert user_msg.role == ChatMessageRole.USER
    assert user_msg.status == ChatMessageStatus.DONE
    assert assistant_msg.role == ChatMessageRole.ASSISTANT
    assert assistant_msg.status == ChatMessageStatus.STREAMING
    assert assistant_msg.attempt_number == 1


def test_generation_blocked_while_streaming(service: ChatService) -> None:
    created = service.create_conversation("alice")
    _start(service, created.conversation_id)
    with pytest.raises(ChatDomainError) as exc_info:
        _start(service, created.conversation_id, "再问一次")
    assert exc_info.value.code == "generation_in_progress"
    assert exc_info.value.status_code == 409


def test_stream_done_persists_content_duration_and_run_lock(
    service: ChatService,
    tmp_path: Path,
) -> None:
    service._gateway = _with_chunks(
        service,
        [StreamChunk(kind="delta", delta="第一段"), StreamChunk(kind="delta", delta="第二段")],
    )
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    events = list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )
    assert [e.kind for e in events] == ["delta", "delta", "done"]
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE
    assert final.content == "第一段第二段"
    assert final.run_lock_id is not None
    assert final.model_id == "qwen3.7-plus-2026-05-26"
    assert final.duration_ms is not None and final.duration_ms >= 1

    # 运行锁已持久化且绑定账户
    database = BridgesDatabase(tmp_path / "bridges.db")
    row = database.connection.execute(
        "SELECT account_id, capability_name, status FROM model_run_locks"
        " WHERE lock_id = ?",
        (final.run_lock_id,),
    ).fetchone()
    assert row is not None
    assert row["account_id"] == "alice"
    assert row["capability_name"] == "qwen_text_chat"


def test_stream_error_persists_actionable_chinese_message(service: ChatService) -> None:
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
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )
    assert [e.kind for e in events] == ["delta", "error"]
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.ERROR
    assert final.content == "部分内容"
    assert final.error_code == "rate_limit"
    # 落库的是可操作中文，不泄漏供应商原文
    assert "限流" in (final.error_message or "")


def test_stream_auth_error_is_fail_closed(service: ChatService) -> None:
    service._gateway = _with_chunks(
        service, connect_error=AuthError("bad key")
    )
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.ERROR
    assert final.error_code == "auth_error"
    assert "Key 无效" in (final.error_message or "")


# ---------------------------------------------------------------------------
# 停止
# ---------------------------------------------------------------------------


def test_stop_generation_finalizes_stopped_and_keeps_content(
    service: ChatService,
) -> None:
    service._gateway = _with_chunks(
        service,
        [StreamChunk(kind="delta", delta=f"块{i}") for i in range(20)],
        slow=True,
    )
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    events: list[Any] = []
    thread = threading.Thread(
        target=lambda: events.extend(
            service.stream_generation(
                "alice", created.conversation_id, assistant.message_id, _context()
            )
        )
    )
    thread.start()
    time.sleep(0.05)
    stopped = service.stop_generation("alice", created.conversation_id, assistant.message_id)
    thread.join(timeout=5)
    assert stopped.status == ChatMessageStatus.STOPPED
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.STOPPED
    assert final.duration_ms is not None and final.duration_ms >= 1
    # 停止是幂等的：再次停止返回同一终态
    again = service.stop_generation("alice", created.conversation_id, assistant.message_id)
    assert again.status == ChatMessageStatus.STOPPED
    assert again.updated_at == final.updated_at


def test_stop_unknown_message_404(service: ChatService) -> None:
    created = service.create_conversation("alice")
    with pytest.raises(ChatDomainError) as exc_info:
        service.stop_generation("alice", created.conversation_id, "missing")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 重试
# ---------------------------------------------------------------------------


def test_retry_creates_new_attempt_and_preserves_failed_history(
    service: ChatService,
) -> None:
    service._gateway = _with_chunks(
        service, connect_error=RateLimitError("slow")
    )
    created = service.create_conversation("alice")
    _, failed = _start(service, created.conversation_id, "帮我分析论文")
    list(
        service.stream_generation(
            "alice", created.conversation_id, failed.message_id, _context()
        )
    )

    # 重试成功
    service._gateway = _with_chunks(service, [StreamChunk(kind="delta", delta="成功回答")])
    user_msg, retried = service.retry_generation(
        "alice", created.conversation_id, failed.message_id
    )
    assert retried.attempt_number == 2
    assert retried.status == ChatMessageStatus.STREAMING
    list(
        service.stream_generation(
            "alice", created.conversation_id, retried.message_id, _context("run-2")
        )
    )

    projection = service.get_conversation("alice", created.conversation_id)
    assert projection is not None
    assistant_messages = [
        m for m in projection.messages if m.role == ChatMessageRole.ASSISTANT
    ]
    assert len(assistant_messages) == 2
    # 历史失败尝试原样保留，未被静默改写为成功
    first, second = assistant_messages
    assert first.attempt_number == 1
    assert first.status == ChatMessageStatus.ERROR
    assert second.attempt_number == 2
    assert second.status == ChatMessageStatus.DONE
    assert second.content == "成功回答"
    assert user_msg.message_id == first_message_of(projection).message_id


def test_retry_user_message_rejected(service: ChatService) -> None:
    created = service.create_conversation("alice")
    user_msg, _ = _start(service, created.conversation_id)
    with pytest.raises(ChatDomainError) as exc_info:
        service.retry_generation("alice", created.conversation_id, user_msg.message_id)
    assert exc_info.value.code == "not_retryable_message"


def test_retry_cross_account_404(service: ChatService) -> None:
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    with pytest.raises(ChatDomainError) as exc_info:
        service.retry_generation("bob", created.conversation_id, assistant.message_id)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 断流与陈旧收敛
# ---------------------------------------------------------------------------


def test_generator_close_finalizes_interrupted(service: ChatService) -> None:
    service._gateway = _with_chunks(
        service,
        [StreamChunk(kind="delta", delta=f"块{i}") for i in range(50)],
        slow=True,
    )
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    gen = service.stream_generation(
        "alice", created.conversation_id, assistant.message_id, _context()
    )
    next(gen)
    next(gen)
    gen.close()  # 模拟客户端断开
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.ERROR
    assert final.error_code == "stream_interrupted"
    assert final.error_message == STREAM_INTERRUPTED_MESSAGE
    assert final.content  # 已接收正文保留


def test_stale_streaming_message_reconciled_on_read(tmp_path: Path) -> None:
    """进程重启后：遗留的 streaming 消息在读取时收敛为可重试错误。"""
    db_path = tmp_path / "bridges.db"

    def _build() -> ChatService:
        database = BridgesDatabase(db_path)
        database.initialize()
        return ChatService(
            repository=ConversationRepository(database),
            gateway=ModelGateway(CapabilityRegistry()),
        )

    first = _build()
    created = first.create_conversation("alice")
    _, assistant = first.start_generation("alice", created.conversation_id, "你好")

    # 模拟进程重启：重建服务（注册表清空），DB 里仍是 streaming 残留
    restarted = _build()
    raw = restarted._repo.get_message("alice", assistant.message_id)
    assert raw is not None
    assert raw.status == ChatMessageStatus.STREAMING
    # 首次读取即收敛为可重试错误，绝不把半截占位当回答
    projection = restarted.get_conversation("alice", created.conversation_id)
    assert projection is not None
    restored = [m for m in projection.messages if m.message_id == assistant.message_id][0]
    assert restored.status == ChatMessageStatus.ERROR
    assert restored.error_code == "stream_interrupted"
    assert restored.error_message == STREAM_INTERRUPTED_MESSAGE
    # 再次读取：已收敛，不再变化
    projection2 = restarted.get_conversation("alice", created.conversation_id)
    assert projection2 is not None
    assert all(m.status != ChatMessageStatus.STREAMING for m in projection2.messages)


def test_long_running_stream_not_reconciled_by_read(service: ChatService) -> None:
    """生成超过 TTL 时长仍不被打断：每个增量续期活跃标记（心跳）。"""
    service._gateway = _with_chunks(
        service,
        [StreamChunk(kind="delta", delta=f"块{i}") for i in range(5)],
        slow=True,
    )
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    events: list[Any] = []
    thread = threading.Thread(
        target=lambda: events.extend(
            service.stream_generation(
                "alice", created.conversation_id, assistant.message_id, _context()
            )
        )
    )
    thread.start()
    time.sleep(0.05)
    # 人为把 TTL 退化为 0.01s（模拟超过 TTL 的长流），但心跳已续期：
    # 读取不得把活跃流误判为僵死
    service._STALE_STREAMING_TTL_SECONDS = 0.01
    time.sleep(0.05)
    projection = service.get_conversation("alice", created.conversation_id)
    assert projection is not None
    streaming = [m for m in projection.messages if m.status == ChatMessageStatus.STREAMING]
    assert len(streaming) == 1
    thread.join(timeout=10)
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE
    assert final.content.count("块") == 5


def test_read_does_not_reconcile_active_streaming_message(service: ChatService) -> None:
    """进行中的生成不受读取影响：陈旧收敛豁免活跃注册表中的消息。"""
    service._gateway = _with_chunks(
        service,
        [StreamChunk(kind="delta", delta=f"块{i}") for i in range(30)],
        slow=True,
    )
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id)
    events: list[Any] = []
    thread = threading.Thread(
        target=lambda: events.extend(
            service.stream_generation(
                "alice", created.conversation_id, assistant.message_id, _context()
            )
        )
    )
    thread.start()
    time.sleep(0.05)
    # 读取历史不打断进行中的生成
    projection = service.get_conversation("alice", created.conversation_id)
    assert projection is not None
    streaming = [m for m in projection.messages if m.status == ChatMessageStatus.STREAMING]
    assert len(streaming) == 1
    thread.join(timeout=10)
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE


# ---------------------------------------------------------------------------
# 模型上下文
# ---------------------------------------------------------------------------


def test_model_history_excludes_failed_and_stopped_attempts(service: ChatService) -> None:
    """失败/停止的尝试不进模型上下文，只带最新已完成回答。"""
    service._gateway = _with_chunks(
        service, connect_error=RateLimitError("slow")
    )
    created = service.create_conversation("alice")
    _, failed = _start(service, created.conversation_id, "第一个问题")
    list(
        service.stream_generation(
            "alice", created.conversation_id, failed.message_id, _context()
        )
    )
    service._gateway = _with_chunks(service, [StreamChunk(kind="delta", delta="正确答案")])
    _, retried = service.retry_generation(
        "alice", created.conversation_id, failed.message_id
    )
    list(
        service.stream_generation(
            "alice", created.conversation_id, retried.message_id, _context("run-2")
        )
    )

    history = service._model_history("alice", created.conversation_id)
    # 首条为当前对话模式的系统角色合同（Issue 14），失败/停止尝试不进上下文
    assert history[0]["role"] == "system"
    assert "日常陪伴" in history[0]["content"]
    assert history[1:] == [
        {"role": "user", "content": "第一个问题"},
        {"role": "assistant", "content": "正确答案"},
    ]


# ---------------------------------------------------------------------------
# 重启恢复（同一数据库文件重新打开）
# ---------------------------------------------------------------------------


def test_restart_recovery_via_same_database_file(tmp_path: Path) -> None:
    db_path = tmp_path / "bridges.db"

    def _build() -> ChatService:
        database = BridgesDatabase(db_path)
        database.initialize()
        repository = ConversationRepository(database)
        registry = CapabilityRegistry()
        registry.register(_chat_capability())
        gateway = ModelGateway(registry)
        gateway.register_adapter("qwen_text_chat", "1", _ProgrammableStreamAdapter())
        return ChatService(repository=repository, gateway=gateway)

    first = _build()
    created = first.create_conversation("alice", title="我的对话")
    first._gateway = _with_chunks(first, [StreamChunk(kind="delta", delta="重启前回答")])
    _, assistant = first.start_generation("alice", created.conversation_id, "在吗")
    list(
        first.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )

    # 模拟完整运行时重启：同一数据库文件重新打开
    second = _build()
    restored = second.get_conversation("alice", created.conversation_id)
    assert restored is not None
    assert restored.title == "我的对话"
    roles = [(m.role.value, m.content) for m in restored.messages]
    assert roles == [("user", "在吗"), ("assistant", "重启前回答")]
    assert all(m.status == ChatMessageStatus.DONE for m in restored.messages)


# ---------------------------------------------------------------------------
# Issue 14：对话双模式与思考摘要
# ---------------------------------------------------------------------------


def test_create_conversation_defaults_to_companion_mode(service: ChatService) -> None:
    created = service.create_conversation("alice")
    assert created.mode == ChatMode.COMPANION
    assert created.mode_events == []
    assert service.list_conversations("alice").conversations[0].mode == ChatMode.COMPANION


def test_create_conversation_supports_study_mode(service: ChatService) -> None:
    created = service.create_conversation("alice", mode=ChatMode.STUDY)
    assert created.mode == ChatMode.STUDY
    listing = service.list_conversations("alice").conversations
    assert listing[0].mode == ChatMode.STUDY


def test_switch_mode_writes_visible_event_and_keeps_history(service: ChatService) -> None:
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id, "先问一个陪伴问题")
    service._gateway = _with_chunks(service, [StreamChunk(kind="delta", delta="陪伴回答")])
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )

    projection, event = service.set_conversation_mode(
        "alice", created.conversation_id, ChatMode.STUDY
    )
    assert event is not None
    assert event.from_mode == ChatMode.COMPANION
    assert event.to_mode == ChatMode.STUDY
    assert projection.mode == ChatMode.STUDY

    # 既有消息、回答与引用不被重写
    assert [(m.role, m.content) for m in projection.messages] == [
        (ChatMessageRole.USER, "先问一个陪伴问题"),
        (ChatMessageRole.ASSISTANT, "陪伴回答"),
    ]
    # 模式切换事件可见且按时间排序
    assert [(e.from_mode, e.to_mode) for e in projection.mode_events] == [
        (ChatMode.COMPANION, ChatMode.STUDY)
    ]


def test_switch_mode_affects_only_later_requests(service: ChatService) -> None:
    """切换后后续生成使用学习模式角色合同，既有回答保持原样。"""
    created = service.create_conversation("alice")
    service.set_conversation_mode("alice", created.conversation_id, ChatMode.STUDY)
    history = service._model_history("alice", created.conversation_id)
    assert "学习模式" in history[0]["content"]
    assert "因材施教" in history[0]["content"]


def test_switch_mode_same_mode_is_idempotent(service: ChatService) -> None:
    created = service.create_conversation("alice")
    _, event = service.set_conversation_mode(
        "alice", created.conversation_id, ChatMode.COMPANION
    )
    assert event is None
    assert service.get_conversation("alice", created.conversation_id).mode_events == []


def test_switch_mode_unknown_conversation_is_404(service: ChatService) -> None:
    with pytest.raises(ChatDomainError) as exc_info:
        service.set_conversation_mode("alice", "missing", ChatMode.STUDY)
    assert exc_info.value.status_code == 404


def test_done_message_carries_public_thinking_summary(service: ChatService) -> None:
    """思考摘要由结构化进度事件构造：初始步骤 + 生成步骤 + 质量结论。"""
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id, "帮我理解量子纠错")
    service._gateway = _with_chunks(service, [StreamChunk(kind="delta", delta="纠错码")])
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None and final.thinking is not None
    assert final.thinking.steps == [
        "理解你的问题与当前语境",
        "组织并生成回答",
    ]
    assert final.thinking.evidence == []
    assert final.thinking.tools == []
    assert final.thinking.quality == ["回答已完整生成并保存"]
    assert final.duration_ms is not None and final.duration_ms >= 1  # 真实生命周期耗时


def test_failed_generation_keeps_steps_with_chinese_quality(service: ChatService) -> None:
    """失败/断流时保留已完成摘要并显示中文状态。"""
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id, "触发失败")
    service._gateway = _with_chunks(
        service, connect_error=RateLimitError("slow down")
    )
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None and final.thinking is not None
    assert final.thinking.steps == [
        "理解你的问题与当前语境",
        "组织并生成回答",
    ]
    assert final.thinking.quality and "限流" in final.thinking.quality[0]
    assert final.duration_ms is not None and final.duration_ms >= 1


def test_stopped_generation_keeps_steps_with_chinese_quality(service: ChatService) -> None:
    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id, "触发停止")
    service._gateway = _with_chunks(
        service, [StreamChunk(kind="delta", delta="部分内容")], slow=True
    )
    generator = service.stream_generation(
        "alice", created.conversation_id, assistant.message_id, _context()
    )
    next(generator)
    service.stop_generation("alice", created.conversation_id, assistant.message_id)
    list(generator)

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None and final.thinking is not None
    assert final.thinking.steps == [
        "理解你的问题与当前语境",
        "组织并生成回答",
    ]
    assert final.thinking.quality == ["已停止生成，保留已生成内容。"]


def test_study_mode_initial_thinking_mentions_learning_contract(service: ChatService) -> None:
    created = service.create_conversation("alice", mode=ChatMode.STUDY)
    _, assistant = _start(service, created.conversation_id, "讲解一个概念")
    service._gateway = _with_chunks(service, [StreamChunk(kind="delta", delta="回答")])
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None and final.thinking is not None
    assert final.thinking.steps[0] == "按学习目标分析你的问题与已有知识"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def first_message_of(projection: Any) -> Any:
    return projection.messages[0]


def _with_chunks(  # noqa: E501
    service: ChatService, chunks: list[StreamChunk] | None = None, **kwargs: Any
) -> ModelGateway:
    adapter = _ProgrammableStreamAdapter(chunks=chunks, **kwargs)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return gateway
