"""证据安全修订模式测试（人味化改造 Issue 06）。

表驱动覆盖 Issue 06 测试计划：
1. claim 分类（观察/相关/预测/比较/机制/因果/显著性/泛化/真实场景有效性）与强度档位。
2. 默认模式风险检测（相关写成因果/无检验写显著/拟合宣称机制/预测等同因果/
   预测等同真实场景有效）只生成独立风险项，不改变正文。
3. 证据安全修订 diff：降级/升级/删除/改写 + 非 claim 句与受保护项不变。
4. 修订提示只消费账本已有信息，禁止新增论据。
5. 普通邮件、观点文、聊天文本不误触发。
"""

from __future__ import annotations

import pytest

from bridges.contracts.evidence_safety import (
    ClaimStrengthKind,
    EvidenceRevisionStatus,
    EvidenceRiskCode,
    RevisionChangeType,
)
from bridges.contracts.expression_task import (
    EvidenceRevisionMode,
    ExpressionTaskContract,
    MaterialSufficiency,
    Operation,
    RealityMode,
    RewriteIntensity,
    SourceScope,
    Surface,
)
from bridges.skills.humanizer.evidence_safety import (
    build_revision_prompt,
    classify_claims,
    compare_claim_changes,
    detect_evidence_risks,
    run_evidence_safety,
)
from bridges.skills.humanizer.source_ledger import compile_source_ledger

# ---------------------------------------------------------------------------
# 1. claim 分类：九类类型 + 强度档位
# ---------------------------------------------------------------------------

_KIND_CASES: list[tuple[str, ClaimStrengthKind]] = [
    # 观察：做了什么、看到什么
    ("实验收集了 5000 例样本，平均反应时间为 320 毫秒。", ClaimStrengthKind.OBSERVATION),
    # 相关（相关标记优先于预测标记：核心 claim 是相关性）
    ("数据显示睡眠时长与记忆力相关。", ClaimStrengthKind.CORRELATION),
    ("新模型的预测结果与人工标注呈正相关。", ClaimStrengthKind.CORRELATION),
    # 预测
    ("该模型在测试集上的预测准确率达到 92%。", ClaimStrengthKind.PREDICTION),
    # 比较
    ("实验组反应时间显著低于对照组。", ClaimStrengthKind.COMPARISON),
    ("新方案优于旧方案。", ClaimStrengthKind.COMPARISON),
    # 机制
    ("研究揭示了该药物通过抑制通路发挥作用的机制。", ClaimStrengthKind.MECHANISM),
    # 因果
    ("吸烟导致肺癌发病率上升。", ClaimStrengthKind.CAUSALITY),
    # 显著性
    ("差异具有统计学显著性（p < 0.01）。", ClaimStrengthKind.SIGNIFICANCE),
    # 泛化
    ("该结论适用于所有年龄人群。", ClaimStrengthKind.GENERALIZATION),
    # 真实场景有效性
    ("该算法在真实临床场景中效果良好。", ClaimStrengthKind.REAL_WORLD_EFFECTIVENESS),
]


@pytest.mark.parametrize(
    ("text", "kind"),
    _KIND_CASES,
    ids=[f"kind-{k.value}-{i}" for i, (_, k) in enumerate(_KIND_CASES)],
)
def test_classify_kinds(text: str, kind: ClaimStrengthKind) -> None:
    claims = classify_claims(text)
    assert len(claims) == 1
    assert claims[0].kind == kind
    # 每项带来源位置与当前强度
    assert claims[0].location.start >= 0
    assert claims[0].location.end > claims[0].location.start
    assert claims[0].tier.value in {"weak", "medium", "strong"}


def test_classify_strength_tiers() -> None:
    strong = classify_claims("实验结果证明该结论成立。")[0]
    medium = classify_claims("数据表明该结论成立。")[0]
    weak = classify_claims("我们观察到该结论可能成立。")[0]
    assert strong.tier.value == "strong"
    assert medium.tier.value == "medium"
    assert weak.tier.value == "weak"


def test_classify_no_false_positive_on_plain_text() -> None:
    plain = [
        "你好，这个方案我觉得可以试一下。",
        "明天下午三点开会，记得带笔记本。",
        "感谢你的帮助，祝工作顺利。",
        "我觉得这篇文章写得不错。",
    ]
    for text in plain:
        assert classify_claims(text) == []


