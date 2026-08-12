"""预注册聚合器（Test plan 4/5：分歧、无效 span、理由矛盾、硬门优先）。"""

from __future__ import annotations

from bridges.humanize_eval.aggregator import (
    DISAGREEMENT_ITEM_RATIO,
    aggregate_panel,
)
from bridges.humanize_eval.generation import GenerationParameters
from bridges.humanize_eval.judges import (
    FakeSystemJudge,
    JudgeOrder,
    JudgePreference,
    JUDGE_REASON_CODES,
    JudgeVerdict,
    validate_evidence,
)
from bridges.humanize_eval.packet import JudgePacketItem
from bridges.humanize_eval.registry import JudgeRegistry, register_judge

ITEM = JudgePacketItem(
    item_id="item-1",
    user_request="改写下面的句子。",
    source_text="原文内容。",
    prior_dialogue=None,
    mode="rewrite",
    audience="普通读者",
    channel="博客",
    target_length="中等",
    rewrite_intensity="standard",
    realism_commitment="不虚构。",
    allowed_materials=[],
    source_boundary="只使用原文。",
    protected_items=["原文内容"],
    output_a="候选 A 的改写文本。",
    output_b="候选 B 的改写文本。",
)


def _registry(families=("family-a", "family-b", "family-c")) -> JudgeRegistry:
    registry = JudgeRegistry(generation_family="qwen")
    for index, family in enumerate(families):
        registry = register_judge(
            registry,
            judge_id=f"j{index}",
            model_family=family,
            provider=f"provider-{family}",
            model_id=f"{family}-model",
            judge_version="v1",
            system_prompt_sha256="sha",
            schema_version="judge-schema-v2",
            parameters=GenerationParameters(),
        )
    return registry.model_copy(update={
        "registrations": {
            jid: reg.model_copy(update={"enabled": True})
            for jid, reg in registry.registrations.items()
        }
    })


def _make_judge(judge_id: str, preference: JudgePreference):
    return FakeSystemJudge(
        judge_id=judge_id,
        model_family=judge_id,
        scripted_preferences={"item-1": preference},
    )


def _outcomes(judges, item=ITEM):
    """按 judge_pair 语义构造 outcomes（双向一致）。"""
    outcomes = []
    for judge in judges:
        ab = judge.judge(item, JudgeOrder.AB)
        ba = judge.judge(item, JudgeOrder.BA)
        outcomes.append(
            type("O", (), {
                "judge_id": judge.judge_id,
                "verdict_ab": ab,
                "verdict_ba": ba,
            })()
        )
    return outcomes


def test_aggregate_majority_consensus():
    """三裁判一致偏好 A：winner=a，无分歧，verdict=passed。"""
    judges = [_make_judge("j0", JudgePreference.A),
              _make_judge("j1", JudgePreference.A),
              _make_judge("j2", JudgePreference.A)]
    result = aggregate_panel(
        registry=_registry(),
        outcomes=_outcomes(judges),
        fidelity_failed_case_ids=set(),
        item_case_ids={"item-1": "case-1"},
    )
    assert result.verdict == "passed"
    assert result.item_conclusions[0].winner == "A"
    assert result.item_conclusions[0].disagreement is False
    assert result.disagreement_items == []
    # 报告标注：系统自动裁判，未经人工验证。
    assert result.automated_only is True
    assert "未经真实用户或人工验证" in result.note


def test_aggregate_tie_majority():
    """两票平局 + 一票偏好：TIE 多数 → winner=tie（有少数派，记分歧）。"""
    judges = [_make_judge("j0", JudgePreference.TIE),
              _make_judge("j1", JudgePreference.TIE),
              _make_judge("j2", JudgePreference.A)]
    result = aggregate_panel(
        registry=_registry(),
        outcomes=_outcomes(judges),
        fidelity_failed_case_ids=set(),
        item_case_ids={"item-1": "case-1"},
    )
    assert result.item_conclusions[0].winner == "TIE"
    # 有少数派（A 票）→ 记分歧但结论由多数给出。
    assert "item-1" in result.disagreement_items


def test_aggregate_disagreement_within_threshold_reported():
    """2-1 分裂：该 item 记分歧但未超阈值 → inconclusive 但列出分歧原因。"""
    judges = [_make_judge("j0", JudgePreference.A),
              _make_judge("j1", JudgePreference.A),
              _make_judge("j2", JudgePreference.B)]
    result = aggregate_panel(
        registry=_registry(),
        outcomes=_outcomes(judges),
        fidelity_failed_case_ids=set(),
        item_case_ids={"item-1": "case-1"},
    )
    assert result.item_conclusions[0].disagreement is True
    assert "item-1" in result.disagreement_items
    assert any("分歧" in reason for reason in result.reasons)
    # 单 item 分歧占比 1.0 > 阈值 → inconclusive。
    assert result.verdict == "inconclusive"


