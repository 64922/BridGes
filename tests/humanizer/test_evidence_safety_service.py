"""证据安全修订服务集成测试（人味化改造 Issue 06）。

使用可编程假模型跑完整文章服务，覆盖 Issue 06 测试计划的服务面：
1. 默认 PRESERVE：风险只生成独立风险项，正文不被改动，模型只调用一次。
2. EVIDENCE_SAFE 无风险：不触发第二次调用，正文保持不变。
3. EVIDENCE_SAFE 有风险：至多一次定向修订，diff 记录前后 span 与变化类型。
4. 修订未通过来源硬门 / 受保护项冲突 / 模型调用失败：保持首稿原文，
   返回稳定 hold_for_user 状态，不自动添加保守套话。
5. 科研体裁不得因体裁自动打开 EVIDENCE_SAFE（显式授权才能打开）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterError, AdapterResult
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.contracts.evidence_safety import (
    EvidenceRevisionStatus,
    RevisionChangeType,
)
from bridges.contracts.expression_task import EvidenceRevisionMode
from bridges.contracts.humanizer import HumanizerResultStatus
from bridges.contracts.workflows import ObjectDomain, RunContextEnvelope
from bridges.skills.humanizer.intent import route_humanizer_message
from bridges.skills.humanizer.service import HumanizerService
from bridges.skills.registry import create_builtin_registry

#: 含「相关写成因果 + 无检验写显著」风险的原文
_SOURCE = "调查显示，使用该产品与满意度相关，用户满意度因此显著提升。该产品售价 199 元。"

#: 修订模型应返回的降级文本（把「因此显著提升」改为观察措辞）
_REVISED = "调查显示，使用该产品与满意度相关，用户满意度因此有所提升。该产品售价 199 元。"


def _structured_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_structured_output",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.6-flash",
        input_schema_version="structured-messages-v1",
        output_schema_version="json-schema-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.01),
    )


class _SequencedAdapter:
    """按调用序号返回不同输出：记录调用次数与载荷。"""

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self._outputs = list(outputs)
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        index = min(self.calls, len(self._outputs) - 1)
        self.calls += 1
        self.payloads.append(payload)
        return AdapterResult(actual_model_id=capability.model_id, output=self._outputs[index])


class _FailingAdapter:
    """修订调用抛错（模型失败）的适配器：首稿成功，后续失败。"""

    def __init__(self, first: dict[str, Any]) -> None:
        self._first = first
        self.calls = 0

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls += 1
        if self.calls > 1:
            raise AdapterError(
                code="adapter_error",
                message="模拟修订调用失败",
                retryable=True,
            )
        return AdapterResult(actual_model_id=capability.model_id, output=self._first)


def _gateway_with(adapter: Any) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_structured_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_structured_output", "1", adapter)
    return gateway


def _make_service(adapter: Any) -> HumanizerService:
    return HumanizerService(
        registry=create_builtin_registry(),
        gateway=_gateway_with(adapter),
    )


def _run_context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-test",
        account_id="acc-test",
        project_id="conv-test",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def _run(service: HumanizerService, skill_input: Any) -> tuple[list[Any], Any]:
    events = list(
        service.run_task(
            "acc-test",
            "conv-test",
            "msg-test",
            skill_input,
            _run_context(),
        )
    )
    result = next(e.result for e in events if e.kind == "result")
    return events, result


def _preserve_input() -> Any:
    routed = route_humanizer_message(f"帮我改写这段话：{_SOURCE}")
    assert routed is not None
    return routed.skill_input


def _evidence_safe_input() -> Any:
    routed = route_humanizer_message(f"帮我改写这段话，可以调整结论强度：{_SOURCE}")
    assert routed is not None
    return routed.skill_input


def _draft_text(final: str) -> dict[str, Any]:
    return {"final_text": final}


# ---------------------------------------------------------------------------
# 1. 默认 PRESERVE：风险独立呈现，正文不被改动，只调用一次
# ---------------------------------------------------------------------------


def test_preserve_mode_keeps_text_and_reports_risks() -> None:
    adapter = _SequencedAdapter([_draft_text(_SOURCE)])
    events, result = _run(_make_service(adapter), _preserve_input())
    assert adapter.calls == 1  # 默认模式不追加修订调用
    assert result.status == HumanizerResultStatus.DONE
    assert result.evidence_safe is not None
    assert result.evidence_safe.mode == EvidenceRevisionMode.PRESERVE
    assert result.evidence_safe.risks  # 风险项独立呈现
    assert result.evidence_safe.revisions == []
    assert result.evidence_safe.revision_status == EvidenceRevisionStatus.NOT_APPLIED
    # 正文保持原结论语义：交付的就是首稿原文
    assert _SOURCE in result.output.final_text
    assert "显著提升" in result.output.final_text


# ---------------------------------------------------------------------------
# 2. EVIDENCE_SAFE 无风险：不触发第二次调用
# ---------------------------------------------------------------------------


def test_evidence_safe_without_risks_keeps_single_call() -> None:
    plain = "今天天气不错，我们出去走走。"
    adapter = _SequencedAdapter([_draft_text(plain)])
    routed = route_humanizer_message(f"帮我改写这段话，可以调整结论强度：{plain}")
    assert routed is not None
    events, result = _run(_make_service(adapter), routed.skill_input)
    assert adapter.calls == 1
    assert result.evidence_safe is not None
    assert result.evidence_safe.risks == []
    assert result.evidence_safe.revision_status == EvidenceRevisionStatus.NOT_APPLIED


# ---------------------------------------------------------------------------
# 3. EVIDENCE_SAFE 有风险：一次定向修订 + 准确 diff
# ---------------------------------------------------------------------------


def test_evidence_safe_applies_revision_with_diff() -> None:
    adapter = _SequencedAdapter([_draft_text(_SOURCE), _draft_text(_REVISED)])
    events, result = _run(_make_service(adapter), _evidence_safe_input())
    assert adapter.calls == 2  # 首稿 + 一次定向修订
    assert result.status == HumanizerResultStatus.DONE
    report = result.evidence_safe
    assert report is not None
    assert report.revision_status == EvidenceRevisionStatus.APPLIED
    assert report.revisions
    # 变化记录：原文 span → 改后 span
    change = report.revisions[0]
    assert "因此显著提升" in change.original_span
    assert "有所提升" in change.revised_span
    assert change.change_type in {
        RevisionChangeType.DOWNGRADED,
        RevisionChangeType.REWORDED,
    }
    assert change.original_location.start >= 0
    assert change.revised_location.start >= 0
    assert change.reason
    # 交付正文是修订后文本；数字与专名保持
    assert result.output.final_text == _REVISED
    assert "199 元" in result.output.final_text
    # 观测步骤轨迹包含证据边界检查
    assert any("证据" in step for step in result.process_steps)


def test_evidence_safe_does_not_trigger_when_not_requested() -> None:
    """自然语言未显式授权时，即使科研体裁也不进入证据安全修订。"""
    routed = route_humanizer_message(
        "帮我改写这段科研报告：实验结果证明，该方案显著提升了效率。"
    )
    assert routed is not None
    assert (
        routed.skill_input.expression_contract.evidence_revision_mode
        == EvidenceRevisionMode.PRESERVE
    )


# ---------------------------------------------------------------------------
# 4. 修订失败：保持原文 + hold_for_user，不自动加保守套话
# ---------------------------------------------------------------------------


def test_revision_blocked_by_fidelity_keeps_draft() -> None:
    # 修订模型试图新增样本量与统计方法（无来源）→ 来源硬门阻止
    contaminated = (
        "调查显示，使用该产品与满意度相关，用户满意度因此有所提升。"
        "该产品售价 199 元。本研究纳入 5000 名参与者，采用配对 t 检验。"
    )
    adapter = _SequencedAdapter([_draft_text(_SOURCE), _draft_text(contaminated)])
    events, result = _run(_make_service(adapter), _evidence_safe_input())
    assert result.status == HumanizerResultStatus.DONE
    report = result.evidence_safe
    assert report is not None
    assert report.revision_status == EvidenceRevisionStatus.HOLD_FOR_USER
    # 正文保持首稿原文，不因修订失败自动添加保守套话
    assert result.output.final_text == _SOURCE
    assert "显著提升" in result.output.final_text
    assert "t 检验" not in result.output.final_text
    # 稳定的待用户确认计数
    assert report.summary.hold_for_user_count > 0
    assert any("证据安全修订未应用" in w for w in result.quality_warnings)


def test_revision_conflict_on_protected_span_keeps_draft() -> None:
    # 修订模型改动受保护数字（199 → 299）→ 冲突，保持首稿
    tampered = "调查显示，使用该产品与满意度相关，用户满意度因此有所提升。该产品售价 299 元。"
    adapter = _SequencedAdapter([_draft_text(_SOURCE), _draft_text(tampered)])
    events, result = _run(_make_service(adapter), _evidence_safe_input())
    assert result.status == HumanizerResultStatus.DONE
    report = result.evidence_safe
    assert report is not None
    assert report.revision_status == EvidenceRevisionStatus.HOLD_FOR_USER
    assert result.output.final_text == _SOURCE
    assert "199 元" in result.output.final_text


def test_revision_model_failure_keeps_draft() -> None:
    adapter = _FailingAdapter(_draft_text(_SOURCE))
    events, result = _run(_make_service(adapter), _evidence_safe_input())
    assert result.status == HumanizerResultStatus.DONE
    report = result.evidence_safe
    assert report is not None
    assert report.revision_status == EvidenceRevisionStatus.HOLD_FOR_USER
    assert result.output.final_text == _SOURCE


def test_revision_with_added_limitation_sentence_is_held() -> None:
    """修订补写纯文字研究局限（无数字/方法词）也被确定性捕获并保持首稿。"""
    with_limitation = (
        "调查显示，使用该产品与满意度相关，用户满意度因此有所提升。"
        "该产品售价 199 元。但本研究样本有限，结论有待进一步验证。"
    )
    adapter = _SequencedAdapter([_draft_text(_SOURCE), _draft_text(with_limitation)])
    events, result = _run(_make_service(adapter), _evidence_safe_input())
    assert result.status == HumanizerResultStatus.DONE
    report = result.evidence_safe
    assert report is not None
    assert report.revision_status == EvidenceRevisionStatus.HOLD_FOR_USER
    # 正文保持首稿原文，不交付带补写局限的修订
    assert result.output.final_text == _SOURCE
    assert "有待进一步验证" not in result.output.final_text
    assert report.summary.protected_conflict_count > 0


def test_revision_that_upgrades_strength_is_held_for_user() -> None:
    # 修订模型把结论升级（必然）→ 变化类型 upgraded，需用户确认
    upgraded = "调查显示，使用该产品与满意度相关，用户满意度必然大幅提升。该产品售价 199 元。"
    adapter = _SequencedAdapter([_draft_text(_SOURCE), _draft_text(upgraded)])
    events, result = _run(_make_service(adapter), _evidence_safe_input())
    assert result.status == HumanizerResultStatus.DONE
    report = result.evidence_safe
    assert report is not None
    assert report.revision_status == EvidenceRevisionStatus.HOLD_FOR_USER
    assert any(
        change.change_type == RevisionChangeType.UPGRADED
        and change.needs_user_confirmation
        for change in report.revisions
    )
    assert result.output.final_text == _SOURCE


# ---------------------------------------------------------------------------
# 5. 契约层：科研体裁不自动打开 EVIDENCE_SAFE
# ---------------------------------------------------------------------------


def test_research_genre_does_not_auto_enable_evidence_safe() -> None:
    routed = route_humanizer_message(
        "帮我润色这段科研汇报：实验数据显示，模型在测试集上准确率 92%。"
    )
    assert routed is not None
    assert (
        routed.skill_input.expression_contract.evidence_revision_mode
        == EvidenceRevisionMode.PRESERVE
    )


def test_explicit_evidence_safe_requires_user_words() -> None:
    from bridges.skills.humanizer.contract_compiler import (
        CompileRequest,
        compile_task_contract,
    )

    for content, expected in (
        ("润色这段科研报告", EvidenceRevisionMode.PRESERVE),
        ("改写这篇文章，允许调整结论强度", EvidenceRevisionMode.EVIDENCE_SAFE),
        ("不允许调整结论，润色这段文字", EvidenceRevisionMode.PRESERVE),
    ):
        result = compile_task_contract(
            CompileRequest(content=content, source_material="原文内容。")
        )
        assert result.contract.evidence_revision_mode == expected