# ---------------------------------------------------------------------------
# 2. 默认模式风险检测：只生成独立风险项，正文不变
# ---------------------------------------------------------------------------

_RISK_CASES: list[tuple[str, EvidenceRiskCode, bool]] = [
    # 相关写成因果（同句既有相关标记又有因果动词）
    (
        "调查显示，使用该产品与满意度相关，用户满意度因此显著提升。",
        EvidenceRiskCode.CORRELATION_AS_CAUSALITY,
        True,
    ),
    (
        "结果显示，锻炼时长与睡眠质量相关。",
        EvidenceRiskCode.CORRELATION_AS_CAUSALITY,
        False,
    ),
    # 无检验写显著（句内与账本均无检验信息）
    (
        "实验证明，该方案显著提升了效率。",
        EvidenceRiskCode.SIGNIFICANCE_WITHOUT_TEST,
        True,
    ),
    (
        "实验结果显示差异显著（p < 0.05）。",
        EvidenceRiskCode.SIGNIFICANCE_WITHOUT_TEST,
        False,
    ),
    # 拟合/聚类/消融宣称机制
    (
        "聚类分析显示两组数据存在差异，说明该因素通过某种机制驱动了变化。",
        EvidenceRiskCode.MECHANISM_FROM_FIT,
        True,
    ),
    (
        "拟合结果表明模型曲线符合预期。",
        EvidenceRiskCode.MECHANISM_FROM_FIT,
        False,
    ),
    # 预测准确等同因果
    (
        "模型预测准确率达到 90%，说明该特征决定了结果。",
        EvidenceRiskCode.PREDICTION_AS_CAUSALITY,
        True,
    ),
    ("模型预测准确率达到 90%。", EvidenceRiskCode.PREDICTION_AS_CAUSALITY, False),
    # 预测等同真实场景有效
    (
        "模型在测试集上准确率 95%，证明该方法在真实场景中有效。",
        EvidenceRiskCode.PREDICTION_AS_REAL_WORLD,
        True,
    ),
    (
        "模型在真实部署环境中表现稳定。",
        EvidenceRiskCode.PREDICTION_AS_REAL_WORLD,
        False,
    ),
]


@pytest.mark.parametrize(
    ("text", "code", "expected"),
    _RISK_CASES,
    ids=[f"risk-{c.value}-{'hit' if e else 'miss'}-{i}" for i, (_, c, e) in enumerate(_RISK_CASES)],
)
def test_detect_evidence_risks(
    text: str, code: EvidenceRiskCode, expected: bool
) -> None:
    risks = detect_evidence_risks(text)
    codes = [risk.code for risk in risks]
    assert (code in codes) is expected
    for risk in risks:
        assert risk.location.start >= 0
        assert risk.category


def test_significance_risk_suppressed_when_ledger_has_test_info() -> None:
    ledger = compile_source_ledger(
        "对照实验采用 t 检验，p = 0.02。"
    )
    text = "实验证明，该方案显著提升了效率。"
    risks = detect_evidence_risks(text, ledger=ledger)
    assert EvidenceRiskCode.SIGNIFICANCE_WITHOUT_TEST not in [
        r.code for r in risks
    ]


def test_significance_risk_suppressed_by_p_value_in_ledger() -> None:
    """账本只有 p 值（无「检验」字样）时也不误报无检验写显著。"""
    ledger = compile_source_ledger("实验结果显示差异显著（p = 0.02）。")
    risks = detect_evidence_risks(
        "实验证明，该方案显著提升了效率。", ledger=ledger
    )
    assert EvidenceRiskCode.SIGNIFICANCE_WITHOUT_TEST not in [
        r.code for r in risks
    ]


def test_default_report_keeps_risks_and_no_revisions() -> None:
    contract = _contract(EvidenceRevisionMode.PRESERVE)
    text = "实验结果证明，该方案显著提升了效率。"
    report = run_evidence_safety(text, contract=contract, ledger=None)
    assert report.revision_status == EvidenceRevisionStatus.NOT_APPLIED
    assert report.risks  # 风险项独立呈现
    assert report.revisions == []
    assert report.summary.risk_count == len(report.risks)
    assert report.contract_hash == contract.version_hash


