"""版本化定向修订触发裁决测试（人味化改造 Issue 05）。

状态机：无问题、一个高置信风格问题、多个低置信警告、可修复事实遗漏、
无来源新增事实、模型协议错误（调用方终态，不进裁决）与契约遗漏分别
给出稳定触发结论；问题清单只含待修项。
"""

from __future__ import annotations

from bridges.contracts.expression_review import (
    ExpressionReviewReport,
    ExpressionReviewSummary,
)
from bridges.contracts.humanizer import (
    FactLockCheckResult,
    FidelityCheckResult,
    FidelityFailure,
    FidelityFailureCode,
    FidelitySeverity,
    FidelitySummary,
)
from bridges.skills.humanizer.intent import route_humanizer_message
from bridges.skills.humanizer.revision_policy import (
    RevisionTriggerKind,
    adjudicate_revision,
)

_SOURCE = "番茄工作法把时间切成 25 分钟的工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"


def _fidelity(
    *,
    blocking: list[FidelityFailure] | None = None,
    needs_confirmation: list[FidelityFailure] | None = None,
) -> FidelityCheckResult:
    blocking = blocking or []
    needs_confirmation = needs_confirmation or []
    return FidelityCheckResult(
        check_id="check-1",
        ledger_version="source-ledger-v1",
        checker_version="fidelity-checker-v1",
        ledger_hash="hash",
        passed=not blocking,
        blocking_failures=blocking,
        needs_confirmation=needs_confirmation,
        summary=FidelitySummary(
            protected_span_count=0,
            preserved_count=0,
            new_claim_count=0,
            attributed_claim_count=0,
            unattributed_claim_count=len(
                [f for f in blocking if f.code == FidelityFailureCode.UNATTRIBUTED_CLAIM]
            ),
            first_person_interception_count=0,
            assumption_count=0,
            blocking_count=len(blocking),
            needs_confirmation_count=len(needs_confirmation),
        ),
    )


def _failure(code: FidelityFailureCode, note: str = "事实被改写") -> FidelityFailure:
    return FidelityFailure(
        failure_id=f"f-{code.value}",
        code=code,
        severity=FidelitySeverity.BLOCKING,
        category="保真冲突",
        item_type="number",
        location=None,
        note=note,
    )


def _contract_check(*, blocking: list[str] | None = None) -> FactLockCheckResult:
    blocking = blocking or []
    return FactLockCheckResult(
        check_id="contract-check-1",
        source_text="",
        entries=[],
        blocking_conflicts=blocking,
        needs_human=[],
        passed=not blocking,
    )


def _empty_review(contract_hash: str = "hash") -> ExpressionReviewReport:
    return ExpressionReviewReport(
        contract_hash=contract_hash,
        scene_profile="generic:standard",
        findings=[],
        no_change_recommended=True,
        summary=ExpressionReviewSummary(
            finding_count=0,
            by_code={},
            warning_count=0,
            suggestion_count=0,
            info_count=0,
            no_change_recommended=True,
        ),
    )


def _expression_contract() -> object:
    routed = route_humanizer_message(f"帮我改写这段话：{_SOURCE}")
    assert routed is not None
    return routed.skill_input.expression_contract


def _review_for(candidate: str) -> ExpressionReviewReport:
    from bridges.skills.humanizer.expression_review import run_expression_review

    return run_expression_review(
        candidate,
        contract=_expression_contract(),
        source_text=_SOURCE,
        ledger=None,
    )


def test_no_problems_does_not_trigger() -> None:
    review = _review_for(
        "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
        "休息 15 分钟，这就是番茄工作法的大致框架。"
    )
    adjudication = adjudicate_revision(
        fidelity_check=_fidelity(),
        contract_check=_contract_check(),
        review=review,
    )
    assert adjudication.triggered is False
    assert adjudication.kind == RevisionTriggerKind.NO_CHANGE
    assert adjudication.problems == ()


def test_single_high_confidence_warning_triggers() -> None:
    review = _review_for(
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
    )
    assert review.summary.warning_count >= 1
    adjudication = adjudicate_revision(
        fidelity_check=_fidelity(),
        contract_check=_contract_check(),
        review=review,
    )
    assert adjudication.triggered is True
    assert adjudication.kind == RevisionTriggerKind.HIGH_CONFIDENCE_EXPRESSION
    assert adjudication.problems
    # 问题清单只含 warning 级高置信发现，不含低置信软信号
    assert all(p.kind == RevisionTriggerKind.HIGH_CONFIDENCE_EXPRESSION for p in adjudication.problems)
    assert all(p.target for p in adjudication.problems)


