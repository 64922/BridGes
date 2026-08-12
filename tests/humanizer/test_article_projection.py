"""版本化文章结果投影契约测试（人味化改造 Issue 08）。

覆盖 Test plan 1 的投影契约场景：成功无风险、软警告、材料不足、硬门
失败、证据风险、证据安全修订、二次修订，以及旧结果兼容读取（AC 10）。
全部断言针对 ``_build_article_projection`` 的确定性输出。
"""

from __future__ import annotations

from typing import Any

from bridges.contracts.evidence_safety import (
    EvidenceRevisionChange,
    EvidenceRevisionMode,
    EvidenceRevisionStatus,
    EvidenceRiskCode,
    EvidenceRiskItem,
    EvidenceSafeReport,
    EvidenceSafeSummary,
    RevisionChangeType,
)
from bridges.contracts.expression_review import (
    ExpressionReviewFinding,
    ExpressionReviewReport,
    ExpressionReviewSummary,
    ReviewCode,
    ReviewSeverity,
)
from bridges.contracts.humanizer import (
    ARTICLE_PROJECTION_VERSION,
    ArticleDeliveryStatus,
    ArticleMaterialState,
    FactLockCheckResult,
    FidelityCheckResult,
    FidelityFailure,
    FidelityFailureCode,
    FidelitySeverity,
    FidelitySummary,
    HumanizerArticleProjection,
    HumanizerOutputContract,
    HumanizerQualityStatus,
    HumanizerResultProjection,
    HumanizerResultStatus,
    HumanizerRevisionAudit,
    SourceEntry,
    SourceLedger,
    SourceType,
    SourceUsage,
    SpanLocation,
)
from bridges.skills.humanizer.service import HumanizerService


def _fidelity_check(
    blocking: list[FidelityFailure] | None = None,
    needs_confirmation: list[FidelityFailure] | None = None,
) -> FidelityCheckResult:
    blocking = blocking or []
    needs_confirmation = needs_confirmation or []
    return FidelityCheckResult(
        check_id="check-1",
        ledger_version="2",
        checker_version="2.0",
        ledger_hash="ledger-hash",
        passed=not blocking,
        blocking_failures=blocking,
        needs_confirmation=needs_confirmation,
        summary=FidelitySummary(
            protected_span_count=0,
            preserved_count=0,
            new_claim_count=0,
            attributed_claim_count=0,
            unattributed_claim_count=0,
            first_person_interception_count=0,
            assumption_count=0,
            blocking_count=len(blocking),
            needs_confirmation_count=len(needs_confirmation),
        ),
    )


def _fidelity_failure(
    code: FidelityFailureCode = FidelityFailureCode.UNATTRIBUTED_CLAIM,
    severity: FidelitySeverity = FidelitySeverity.BLOCKING,
    note: str = "新增结论没有来源绑定",
) -> FidelityFailure:
    return FidelityFailure(
        failure_id="f-1",
        code=code,
        severity=severity,
        category="无来源新增 claim",
        item_type="claim",
        location=SpanLocation(start=0, end=10),
        note=note,
    )


def _style_review(
    warning: bool = False,
    suggestion: bool = False,
) -> ExpressionReviewReport:
    findings: list[ExpressionReviewFinding] = []
    if warning:
        findings.append(
            ExpressionReviewFinding(
                finding_id="fnd-1",
                code=ReviewCode.REPEATED_OPENING,
                severity=ReviewSeverity.WARNING,
                category="重复开场",
                location=SpanLocation(start=0, end=12),
                evidence="首先，我们来了解一下……",
                explanation="固定开头模板动作。",
                suggestion="删去「首先」直接进入主题。",
                scene_profile="popular_science:medium",
            )
        )
    if suggestion:
        findings.append(
            ExpressionReviewFinding(
                finding_id="fnd-2",
                code=ReviewCode.DENSE_RHETORIC,
                severity=ReviewSeverity.SUGGESTION,
                category="密集排比设问",
                location=SpanLocation(start=20, end=40),
                evidence="可能或许大概",
                explanation="限定词密度偏高。",
                suggestion="保留一处限定即可。",
                scene_profile="popular_science:medium",
            )
        )
    return ExpressionReviewReport(
        contract_hash="c-hash",
        scene_profile="popular_science:medium",
        findings=findings,
        no_change_recommended=not findings,
        summary=ExpressionReviewSummary(
            finding_count=len(findings),
            by_code={f.code.value: 1 for f in findings},
            warning_count=sum(
                1 for f in findings if f.severity == ReviewSeverity.WARNING
            ),
            suggestion_count=sum(
                1 for f in findings if f.severity == ReviewSeverity.SUGGESTION
            ),
            info_count=0,
            no_change_recommended=not findings,
        ),
    )