def test_plain_article_without_risks_produces_no_fixed_summary() -> None:
    """普通无风险文章不生成空洞的固定「事实核查总结」。"""
    contract = _contract(EvidenceRevisionMode.PRESERVE)
    report = run_evidence_safety(
        "大家好，本次会议主要讨论下季度排期。", contract=contract
    )
    assert report.risks == []
    assert report.claims == []
    assert report.summary.risk_count == 0


# ---------------------------------------------------------------------------
# 3. 修订 diff：降级/升级/删除 + 非 claim 句与受保护项不变
# ---------------------------------------------------------------------------


def test_compare_downgrades_causality_to_correlation() -> None:
    original = "结果显示，锻炼时长与睡眠质量相关，因此锻炼导致睡眠改善。"
    revised = "结果显示，锻炼时长与睡眠质量相关，锻炼与睡眠改善存在关联。"
    claims = classify_claims(original)
    changes, conflicts = compare_claim_changes(original, revised, claims)
    assert conflicts == []
    downgrades = [c for c in changes if c.change_type == RevisionChangeType.DOWNGRADED]
    assert len(downgrades) == 1
    change = downgrades[0]
    assert "导致睡眠改善" in change.original_span
    assert "与睡眠改善存在关联" in change.revised_span
    assert change.original_location.start >= 0
    assert change.revised_location.start >= 0
    assert change.needs_user_confirmation is False
    # 确定性重建：同一输入输出相同结构
    again, _ = compare_claim_changes(original, revised, claims)
    assert [c.original_span for c in again] == [c.original_span for c in changes]


def test_compare_flags_upgrade_as_user_confirmation() -> None:
    original = "数据表明该方案显著提升效率。"
    revised = "数据表明该方案必然显著提升效率。"
    claims = classify_claims(original)
    changes, conflicts = compare_claim_changes(original, revised, claims)
    upgrades = [c for c in changes if c.change_type == RevisionChangeType.UPGRADED]
    assert len(upgrades) == 1
    assert upgrades[0].needs_user_confirmation is True


def test_compare_flags_removed_claim_as_user_confirmation() -> None:
    original = "结果显示，该药物导致血压下降。结论可靠。"
    revised = "结论可靠。"
    claims = classify_claims(original)
    changes, conflicts = compare_claim_changes(original, revised, claims)
    removed = [c for c in changes if c.change_type == RevisionChangeType.REMOVED]
    assert len(removed) == 1
    assert removed[0].needs_user_confirmation is True


def test_compare_protects_non_claim_sentences() -> None:
    original = "大家好，这是背景介绍。结果显示，该方案显著提升效率。最后，感谢大家。"
    revised = "大家好，这是背景介绍。结果显示，该方案可能与效率提升有关。最后，感谢大家。"
    claims = classify_claims(original)
    changes, conflicts = compare_claim_changes(original, revised, claims)
    assert conflicts == []  # 非 claim 句逐字保持
    assert any(c.change_type == RevisionChangeType.DOWNGRADED for c in changes)


def test_compare_reports_non_claim_edit_conflict() -> None:
    original = "背景：这是介绍段落。结果显示，该方案显著提升效率。"
    revised = "背景：这是被改写的介绍段落。结果显示，该方案可能与效率提升有关。"
    claims = classify_claims(original)
    changes, conflicts = compare_claim_changes(original, revised, claims)
    assert any("非 claim" in message for message in conflicts)


def test_compare_protects_figure_table_and_model_names() -> None:
    original = (
        "如图 3 所示，BERT 模型与 RoBERTa 数据集实验结果证明性能提升显著。"
        "我们的 TextModel 模型在 GraphSet 数据集上表现良好。"
    )
    revised = (
        "如图 3 所示，BERT 模型与 RoBERTa 数据集实验结果与性能提升存在关联。"
        "我们的 TextModel 模型在 GraphSet 数据集上表现良好。"
    )
    claims = classify_claims(original)
    changes, conflicts = compare_claim_changes(original, revised, claims)
    assert conflicts == []
    # 图表编号被改动 → 冲突
    bad = revised.replace("图 3", "图 4")
    _, bad_conflicts = compare_claim_changes(original, bad, claims)
    assert any("图表编号" in message for message in bad_conflicts)


def test_compare_unchanged_text_has_no_changes() -> None:
    original = "结果显示，该方案显著提升效率。"
    claims = classify_claims(original)
    changes, conflicts = compare_claim_changes(original, original, claims)
    assert changes == []
    assert conflicts == []