def test_multiple_low_confidence_warnings_do_not_trigger() -> None:
    # 三句等长（INFO 级）+ 密集排比需达到阈值才触发：先验证纯低置信不触发
    review = _review_for(
        "工作块切 25 分钟。休息块切 5 分钟。四块后休息 15 分钟。"
    )
    assert review.summary.warning_count == 0
    adjudication = adjudicate_revision(
        fidelity_check=_fidelity(),
        contract_check=_contract_check(),
        review=review,
    )
    assert adjudication.triggered is False
    assert adjudication.kind == RevisionTriggerKind.NO_CHANGE


def test_weak_signal_suggestion_never_triggers_even_when_dense() -> None:
    """低置信软信号（suggestion 级 dense_rhetoric 多条）也不触发修订。"""
    from bridges.skills.humanizer.expression_review import run_expression_review

    contract = _expression_contract()
    dense = (
        "番茄工作法为什么有效？因为它把工作切成 25 分钟的工作块和"
        "5 分钟的休息块。为什么是 25 分钟？因为注意力周期大约如此。"
        "为什么休息 5 分钟？因为短暂放松能恢复专注。四个工作块后"
        "休息 15 分钟，这就是番茄工作法的大致框架。"
    )
    review = run_expression_review(dense, contract=contract, source_text=_SOURCE)
    # dense_rhetoric 是 suggestion 级低置信软信号（不达高置信）
    assert review.summary.by_code.get("dense_rhetoric", 0) >= 1
    assert review.summary.warning_count == 0
    adjudication = adjudicate_revision(
        fidelity_check=_fidelity(),
        contract_check=_contract_check(),
        review=review,
    )
    assert adjudication.triggered is False
    assert adjudication.kind == RevisionTriggerKind.NO_CHANGE


def test_repairable_fidelity_failure_triggers() -> None:
    review = _empty_review()
    adjudication = adjudicate_revision(
        fidelity_check=_fidelity(
            blocking=[_failure(FidelityFailureCode.NUMBER_CHANGED)]
        ),
        contract_check=_contract_check(),
        review=review,
    )
    assert adjudication.triggered is True
    assert adjudication.kind == RevisionTriggerKind.REPAIRABLE_FIDELITY
    assert adjudication.problems[0].code == FidelityFailureCode.NUMBER_CHANGED.value
    assert "恢复为来源中的原值" in adjudication.problems[0].target


def test_unsourced_claim_triggers_as_unsourced() -> None:
    review = _empty_review()
    adjudication = adjudicate_revision(
        fidelity_check=_fidelity(
            blocking=[_failure(FidelityFailureCode.UNATTRIBUTED_CLAIM, "无来源新增 claim")]
        ),
        contract_check=_contract_check(),
        review=review,
    )
    assert adjudication.triggered is True
    assert adjudication.kind == RevisionTriggerKind.UNSOURCED_CLAIM
    assert "删除或改写该无来源内容" in adjudication.problems[0].target


def test_contract_omission_triggers() -> None:
    review = _empty_review()
    adjudication = adjudicate_revision(
        fidelity_check=_fidelity(),
        contract_check=_contract_check(blocking=["必须包含「每小时休息」"]),
        review=review,
    )
    assert adjudication.triggered is True
    assert adjudication.kind == RevisionTriggerKind.CONTRACT_OMISSION
    assert adjudication.problems[0].code == "contract"
    assert "补齐任务契约要求" in adjudication.problems[0].target


def test_priority_hard_problem_over_expression() -> None:
    """同时存在保真硬问题与高置信表达问题时，触发类别取硬问题。"""
    review = _review_for(
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。"
    )
    assert review.summary.warning_count >= 1
    adjudication = adjudicate_revision(
        fidelity_check=_fidelity(
            blocking=[_failure(FidelityFailureCode.NUMBER_CHANGED)]
        ),
        contract_check=_contract_check(),
        review=review,
    )
    assert adjudication.triggered is True
    assert adjudication.kind == RevisionTriggerKind.REPAIRABLE_FIDELITY
    # 问题清单同时包含硬问题与表达问题（都作为待修项）
    kinds = {p.kind for p in adjudication.problems}
    assert RevisionTriggerKind.HIGH_CONFIDENCE_EXPRESSION in kinds


def test_no_ledger_is_fail_open_for_expression_only() -> None:
    """账本缺失（旧流程）时：无保真输入即按表达审稿裁决，不误触发。"""
    review = _review_for(
        "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
        "休息 15 分钟，这就是番茄工作法的大致框架。"
    )
    adjudication = adjudicate_revision(
        fidelity_check=None,
        contract_check=_contract_check(),
        review=review,
    )
    assert adjudication.triggered is False
    assert adjudication.kind == RevisionTriggerKind.NO_CHANGE