def _evidence_report(
    risks: list[EvidenceRiskItem] | None = None,
    revisions: list[EvidenceRevisionChange] | None = None,
    status: EvidenceRevisionStatus = EvidenceRevisionStatus.NOT_APPLIED,
) -> EvidenceSafeReport:
    risks = risks or []
    revisions = revisions or []
    return EvidenceSafeReport(
        report_version="1",
        contract_hash="c-hash",
        mode=EvidenceRevisionMode.PRESERVE,
        risks=risks,
        revisions=revisions,
        revision_status=status,
        summary=EvidenceSafeSummary(
            claim_count=0,
            risk_count=len(risks),
            revision_count=len(revisions),
            protected_conflict_count=0,
            hold_for_user_count=1 if status == EvidenceRevisionStatus.HOLD_FOR_USER else 0,
        ),
    )


def _risk_item() -> EvidenceRiskItem:
    return EvidenceRiskItem(
        risk_id="r-1",
        code=EvidenceRiskCode.CORRELATION_AS_CAUSALITY,
        category="把相关性说成因果",
        surface="研究显示 A 导致 B 显著增加。",
        location=SpanLocation(start=0, end=20),
        explanation="A 与 B 的关系在来源中只是相关，不是因果。",
    )


def _change_item() -> EvidenceRevisionChange:
    return EvidenceRevisionChange(
        change_id="ch-1",
        change_type=RevisionChangeType.DOWNGRADED,
        kind="causality",
        original_span="A 导致 B",
        revised_span="A 与 B 相关",
        original_location=SpanLocation(start=0, end=8),
        revised_location=SpanLocation(start=0, end=8),
        source_entry_id="e-1",
        reason="来源账本只支持相关结论。",
        needs_user_confirmation=False,
    )


def _ledger() -> SourceLedger:
    return SourceLedger(
        ledger_hash="ledger-hash",
        entries=[
            SourceEntry(
                entry_id="e-1",
                source_type=SourceType.USER_ORIGINAL,
                content_hash="h",
                usage=[SourceUsage.REWRITE],
                source_label="用户原文",
            )
        ],
    )


def _revision_audit() -> HumanizerRevisionAudit:
    return HumanizerRevisionAudit(
        revision_version="revision-policy-v1",
        triggered=True,
        trigger_code=FidelityFailureCode.UNATTRIBUTED_CLAIM.value,
        problem_count=1,
        draft_fidelity_blocking=1,
        draft_contract_omissions=0,
        draft_warning_count=0,
        revised_fidelity_blocking=0,
        revised_contract_omissions=0,
        revised_warning_count=0,
        resolved_problem_count=1,
        final_state="deliver_revised",
    )


def _output(final_text: str = "最终正文。") -> HumanizerOutputContract:
    return HumanizerOutputContract(
        final_text=final_text,
        quality_status=HumanizerQualityStatus.OK,
    )


def _build(**kwargs: Any) -> HumanizerArticleProjection:
    return HumanizerService._build_article_projection(**kwargs)


# ---------------------------------------------------------------------------
# AC 1/2：成功且无风险 → delivered、正文可交付、无空洞审计数据
# ---------------------------------------------------------------------------


