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

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import (
    STREAM_INTERRUPTED_MESSAGE,
    ChatService,
)
from bridges.contracts.chat import ChatMessageStatus
from bridges.storage.database import BridgesDatabase
from tests.chat.chat_service_support import (
    _chat_capability,
    _context,
    _ProgrammableStreamAdapter,
    _start,
    _with_chunks,
)


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
    # Issue 06：阶段事件先于正文；消费到首个 delta（已接收正文）再断开
    for event in gen:
        if event.kind == "delta":
            break
    gen.close()  # 模拟客户端断开
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.ERROR
    assert final.error_code == "stream_interrupted"
    assert final.error_message == STREAM_INTERRUPTED_MESSAGE
    assert final.content  # 已接收正文保留


def test_stale_streaming_message_without_run_reconciled_on_read(
    service: ChatService,
) -> None:
    """旧版本遗留：无运行记录的 streaming 消息在读取时收敛为可重试错误。

    Issue 02 迁移后正常路径不再产生该状态（运行持久化）；本测试锁定
    历史数据兜底语义：没有运行记录的半截占位绝不冒充回答。
    """
    created = service.create_conversation("alice")
    user_msg, assistant = _start(service, created.conversation_id)
    # 手工删除运行记录，模拟旧版本遗留的 streaming 消息
    service._repo.delete_run_by_message("alice", assistant.message_id)
    raw = service._repo.get_message("alice", assistant.message_id)
    assert raw is not None
    assert raw.status == ChatMessageStatus.STREAMING
    projection = service.get_conversation("alice", created.conversation_id)
    assert projection is not None
    restored = [m for m in projection.messages if m.message_id == assistant.message_id][0]
    assert restored.status == ChatMessageStatus.ERROR
    assert restored.error_code == "stream_interrupted"
    assert restored.error_message == STREAM_INTERRUPTED_MESSAGE


def test_restarted_streaming_message_with_active_run_survives_read(tmp_path: Path) -> None:
    """进程重启后：仍有活跃运行的消息保持生成中，读取不收敛也不打断。

    Issue 02：运行持久化在数据库，重启后 queued/running 运行仍是活跃
    判定源；执行器重新领取后按同一份任务契约恢复执行。运行终态后消息
    由执行器收敛（本测试模拟执行器完成路径）。
    """
    db_path = tmp_path / "bridges.db"

    def _build() -> ChatService:
        database = BridgesDatabase(db_path)
        database.initialize()
        registry = CapabilityRegistry()
        registry.register(_chat_capability())
        gateway = ModelGateway(registry)
        gateway.register_adapter(
            "qwen_text_chat", "1", _ProgrammableStreamAdapter()
        )
        return ChatService(
            repository=ConversationRepository(database),
            gateway=gateway,
        )

    first = _build()
    created = first.create_conversation("alice")
    _, assistant, _ = first.start_generation("alice", created.conversation_id, "你好")

    # 模拟进程重启：重建服务（内存注册表清空），DB 里仍是 streaming + queued 运行
    restarted = _build()
    raw = restarted._repo.get_message("alice", assistant.message_id)
    assert raw is not None
    assert raw.status == ChatMessageStatus.STREAMING
    run = restarted._repo.get_run_by_message("alice", assistant.message_id)
    assert run is not None
    assert run.status == "queued"
    # 读取不收敛：活跃运行的消息保持 streaming，并携带可恢复的运行视图
    projection = restarted.get_conversation("alice", created.conversation_id)
    assert projection is not None
    restored = [m for m in projection.messages if m.message_id == assistant.message_id][0]
    assert restored.status == ChatMessageStatus.STREAMING
    assert restored.active_run is not None
    assert restored.active_run.run_id == run.run_id
    # 模拟执行器完成路径：消费生成收敛消息，再收敛运行终态
    list(
        restarted.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )
    restarted._repo.finalize_generation_run(
        "alice",
        run.run_id,
        status="done",
        error_code=None,
        error_message=None,
        duration_ms=42,
        now=datetime.now(UTC),
    )
    projection2 = restarted.get_conversation("alice", created.conversation_id)
    assert projection2 is not None
    final = [m for m in projection2.messages if m.message_id == assistant.message_id][0]
    assert final.status == ChatMessageStatus.DONE
    # 再次读取：不再变化
    projection3 = restarted.get_conversation("alice", created.conversation_id)
    assert projection3 is not None
    assert all(m.status != ChatMessageStatus.STREAMING for m in projection3.messages)


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
    _, assistant, _ = first.start_generation("alice", created.conversation_id, "在吗")
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
# Issue 05：首轮提交后锁定对话模式
# ---------------------------------------------------------------------------
