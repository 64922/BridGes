"""Issue 26: 聊天与画像记忆意图的集成 seam。

The seam under test: sending a user message runs the deterministic memory
intent pipeline (explicit remember / permissioned auto-write / sensitive
candidate / transient emotion) without blocking the chat; notifications are
re-emittable per message and profile failures never break generation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.profiles import (
    ProfileDimension,
    ProfileNotificationKind,
    ProfilePermissionUpdateRequest,
)
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.profiles import ProfileService
from bridges.profiles.adapters import InMemoryProfileRepository
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
    """可编程流式适配器：预置块序列。"""

    def __init__(self, chunks: list[StreamChunk] | None = None) -> None:
        self._chunks = chunks or [StreamChunk(kind="delta", delta="好的。")]

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
        yield from self._chunks


class _BrokenProfileService(ProfileService):
    """模拟画像服务故障：任何处理都抛错（聊天不得被阻断）。"""

    def process_conversation_message(self, *args: object, **kwargs: object):
        raise RuntimeError("profile store broken")


@pytest.fixture
def chat_service(tmp_path: Path) -> ChatService:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        "qwen_text_chat", "1", _ProgrammableStreamAdapter()
    )
    return ChatService(
        repository=repository, gateway=gateway, profile_service=ProfileService(
            repository=InMemoryProfileRepository()
        )
    )


def _run_generation(
    service: ChatService, account_id: str, conversation_id: str, message_id: str
) -> None:
    list(service.stream_generation(account_id, conversation_id, message_id, _context()))


def test_explicit_remember_creates_assertion_and_notification(
    chat_service: ChatService,
) -> None:
    conversation = chat_service.create_conversation("alice")
    user_message, assistant, _ = chat_service.start_generation(
        "alice", conversation.conversation_id, "记住我的目标是今年通过雅思考试"
    )
    _run_generation(
        chat_service, "alice", conversation.conversation_id, assistant.message_id
    )
    notifications = chat_service.profile_notifications_for_message(
        "alice", conversation.conversation_id, user_message.message_id
    )
    assert len(notifications) == 1
    assert notifications[0].kind == ProfileNotificationKind.INTENT_RECORDED
    assert notifications[0].source_ref == (
        f"{conversation.conversation_id}:{user_message.message_id}"
    )
    # 断言已写入（画像服务独立可查）
    profile: ProfileService = chat_service._profiles  # type: ignore[attr-defined]
    assertions = profile.list_assertions("alice")
    assert [a.value_or_rule for a in assertions] == ["今年通过雅思考试"]


def test_permissioned_auto_write_notification_recallable(
    chat_service: ChatService,
) -> None:
    profile: ProfileService = chat_service._profiles  # type: ignore[attr-defined]
    profile.set_permission(
        "alice",
        ProfilePermissionUpdateRequest(
            dimension=ProfileDimension.INTEREST_PREFERENCE,
            scene="companion",
            enabled=True,
        ),
    )
    conversation = chat_service.create_conversation("alice")
    user_message, assistant, _ = chat_service.start_generation(
        "alice", conversation.conversation_id, "我喜欢蓝色"
    )
    _run_generation(
        chat_service, "alice", conversation.conversation_id, assistant.message_id
    )
    notifications = chat_service.profile_notifications_for_message(
        "alice", conversation.conversation_id, user_message.message_id
    )
    assert len(notifications) == 1
    assert notifications[0].kind == ProfileNotificationKind.AUTO_WRITE
    assert notifications[0].recallable is True


def test_transient_emotion_does_not_block_chat(
    chat_service: ChatService,
) -> None:
    conversation = chat_service.create_conversation("alice")
    user_message, assistant, _ = chat_service.start_generation(
        "alice", conversation.conversation_id, "我今天有点焦虑"
    )
    _run_generation(
        chat_service, "alice", conversation.conversation_id, assistant.message_id
    )
    final = chat_service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE
    notifications = chat_service.profile_notifications_for_message(
        "alice", conversation.conversation_id, user_message.message_id
    )
    assert len(notifications) == 1
    assert notifications[0].kind == ProfileNotificationKind.TRANSIENT_EMOTION
    profile: ProfileService = chat_service._profiles  # type: ignore[attr-defined]
    assert profile.list_assertions("alice") == []


def test_profile_failure_never_blocks_chat(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "failing.db")
    database.initialize()
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        "qwen_text_chat", "1", _ProgrammableStreamAdapter()
    )
    broken = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        profile_service=_BrokenProfileService(repository=InMemoryProfileRepository()),
    )
    conversation = broken.create_conversation("alice")
    _, assistant, _ = broken.start_generation(
        "alice", conversation.conversation_id, "记住我的目标是过雅思"
    )
    _run_generation(
        broken, "alice", conversation.conversation_id, assistant.message_id
    )
    final = broken.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE
    assert final.content == "好的。"


def test_retry_round_emits_same_notifications(chat_service: ChatService) -> None:
    conversation = chat_service.create_conversation("alice")
    user_message, assistant, _ = chat_service.start_generation(
        "alice", conversation.conversation_id, "记住我的目标是今年通过雅思考试"
    )
    first = chat_service.profile_notifications_for_message(
        "alice", conversation.conversation_id, user_message.message_id
    )
    assert len(first) == 1
    # 重试轮次读取同一份持久化通知，不重复写入
    again = chat_service.profile_notifications_for_message(
        "alice", conversation.conversation_id, user_message.message_id
    )
    assert [n.notification_id for n in again] == [n.notification_id for n in first]
    profile: ProfileService = chat_service._profiles  # type: ignore[attr-defined]
    assert len(profile.list_assertions("alice")) == 1