def test_success_clean_projection_delivered() -> None:
    article = _build(
        status=HumanizerResultStatus.DONE,
        output=_output("光合作用把光能转化为化学能。"),
        fidelity_check=_fidelity_check(),
        review=_style_review(),
    )
    assert article.projection_version == ARTICLE_PROJECTION_VERSION
    assert article.delivery_status == ArticleDeliveryStatus.DELIVERED
    assert article.material_state == ArticleMaterialState.SUFFICIENT
    assert article.final_text == "光合作用把光能转化为化学能。"
    # 保真通过：无失败项，也不生成「已核实」式空洞总结
    assert article.fidelity is not None
    assert article.fidelity.passed
    assert article.fidelity.items == []
    assert article.style_review is not None
    assert article.style_review.finding_count == 0
    assert article.style_review.items == []
    # 无风险：证据项、待确认与修订摘要都为空
    assert article.evidence == []
    assert article.confirmations == []
    assert article.revision is None


# ---------------------------------------------------------------------------
# AC 2/3：软警告 → 正文照常交付，审稿定向项进入投影
# ---------------------------------------------------------------------------


def test_soft_warning_projection_delivers_with_style_review() -> None:
    review = _style_review(warning=True, suggestion=True)
    article = _build(
        status=HumanizerResultStatus.DONE,
        output=_output(),
        fidelity_check=_fidelity_check(),
        review=review,
    )
    assert article.delivery_status == ArticleDeliveryStatus.DELIVERED
    assert article.style_review is not None
    assert article.style_review.warning_count == 1
    assert article.style_review.suggestion_count == 1
    assert article.style_review.finding_count == 2
    items = article.style_review.items
    assert len(items) == 2
    assert items[0].severity == ReviewSeverity.WARNING.value
    assert items[0].evidence == "首先，我们来了解一下……"
    assert items[0].suggestion.startswith("删去")
    # 每个定向项都带可定位证据（AC 8：无证据的泛化理由不展示）
    assert all(item.evidence for item in items)
    assert all(item.location.start >= 0 for item in items)


# ---------------------------------------------------------------------------
# AC 5：材料不足 → 不交付，只标记材料不足
# ---------------------------------------------------------------------------


def test_insufficient_material_projection() -> None:
    article = _build(
        status=HumanizerResultStatus.ERROR,
        output=None,
        error_code="empty_source",
    )
    assert article.delivery_status == ArticleDeliveryStatus.FAILED
    assert article.material_state == ArticleMaterialState.INSUFFICIENT
    assert article.final_text is None
    # 材料不足不是质量审查问题：不堆叠审计项
    assert article.fidelity is None
    assert article.evidence == []


def test_empty_topic_is_insufficient_material() -> None:
    article = _build(
        status=HumanizerResultStatus.ERROR,
        output=None,
        error_code="empty_topic",
    )
    assert article.material_state == ArticleMaterialState.INSUFFICIENT


def test_contract_ask_one_question_is_insufficient_with_single_question() -> None:
    """表达契约裁决材料不足：投影为 INSUFFICIENT 且只带一个最高价值问题。"""
    article = _build(
        status=HumanizerResultStatus.DONE,
        output=_output("（短稿）"),
        material_sufficiency="ask_one_question",
        one_question="你更想让我先写引言部分，还是先补充实验数据？",
    )
    assert article.material_state == ArticleMaterialState.INSUFFICIENT
    assert article.delivery_status == ArticleDeliveryStatus.DELIVERED
    assert article.one_question == "你更想让我先写引言部分，还是先补充实验数据？"
    # 不堆叠通用建议：除最高价值问题外无额外审计项
    assert article.confirmations == []
    assert article.evidence == []


def test_contract_shorten_and_placeholders_are_insufficient() -> None:
    for mode in ("shorten", "use_placeholders"):
        article = _build(
            status=HumanizerResultStatus.DONE,
            output=_output("短稿。"),
            material_sufficiency=mode,
        )
        assert article.material_state == ArticleMaterialState.INSUFFICIENT
    article = _build(
        status=HumanizerResultStatus.DONE,
        output=_output(),
        material_sufficiency="sufficient",
    )
    assert article.material_state == ArticleMaterialState.SUFFICIENT
    assert article.one_question is None


