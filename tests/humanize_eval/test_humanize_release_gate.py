"""发布质量门（Issue 11 Test plan 3/4/5/6）。

- 改善门与非劣效门在边界上下、全 tie 的精确状态；
- 有效样本不足、裁判位置偏差、panel 分歧、大量无法判断 → inconclusive；
- 关键事实失败/虚构亲历即使自然度全 5 分仍阻止发布；保真缺失 fail closed；
- 聊天/文章分别裁决，关键切片退化不误报通过；
- 报告元数据标注 automated_system_judges_only / human_validated。
"""

from __future__ import annotations

from bridges.humanize_eval.judges import JudgePreference
from bridges.humanize_eval.paired_stats import (
    ItemPreference,
    compute_paired_statistics,
)
from bridges.humanize_eval.release_gate import (
    FidelityGateInput,
    HumanizeGateReport,
    PanelHealth,
    StyleGateInput,
    SURFACE_ARTICLE,
    SURFACE_CHAT,
    SurfaceInput,
    evaluate_humanize_gate,
    format_gate_blockers,
)
from bridges.humanize_eval.statistics_plan import StatisticsPlan

MIN_CASES = StatisticsPlan().min_cases_per_surface


def _prefs(scores: list[float]) -> list[ItemPreference]:
    prefs = []
    for index, score in enumerate(scores):
        if score == 1.0:
            preference = JudgePreference.A
        elif score == 0.0:
            preference = JudgePreference.B
        else:
            preference = JudgePreference.TIE
        prefs.append(
            ItemPreference("j1", f"item-{index}", f"case-{index}", preference)
        )
    return prefs


def _stats(
    scores: list[float], peer: str = "current-production"
) -> object:
    return compute_paired_statistics(
        peer_sut_id=peer,
        preferences=_prefs(scores),
        case_ids=[f"case-{index}" for index in range(len(scores))],
    )


def _scores(wins: int, ties: int = 0, losses: int = 0) -> list[float]:
    return [1.0] * wins + [0.5] * ties + [0.0] * losses


def _healthy_panel(**kwargs) -> PanelHealth:
    defaults = {
        "judge_count": 3,
        "panel_gate_ok": True,
        "canary_all_passed": True,
        "drift_all_ok": True,
    }
    defaults.update(kwargs)
    return PanelHealth(**defaults)


def _gate(
    *,
    chat_scores: list[float] | None = None,
    article_scores: list[float] | None = None,
    ref_scores: list[float] | None = None,
    panel: PanelHealth | None = None,
    fidelity: FidelityGateInput | None = None,
    style: StyleGateInput | None = None,
    reference_available: bool = True,
    lock_ok: bool = True,
    plan: StatisticsPlan | None = None,
) -> HumanizeGateReport:
    plan = plan or StatisticsPlan()
    surface_inputs: dict[str, SurfaceInput] = {}
    if chat_scores is not None:
        surface_inputs[SURFACE_CHAT] = SurfaceInput(
            paired_statistics={
                "current-production": _stats(chat_scores),
            },
            fidelity=fidelity or FidelityGateInput(),
            style=style or StyleGateInput(),
        )
    if article_scores is not None:
        paired = {"current-production": _stats(article_scores)}
        if ref_scores is not None:
            paired["humanizer-zh-reference"] = _stats(
                ref_scores, peer="humanizer-zh-reference"
            )
        surface_inputs[SURFACE_ARTICLE] = SurfaceInput(
            paired_statistics=paired,
            fidelity=fidelity or FidelityGateInput(),
            style=style or StyleGateInput(),
            reference_available=reference_available,
        )
    return evaluate_humanize_gate(
        plan=plan,
        surface_inputs=surface_inputs,
        panel=panel or _healthy_panel(),
        judge_versions={"family-a-judge-1": "v1"},
        disagreement_items=[],
        missing_lock_items=[] if lock_ok else ["lock_id"],
        failed_case_ids=[],
        cost_latency={"total_generation_calls": 100},
    )


# ---------------------------------------------------------------------------
# Test plan 3：改善门边界（chat；AC-4 CI 下界 > 50%）
# ---------------------------------------------------------------------------

def test_improvement_gate_passes_when_ci_lower_bound_above_50():
    report = _gate(chat_scores=_scores(39, 0, 1))
    assert report.surface_verdicts[SURFACE_CHAT] == "passed"
    chat = report.surfaces[SURFACE_CHAT]
    improvement = next(
        check for check in chat.checks if check.check_id == "improvement_gate"
    )
    assert improvement.passed
    assert "CI 下界" in improvement.detail and "显著改善" in improvement.detail


def test_improvement_gate_fails_below_boundary():
    # 22 胜 18 败：mean 0.55，bootstrap CI 下界 0.40 < 0.50 → 明确失败。
    report = _gate(chat_scores=_scores(22, 0, 18))
    assert report.surface_verdicts[SURFACE_CHAT] == "failed"
    assert any("显著改善" in r for r in report.surfaces[SURFACE_CHAT].reasons)


