"""Issue 27: 聊天 × 最小画像切片、披露与反馈闭环集成测试。

验证：生成前按对话模式编译最小画像切片并注入模型请求（捕获适配器
载荷，确认只含期望切片）；发送前关闭画像后请求、披露与审计均不含
画像内容；「本次上下文说明」披露快照固化在消息上；「初始回答—用户
纠正—画像更新—后续回答改变」固定多轮回放；学习与日常模式注入不同
切片；编译失败不阻断生成；审计不复制画像正文；反馈幂等、账户隔离、
修正说明可记录。
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
from bridges.contracts.chat import (
    ChatMessageRole,
    ChatMessageStatus,
    ChatMode,
    ContextNoteState,
)
from bridges.contracts.feedback import (
    AnswerFeedbackRequest,
    FeedbackKind,
    FeedbackStatus,
)
from bridges.contracts.observability import AuditAction
from bridges.contracts.profiles import (
    ManualAssertionCreateRequest,
    ProfileDimension,
    ProfileSensitivityClass,
)
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.profiles import ProfileService
from bridges.profiles.sqlite_repository import SqliteProfileRepository
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


class _CapturingStreamAdapter:
    """捕获模型载荷的流式适配器（验证切片注入与关闭画像）。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="好的，我记住了。")
        yield StreamChunk(kind="done")


class _BrokenProfileService(ProfileService):
    """模拟画像服务故障：切片编译抛错（生成不得被阻断）。"""

    def compile_chat_slice(self, *args: object, **kwargs: object):
        raise RuntimeError("profile store broken")


class _PermissiveTeachingService:
    """放行证据门的假教学服务：让学习模式走到模型调用（测切片注入）。"""

    def required_search(
        self, query: str, retrieval: Any
    ) -> Any:
        from bridges.contracts.teaching import TeachingSearchSource

        return TeachingSearchSource.NONE

    def initial(self, query: str, *, recovery: bool = False) -> Any:
        return self._ready_projection()

    def classify_intent(self, text: str, mission: Any) -> Any:
        # Issue 08：放行桩不建立 mission，事实提问直接走 prepare。
        from bridges.contracts.teaching import TeachingIntent

        return TeachingIntent.FACT_QUESTION

    def prepare(self, query: str, **kwargs: object) -> Any:
        return self._ready_projection()

    @staticmethod
    def _ready_projection() -> Any:
        from bridges.contracts.teaching import (
            TeachingCardStatus,
            TeachingEvidenceGate,
            TeachingEvidenceStatus,
            TeachingSearchSource,
            TeachingTurnProjection,
        )
        from datetime import UTC, datetime

        gate = TeachingEvidenceGate(
            status=TeachingEvidenceStatus.SUFFICIENT,
            reason="测试材料充分。",
            required_search=TeachingSearchSource.NONE,
            checked_at=datetime.now(UTC),
        )
        return TeachingTurnProjection(
            status=TeachingCardStatus.READY,
            goal="理解目标",
            level_assumption="当前水平假设",
            steps=["第一步"],
            check_method="复述",
            evidence_gate=gate,
            next_prompt="下一步",
            can_answer_reliably=True,
            can_retry=True,
        )


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    profile_service = ProfileService(SqliteProfileRepository(database))
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    adapter = _CapturingStreamAdapter()
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    observability = ObservabilityService()
    service = ChatService(
        repository=repository,
        gateway=gateway,
        profile_service=profile_service,
        observability_service=observability,
    )
    return {
        "database": database,
        "chat": service,
        "profile": profile_service,
        "adapter": adapter,
        "observability": observability,
        "account": "alice",
    }


@pytest.fixture
def study_env(tmp_path: Path) -> dict[str, Any]:
    """学习模式环境：放行证据门，让模型请求可被捕获。"""
    database = BridgesDatabase(tmp_path / "study.db")
    database.initialize()
    profile_service = ProfileService(SqliteProfileRepository(database))
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    adapter = _CapturingStreamAdapter()
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        profile_service=profile_service,
        teaching_service=_PermissiveTeachingService(),
    )
    return {
        "chat": service,
        "profile": profile_service,
        "adapter": adapter,
        "account": "alice",
    }