def test_disagreement_ratio_below_threshold():
    """分歧 item 占比低于阈值时仍可给出结论（未调用仲裁模型）。"""
    judges_a = [_make_judge("j0", JudgePreference.A),
                _make_judge("j1", JudgePreference.A),
                _make_judge("j2", JudgePreference.A)]
    # 第 4 个 item 用分歧 panel（A/A/B）；前 3 个用全票一致 panel。
    # scripted 键必须覆盖对应 item_id。
    item_ids = {f"item-{i + 1}" for i in range(4)}
    judges_b = [
        FakeSystemJudge(
            judge_id=f"j{index}-b",
            model_family=f"family-{index}-b",
            scripted_preferences={"item-4": preference},
        )
        for index, preference in enumerate(
            (JudgePreference.A, JudgePreference.A, JudgePreference.B)
        )
    ]
    outcomes = []
    for index in range(4):
        item_now = ITEM.model_copy(update={"item_id": f"item-{index + 1}"})
        judges_now = judges_a if index < 3 else judges_b
        for judge in judges_now:
            ab = judge.judge(item_now, JudgeOrder.AB)
            ba = judge.judge(item_now, JudgeOrder.BA)
            outcomes.append(
                type("O", (), {
                    "judge_id": judge.judge_id,
                    "verdict_ab": ab,
                    "verdict_ba": ba,
                })()
            )
    result = aggregate_panel(
        registry=_registry(),
        outcomes=outcomes,
        fidelity_failed_case_ids=set(),
        item_case_ids={iid: f"case-{iid}" for iid in item_ids},
    )
    assert len(result.disagreement_items) == 1
    # 分歧存在（报告），但占比未超阈值 → 不因分歧额外拒绝。
    assert not any("超过预注册阈值" in reason for reason in result.reasons)
    assert result.verdict == "passed"
    # 分歧未调用任何仲裁模型。
    assert all(len(c.opinions) == 3 for c in result.item_conclusions)


def test_fidelity_hard_gate_overrides_majority():
    """保真硬门优先：全部裁判偏好 A，但 case 保真失败 → failed，多数票不放行。"""
    judges = [_make_judge("j0", JudgePreference.A),
              _make_judge("j1", JudgePreference.A),
              _make_judge("j2", JudgePreference.A)]
    result = aggregate_panel(
        registry=_registry(),
        outcomes=_outcomes(judges),
        fidelity_failed_case_ids={"case-1"},
        item_case_ids={"item-1": "case-1"},
    )
    assert result.verdict == "failed"
    assert "case-1" in result.fidelity_failed_items
    assert any("多数票不能放行" in reason for reason in result.reasons)
    # 即使裁判给满分也不影响硬门结果。
    assert result.item_conclusions[0].winner == "A"  # 偏好照常报告


def test_cannot_judge_ratio_triggers_inconclusive():
    """CANNOT_JUDGE 票占比超过阈值 → inconclusive。"""
    judges = [_make_judge("j0", JudgePreference.CANNOT_JUDGE),
              _make_judge("j1", JudgePreference.CANNOT_JUDGE),
              _make_judge("j2", JudgePreference.A)]
    result = aggregate_panel(
        registry=_registry(),
        outcomes=_outcomes(judges),
        fidelity_failed_case_ids=set(),
        item_case_ids={"item-1": "case-1"},
    )
    assert result.verdict == "inconclusive"
    assert any("无法判断" in reason for reason in result.reasons)


def test_panel_gate_failure_means_inconclusive():
    """panel 门不通过（多样性不足）→ 聚合直接 inconclusive。"""
    registry = _registry(families=("qwen", "qwen", "qwen"))
    judges = [_make_judge("j0", JudgePreference.A),
              _make_judge("j1", JudgePreference.A),
              _make_judge("j2", JudgePreference.A)]
    result = aggregate_panel(
        registry=registry,
        outcomes=_outcomes(judges),
        fidelity_failed_case_ids=set(),
        item_case_ids={"item-1": "case-1"},
    )
    assert result.verdict == "inconclusive"
    assert any("多样性不足" in reason for reason in result.reasons)


def test_per_judge_reliability_reported():
    """每个裁判报告双向结果、无效票与偏好分布（可靠性）。"""
    judges = [
        _make_judge("j0", JudgePreference.A),
        _make_judge("j1", JudgePreference.A),
        FakeSystemJudge(
            judge_id="j2",
            model_family="family-c",
            scripted_preferences={"item-1": JudgePreference.A},
            scripted_span_mode="invalid",
        ),
    ]
    result = aggregate_panel(
        registry=_registry(),
        outcomes=_outcomes(judges),
        fidelity_failed_case_ids=set(),
        item_case_ids={"item-1": "case-1"},
    )
    assert "j0" in result.per_judge
    # j2 对 item-1 的一票意见（双向合并）无效。
    assert result.per_judge["j2"].invalid_verdicts == 1
    assert result.per_judge["j2"].evidence_invalid_count == 1
    assert result.invalid_verdicts == 1