def test_all_tie_fails_improvement():
    # 全部 tie：CI 下界 = 0.50，不 > 0.50 → 不能声称显著改善。
    report = _gate(chat_scores=_scores(0, 40, 0))
    assert report.surface_verdicts[SURFACE_CHAT] == "failed"


def test_balanced_split_fails_improvement():
    report = _gate(chat_scores=_scores(20, 0, 20))
    assert report.surface_verdicts[SURFACE_CHAT] == "failed"


def test_improvement_passes_with_ties():
    # 30 胜 5 平 5 败：CI (0.700, 0.912) → 通过。
    report = _gate(chat_scores=_scores(30, 5, 5))
    assert report.surface_verdicts[SURFACE_CHAT] == "passed"


# ---------------------------------------------------------------------------
# Test plan 3：inconclusive 条件（AC-10）
# ---------------------------------------------------------------------------

def test_insufficient_valid_cases_inconclusive():
    report = _gate(chat_scores=_scores(9, 0, 1))
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"
    assert any("有效案例" in r for r in report.surfaces[SURFACE_CHAT].reasons)


def test_position_bias_inconclusive():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        panel=_healthy_panel(inconsistent_items=["item-1"]),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"
    assert any("双向一致性" in r for r in report.surfaces[SURFACE_CHAT].reasons)


def test_panel_not_diverse_inconclusive():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        panel=_healthy_panel(panel_gate_ok=False),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"
    assert any("多样性" in r for r in report.surfaces[SURFACE_CHAT].reasons)


def test_insufficient_judges_inconclusive():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        panel=_healthy_panel(judge_count=2),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"


def test_canary_not_passed_inconclusive():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        panel=_healthy_panel(canary_all_passed=False),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"
    assert any("canary" in r for r in report.surfaces[SURFACE_CHAT].reasons)


def test_judge_drift_inconclusive():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        panel=_healthy_panel(drift_all_ok=False),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"


def test_cannot_judge_ratio_inconclusive():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        panel=_healthy_panel(cannot_judge_ratio=0.5),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"
    assert any("无法判断" in r for r in report.surfaces[SURFACE_CHAT].reasons)


def test_panel_disagreement_verdict_inconclusive():
    """聚合器分歧超阈值（aggregation_verdict != passed）→ inconclusive。

    不得因门内统计通过而误报 passed（Test plan 3 panel 分歧状态）。
    """
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        panel=_healthy_panel(aggregation_verdict="inconclusive"),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"
    assert any("分歧" in r for r in report.surfaces[SURFACE_CHAT].reasons)


def test_lock_incomplete_inconclusive():
    report = _gate(chat_scores=_scores(39, 0, 1), lock_ok=False)
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"
    assert any("运行锁" in r for r in report.surfaces[SURFACE_CHAT].reasons)


# ---------------------------------------------------------------------------
# Test plan 4：硬门（AC-7）—— 即使自然度全 5 分仍阻止发布
# ---------------------------------------------------------------------------

def test_fidelity_critical_failure_blocks_even_with_perfect_scores():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        fidelity=FidelityGateInput(critical_failed_cases=["case-3"]),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "failed"
    assert "case-3" in report.failed_case_ids
    blockers = format_gate_blockers(report)
    assert any("硬门" in b for b in blockers)


def test_fabricated_first_person_blocks_article():
    report = _gate(
        article_scores=_scores(39, 0, 1),
        ref_scores=_scores(40),
        fidelity=FidelityGateInput(critical_failed_cases=["article-case-7"]),
    )
    assert report.surface_verdicts[SURFACE_ARTICLE] == "failed"
    assert "article-case-7" in report.failed_case_ids


def test_fidelity_missing_checks_fail_closed_inconclusive():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        fidelity=FidelityGateInput(
            missing_checks=["case-1:数字"], missing_cases=["case-1"]
        ),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"
    assert any("fail closed" in r or "完整性" in r
               for r in report.surfaces[SURFACE_CHAT].reasons)


def test_noncritical_fidelity_rate_below_99_fails():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        fidelity=FidelityGateInput(
            noncritical_pass_rate=0.95,
            noncritical_check_count=200,
        ),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "failed"
    assert any("99" in r for r in report.surfaces[SURFACE_CHAT].reasons)


def test_protocol_leak_blocks_release():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        style=StyleGateInput(blocked_cases=["case-2"]),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "failed"
    assert any("协议/助手身份泄漏" in r
               for r in report.surfaces[SURFACE_CHAT].reasons)


def test_style_fail_closed_not_passed():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        style=StyleGateInput(fail_closed_cases=["case-2"]),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"


# ---------------------------------------------------------------------------
# Test plan 3/5：文章非劣效门（AC-5）与参考缺失
# ---------------------------------------------------------------------------

def test_article_passes_both_gates():
    # 改善门（vs current）+ 非劣效门（vs Humanizer-zh，CI 下界 ≥ 45%）。
    report = _gate(
        article_scores=_scores(39, 0, 1),
        ref_scores=_scores(40),
    )
    assert report.surface_verdicts[SURFACE_ARTICLE] == "passed"
    article = report.surfaces[SURFACE_ARTICLE]
    noninferiority = next(
        check for check in article.checks
        if check.check_id == "noninferiority_gate"
    )
    assert noninferiority.passed
    assert "5pp" in noninferiority.detail or "45%" in noninferiority.detail