def test_model_error_is_sufficient_material() -> None:
    article = _build(
        status=HumanizerResultStatus.ERROR,
        output=None,
        error_code="model_error:timeout",
    )
    assert article.material_state == ArticleMaterialState.SUFFICIENT


# ---------------------------------------------------------------------------
# AC 4：硬门失败 → 违规候选绝不标记为最终正文
# ---------------------------------------------------------------------------


def test_hard_gate_failure_blocks_delivery() -> None:
    blocking = [_fidelity_failure()]
    article = _build(
        status=HumanizerResultStatus.ERROR,
        output=None,
        error_code="fidelity_gate_conflict",
        fidelity_check=_fidelity_check(blocking=blocking),
    )
    assert article.delivery_status == ArticleDeliveryStatus.FAILED
    assert article.final_text is None
    assert article.fidelity is not None
    assert not article.fidelity.passed
    assert article.fidelity.blocking_count == 1
    item = article.fidelity.items[0]
    assert item.code == FidelityFailureCode.UNATTRIBUTED_CLAIM.value
    assert item.severity == FidelitySeverity.BLOCKING.value
    assert item.note == "新增结论没有来源绑定"


# ---------------------------------------------------------------------------
# AC 6：证据风险与证据安全修订 → 确定性前后差异，不伪装已修正
# ---------------------------------------------------------------------------


def test_evidence_risk_projection_is_risk_not_revised() -> None:
    article = _build(
        status=HumanizerResultStatus.DONE,
        output=_output(),
        fidelity_check=_fidelity_check(),
        evidence_report=_evidence_report(risks=[_risk_item()]),
    )
    assert article.evidence
    item = article.evidence[0]
    assert item.kind == "risk"
    assert item.code == EvidenceRiskCode.CORRELATION_AS_CAUSALITY.value
    assert item.category == "把相关性说成因果"
    assert item.original_span == "研究显示 A 导致 B 显著增加。"
    assert item.revised_span is None
    assert not item.needs_user_confirmation


def test_evidence_safe_revision_change_projection() -> None:
    report = _evidence_report(
        revisions=[_change_item()],
        status=EvidenceRevisionStatus.APPLIED,
    )
    article = _build(
        status=HumanizerResultStatus.DONE,
        output=_output("A 与 B 相关。"),
        fidelity_check=_fidelity_check(),
        evidence_report=report,
        ledger=_ledger(),
    )
    assert article.evidence
    item = article.evidence[0]
    assert item.kind == "change"
    assert item.original_span == "A 导致 B"
    assert item.revised_span == "A 与 B 相关"
    assert item.reason == "来源账本只支持相关结论。"
    # 来源从账本解析出显示名（AC 6：展示来源）
    assert item.source_label == "用户原文"


def test_evidence_hold_for_user_projection() -> None:
    report = _evidence_report(status=EvidenceRevisionStatus.HOLD_FOR_USER)
    article = _build(
        status=HumanizerResultStatus.DONE,
        output=_output(),
        fidelity_check=_fidelity_check(),
        evidence_report=report,
    )
    assert article.evidence
    item = article.evidence[0]
    assert item.kind == "hold"
    assert item.needs_user_confirmation
    assert "人工确认" in item.reason


# ---------------------------------------------------------------------------
# AC 7：二次修订 → 为何触发、解决哪些问题、仍有哪些风险
# ---------------------------------------------------------------------------


def test_targeted_revision_summary_projection() -> None:
    article = _build(
        status=HumanizerResultStatus.DONE,
        output=_output(),
        fidelity_check=_fidelity_check(),
        revision_audit=_revision_audit(),
    )
    assert article.revision is not None
    assert article.revision.triggered
    assert article.revision.trigger_label == "来源保真"
    assert article.revision.problem_count == 1
    assert article.revision.resolved_count == 1
    assert article.revision.remaining_count == 0
    assert article.revision.skipped_reason is None