def _manual(
    dimension: ProfileDimension,
    value: str,
    *,
    scenes: list[str] | None = None,
) -> ManualAssertionCreateRequest:
    return ManualAssertionCreateRequest(
        dimension=dimension,
        value_or_rule=value,
        applicable_scenes=scenes or ["companion", "study"],
        sensitivity_class=ProfileSensitivityClass.PREFERENCE,
        authorization_scope="general",
        source_note="用户手动记录",
    )


def _send(
    env: dict[str, Any],
    conversation_id: str,
    content: str,
    *,
    use_profile: bool = True,
    mode: str = "companion",
) -> tuple[Any, Any]:
    """发送消息并消费流事件，返回 (用户消息投影, 助手消息投影)。"""
    chat: ChatService = env["chat"]
    user, assistant = chat.start_generation(
        env["account"], conversation_id, content
    )
    list(
        chat.stream_generation(
            env["account"],
            conversation_id,
            assistant.message_id,
            _context(),
            use_profile=use_profile,
        )
    )
    final = chat.message_projection(env["account"], assistant.message_id)
    assert final is not None and final.status == ChatMessageStatus.DONE
    return user, final


def _slice_system_blocks(payload: dict[str, Any]) -> list[str]:
    """返回请求中标记为画像切片的 system 块内容。"""
    return [
        message["content"]
        for message in payload["messages"]
        if message["role"] == "system" and "画像切片" in message["content"]
    ]


def _audit_events(env: dict[str, Any], action: AuditAction) -> list[Any]:
    return env["observability"].list_audit_events(
        account_id=env["account"], action=action
    )


def test_study_mode_injects_only_expected_slice(study_env: dict[str, Any]) -> None:
    """学习模式只注入目标/知识/兴趣切片；日常偏好维度不进入请求。"""
    profile: ProfileService = study_env["profile"]
    profile.manual_create_assertion(
        study_env["account"], _manual(ProfileDimension.KNOWLEDGE_STATE, "已掌握极限与导数")
    )
    profile.manual_create_assertion(
        study_env["account"], _manual(ProfileDimension.EXPRESSION_HABIT, "喜欢简洁回答")
    )
    conversation = study_env["chat"].create_conversation(
        study_env["account"], mode=ChatMode.STUDY
    )
    _, final = _send(study_env, conversation.conversation_id, "请讲解洛必达法则")

    blocks = _slice_system_blocks(study_env["adapter"].payloads[-1])
    assert len(blocks) == 1
    assert "已掌握极限与导数" in blocks[0]
    assert "喜欢简洁回答" not in blocks[0]  # 日常偏好不进入学习模式
    # 披露快照：ready 态、类别标签、来源链接与使用时间
    note = final.context_note
    assert note is not None
    assert note.state == ContextNoteState.READY
    assert note.profile_enabled is True
    assert note.profile_items[0].dimension_label == "知识状态"
    assert note.profile_items[0].assertion_id
    assert note.profile_items[0].used_at is not None
    assert "最小切片" in note.note


def test_companion_mode_injects_only_preference_slice(env: dict[str, Any]) -> None:
    """日常模式只注入偏好/表达/基本情况；教学维度不进入请求。"""
    profile: ProfileService = env["profile"]
    profile.manual_create_assertion(
        env["account"], _manual(ProfileDimension.EXPRESSION_HABIT, "喜欢比喻表达")
    )
    profile.manual_create_assertion(
        env["account"], _manual(ProfileDimension.KNOWLEDGE_STATE, "正在学概率论")
    )
    conversation = env["chat"].create_conversation(env["account"])
    _, _ = _send(env, conversation.conversation_id, "今天有点累")

    blocks = _slice_system_blocks(env["adapter"].payloads[-1])
    assert len(blocks) == 1
    assert "喜欢比喻表达" in blocks[0]
    assert "正在学概率论" not in blocks[0]  # 教学结构不强制给日常陪伴