def test_article_noninferiority_fails_below_45():
    # 22 胜 18 败：CI 下界 0.40 < 0.45 → 非劣效门失败。
    report = _gate(
        article_scores=_scores(39, 0, 1),
        ref_scores=_scores(22, 0, 18),
    )
    assert report.surface_verdicts[SURFACE_ARTICLE] == "failed"
    assert any("非劣效" in r for r in report.surfaces[SURFACE_ARTICLE].reasons)


def test_all_tie_passes_noninferiority_boundary():
    # 全 tie：CI 下界 = 0.50 ≥ 0.45，非劣效通过（改善门仍失败）。
    report = _gate(
        article_scores=_scores(0, 40, 0),
        ref_scores=_scores(0, 40, 0),
    )
    article = report.surfaces[SURFACE_ARTICLE]
    noninferiority = next(
        check for check in article.checks
        if check.check_id == "noninferiority_gate"
    )
    assert noninferiority.passed
    assert report.surface_verdicts[SURFACE_ARTICLE] == "failed"


def test_article_reference_unavailable_inconclusive():
    report = _gate(
        article_scores=_scores(39, 0, 1),
        reference_available=False,
    )
    assert report.surface_verdicts[SURFACE_ARTICLE] == "inconclusive"
    assert any("Humanizer-zh" in r for r in report.surfaces[SURFACE_ARTICLE].reasons)


def test_chat_does_not_use_article_reference():
    """聊天不以文章参考结果代替自己的门（AC-5/6 独立裁决）。"""
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        article_scores=_scores(39, 0, 1),
        ref_scores=_scores(39, 0, 1),
        reference_available=False,
    )
    # 参考不可用只影响文章；聊天独立通过。
    assert report.surface_verdicts[SURFACE_CHAT] == "passed"
    assert report.surface_verdicts[SURFACE_ARTICLE] == "inconclusive"


def test_surfaces_judged_independently():
    """聊天通过/文章失败：整体不误报通过。"""
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        article_scores=_scores(20, 0, 20),
        ref_scores=_scores(40),
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "passed"
    assert report.surface_verdicts[SURFACE_ARTICLE] == "failed"
    assert not report.passed


def test_slice_regression_fails_surface():
    """预注册关键切片明显退化 → 该面失败，不进入人工裁决。"""
    from bridges.humanize_eval.release_gate import CaseSliceInput

    # 40 个 case 总体胜率高（34 胜 6 败，改善门通过），
    # 但 risk:high 切片（6 个 case）全部失败 → 明显退化。
    chat_stats = _stats(_scores(34, 0, 6))
    slice_cases = [
        CaseSliceInput(
            case_id=f"case-{index}",
            preference=(
                JudgePreference.B if index >= 34 else JudgePreference.A
            ),
            slices={"risk": "high" if index >= 34 else "low"},
        )
        for index in range(40)
    ]
    surface_input = SurfaceInput(
        paired_statistics={"current-production": chat_stats},
        fidelity=FidelityGateInput(),
        style=StyleGateInput(),
        slice_cases=slice_cases,
    )
    report = evaluate_humanize_gate(
        plan=StatisticsPlan(),
        surface_inputs={SURFACE_CHAT: surface_input},
        panel=_healthy_panel(),
        judge_versions={},
        disagreement_items=[],
        missing_lock_items=[],
        failed_case_ids=[],
        cost_latency={},
    )
    assert report.surface_verdicts[SURFACE_CHAT] == "failed"
    assert report.surfaces[SURFACE_CHAT].regression_slices
    assert any("切片" in r for r in report.surfaces[SURFACE_CHAT].reasons)


# ---------------------------------------------------------------------------
# AC-11：元数据与机器可读标注
# ---------------------------------------------------------------------------

def test_report_metadata_marks_automated_judges_only():
    report = _gate(chat_scores=_scores(39, 0, 1))
    assert report.automated_system_judges_only is True
    assert report.human_validated is False
    assert "未经真实用户或人工验证" in report.note


def test_blockers_are_stable_chinese_reasons():
    report = _gate(
        chat_scores=_scores(39, 0, 1),
        fidelity=FidelityGateInput(critical_failed_cases=["case-3"]),
    )
    blockers = format_gate_blockers(report)
    assert blockers, "未通过时应给出稳定中文阻断原因"
    for blocker in blockers:
        assert blocker.startswith(f"[{SURFACE_CHAT}] ")


def test_statistics_gate_not_judged_when_samples_insufficient():
    """样本不足时统计门未判定（不产生误导性通过结论）。"""
    report = _gate(chat_scores=_scores(10, 0, 5))
    chat = report.surfaces[SURFACE_CHAT]
    improvement = next(
        check for check in chat.checks if check.check_id == "improvement_gate"
    )
    assert "未判定" in improvement.detail
    assert report.surface_verdicts[SURFACE_CHAT] == "inconclusive"
