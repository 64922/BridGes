"""Issue 07：普通聊天轻量有人味策略（形态路由、规则编译、边界与审计）。

覆盖 Test plan：
1. 每种回答形态编译快照：规则数量 6—10、优先级与不包含文章方法块。
2. 真实生成 development cases 的形态路由。
4. 有限个性化（称呼/篇幅/专业程度/表达偏好）与画像缺失降级。
5. 保护区逐字保持的编译边界声明。
6. 普通聊天路径只有一次模型生成，重试沿用原快照。
12. 快照元数据不保存系统提示、画像正文或私人聊天正文。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.chat.global_writing_policy import (
    SAFE_BASELINE_POLICY_VERSION,
    GlobalWritingPolicyCompiler,
    restore_protected_regions,
)
from bridges.chat.lightweight_policy import (
    GLOBAL_CHAT_LIGHTWEIGHT_VERSION,
    ChatLightweightPolicySnapshot,
    ChatResponseForm,
    detect_response_form,
)
from bridges.contracts.chat import ChatMode
from bridges.contracts.expression_task import (
    ConversationMode,
    EvidenceRevisionMode,
    ExpressionTaskContract,
    MaterialSufficiency,
    Operation,
    RealityMode,
    RewriteIntensity,
    SourceScope,
    Surface,
)
from bridges.contracts.profiles import ProfileSensitivityClass, ProfileSliceItem
from bridges.skills.humanizer.method_rules import METHOD_RULES_BLOCK_MARKER

#: 文章体裁规则内容（不是边界声明里的“改写证据”等一般动词）。
_ARTICLE_BODY_TERMS = ("科普必现", "体裁", "去模板腔", "钩子", "金句", "引用原句")


def _profile_item(
    value: str, dimension: str = "knowledge_interest"
) -> ProfileSliceItem:
    return ProfileSliceItem(
        assertion_id=f"assertion-{dimension}-1",
        dimension=dimension,
        value_or_rule=value,
        inclusion_reason="与当前问题相关",
        sensitivity_class=ProfileSensitivityClass.PREFERENCE,
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )


def _chat_contract(mode: ConversationMode = ConversationMode.CASUAL) -> ExpressionTaskContract:
    contract = ExpressionTaskContract(
        schema_version="expression-task-v1",
        version_hash="",
        surface=Surface.CHAT,
        operation=Operation.GENERATE_BY_TOPIC,
        conversation_mode=mode,
        reality_mode=RealityMode.REAL,
        rewrite_intensity=RewriteIntensity.STANDARD,
        speaker_position="助手（以对话者身份）",
        first_person_permission=False,
        hypothetical_permission=False,
        material_sufficiency=MaterialSufficiency.SUFFICIENT,
        source_scope=SourceScope.ORIGINAL_ONLY,
        evidence_revision_mode=EvidenceRevisionMode.PRESERVE,
        profile_item_count=0,
    )
    contract.version_hash = contract.compute_version_hash()
    return contract


def _article_contract() -> ExpressionTaskContract:
    contract = ExpressionTaskContract(
        schema_version="expression-task-v1",
        version_hash="",
        surface=Surface.ARTICLE,
        operation=Operation.REWRITE,
        conversation_mode=None,
        reality_mode=RealityMode.REAL,
        rewrite_intensity=RewriteIntensity.STANDARD,
        speaker_position="用户本人（以作者身份）",
        first_person_permission=False,
        hypothetical_permission=False,
        material_sufficiency=MaterialSufficiency.SUFFICIENT,
        source_scope=SourceScope.ORIGINAL_ONLY,
        evidence_revision_mode=EvidenceRevisionMode.PRESERVE,
        profile_item_count=0,
    )
    contract.version_hash = contract.compute_version_hash()
    return contract


# ---------------------------------------------------------------------------
# 形态路由（Test plan 2：development cases）
# ---------------------------------------------------------------------------


def test_detect_form_short_fact_question() -> None:
    assert (
        detect_response_form("光在真空中的传播速度是多少？", ChatMode.COMPANION)
        == ChatResponseForm.SHORT_ANSWER
    )


def test_detect_form_concept_explanation() -> None:
    assert (
        detect_response_form("什么是量子纠缠？为什么会有纠缠？", ChatMode.COMPANION)
        == ChatResponseForm.EXPLANATION
    )


def test_detect_form_advice_request() -> None:
    assert (
        detect_response_form("学编程坚持不下去怎么办？有什么建议吗", ChatMode.COMPANION)
        == ChatResponseForm.ADVICE
    )


def test_detect_form_correction_judgment() -> None:
    assert (
        detect_response_form("我觉得光速在水里也是每秒三十万公里，对吗？", ChatMode.COMPANION)
        == ChatResponseForm.CORRECTION
    )


def test_detect_form_empathy_low_mood_without_asking() -> None:
    assert (
        detect_response_form("今天好累，压力好大，感觉撑不住了", ChatMode.COMPANION)
        == ChatResponseForm.EMPATHY
    )


def test_detect_form_clarification_vague_command() -> None:
    assert (
        detect_response_form("帮我看看", ChatMode.COMPANION)
        == ChatResponseForm.CLARIFICATION
    )


def test_detect_form_low_confidence_compact_default() -> None:
    assert (
        detect_response_form("嗯嗯", ChatMode.COMPANION)
        == ChatResponseForm.COMPACT_DEFAULT
    )


def test_detect_form_system_signals_override_text() -> None:
    assert (
        detect_response_form(
            "帮我查一下温度", ChatMode.COMPANION, tool_error=True
        )
        == ChatResponseForm.ERROR_REFUSAL
    )
    assert (
        detect_response_form(
            "查询结果如下", ChatMode.COMPANION, tool_result=True
        )
        == ChatResponseForm.TOOL_RESULT
    )
    assert (
        detect_response_form("你好", ChatMode.COMPANION, refusal=True)
        == ChatResponseForm.ERROR_REFUSAL
    )


def test_detect_form_lesson_only_with_learning_signal() -> None:
    assert (
        detect_response_form("继续上课", ChatMode.STUDY, lesson=True)
        == ChatResponseForm.LESSON
    )
    # 学习模式普通短问不因模式自动加载课时形态。
    assert (
        detect_response_form("光速是多少？", ChatMode.STUDY)
        == ChatResponseForm.SHORT_ANSWER
    )
    # 无课时信号时即使 STUDY 也不进课时形态。
    assert (
        detect_response_form("继续上课", ChatMode.STUDY)
        == ChatResponseForm.COMPACT_DEFAULT
    )


# ---------------------------------------------------------------------------
# 编译快照：规则数量、优先级与不注入文章方法块（Test plan 1、3）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_form"),
    [
        ("光在真空中的传播速度是多少？", ChatResponseForm.SHORT_ANSWER),
        ("什么是黑洞？讲讲原理", ChatResponseForm.EXPLANATION),
        ("学编程怎么办？", ChatResponseForm.ADVICE),
        ("这个说法对吗？", ChatResponseForm.CORRECTION),
        ("今天很难过", ChatResponseForm.EMPATHY),
        ("帮我看看", ChatResponseForm.CLARIFICATION),
        ("随便聊聊", ChatResponseForm.COMPACT_DEFAULT),
    ],
)
def test_compile_snapshot_per_form_rule_count_within_limit(
    text: str, expected_form: ChatResponseForm
) -> None:
    compiler = GlobalWritingPolicyCompiler()
    snapshot = compiler.compile(ChatMode.COMPANION, user_text=text)

    assert snapshot.version == GLOBAL_CHAT_LIGHTWEIGHT_VERSION
    assert snapshot.form == expected_form
    assert 6 <= snapshot.rule_count <= 10
    assert len(snapshot.rule_ids) == snapshot.rule_count
    # 规则按固定优先级链：全局默认在前，形态规则在后。
    assert snapshot.rule_ids[:4] == (
        "answer-first",
        "brief-answer",
        "structure-optional",
        "no-filler-ending",
    )


def test_compile_snapshot_for_system_signal_forms() -> None:
    """工具结果/错误拒答/学习课时形态由系统信号确定（非文本）。"""
    tool_snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="查询结果如下", tool_result=True
    )
    error_snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="查一下", tool_error=True
    )
    lesson_snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.STUDY, user_text="继续上课", lesson=True
    )

    assert tool_snapshot.form == ChatResponseForm.TOOL_RESULT
    assert error_snapshot.form == ChatResponseForm.ERROR_REFUSAL
    assert lesson_snapshot.form == ChatResponseForm.LESSON
    for snapshot in (tool_snapshot, error_snapshot, lesson_snapshot):
        assert 6 <= snapshot.rule_count <= 10


def test_compile_snapshot_renders_rules_without_article_or_method_ids() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="什么是黑洞？讲讲原理"
    )

    block = snapshot.system_block
    assert METHOD_RULES_BLOCK_MARKER not in block
    assert "chat.frequency-alert" not in block
    assert "rewrite.main-clause-first" not in block
    assert all(term not in block for term in _ARTICLE_BODY_TERMS)
    # 渲染结果只出现中文规则，不出现英文规则 ID（审计元数据才有）。
    assert "answer-first" not in block
    assert "先直接回答当前问题" in block
    assert "没有真实下一步时不追加礼貌性尾句" in block


def test_compile_snapshot_rule_ids_cover_global_and_form_rules() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="今天很难过"
    )

    assert snapshot.form == ChatResponseForm.EMPATHY
    assert "visible-info-only" in snapshot.rule_ids
    assert "empathy-with-action" in snapshot.rule_ids
    assert "no-mind-reading" in snapshot.rule_ids
    assert "no-parroting" in snapshot.rule_ids


# ---------------------------------------------------------------------------
# 契约消费（Test plan 1：只消费 Issue 03 版本化表达任务契约）
# ---------------------------------------------------------------------------


def test_compile_consumes_expression_contract() -> None:
    compiler = GlobalWritingPolicyCompiler()
    contract = _chat_contract()

    snapshot = compiler.compile(
        ChatMode.COMPANION,
        user_text="什么是黑洞？",
        expression_contract=contract,
    )

    assert snapshot.contract_schema_version == "expression-task-v1"
    assert snapshot.contract_version_hash == contract.version_hash
    assert snapshot.form == ChatResponseForm.EXPLANATION


def test_compile_rejects_article_contract() -> None:
    with pytest.raises(ValueError, match="文章"):
        GlobalWritingPolicyCompiler().compile(
            ChatMode.COMPANION,
            user_text="帮我润色这段",
            expression_contract=_article_contract(),
        )


def test_compile_internal_contract_keeps_snapshot_bound() -> None:
    first = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="什么是黑洞？"
    )
    second = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="什么是黑洞？"
    )

    assert first.contract_version_hash == second.contract_version_hash
    assert first.contract_schema_version == "expression-task-v1"
    assert first.version == second.version


# ---------------------------------------------------------------------------
# 画像限制（Test plan 4：有限个性化与克制默认）
# ---------------------------------------------------------------------------


def test_compile_limits_profile_to_allowed_dimensions() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="什么是黑洞？",
        profile_slice_id="slice-alice-1",
        profile_items=[
            _profile_item("用户偏好称呼：小宇", "basic_information"),
            _profile_item("本科在读，专业是物理", "academic_status"),
            _profile_item("喜欢用天文例子理解概念", "knowledge_interest"),
            _profile_item("回答喜欢简短直接", "expression_habit"),
            _profile_item("喜欢打羽毛球", "hobby"),
            _profile_item("最近目标是通过期中考试", "stage_goal"),
            _profile_item("情绪有时焦虑", "emotion_trend"),
        ],
    )

    assert snapshot.profile_items == (
        "用户偏好称呼：小宇",
        "本科在读，专业是物理",
        "回答喜欢简短直接",
    )
    # 知识兴趣、爱好、阶段目标与情绪维度不在 AC9 封闭清单内，不得影响表达。
    assert "喜欢用天文例子理解概念" not in snapshot.system_block
    assert "喜欢打羽毛球" not in snapshot.system_block
    assert "通过期中考试" not in snapshot.system_block
    assert "情绪有时焦虑" not in snapshot.system_block
    assert "assertion-" not in snapshot.system_block


def test_compile_profile_missing_uses_restrained_default() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="什么是黑洞？",
        profile_items=[_profile_item("本科在读，专业是物理", "academic_status")],
        profile_failed=True,
    )

    assert snapshot.version == SAFE_BASELINE_POLICY_VERSION
    assert snapshot.fallback_reason == "profile_slice_unavailable"
    assert snapshot.profile_items == ()
    assert "不得自行推断用户经历、身份、人格或偏好" in snapshot.system_block


def test_compile_empty_profile_does_not_invent_details() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="什么是黑洞？"
    )

    assert snapshot.profile_items == ()
    assert "不得自行推断用户经历、身份、人格或偏好" in snapshot.system_block


# ---------------------------------------------------------------------------
# 保护区与降级（Test plan 5）
# ---------------------------------------------------------------------------


def test_compile_system_block_declares_protected_regions() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION, user_text="帮我运行这段代码"
    )

    assert "代码、公式、JSON、引用、链接" in snapshot.system_block
    assert "不得改写证据" in snapshot.system_block


def test_restore_protected_regions_keeps_contract_verbatim() -> None:
    original = (
        "请保留 `result = 42`、$E=mc^2$、https://example.com/a?q=1、"
        '{"answer": 42} 与引用 [1,2]。'
    )
    candidate = (
        "请保留 `result = 0`、$E=mc^2$、https://example.com/b、"
        '{"answer": 0} 与引用 [1,2]。'
    )

    restored = restore_protected_regions(original, candidate)

    assert "`result = 42`" in restored
    assert "https://example.com/a?q=1" in restored
    assert '{"answer": 42}' in restored


def test_compile_fallback_when_resource_missing() -> None:
    snapshot = GlobalWritingPolicyCompiler(resource=None).compile(
        ChatMode.STUDY,
        user_text="什么是黑洞？",
        profile_items=[_profile_item("这条画像不应进入安全基线")],
    )

    assert snapshot.version == SAFE_BASELINE_POLICY_VERSION
    assert snapshot.fallback_reason == "policy_resource_unavailable"
    assert snapshot.profile_items == ()
    assert "代码、公式、JSON、引用、链接" in snapshot.system_block


# ---------------------------------------------------------------------------
# 学习模式边界（Test plan 7：学习合同不被表达策略重写）
# ---------------------------------------------------------------------------


def test_study_short_question_does_not_load_lesson_structure() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.STUDY, user_text="光速是多少？"
    )

    assert snapshot.form == ChatResponseForm.SHORT_ANSWER
    block = snapshot.system_block
    assert "学习目标" not in block
    assert "先修知识" not in block
    assert "练习" not in block
    assert "继续邀请" not in block


def test_study_lesson_signal_loads_lesson_rules() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.STUDY, user_text="继续上课", lesson=True
    )

    assert snapshot.form == ChatResponseForm.LESSON
    assert "lesson-contract" in snapshot.rule_ids
    assert "no-forced-structure" in snapshot.rule_ids


# ---------------------------------------------------------------------------
# 重试与快照复用（Test plan 1、6：旧任务重试继续复用原策略快照）
# ---------------------------------------------------------------------------


def test_retry_reuses_original_snapshot_even_after_resource_update() -> None:
    from bridges.chat.global_writing_policy import GlobalWritingPolicyResource

    first = GlobalWritingPolicyCompiler(
        GlobalWritingPolicyResource(version="global-chat-lightweight-v0")
    ).compile(ChatMode.COMPANION, user_text="什么是黑洞？")

    updated = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="什么是黑洞？",
        existing_snapshot=first,
    )

    assert updated.version == first.version
    assert updated.form == first.form
    assert updated.rule_ids == first.rule_ids
    assert updated.system_block == first.system_block
    assert "global-chat-lightweight-v0" in updated.system_block


def test_legacy_snapshot_retry_stays_compatible() -> None:
    legacy = {
        "version": "global-humanized-writing-v2",
        "mode": ChatMode.COMPANION.value,
        "profile_slice_id": None,
        "profile_items": (),
        "profile_context": None,
        "snapshot_complete": True,
        "fallback_reason": None,
        "source_record": "legacy",
        "system_block": "旧版策略正文",
    }

    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="什么是黑洞？",
        existing_snapshot=legacy,
    )

    assert isinstance(snapshot, ChatLightweightPolicySnapshot)
    assert snapshot.version == "global-humanized-writing-v2"
    assert snapshot.system_block == "旧版策略正文"
    assert snapshot.form == ChatResponseForm.COMPACT_DEFAULT
    assert snapshot.rule_ids == ()


# ---------------------------------------------------------------------------
# 审计元数据（验收 12：不保存系统提示、画像正文或私人聊天正文）
# ---------------------------------------------------------------------------


def test_metadata_never_contains_body_text() -> None:
    snapshot = GlobalWritingPolicyCompiler().compile(
        ChatMode.COMPANION,
        user_text="什么是黑洞？量子纠缠又是怎么回事",
        profile_slice_id="slice-alice-1",
        profile_items=[_profile_item("喜欢用天文例子理解概念")],
    )

    metadata = snapshot.metadata()

    assert metadata["version"] == GLOBAL_CHAT_LIGHTWEIGHT_VERSION
    assert metadata["form"] == ChatResponseForm.EXPLANATION.value
    assert metadata["rule_count"] == snapshot.rule_count
    assert "contract_version_hash" in metadata
    assert "什么是黑洞" not in str(metadata)
    assert "天文例子" not in str(metadata)
    assert snapshot.system_block not in str(metadata)
    assert "assertion-" not in str(metadata)


def test_seed_produces_unbound_snapshot() -> None:
    seed = GlobalWritingPolicyCompiler().seed(ChatMode.COMPANION)

    assert seed.snapshot_complete is False
    assert seed.version == GLOBAL_CHAT_LIGHTWEIGHT_VERSION
    assert seed.form == ChatResponseForm.COMPACT_DEFAULT
    assert seed.system_block != ""


# ---------------------------------------------------------------------------
# 集成：一次模型生成 + 重试沿用快照（Test plan 6）
# ---------------------------------------------------------------------------


class _CountingStreamAdapter:
    """可编程流式适配器：记录 payload，统计调用次数。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.stream_calls = 0

    def stream_call(
        self,
        capability: Any,
        run_context: Any,
        payload: dict[str, Any],
    ):
        from bridges.ai.adapters import StreamChunk

        self.stream_calls += 1
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="普通回答")