def test_disabling_profile_excludes_everything(env: dict[str, Any]) -> None:
    """发送前关闭画像：请求、披露与审计均不含画像内容。"""
    profile: ProfileService = env["profile"]
    profile.manual_create_assertion(
        env["account"], _manual(ProfileDimension.EXPRESSION_HABIT, "喜欢简洁回答")
    )
    conversation = env["chat"].create_conversation(env["account"])
    _, final = _send(
        env, conversation.conversation_id, "你好", use_profile=False
    )

    blocks = _slice_system_blocks(env["adapter"].payloads[-1])
    assert blocks == []  # 模型请求不含任何画像内容
    note = final.context_note
    assert note is not None
    assert note.state == ContextNoteState.OFF
    assert note.profile_enabled is False
    assert note.profile_items == []
    assert "关闭" in note.note
    # 审计记录 disabled 快照，且不复制画像正文
    events = _audit_events(env, AuditAction.PROFILE_SLICE_USED)
    assert len(events) == 1
    assert events[0].details["profile_enabled"] is False
    assert "喜欢简洁回答" not in str(events[0].model_dump())


def test_empty_profile_yields_empty_state(env: dict[str, Any]) -> None:
    """启用画像但没有匹配记录：合法空态（empty），不注入内容。"""
    conversation = env["chat"].create_conversation(env["account"])
    _, final = _send(env, conversation.conversation_id, "你好")

    assert _slice_system_blocks(env["adapter"].payloads[-1]) == []
    note = final.context_note
    assert note is not None
    assert note.state == ContextNoteState.EMPTY
    assert note.profile_items == []
    assert "没有" in note.note


def test_multi_turn_replay_after_user_correction(env: dict[str, Any]) -> None:
    """「初始回答—用户纠正—画像更新—后续回答改变」闭环回放。

    历史消息保留当时切片版本（旧值快照），下一轮使用新版本；披露可
    对比修正前后差异（版本号不同）。
    """
    profile: ProfileService = env["profile"]
    assertion = profile.manual_create_assertion(
        env["account"], _manual(ProfileDimension.EXPRESSION_HABIT, "喜欢简洁回答")
    )
    conversation = env["chat"].create_conversation(env["account"])

    # 第一轮：使用旧画像
    _, first = _send(env, conversation.conversation_id, "给我讲个科学故事")
    first_blocks = _slice_system_blocks(env["adapter"].payloads[0])
    assert "喜欢简洁回答" in first_blocks[0]
    first_note = first.context_note
    assert first_note is not None
    assert first_note.profile_items[0].version == 1

    # 用户纠正画像（与反馈闭环配套的修正动作）
    profile.modify_assertion(
        env["account"],
        assertion.assertion_id,
        value_or_rule="喜欢详细有例子",
        applicable_scenes=["companion", "study"],
        reason="用户反馈画像有误",
    )

    # 第二轮：模型请求使用新版本
    _, second = _send(env, conversation.conversation_id, "再给我讲一个")
    second_blocks = _slice_system_blocks(env["adapter"].payloads[1])
    assert "喜欢详细有例子" in second_blocks[0]
    assert "喜欢简洁回答" not in second_blocks[0]
    second_note = second.context_note
    assert second_note is not None
    assert second_note.profile_items[0].version == 2
    # 历史回答保留当时切片版本：可回放修正前后差异
    first_again = env["chat"].message_projection(
        env["account"], first.message_id
    )
    assert first_again is not None
    assert first_again.context_note is not None
    assert first_again.context_note.profile_items[0].value_summary == "喜欢简洁回答"
    assert first_again.context_note.profile_items[0].version == 1
    assert second_note.profile_items[0].version == 2


