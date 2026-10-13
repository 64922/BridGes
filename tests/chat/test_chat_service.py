"""聊天服务测试：持久化状态机、停止/重试、断流收敛与账户隔离（Issue 11）。

服务层直接使用临时 sqlite 数据库与可编程流式适配器，不经过网络；
生成器通过 ``list(...)`` 消费以模拟流式推进。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import AuthError, RateLimitError, StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.lightweight_policy import RELEASE_BASELINE_POLICY_VERSION
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import (
    ChatDomainError,
    ChatService,
)
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.storage.database import BridgesDatabase
from tests.chat.chat_service_support import (
    _chat_capability,
    _context,
    _ProgrammableStreamAdapter,
    _start,
    _with_chunks,
    first_message_of,
)

# 对话生命周期
# ---------------------------------------------------------------------------


def test_create_and_list_conversation(service: ChatService) -> None:
    created = service.create_conversation("alice", title="可回访对话")
    listing = service.list_conversations("alice")
    assert len(listing.conversations) == 1
    assert listing.conversations[0].conversation_id == created.conversation_id
    assert listing.conversations[0].title == "可回访对话"
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


def test_conversation_lifecycle_persists_pin_rename_and_order(
    service: ChatService,
    tmp_path: Path,
) -> None:
    with pytest.raises(ChatDomainError) as exc_info:
        service.create_conversation("alice", title="退役项目", project_id="project-1")
    assert exc_info.value.code == "legacy_file_source_retired"
    assert exc_info.value.status_code == 410

    recent = service.create_conversation("alice", title="最近对话")
    older = service.create_conversation("alice", title="较早对话")

    service.update_conversation("alice", older.conversation_id, pinned=True)
    renamed = service.update_conversation(
        "alice", recent.conversation_id, title="已改名对话"
    )

    assert renamed.title == "已改名对话"
    assert renamed.project_id is None
    assert renamed.pinned is False
    listing = service.list_conversations("alice").conversations
    assert [item.conversation_id for item in listing] == [
        older.conversation_id,
        recent.conversation_id,
    ]
    assert listing[0].pinned is True

    # 重新打开同一数据库后，列表状态仍由持久化数据恢复。
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    restored = ChatService(
        repository=ConversationRepository(database), gateway=service._gateway
    ).list_conversations("alice")
    assert restored.conversations[0].title == "较早对话"
    assert restored.conversations[0].pinned is True
    assert restored.conversations[1].project_id is None


def test_unnamed_empty_draft_is_not_a_recent_conversation(service: ChatService) -> None:
    service.create_conversation("alice")
    assert service.list_conversations("alice").conversations == []


def test_conversation_delete_is_account_scoped_and_removes_history(
    service: ChatService,
) -> None:
    created = service.create_conversation("alice", title="待删除")
    _, assistant = _start(service, created.conversation_id, "会被删除的消息")
    service.stop_generation("alice", created.conversation_id, assistant.message_id)

    with pytest.raises(ChatDomainError) as exc_info:
        service.delete_conversation("bob", created.conversation_id)
    assert exc_info.value.code == "conversation_not_found"
    assert service.get_conversation("alice", created.conversation_id) is not None

    service.delete_conversation("alice", created.conversation_id)
    assert service.get_conversation("alice", created.conversation_id) is None
    assert service.list_conversations("alice").conversations == []


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
    assert [e.kind for e in events if e.kind != "stage"] == ["delta", "delta", "done"]
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


def test_ordinary_generation_uses_one_global_policy_snapshot(
    service: ChatService,
) -> None:
    adapter = _ProgrammableStreamAdapter(
        chunks=[StreamChunk(kind="delta", delta="普通回答")]
    )
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service._gateway = gateway

    created = service.create_conversation("alice")
    _, assistant = _start(service, created.conversation_id, "解释一下这个概念")
    queued = service.run_view_of("alice", assistant.message_id)
    assert queued is not None
    assert queued.global_writing_policy_version == RELEASE_BASELINE_POLICY_VERSION
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )

    assert len(adapter.payloads) == 1
    payload = adapter.payloads[0]
    metadata = payload["global_writing_policy"]
    assert metadata["version"] == RELEASE_BASELINE_POLICY_VERSION
    assert metadata["form"] == "explanation"
    assert any(
        message["role"] == "system"
        and "全局轻量有人味表达策略" in message["content"]
        for message in payload["messages"]
    )
    run = service._repo.get_run_by_message("alice", assistant.message_id)
    assert run is not None
    assert run.config["global_writing_policy"]["version"] == metadata["version"]


def test_ordinary_generation_restores_protected_contract_fragments(
    service: ChatService,
) -> None:
    service._gateway = _with_chunks(
        service,
        [
            StreamChunk(
                kind="delta",
                delta=(
                    "请保留 `result = 0`、$E=mc^2$、"
                    "https://example.com/changed 和 {\"answer\": 0}"
                ),
            )
        ],
    )
    created = service.create_conversation("alice")
    _, assistant = _start(
        service,
        created.conversation_id,
        "请保留 `result = 42`、$E=mc^2$、https://example.com/a?q=1 和 {\"answer\": 42}",
    )
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert "`result = 42`" in final.content
    assert "https://example.com/a?q=1" in final.content
    assert '{"answer": 42}' in final.content


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
    assert [e.kind for e in events if e.kind != "stage"] == ["delta", "error"]
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
    user_msg, retried, _ = service.retry_generation(
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