def test_no_arbitrator_model_called():
    """分歧不调用第四个仲裁模型：聚合只基于预注册 panel 有效票。"""
    judges = [_make_judge("j0", JudgePreference.A),
              _make_judge("j1", JudgePreference.B),
              _make_judge("j2", JudgePreference.TIE)]
    result = aggregate_panel(
        registry=_registry(),
        outcomes=_outcomes(judges),
        fidelity_failed_case_ids=set(),
        item_case_ids={"item-1": "case-1"},
    )
    # 无多数 → 分歧 → inconclusive；没有第四个裁判介入的路径。
    assert result.verdict == "inconclusive"
    assert len(result.item_conclusions[0].opinions) == 3


# ---------------------------------------------------------------------------
# 证据 span / reason code 校验（AC-8）
# ---------------------------------------------------------------------------

def test_non_tie_requires_valid_span():
    """非平局必须引用候选中的有效 span：缺失/编造/来自任务说明都无效。"""
    # 无 span。
    verdict = JudgeVerdict(
        judge_id="j", judge_version="v1", item_id="item-1",
        order=JudgeOrder.AB, preference=JudgePreference.A,
        reason_code="naturalness", evidence_span="",
    )
    assert validate_evidence(verdict, ITEM) is not None
    # span 不存在于候选（编造）。
    verdict = JudgeVerdict(
        judge_id="j", judge_version="v1", item_id="item-1",
        order=JudgeOrder.AB, preference=JudgePreference.A,
        reason_code="naturalness", evidence_span="不存在的片段xyz",
    )
    assert "不存在于任何候选" in validate_evidence(verdict, ITEM)
    # span 来自任务说明而非候选。
    verdict = JudgeVerdict(
        judge_id="j", judge_version="v1", item_id="item-1",
        order=JudgeOrder.AB, preference=JudgePreference.A,
        reason_code="naturalness", evidence_span="原文内容。",
    )
    invalid = validate_evidence(verdict, ITEM)
    assert invalid is not None and "任务说明" in invalid


def test_reason_must_match_preference():
    """理由与选择矛盾：span 属于 B 却偏好 A → 无效。"""
    verdict = JudgeVerdict(
        judge_id="j", judge_version="v1", item_id="item-1",
        order=JudgeOrder.AB, preference=JudgePreference.A,
        reason_code="naturalness",
        evidence_span="候选 B 的改写文本。",
    )
    assert "矛盾" in validate_evidence(verdict, ITEM)


def test_valid_span_and_reason_pass():
    """span 在偏好候选内 + 合法 reason code → 有效。"""
    verdict = JudgeVerdict(
        judge_id="j", judge_version="v1", item_id="item-1",
        order=JudgeOrder.AB, preference=JudgePreference.A,
        reason_code="naturalness", evidence_span="候选 A 的改写文本。",
    )
    assert validate_evidence(verdict, ITEM) is None


def test_reason_code_must_be_preregistered():
    """reason code 必须在预注册集合内；neutral 仅平局可用。"""
    for code in ("not-a-code", "", "neutral"):
        verdict = JudgeVerdict(
            judge_id="j", judge_version="v1", item_id="item-1",
            order=JudgeOrder.AB, preference=JudgePreference.A,
            reason_code=code, evidence_span="候选 A 的改写文本。",
        )
        assert validate_evidence(verdict, ITEM) is not None
    assert "naturalness" in JUDGE_REASON_CODES
    assert "fidelity_risk" in JUDGE_REASON_CODES


def test_tie_needs_no_evidence():
    """平局/无法判断不要求证据 span。"""
    for preference in (JudgePreference.TIE, JudgePreference.CANNOT_JUDGE):
        verdict = JudgeVerdict(
            judge_id="j", judge_version="v1", item_id="item-1",
            order=JudgeOrder.AB, preference=preference,
        )
        assert validate_evidence(verdict, ITEM) is None


def test_fidelity_score_cannot_be_covered_by_other_dimensions():
    """事实忠实失败不能被其他维度 5 分覆盖（聚合层面硬门优先）。"""
    judges = [_make_judge("j0", JudgePreference.A),
              _make_judge("j1", JudgePreference.A),
              _make_judge("j2", JudgePreference.A)]
    # 裁判给 A 全部维度 5 分，但 case 保真硬门失败 → 仍 failed。
    result = aggregate_panel(
        registry=_registry(),
        outcomes=_outcomes(judges),
        fidelity_failed_case_ids={"case-1"},
        item_case_ids={"item-1": "case-1"},
    )
    assert result.verdict == "failed"
    assert "case-1" in result.fidelity_failed_items
