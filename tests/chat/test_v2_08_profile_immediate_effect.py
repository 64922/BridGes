"""V2 Issue 08：聊天 × 原子画像的回合接缝。

验证「记住／忘掉本轮立即生效」在真实回合管线里成立：指令在生成前落库，
本轮画像块与记忆结果块都据此构造；删除的条目在下一轮上下文中消失，旧消息
重放不会让它复活；原子切片无类别渲染；输入预算紧张时先裁画像材料。
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
from bridges.contracts.atomic_profile import AtomicProfileItemModifyRequest
from bridges.contracts.chat import ChatMessageStatus, ContextNoteState
from bridges.contracts.observability import AuditAction
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
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.service import ProfileService
from bridges.storage.database import BridgesDatabase

ACCOUNT = "alice"
# 本地规则抽取器能从这句话稳定抽出一条长期信息（用于验证自动抽取镜像）。
GOAL_MESSAGE = "我的目标是今年通过雅思考试"
GOAL_ITEM = "今年通过雅思考试"
# 「记住／忘掉」指令的直接对象；抽取器不会自发产出这句话。
STUDY_TEXT = "我在准备雅思考试"


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

    def __init__(self, tmp_path: Path, account: str = ACCOUNT) -> None:
        self.account = account
        self.path = tmp_path / f"bridges-{account}.db"
        database = BridgesDatabase(self.path)
        database.initialize()
        self.database = database
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
        self.conversation_id = self.chat.create_conversation(
            self.account
        ).conversation_id
        self.turn = 0

    def start(self, content: str) -> tuple[Any, Any]:
        """发送一条消息，返回 (用户消息, 助手消息)；此时尚未生成。"""

        user, assistant, _ = self.chat.start_generation(
            self.account, self.conversation_id, content
        )
        return user, assistant

    def run(self, assistant: Any, **kwargs: Any) -> Any:
        """消费生成流并返回助手消息终态投影。"""

        self.turn += 1
        list(
            self.chat.stream_generation(
                self.account,
                self.conversation_id,
                assistant.message_id,
                _context(f"run-{self.turn}"),
                **kwargs,
            )
        )
        final = self.chat.message_projection(self.account, assistant.message_id)
        assert final is not None and final.status == ChatMessageStatus.DONE
        return final

    def payload(self) -> dict[str, Any]:
        return self.adapter.payloads[-1]

    def system_blocks(self, marker: str) -> list[str]:
        return [
            message["content"]
            for message in self.payload()["messages"]
            if message["role"] == "system" and marker in message["content"]
        ]

    def items(self) -> list[str]:
        return [item.text for item in self.atomic.projections(self.account)]

    def slice_audits(self) -> list[Any]:
        return self.observability.list_audit_events(
            account_id=self.account, action=AuditAction.PROFILE_SLICE_USED
        )


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    return _Env(tmp_path)


_SLICE_MARKER = "以下是本轮为你参考的已授权信息"
_MEMORY_MARKER = "【本轮记忆操作结果】"


def test_remember_applies_before_this_turn_and_enters_the_same_slice(
    env: _Env,
) -> None:
    """「记住」在生成前落库，本轮就带着这条无类别信息回答。"""

    _, assistant = env.start(f"请记住：{STUDY_TEXT}")

    # 生成还没开始，条目已经是用户权威版本（本轮立即生效）。
    assert env.items() == [STUDY_TEXT]
    assert env.atomic.projections(ACCOUNT)[0].user_edited_at is not None

    final = env.run(assistant)

    slice_blocks = env.system_blocks(_SLICE_MARKER)
    assert len(slice_blocks) == 1
    assert STUDY_TEXT in slice_blocks[0]
    # 原子切片对模型也不带类别。
    assert "（类别：" not in slice_blocks[0]

    memory_blocks = env.system_blocks(_MEMORY_MARKER)
    assert len(memory_blocks) == 1
    assert "已真实写入用户画像列表" in memory_blocks[0]
    # 结果块只讲状态，不复制刚记住的正文（正文另有本轮用户消息）。
    assert STUDY_TEXT not in memory_blocks[0]

    note = final.context_note
    assert note is not None
    assert note.state == ContextNoteState.READY
    assert note.profile_item_count == 1
    assert "（类别" not in note.note


def test_forget_deletes_before_this_turn_and_next_slice_is_empty(env: _Env) -> None:
    """「忘掉」本轮立即生效：本轮与下一轮上下文都不再包含被删内容。"""

    _, seeded = env.start(GOAL_MESSAGE)
    env.run(seeded)
    assert env.items() == [GOAL_ITEM]

    _, forget = env.start(f"忘掉{GOAL_ITEM}")

    # 生成前条目已删除，且不是靠事后过滤。
    assert env.items() == []

    final = env.run(forget)
    memory_blocks = env.system_blocks(_MEMORY_MARKER)
    assert len(memory_blocks) == 1
    assert "已从用户画像中删除匹配的条目" in memory_blocks[0]
    assert env.system_blocks(_SLICE_MARKER) == []
    note = final.context_note
    assert note is not None
    assert note.state == ContextNoteState.EMPTY
    assert note.profile_item_count == 0

    # 下一轮同一个问题：被删内容不得回到上下文。
    _, question = env.start("帮我安排雅思考试的复习计划")
    next_final = env.run(question)
    assert env.system_blocks(_SLICE_MARKER) == []
    assert next_final.context_note is not None
    assert next_final.context_note.state == ContextNoteState.EMPTY


def test_replayed_message_does_not_revive_a_deleted_item(env: _Env) -> None:
    """旧消息重放：同一条消息再抽取一次，被删除的条目不会复活。"""

    _, seeded = env.start(GOAL_MESSAGE)
    env.run(seeded)
    item = env.atomic.projections(ACCOUNT)[0]
    env.atomic.delete_item(ACCOUNT, item.profile_item_id, item.version)

    _, replay = env.start(GOAL_MESSAGE)
    final = env.run(replay)

    assert env.items() == []
    assert env.system_blocks(_SLICE_MARKER) == []
    assert final.context_note is not None
    assert final.context_note.state == ContextNoteState.EMPTY


def test_automatic_extraction_mirrors_into_the_atomic_list(env: _Env) -> None:
    """普通消息的自动抽取镜像成无类别条目，来源指向本轮用户消息。"""

    user, assistant = env.start(GOAL_MESSAGE)

    projection = env.atomic.projections(ACCOUNT)[0]
    assert projection.text == GOAL_ITEM
    assert projection.source_message_ids == [user.message_id]
    assert projection.write_origin.value == "automatic"
    # 设计口径：普通异步提取下一轮生效，本轮不把它塞进上下文。
    final = env.run(assistant)
    assert env.system_blocks(_SLICE_MARKER) == []
    assert final.context_note is not None
    assert final.context_note.state == ContextNoteState.EMPTY

    # 换个话题：与本轮任务无关的条目同样不会被塞进上下文。
    _, unrelated = env.start("你好，今天先随便聊聊")
    unrelated_final = env.run(unrelated)
    assert env.system_blocks(_SLICE_MARKER) == []
    assert unrelated_final.context_note is not None
    assert unrelated_final.context_note.state == ContextNoteState.EMPTY

    # 再回到相关提问：这时才带这一条，且无类别标签。
    _, question = env.start("帮我安排雅思考试的复习计划")
    env.run(question)
    blocks = env.system_blocks(_SLICE_MARKER)
    assert len(blocks) == 1
    assert GOAL_ITEM in blocks[0]
    assert "（类别：" not in blocks[0]


def test_user_edit_wins_over_later_extraction(env: _Env) -> None:
    """用户改过的条目不会被后续抽取改写回旧正文。"""

    _, seeded = env.start(GOAL_MESSAGE)
    env.run(seeded)
    item = env.atomic.projections(ACCOUNT)[0]
    env.atomic.modify_item(
        ACCOUNT,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text="目标改成明年通过雅思", version=item.version),
    )

    _, replay = env.start(GOAL_MESSAGE)
    env.run(replay)

    assert env.items() == ["目标改成明年通过雅思"]


def test_profile_block_is_trimmed_first_when_input_budget_is_tight(
    env: _Env,
) -> None:
    """输入预算紧张时先裁画像材料，并且如实披露本轮没用画像。"""

    _, seeded = env.start(GOAL_MESSAGE)
    env.run(seeded)

    # 相关性先确认：这条提问确实命中该条目，紧预算下被裁不是因为无关。
    probe = env.atomic.compile_chat_slice(
        ACCOUNT, run_id="probe", current_question="帮我安排雅思考试的复习计划"
    )
    assert [item.value_or_rule for item in probe.included_items] == [GOAL_ITEM]

    _, generous = env.start("帮我安排雅思考试的复习计划")
    generous_final = env.run(generous)
    assert len(env.system_blocks(_SLICE_MARKER)) == 1
    assert generous_final.context_note is not None
    assert generous_final.context_note.state == ContextNoteState.READY

    _, tight = env.start("帮我安排雅思考试的复习计划")
    tight_final = env.run(
        tight,
        context_budget={"input_budget_tokens": 100, "input_token_estimate": 100},
    )

    assert env.system_blocks(_SLICE_MARKER) == []
    assert tight_final.context_note is not None
    assert tight_final.context_note.state == ContextNoteState.EMPTY
    # 披露审计如实区分「用了 1 条」与「因为预算裁掉 1 条」。
    details = [event.details for event in env.slice_audits()]
    assert details[-2]["item_count"] == 1
    assert details[-2]["excluded_count"] == 0
    assert details[-1]["item_count"] == 0
    assert details[-1]["excluded_count"] == 1


def test_atomic_items_stay_account_scoped_through_the_turn(
    env: _Env, tmp_path: Path
) -> None:
    """另一个账户的回合看不到、也带不上别人的条目。"""

    _, seeded = env.start(f"请记住：{STUDY_TEXT}")
    env.run(seeded)
    assert env.items() == [STUDY_TEXT]

    other = _Env(tmp_path, account="bob")
    _, assistant = other.start("帮我安排雅思考试的复习计划")
    other_final = other.run(assistant)

    assert other.items() == []
    assert other.system_blocks(_SLICE_MARKER) == []
    assert other_final.context_note is not None
    assert other_final.context_note.state == ContextNoteState.EMPTY