@pytest.fixture
def counting_env(tmp_path: Path) -> dict[str, Any]:
    from bridges.ai import ModelGateway
    from bridges.ai.capability_registry import CapabilityRegistry
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.service import ChatService
    from bridges.contracts.ai import CapabilityKind, CapabilityRecord
    from bridges.contracts.projects import ObjectDomain
    from bridges.contracts.workflows import RunContextEnvelope
    from bridges.storage.database import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus-2026-05-26",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
        )
    )
    adapter = _CountingStreamAdapter()
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service = ChatService(repository=repository, gateway=gateway)
    return {
        "service": service,
        "adapter": adapter,
        "context": RunContextEnvelope(
            run_id="run-1",
            account_id="alice",
            project_id="conversation-1",
            workflow_name="chat",
            workflow_version="1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            submitted_at=datetime.now(UTC),
        ),
    }


def test_ordinary_generation_uses_exactly_one_model_call(counting_env) -> None:
    service = counting_env["service"]
    adapter = counting_env["adapter"]
    context = counting_env["context"]

    created = service.create_conversation("alice")
    _, assistant = service.start_generation(
        "alice", created.conversation_id, "什么是黑洞？讲讲原理"
    )
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, context
        )
    )

    assert adapter.stream_calls == 1
    assert len(adapter.payloads) == 1
    payload = adapter.payloads[0]
    metadata = payload["global_writing_policy"]
    assert metadata["version"] == GLOBAL_CHAT_LIGHTWEIGHT_VERSION
    assert metadata["form"] == ChatResponseForm.EXPLANATION.value
    run = service._repo.get_run_by_message("alice", assistant.message_id)
    stored = (run.config or {})["global_writing_policy"]
    assert stored["form"] == ChatResponseForm.EXPLANATION.value
    assert stored["contract_schema_version"] == "expression-task-v1"