def test_compare_flags_added_sentences_as_conflict() -> None:
    """修订新增原文没有的句子（纯文字局限/论据）也必须是冲突。

    即使新增句不带数字或方法词，也不能绕过确定性防线（AC4/AC7）。
    """
    original = "结果显示，该方案显著提升效率。"
    revised = "结果显示，该方案与效率提升存在关联。但本研究样本有限，结论有待验证。"
    claims = classify_claims(original)
    changes, conflicts = compare_claim_changes(original, revised, claims)
    assert any("新增了原文没有的句子" in message for message in conflicts)


def test_compare_ids_are_deterministic() -> None:
    """同一输入两次比较产出相同变化 ID（确定性 diff 可重建）。"""
    original = "结果显示，锻炼时长与睡眠质量相关，因此锻炼导致睡眠改善。"
    revised = "结果显示，锻炼时长与睡眠质量相关，锻炼与睡眠改善存在关联。"
    claims = classify_claims(original)
    first, _ = compare_claim_changes(original, revised, claims)
    second, _ = compare_claim_changes(original, revised, claims)
    assert first and second
    assert [c.change_id for c in first] == [c.change_id for c in second]
    assert [c.change_type for c in first] == [c.change_type for c in second]


# ---------------------------------------------------------------------------
# 4. 修订提示：只消费账本已有信息，禁止新增论据
# ---------------------------------------------------------------------------


def test_revision_prompt_contains_risks_and_material_boundary() -> None:
    contract = _contract(EvidenceRevisionMode.EVIDENCE_SAFE)
    text = "实验结果证明，该方案显著提升了效率。"
    risks = detect_evidence_risks(text)
    ledger = compile_source_ledger("对照实验收集了 200 例样本，未进行统计检验。")
    prompt = build_revision_prompt(contract, text, risks, ledger)
    assert "证据安全修订" in prompt
    assert "显著提升了效率" in prompt
    assert "200" in prompt  # 账本数据
    assert "不新增论据" in prompt
    assert "统计方法说明" in prompt
    assert "研究局限" in prompt
    assert "图表编号" in prompt
    # 修订输出合同与首稿一致：只输出 final_text
    assert '"final_text"' in prompt


def test_revision_prompt_requires_at_least_one_risk() -> None:
    with pytest.raises(ValueError):
        build_revision_prompt(
            _contract(EvidenceRevisionMode.EVIDENCE_SAFE),
            "普通文本。",
            [],
            None,
        )


# ---------------------------------------------------------------------------
# 5. 来源绑定与账本消费
# ---------------------------------------------------------------------------


def test_claim_binds_to_ledger_entry() -> None:
    ledger = compile_source_ledger(
        "实验收集了 5000 例样本，测得平均反应时间 320 毫秒。"
    )
    claims = classify_claims("实验收集了 5000 例样本。", ledger=ledger)
    assert claims
    assert claims[0].source_entry_id is not None


def test_ledger_without_test_info_still_flags_significance() -> None:
    ledger = compile_source_ledger("实验收集了 200 例样本。")
    risks = detect_evidence_risks(
        "实验证明，该方案显著提升了效率。", ledger=ledger
    )
    assert EvidenceRiskCode.SIGNIFICANCE_WITHOUT_TEST in [
        r.code for r in risks
    ]


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _contract(mode: EvidenceRevisionMode) -> ExpressionTaskContract:
    contract = ExpressionTaskContract(
        schema_version="expression-task-v1",
        version_hash="",
        surface=Surface.ARTICLE,
        operation=Operation.REWRITE,
        conversation_mode=None,
        reality_mode=RealityMode.REAL,
        rewrite_intensity=RewriteIntensity.STANDARD,
        speaker_position="用户（以作者身份）",
        first_person_permission=False,
        hypothetical_permission=False,
        audience=None,
        channel=None,
        length_target=None,
        genre=None,
        material_sufficiency=MaterialSufficiency.SUFFICIENT,
        source_scope=SourceScope.ORIGINAL_ONLY,
        evidence_revision_mode=mode,
        user_constraints=[],
        one_question=None,
        profile_slice_id=None,
        profile_item_count=0,
        source_text_present=True,
    )
    contract.version_hash = contract.compute_version_hash()
    return contract