def test_revision_skipped_projection_reports_draft_state() -> None:
    audit = _revision_audit()
    audit.skipped_reason = "call_limit_reached"
    audit.revised_fidelity_blocking = None
    audit.revised_contract_omissions = None
    audit.revised_warning_count = None
    audit.resolved_problem_count = None
    article = _build(
        status=HumanizerResultStatus.DONE,
        output=_output(),
        fidelity_check=_fidelity_check(),
        revision_audit=audit,
    )
    assert article.revision is not None
    # 未修订：剩余风险反映首稿真实检查状态，不伪装已解决
    assert article.revision.resolved_count == 0
    assert article.revision.remaining_count == 1
    assert article.revision.skipped_reason == "写作调用额度已达上限"


def test_revision_trigger_label_maps_contract_and_fidelity_only() -> None:
    contract_audit = _revision_audit()
    contract_audit.trigger_code = "contract"
    assert (
        _build(
            status=HumanizerResultStatus.DONE,
            output=_output(),
            revision_audit=contract_audit,
        ).revision.trigger_label
        == "任务契约遗漏"
    )
    # 未知/未注册 code 不冒充判定：不猜测为表达审稿（避免显示错误触发原因）
    expression_audit = _revision_audit()
    expression_audit.trigger_code = "unregistered_code"
    revision = _build(
        status=HumanizerResultStatus.DONE,
        output=_output(),
        revision_audit=expression_audit,
    ).revision
    assert revision.trigger_label is None
    # 未触发时无标签
    untriggered = _revision_audit()
    untriggered.triggered = False
    untriggered.trigger_code = None
    assert (
        _build(
            status=HumanizerResultStatus.DONE,
            output=_output(),
            revision_audit=untriggered,
        ).revision.trigger_label
        is None
    )


# ---------------------------------------------------------------------------
# AC：待用户确认汇总（保真待确认 + 契约需人工 + 事实锁需人工）
# ---------------------------------------------------------------------------


def test_confirmations_aggregated_from_all_gates() -> None:
    fidelity = _fidelity_check(
        needs_confirmation=[
            _fidelity_failure(
                code=FidelityFailureCode.UNDETERMINED,
                severity=FidelitySeverity.NEEDS_USER_CONFIRMATION,
                note="无法判定该数字是否保持。",
            )
        ]
    )
    contract_check = FactLockCheckResult(
        check_id="c-1",
        source_text="原文",
        blocking_conflicts=[],
        needs_human=["硬约束「必须保留引用」未被满足。"],
        passed=True,
    )
    fact_lock_check = FactLockCheckResult(
        check_id="f-1",
        source_text="原文",
        blocking_conflicts=[],
        needs_human=["结论强度升级需人工确认。"],
        passed=True,
    )
    article = _build(
        status=HumanizerResultStatus.NEEDS_HUMAN,
        output=_output(),
        fidelity_check=fidelity,
        contract_check=contract_check,
        fact_lock_check=fact_lock_check,
    )
    assert article.delivery_status == ArticleDeliveryStatus.DELIVERED
    assert len(article.confirmations) == 3
    labels = {c.label for c in article.confirmations}
    assert labels == {"来源保真", "任务契约", "事实锁"}
    assert article.confirmations[0].code == FidelityFailureCode.UNDETERMINED.value


# ---------------------------------------------------------------------------
# AC 10：旧文章结果兼容读取，明确 legacy（article=None）
# ---------------------------------------------------------------------------


def test_legacy_projection_without_article_readable() -> None:
    old = HumanizerResultProjection.model_validate(
        {
            "task_id": "t-1",
            "skill_id": "bridges-humanizer",
            "skill_version": "1.0.0",
            "path": "rewrite",
            "genre": None,
            "contract": {"path": "rewrite", "source_text": "原文"},
            "status": "done",
            "output": {
                "final_text": "旧版正文",
                "edits": [],
                "fact_check": [{"item": "x", "result": "已核实", "evidence": "y"}],
                "open_questions": [],
            },
        }
    )
    assert old.article is None
    assert old.output is not None
    assert old.output.final_text == "旧版正文"