def test_retry_reuses_stored_form_snapshot_without_second_compile(counting_env) -> None:
    service = counting_env["service"]
    adapter = counting_env["adapter"]
    context = counting_env["context"]

    created = service.create_conversation("alice")
    _, assistant = service.start_generation(
        "alice", created.conversation_id, "什么是黑洞？"
    )
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, context
        )
    )
    stored_first = service._repo.get_run_by_message(
        "alice", assistant.message_id
    ).config["global_writing_policy"]

    # 创建重试尝试（沿用旧轮次策略快照）。
    owner, retried = service.retry_generation(
        "alice", created.conversation_id, assistant.message_id
    )
    list(
        service.stream_generation(
            "alice", created.conversation_id, retried.message_id, context
        )
    )
    stored_second = service._repo.get_run_by_message(
        "alice", retried.message_id
    ).config["global_writing_policy"]

    assert adapter.stream_calls == 2  # 每轮各一次，绝无策略触发的隐式第二次
    assert stored_second["version"] == stored_first["version"]
    assert stored_second["form"] == stored_first["form"]
    assert stored_second["contract_version_hash"] == stored_first["contract_version_hash"]
    assert stored_second["rule_ids"] == stored_first["rule_ids"]


def test_streaming_generation_keeps_business_terminal_state(counting_env) -> None:
    """表达策略不改变业务终态（Test plan 7 的流式回归代表）。"""
    from bridges.chat.service import ChatMessageStatus

    service = counting_env["service"]
    adapter = counting_env["adapter"]
    context = counting_env["context"]

    created = service.create_conversation("alice")
    _, assistant = service.start_generation(
        "alice", created.conversation_id, "光速是多少？"
    )
    events = list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, context
        )
    )
    messages = service._repo.list_messages("alice", created.conversation_id)
    done = next(m for m in messages if m.role.value == "assistant")
    assert done.status == ChatMessageStatus.DONE
    assert done.content == "普通回答"
    assert events  # 流式事件照常推进
    assert adapter.stream_calls == 1