def test_withdrawn_record_stops_entering_next_turn(env: dict[str, Any]) -> None:
    """撤回后下一轮不再使用该记录；历史披露仍保留当时快照。"""
    profile: ProfileService = env["profile"]
    assertion = profile.manual_create_assertion(
        env["account"], _manual(ProfileDimension.EXPRESSION_HABIT, "喜欢幽默风格")
    )
    conversation = env["chat"].create_conversation(env["account"])
    _, first = _send(env, conversation.conversation_id, "聊天")

    profile.withdraw_assertion(env["account"], assertion.assertion_id, "不再适用")

    _, second = _send(env, conversation.conversation_id, "继续聊")
    second_blocks = _slice_system_blocks(env["adapter"].payloads[1])
    assert second_blocks == []  # 撤回后无匹配记录：不注入任何画像内容
    second_note = second.context_note
    assert second_note is not None
    assert second_note.state == ContextNoteState.EMPTY
    assert second_note.excluded_count >= 1
    first_again = env["chat"].message_projection(env["account"], first.message_id)
    assert first_again is not None and first_again.context_note is not None
    assert first_again.context_note.profile_items[0].value_summary == "喜欢幽默风格"


def test_audit_records_categories_without_private_body(env: dict[str, Any]) -> None:
    """审计只记录类别、切片 ID 与条目数，不复制画像正文或完整提示。"""
    profile: ProfileService = env["profile"]
    profile.manual_create_assertion(
        env["account"], _manual(ProfileDimension.EXPRESSION_HABIT, "绝密表达偏好")
    )
    conversation = env["chat"].create_conversation(env["account"])
    _send(env, conversation.conversation_id, "你好")

    events = _audit_events(env, AuditAction.PROFILE_SLICE_USED)
    assert len(events) == 1
    details = events[0].details
    assert details["profile_enabled"] is True
    assert details["slice_id"]
    assert details["item_count"] == 1
    assert details["mode"] == "companion"
    serialized = str(events[0].model_dump())
    assert "绝密表达偏好" not in serialized  # 画像正文不进入审计
    assert "完整画像" not in serialized


def test_broken_profile_service_does_not_block_generation(
    tmp_path: Path,
) -> None:
    """切片编译失败：披露 error 态、不注入、回答照常生成。"""
    database = BridgesDatabase(tmp_path / "broken.db")
    database.initialize()
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    adapter = _CapturingStreamAdapter()
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        profile_service=_BrokenProfileService(SqliteProfileRepository(database)),
    )
    conversation = service.create_conversation("alice")
    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, "你好"
    )
    events = list(
        service.stream_generation(
            "alice", conversation.conversation_id, assistant.message_id, _context()
        )
    )
    assert any(event.kind == "done" for event in events)
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None and final.status == ChatMessageStatus.DONE
    assert final.context_note is not None
    assert final.context_note.state == ContextNoteState.ERROR
    assert _slice_system_blocks(adapter.payloads[0]) == []  # 不注入未验证内容


def test_feedback_submit_is_idempotent_and_account_scoped(env: dict[str, Any]) -> None:
    """反馈幂等去重、按账户隔离；画像有误必须定位记录。"""
    conversation = env["chat"].create_conversation(env["account"])
    _, final = _send(env, conversation.conversation_id, "你好")

    request = AnswerFeedbackRequest(
        kind=FeedbackKind.ANSWER_INAPPROPRIATE,
        feedback_text="回答太长了，希望更简洁",
        preference="尽量三句话内说完",
    )
    first = env["chat"].submit_feedback(
        env["account"], conversation.conversation_id, final.message_id, request
    )
    duplicate = env["chat"].submit_feedback(
        env["account"], conversation.conversation_id, final.message_id, request
    )
    assert duplicate.feedback_id == first.feedback_id  # 幂等

    # 画像有误必须携带断言 ID
    with pytest.raises(Exception) as exc_info:
        env["chat"].submit_feedback(
            env["account"],
            conversation.conversation_id,
            final.message_id,
            AnswerFeedbackRequest(
                kind=FeedbackKind.PROFILE_INCORRECT, feedback_text="记录错了"
            ),
        )
    assert "请先选择" in str(exc_info.value)

    # 账户隔离：其他账户查不到这条反馈
    assert env["chat"].list_feedback("bob", conversation.conversation_id) == []

    # 修正说明可记录
    resolved = env["chat"].resolve_feedback(
        env["account"], first.feedback_id, "已将表达偏好更新为简洁风格"
    )
    assert resolved.status == FeedbackStatus.RESOLVED
    assert resolved.resolution_note == "已将表达偏好更新为简洁风格"
    # 幂等 resolve
    again = env["chat"].resolve_feedback(
        env["account"], first.feedback_id, "重复处理"
    )
    assert again.status == FeedbackStatus.RESOLVED
    assert again.resolution_note == "已将表达偏好更新为简洁风格"


