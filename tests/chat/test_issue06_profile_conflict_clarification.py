"""画像工单 06：聊天回合里的冲突澄清接缝。

未解决冲突不进画像切片（召回门禁已整条排除），只有当前问题确实依赖冲突
属性时才注入确定性澄清问句：先用日常语言向用户确认，确认之前不把任一说
法的值当作确定信息；无关问题不打扰用户。
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
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
)
from bridges.profiles.automatic import (
    AutomaticProfileService,
    InMemoryAutomaticProfileRepository,
)
from bridges.profiles.commit import ProfileFactSubmission
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.service import ProfileService
from bridges.storage.database import BridgesDatabase

ACCOUNT = "alice"
_SLICE_MARKER = "以下是本轮为你参考的已授权信息"
_CLARIFY_MARKER = "以下信息在你此前的说法里互相矛盾"


def _context(run_id: str) -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id=ACCOUNT,
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


class _CapturingStreamAdapter:
    """捕获模型载荷的流式适配器。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="好的。")
        yield StreamChunk(kind="done")


class _Env:
    """被测组合：聊天 + 四维记录 + 自动抽取 + 原子列表（生产接线同形）。"""

    def __init__(self, tmp_path: Path) -> None:
        database = BridgesDatabase(tmp_path / "bridges.db")
        database.initialize()
        self.four_dimensions = FourDimensionProfileService(
            source_repository=InMemoryProfileRepository(),
            repository=InMemoryFourDimensionProfileRepository(),
        )
        self.atomic = AtomicProfileService(
            self.four_dimensions, InMemoryAtomicProfileRepository()
        )
        self.automatic = AutomaticProfileService(
            four_dimension_service=self.four_dimensions,
            repository=InMemoryAutomaticProfileRepository(),
            atomic_profile_service=self.atomic,
        )
        registry = CapabilityRegistry()
        registry.register(_chat_capability())
        gateway = ModelGateway(registry)
        self.adapter = _CapturingStreamAdapter()
        gateway.register_adapter("qwen_text_chat", "1", self.adapter)
        self.observability = ObservabilityService()
        self.chat = ChatService(
            repository=ConversationRepository(database),
            gateway=gateway,
            profile_service=ProfileService(repository=InMemoryProfileRepository()),
            automatic_profile_service=self.automatic,
            four_dimension_profile_service=self.four_dimensions,
            atomic_profile_service=self.atomic,
            observability_service=self.observability,
        )
        self.conversation_id = self.chat.create_conversation(ACCOUNT).conversation_id
        self.turn = 0

    def commit(self, text: str) -> None:
        self.atomic.commit_fact(
            ACCOUNT,
            ProfileFactSubmission(
                text=text,
                dimension=FourDimension.ACADEMIC_STATUS,
                confidence=FourDimensionConfidence.HIGH,
                evidence_quote=text,
            ),
        )

    def ask(self, content: str) -> None:
        _, assistant, _ = self.chat.start_generation(
            ACCOUNT, self.conversation_id, content
        )
        self.turn += 1
        list(
            self.chat.stream_generation(
                ACCOUNT,
                self.conversation_id,
                assistant.message_id,
                _context(f"run-{self.turn}"),
            )
        )
        self.automatic.run_retry_tick()
        final = self.chat.message_projection(ACCOUNT, assistant.message_id)
        assert final is not None and final.status == ChatMessageStatus.DONE

    def payload_text(self) -> str:
        return "\n".join(message["content"] for message in self.adapter.payloads[-1]["messages"])

    def slice_text(self) -> str:
        return "\n".join(
            message["content"]
            for message in self.adapter.payloads[-1]["messages"]
            if message["role"] == "system" and _SLICE_MARKER in message["content"]
        )


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    return _Env(tmp_path)


def test_conflicted_slot_is_clarified_only_when_the_question_depends_on_it(
    env: _Env,
) -> None:
    """依赖冲突属性的一轮注入澄清问句；无关问题不打扰，争议值不作事实。"""

    env.commit("我是软件工程专业")
    env.commit("我是计算机专业")

    env.ask("帮我推荐几个计算机相关的选修课")
    payload = env.payload_text()
    assert _CLARIFY_MARKER in payload
    assert "以哪个为准" in payload
    assert "我是软件工程专业" in payload
    assert "我是计算机专业" in payload
    # 争议条目不作为编号事实进入画像切片；无条目时画像块因需澄清照常渲染。
    assert _SLICE_MARKER in payload
    assert "[1]" not in env.slice_text()
    assert "请直接向用户确认" in env.slice_text()

    env.ask("今晚吃什么好")
    assert _CLARIFY_MARKER not in env.payload_text()
    assert "以哪个为准" not in env.payload_text()
