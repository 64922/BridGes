"""V2 Issue 05：按保留意图绑定事实片段，修复盲替换。

覆盖验收标准：

1. 甲/乙两个同类片段逐一正确绑定，顺序变化不串对象。
2. 用户授权的纠正（``x = 1`` → ``x = 2``）与任务计算出的新值保留；合法引用
   ``a`` 不被替换成 ``b``。
3. 非必需遗漏片段不被强行补尾；是否必须出现由任务合同（``append_missing``）
   决定；缺失与错误分别处理。
4. 裸数字、中文单位、否定、条件、因果与结论强度有明确语义参考判断，不只断言
   字符串存在。
5. 净室原创与已退役文章人味化边界保持，修改不新增人味化模型调用（保护区恢复
   是确定性纯函数，不经过模型网关）。

协议级用例直接调用 :mod:`bridges.chat.fact_protection`；链路级用例走正式
``ChatService`` 普通生成链，验证落库正文。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.fact_protection import (
    FACT_PROTECTION_PROTOCOL_VERSION,
    FragmentKind,
    ProtectionIntent,
    assess_semantic_reference,
    compile_protected_fragments,
    detect_protection_intent,
    plan_fragment_protection,
)
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.storage.database import BridgesDatabase

# ---------------------------------------------------------------------------
# 协议级：多片段逐一绑定、换序、已消费目标不复用
# ---------------------------------------------------------------------------


def test_similar_fragments_bind_by_object_anchor() -> None:
    """甲/乙两个同类片段逐一正确绑定：甲 10 ms、乙 20 ms 各回本位。"""
    result = plan_fragment_protection(
        "甲 10 ms；乙 20 ms", "甲 11 ms；乙 21 ms"
    )

    assert result.content == "甲 10 ms；乙 20 ms"
    assert result.intent is ProtectionIntent.VERBATIM
    # 绑定明细按对象锚点给出，可审计。
    assert ("number_with_unit", "10 ms", "11 ms") in result.bindings
    assert ("number_with_unit", "20 ms", "21 ms") in result.bindings


def test_reordered_fragments_do_not_cross_objects() -> None:
    """顺序变化不串对象：候选把乙提前，仍按对象标签各自回填。"""
    result = plan_fragment_protection(
        "甲 10 ms；乙 20 ms", "乙 21 ms；甲 11 ms"
    )

    assert result.content == "乙 20 ms；甲 10 ms"


def test_consumed_target_is_not_reused_for_another_source() -> None:
    """一个候选片段不能同时回填两个源片段（旧盲替换会复用第一个同类候选）。"""
    result = plan_fragment_protection("甲 10 ms；乙 20 ms", "甲 11 ms")

    assert result.content == "甲 10 ms"
    # 乙 的片段没有可绑定的候选，记为缺失，绝不复用甲的候选。
    assert [item.kind for item in result.inconsistencies] == ["missing_fragment"]


def test_extra_candidate_fragment_is_not_touched() -> None:
    """候选里新增/计算出的同类片段不被回填覆盖。"""
    result = plan_fragment_protection("结果 10 ms", "结果 11 ms，另参考 21 ms")

    assert result.content == "结果 10 ms，另参考 21 ms"
    assert "21 ms" in result.content


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        ("甲 10 ms；乙 20 ms", "乙的值为 21 ms；甲的值为 11 ms"),
        ("甲 10 ms", "乙 20 ms"),
        ("值 10 ms；值 20 ms", "值 21 ms；值 11 ms"),
    ],
)
def test_unreliable_objects_are_never_paired_by_count(source: str, candidate: str) -> None:
    result = plan_fragment_protection(source, candidate)

    assert result.content == candidate
    assert result.inconsistencies


def test_correction_does_not_release_adjacent_preserved_object() -> None:
    result = plan_fragment_protection(
        "请把 `x = 1` 改为 `x = 2`，保持 `y = 3` 不变。",
        "请把 `x = 2`，保持 `y = 4` 不变。",
    )

    assert "`x = 2`" in result.content
    assert "`y = 3`" in result.content
    assert "`y = 4`" not in result.content


def test_correct_reordered_values_survive_changed_labels() -> None:
    candidate = "乙的值为 20 ms；甲的值为 10 ms"
    result = plan_fragment_protection("甲 10 ms；乙 20 ms", candidate)
    assert result.content == candidate
    assert not result.has_critical_inconsistency


def test_matching_values_do_not_hide_swapped_known_objects() -> None:
    result = plan_fragment_protection("甲 10 ms；乙 20 ms", "甲 20 ms；乙 10 ms")
    assert result.content == "甲 10 ms；乙 20 ms"


@pytest.mark.parametrize("query", [
    "请把 `x = 1` 改为 `x = 2` 并保留 `y = 3` 不变。",
    "请保留 `y = 3` 并把 `x = 1` 改为 `x = 2`。",
])
def test_same_sentence_mixed_intents_accept_correct_answer(query: str) -> None:
    result = plan_fragment_protection(query, "`x = 2`，`y = 3`。")
    assert result.content == "`x = 2`，`y = 3`。"
    assert not result.has_critical_inconsistency


def test_same_sentence_correction_cannot_release_preserved_object() -> None:
    result = plan_fragment_protection(
        "请把 `x = 1` 改为 `x = 2` 并保留 `y = 3` 不变。",
        "`x = 2`，`y = 4`。",
    )
    assert "`x = 2`" in result.content
    assert result.has_critical_inconsistency


def test_explicit_verbatim_contract_makes_missing_fragment_critical() -> None:
    result = plan_fragment_protection("`a`", "", intent=ProtectionIntent.VERBATIM)
    assert result.has_critical_inconsistency


def test_forbidden_correction_is_a_required_preservation_contract() -> None:
    result = plan_fragment_protection("不要把 `x = 1` 改为 `x = 2`。", "`x = 2`。")
    assert result.intent is ProtectionIntent.VERBATIM
    assert result.has_critical_inconsistency


def test_unrelated_previous_object_is_not_a_correction_target() -> None:
    result = plan_fragment_protection(
        "保持甲 `x = 1`。乙应改为 `x = 2`。",
        "保持甲 `x = 9`。乙应改为 `x = 2`。",
    )
    assert result.content == "保持甲 `x = 1`。乙应改为 `x = 2`。"


@pytest.mark.parametrize("instruction", ["不要计算，原样保留", "原样保留这句："])
def test_negated_or_quoted_calculation_does_not_release_facts(instruction: str) -> None:
    source = instruction + "甲 10 ms；乙 20 ms"
    if instruction.endswith("："):
        source = '原样保留这句：“计算甲 10 ms；乙 20 ms”'
    result = plan_fragment_protection(source, source.replace("10 ms", "11 ms"))

    assert "10 ms" in result.content
    assert "11 ms" not in result.content


def test_missing_fragment_is_not_force_appended() -> None:
    """非必需遗漏片段不强行补尾，并记为可裁决的不一致。"""
    result = plan_fragment_protection("见 `a` 与 `b`。", "见 `a`。")

    assert result.content == "见 `a`。"
    assert "`b`" not in result.content
    assert [item.kind for item in result.inconsistencies] == ["missing_fragment"]


def test_missing_fragment_appended_only_when_task_requires() -> None:
    """任务合同要求片段必须出现时，才补回遗漏片段。"""
    result = plan_fragment_protection(
        "见 `a` 与 `b`。", "见 `a`。", append_missing=True
    )

    assert "`a`" in result.content
    assert "`b`" in result.content
    assert result.inconsistencies == ()


# ---------------------------------------------------------------------------
# 协议级：保留意图（原样保留 vs 允许纠正/计算）
# ---------------------------------------------------------------------------


def test_detect_protection_intent_defaults_to_verbatim() -> None:
    assert detect_protection_intent("帮我自然地讲解这段记录") is ProtectionIntent.REFERENCE
    assert detect_protection_intent("不要改动事实，保持准确") is ProtectionIntent.VERBATIM
    assert detect_protection_intent("请把数值改为 2") is ProtectionIntent.CORRECTION
    assert detect_protection_intent("计算这个表达式的值") is ProtectionIntent.CORRECTION


def test_user_authorized_correction_is_preserved() -> None:
    """明确把 x=1 改为 x=2 时，保留用户授权结果，不把旧输入当事实锁。"""
    result = plan_fragment_protection(
        "这行 `x = 1` 不对，请把数值改为 2。",
        "改为 `x = 2`。",
    )

    assert result.content == "改为 `x = 2`。"
    assert result.intent is ProtectionIntent.CORRECTION


def test_computed_new_value_is_not_overwritten() -> None:
    """计算任务：新算出的值不被原输入片段覆盖。"""
    result = plan_fragment_protection(
        "已知 `x = 1`，请计算 `y = x + 1`。",
        "已知 `x = 1`，所以 `y = 2`。",
    )

    assert result.content == "已知 `x = 1`，所以 `y = 2`。"


# ---------------------------------------------------------------------------
# 协议级：可引用来源只限定资格
# ---------------------------------------------------------------------------


def test_legal_citation_is_not_replaced_by_another_source() -> None:
    """已合法选中的来源 a 不被清单里的 b 强制换掉。"""
    result = plan_fragment_protection(
        "",
        "应看甲的证据 https://example.com/a",
        additional_sources=("https://example.com/a", "https://example.com/b"),
    )

    assert result.content == "应看甲的证据 https://example.com/a"
    assert result.inconsistencies == ()


def test_ineligible_citation_is_not_replaced_even_with_one_eligible_source() -> None:
    """资格清单不能证明来源关系，即使只有一个来源也不能猜测换链接。"""
    result = plan_fragment_protection(
        "",
        "答案见 https://example.com/changed",
        additional_sources=("https://example.com/source",),
    )

    assert result.content == "答案见 https://example.com/changed"
    assert [item.kind for item in result.inconsistencies] == ["ineligible_citation"]
    assert result.has_critical_inconsistency


def test_ineligible_citation_without_eligible_source_is_flagged() -> None:
    """清单外链接且无可用合法来源时记录不一致，不伪造来源。"""
    result = plan_fragment_protection(
        "",
        "见 https://example.com/source 和 https://example.com/fabricated",
        additional_sources=("https://example.com/source",),
    )

    assert "https://example.com/source" in result.content
    assert [item.kind for item in result.inconsistencies] == ["ineligible_citation"]


# ---------------------------------------------------------------------------
# 协议级：语义参考判断（只报告，不替换）
# ---------------------------------------------------------------------------


def test_semantic_reference_flags_bare_number_and_negation() -> None:
    report = assess_semantic_reference(
        "样本量为30，未发现显著差异。", "样本量为300，发现显著差异。"
    )

    assert report.bare_number_changes == (("30", "300"),)
    assert report.negation_changed is True
    assert report.is_consistent is False


def test_semantic_reference_flags_chinese_unit_condition_and_strength() -> None:
    report = assess_semantic_reference(
        "如果温度升高 10 米，则必然显著。", "温度升高 20 米，可能显著。"
    )

    assert report.chinese_unit_changes == (("10 米", "20 米"),)
    assert report.condition_changed is True
    assert report.conclusion_strength_changed is True


def test_semantic_reference_reports_causality_change() -> None:
    report = assess_semantic_reference(
        "因为样本量不足，所以结论有限。", "样本量不足，结论有限。"
    )

    assert report.causal_changed is True


def test_semantic_reference_consistent_when_unchanged() -> None:
    report = assess_semantic_reference("未发现显著差异。", "未发现显著差异。")

    assert report.is_consistent is True


def test_semantic_check_is_opt_in_on_plan() -> None:
    result = plan_fragment_protection(
        "样本量为30。", "样本量为300。", semantic_check=True
    )

    assert result.semantic is not None
    assert result.semantic.bare_number_changes == (("30", "300"),)
    # 语义参考判断不做替换：裸数字不在保护区，正文保持候选。
    assert result.content == "样本量为300。"


@pytest.mark.parametrize(
    ("source", "answer"),
    [("1 s 等于多少 ms？", "1000 ms。"),
     ("`x = 1` 执行后，`x + 1` 是多少？", "`2`。"),
     ("解释 `x + 1` 的结果", "`2`。"),
     ("把 1 s 表示成毫秒。", "1000 ms。"),
     ("解释 `保留` 的含义？", "保留就是留下。")],
)
def test_question_inputs_are_references_not_required_output(source: str, answer: str) -> None:
    result = plan_fragment_protection(source, answer)

    assert result.intent is ProtectionIntent.REFERENCE
    assert result.content == answer
    assert not result.inconsistencies


def test_semantic_reference_detects_negation_moving_between_objects() -> None:
    result = assess_semantic_reference("甲不会提高，乙会提高。", "甲会提高，乙不会提高。")
    assert result.negation_changed


# ---------------------------------------------------------------------------
# 协议级：可绑定片段协议接缝与净室边界
# ---------------------------------------------------------------------------


def test_protocol_version_constant_is_published_for_consumers() -> None:
    """Issue 06/21 复用同一可绑定片段协议版本。"""
    assert FACT_PROTECTION_PROTOCOL_VERSION == "intent-bound-fragment-v1"
    result = plan_fragment_protection("`a`", "`b`")
    assert result.protocol_version == FACT_PROTECTION_PROTOCOL_VERSION


def test_compile_protected_fragments_drops_nested_matches() -> None:
    fragments = compile_protected_fragments("```py\nx = 1\n``` 与 `y`")

    kinds = [fragment.kind for fragment in fragments]
    assert FragmentKind.CODE_FENCE in kinds
    assert FragmentKind.INLINE_CODE in kinds
    # 代码围栏内部不再产生重叠片段。
    assert sum(kind is FragmentKind.CODE_FENCE for kind in kinds) == 1


# ---------------------------------------------------------------------------
# 链路级：正式普通生成链验证落库正文
# ---------------------------------------------------------------------------

USER_QUERY = "帮我自然地讲解这段实验记录，不要改动事实：\n甲 10 ms；乙 20 ms。"
DRIFTED_ANSWER = "好的，帮你顺一下：甲 11 ms；乙 21 ms。"


def _context(run_id: str = "run-issue05") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="alice",
        project_id="conversation-issue05",
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


class _DriftingStreamAdapter:
    """把甲/乙两个同类片段一并换写的流式适配器，并统计调用次数。"""

    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.stream_calls = 0

    def call(self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]) -> Any:
        from bridges.ai.adapters import AdapterResult

        return AdapterResult(
            actual_model_id=capability.model_id, output={"content": self._answer}
        )

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ):
        self.stream_calls += 1
        for index in range(0, len(self._answer), 8):
            yield StreamChunk(kind="delta", delta=self._answer[index : index + 8])


def _make_chain(
    tmp_path: Path,
    answer: str,
    observability: ObservabilityService | None = None,
) -> tuple[ChatService, _DriftingStreamAdapter]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    adapter = _DriftingStreamAdapter(answer)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return ChatService(
        repository=repository, gateway=gateway, observability_service=observability
    ), adapter


@pytest.fixture
def chain(tmp_path: Path) -> tuple[ChatService, _DriftingStreamAdapter]:
    return _make_chain(tmp_path, DRIFTED_ANSWER)


def test_generation_chain_restores_multi_fragment_facts(
    chain: tuple[ChatService, _DriftingStreamAdapter],
) -> None:
    """普通生成链：同类多片段逐一回填，且不新增人味化模型调用。"""
    service, adapter = chain
    created = service.create_conversation("alice", mode=ChatMode.COMPANION)
    _, assistant, _ = service.start_generation(
        "alice", created.conversation_id, USER_QUERY
    )
    list(
        service.stream_generation(
            "alice", created.conversation_id, assistant.message_id, _context()
        )
    )

    message = service._repo.get_message("alice", assistant.message_id)  # noqa: SLF001
    assert message is not None
    assert message.status == ChatMessageStatus.DONE
    assert "甲 10 ms" in message.content
    assert "乙 20 ms" in message.content
    assert "甲 11 ms" not in message.content
    assert "乙 21 ms" not in message.content
    # 保护区恢复是确定性纯函数，整轮仍只有一次模型生成调用。
    assert adapter.stream_calls == 1


@pytest.mark.parametrize(("query", "result"), [
    ("请原样保留：甲 10 ms；乙 20 ms", AuditResult.BLOCKED),
    ("甲 10 ms；乙 20 ms", AuditResult.DEGRADED),
])
def test_fact_protection_audit_matches_verdict_without_fragment_content(
    tmp_path: Path, query: str, result: AuditResult,
) -> None:
    observability = ObservabilityService()
    service, _ = _make_chain(tmp_path, "甲 10 ms", observability)
    created = service.create_conversation("alice", mode=ChatMode.COMPANION)
    _, assistant, _ = service.start_generation("alice", created.conversation_id, query)
    list(service.stream_generation(
        "alice", created.conversation_id, assistant.message_id, _context()
    ))
    audit = [event for event in observability.list_audit_events(account_id="alice")
             if event.action is AuditAction.FACT_PROTECTION]
    assert len(audit) == 1
    assert audit[0].result is result
    assert audit[0].details["count"] == 1
    assert "20 ms" not in str(audit[0].details)


@pytest.mark.parametrize(
    ("query", "answer", "status"),
    [
        ("请原样保留：甲 10 ms；乙 20 ms", "甲 10 ms", ChatMessageStatus.ERROR),
        ("甲 10 ms；乙 20 ms", "甲 10 ms", ChatMessageStatus.DONE),
        ("请原样保留：甲 10 ms", "乙 20 ms", ChatMessageStatus.ERROR),
        ("1 s 等于多少 ms？", "1000 ms。", ChatMessageStatus.DONE),
        ("请把 `x = 1` 改为 `x = 2`", "`x = 2`", ChatMessageStatus.DONE),
        ("不要把 `x = 1` 改为 `x = 2`。", "`x = 2`。", ChatMessageStatus.ERROR),
        ("请保留 `result = 42`", "结果见 `result = 0`", ChatMessageStatus.ERROR),
    ],
)
def test_generation_chain_applies_task_specific_terminal_status(
    chain: tuple[ChatService, _DriftingStreamAdapter],
    query: str,
    answer: str,
    status: ChatMessageStatus,
) -> None:
    service, adapter = chain
    adapter._answer = answer
    created = service.create_conversation("alice", mode=ChatMode.COMPANION)
    _, assistant, _ = service.start_generation("alice", created.conversation_id, query)
    events = list(service.stream_generation(
        "alice", created.conversation_id, assistant.message_id, _context()
    ))

    message = service._repo.get_message("alice", assistant.message_id)
    assert message is not None
    assert message.status == status
    assert message.content == answer
    assert events[-1].kind == ("error" if status is ChatMessageStatus.ERROR else "done")
    if status is ChatMessageStatus.ERROR:
        assert message.error_code == "fact_protection_inconsistent"
    assert adapter.stream_calls == 1