def test_profile_incorrect_feedback_locates_assertion(env: dict[str, Any]) -> None:
    """画像有误反馈定位到披露中的记录，并可走修正闭环。"""
    profile: ProfileService = env["profile"]
    assertion = profile.manual_create_assertion(
        env["account"], _manual(ProfileDimension.EXPRESSION_HABIT, "喜欢简洁回答")
    )
    conversation = env["chat"].create_conversation(env["account"])
    _, final = _send(env, conversation.conversation_id, "你好")
    assert final.context_note is not None
    disclosed_id = final.context_note.profile_items[0].assertion_id
    assert disclosed_id == assertion.assertion_id

    feedback = env["chat"].submit_feedback(
        env["account"],
        conversation.conversation_id,
        final.message_id,
        AnswerFeedbackRequest(
            kind=FeedbackKind.PROFILE_INCORRECT,
            feedback_text="这条记录不准",
            assertion_id=assertion.assertion_id,
        ),
    )
    assert feedback.assertion_id == assertion.assertion_id
    # 反馈审计动作落库
    events = _audit_events(env, AuditAction.ANSWER_FEEDBACK)
    assert len(events) == 1
    assert events[0].details["assertion_id"] == assertion.assertion_id

    # 修正后下一轮使用新版本（闭环验证在 test_multi_turn_replay 中覆盖）
    profile.modify_assertion(
        env["account"],
        assertion.assertion_id,
        value_or_rule="喜欢详尽回答",
        applicable_scenes=["companion", "study"],
        reason="用户反馈画像有误",
    )
    _, next_final = _send(env, conversation.conversation_id, "再聊聊")
    assert next_final.context_note is not None
    assert next_final.context_note.profile_items[0].value_summary == "喜欢详尽回答"
    assert next_final.context_note.profile_items[0].version == 2


def test_feedback_requires_owned_message(env: dict[str, Any]) -> None:
    """跨账户/不存在的消息反馈统一安全失败。"""
    conversation = env["chat"].create_conversation(env["account"])
    with pytest.raises(Exception) as exc_info:
        env["chat"].submit_feedback(
            "bob",
            conversation.conversation_id,
            "message-does-not-exist",
            AnswerFeedbackRequest(
                kind=FeedbackKind.ANSWER_INAPPROPRIATE, feedback_text="有问题"
            ),
        )
    assert "不存在" in str(exc_info.value) or "访问权限" in str(exc_info.value)


def test_context_note_thinking_tool_entry(env: dict[str, Any]) -> None:
    """思考摘要工具条目披露画像使用情况（不暴露思维链）。"""
    profile: ProfileService = env["profile"]
    profile.manual_create_assertion(
        env["account"], _manual(ProfileDimension.EXPRESSION_HABIT, "喜欢简洁回答")
    )
    conversation = env["chat"].create_conversation(env["account"])
    _, final = _send(env, conversation.conversation_id, "你好")
    assert final.thinking is not None
    assert any("画像记录" in tool for tool in final.thinking.tools)
