"""人味化发布质量门（Issue 11）。

聊天与文章分别裁决（AC-6），不互相抵消：

- 改善门：candidate 相对 current production 的偏好分 95% CI 下界
  大于 50% 才能声称显著改善（AC-4）；
- 非劣效门（仅文章）：candidate 相对 Humanizer-zh 的偏好分 CI 下界
  至少 45%（相对 50% 基线不差超过 5 个百分点，AC-5）；Humanizer-zh
  不可用时该门为 inconclusive（缺参考，不得用聊天结果代替）。
- 硬门：关键数字/单位/日期/专名/公式/引语/URL/否定边界/虚构亲历与
  保护区零严重失败；任一严重失败阻止对应 surface 发布（AC-7）。
- 非关键保真通过率至少 99%（AC-7）。
- 自动风格诊断：协议/助手身份泄漏阻止发布；风格短语仅诊断（AC-8）。
- 预注册关键切片（模式/强度/体裁/长度/风险/do-no-harm）明显退化
  直接失败或 inconclusive，不进入人工裁决（AC-9）。

未满足最少 case、至少三个合格裁判、裁判多样性、双向一致性、canary
校准、漂移、运行锁、参考哈希或硬门完整性时固定为 inconclusive
（AC-10）。报告标注 automated_system_judges_only=true 与
human_validated=false（AC-11），不复制私人正文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from bridges.humanize_eval.aggregator import AUTOMATED_ONLY_NOTE
from bridges.humanize_eval.judges import JudgePreference
from bridges.humanize_eval.paired_stats import (
    DimensionStatistics,
    PairedStatistics,
)
from bridges.humanize_eval.statistics_plan import StatisticsPlan

#: 语料面报告名（与 runner.SURFACE_REPORT_NAMES 对齐）。
SURFACE_CHAT = "chat_naturalness"
SURFACE_ARTICLE = "article_humanization"

#: 门检查项标识（报告/CLI 用）。
CHECK_MIN_CASES = "min_cases"
CHECK_PANEL = "panel_health"
CHECK_BIDIRECTIONAL = "bidirectional_consistency"
CHECK_CANARY = "canary_calibration"
CHECK_DRIFT = "judge_drift"
CHECK_LOCK = "run_lock"
CHECK_REFERENCE = "reference_available"
CHECK_FIDELITY_HARD = "fidelity_hard_gate"
CHECK_FIDELITY_NONCRITICAL = "fidelity_noncritical_pass_rate"
CHECK_FIDELITY_COMPLETE = "fidelity_check_completeness"
CHECK_PROTOCOL_LEAK = "protocol_leak"
CHECK_IMPROVEMENT = "improvement_gate"
CHECK_NONINFERIORITY = "noninferiority_gate"
CHECK_SLICES = "key_slices"
CHECK_CANNOT_JUDGE = "cannot_judge_ratio"


class GateCheck(BaseModel):
    """一条发布门检查。"""

    check_id: str
    passed: bool
    detail: str = Field(description="稳定中文原因。")
    skipped: bool = Field(
        default=False, description="未判定（样本不足等前置条件不满足）。"
    )

    def blocker(self) -> str:
        return f"[{self.check_id}] {self.detail}"


@dataclass(frozen=True)
class PanelHealth:
    """正式 panel 的健康观测（来自 runner/registry/canary）。

    ``panel_gate_ok`` 为预注册 panel 门（至少三个有效裁判 + 家族/
    提供方多样性 + 不与生成模型同家族，见 registry.panel_gate_issues）
    整体是否通过；``aggregation_verdict`` 为聚合器对分歧/无法判断
    占比阈值的裁决结果。
    """

    judge_count: int = 0
    panel_gate_ok: bool = False
    canary_all_passed: bool = False
    drift_all_ok: bool = True
    inconsistent_items: list[str] = field(default_factory=list)
    cannot_judge_ratio: float = 0.0
    aggregation_verdict: str = "passed"
    aggregation_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FidelityGateInput:
    """保真观测（fail closed）。"""

    critical_failed_cases: list[str] = field(default_factory=list)
    noncritical_pass_rate: float = 1.0
    noncritical_fail_count: int = 0
    noncritical_check_count: int = 0
    missing_checks: list[str] = field(default_factory=list)
    missing_cases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StyleGateInput:
    """自动风格诊断观测（AC-8）。"""

    blocked_cases: list[str] = field(default_factory=list)
    fail_closed_cases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CaseSliceInput:
    """一个 case 的还原偏好与切片属性（关键切片判定用）。"""

    case_id: str
    preference: JudgePreference
    slices: dict[str, str] = field(default_factory=dict)


class SurfaceGateResult(BaseModel):
    """一个语料面的发布门结果。"""

    surface: str
    verdict: str = Field(description="passed / failed / inconclusive。")
    checks: list[GateCheck] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    paired_statistics: dict[str, PairedStatistics] = Field(
        default_factory=dict, description="peer_sut_id -> 配对统计。"
    )
    slice_rates: dict[str, float] = Field(
        default_factory=dict, description="关键切片偏好率（诊断展示）。"
    )
    regression_slices: list[str] = Field(
        default_factory=list, description="明显退化的关键切片。"
    )


class HumanizeGateReport(BaseModel):
    """发布质量门完整报告（机器可读；不含私人正文）。"""

    plan_digest: str = Field(description="预注册统计计划哈希。")
    surface_verdicts: dict[str, str] = Field(
        default_factory=dict, description="surface -> passed/failed/inconclusive。"
    )
    surfaces: dict[str, SurfaceGateResult] = Field(default_factory=dict)
    judge_versions: dict[str, str] = Field(
        default_factory=dict, description="裁判 id -> 版本。"
    )
    judge_disagreement_items: list[str] = Field(
        default_factory=list, description="panel 分歧 item。"
    )
    dimensions: dict[str, dict[str, dict[str, DimensionStatistics]]] = Field(
        default_factory=dict,
        description="surface -> peer -> 维度 -> 成对统计（AC-6）。",
    )
    missing_items: list[str] = Field(
        default_factory=list, description="缺失/缺项清单。"
    )
    failed_case_ids: list[str] = Field(
        default_factory=list, description="保真硬门失败 case id（去重）。"
    )
    cost_latency: dict[str, Any] = Field(
        default_factory=dict, description="运行成本与延迟观测。"
    )
    automated_system_judges_only: bool = True
    human_validated: bool = False
    note: str = AUTOMATED_ONLY_NOTE

    @property
    def passed(self) -> bool:
        """全部面通过才算整体通过。"""
        return bool(self.surface_verdicts) and all(
            verdict == "passed" for verdict in self.surface_verdicts.values()
        )


def _base_checks(
    *,
    plan: StatisticsPlan,
    stats: PairedStatistics,
    panel: PanelHealth,
    fidelity: FidelityGateInput,
    style: StyleGateInput,
    lock_complete: bool,
    reference_available: bool,
    requires_reference: bool,
) -> tuple[list[GateCheck], list[str]]:
    """面级公共前置检查：样本/panel/一致性/canary/漂移/锁/参考/硬门。

    任一不满足 → inconclusive（除硬门失败与协议泄漏 → failed）。
    返回 (checks, 结论性 reasons)。无结论性问题时 reasons 为空。
    """
    checks: list[GateCheck] = []
    reasons: list[str] = []

    if stats.n_valid_cases < plan.min_cases_per_surface:
        checks.append(
            GateCheck(
                check_id=CHECK_MIN_CASES,
                passed=False,
                detail=(
                    f"有效案例 {stats.n_valid_cases} 少于预注册最小样本 "
                    f"{plan.min_cases_per_surface}，结论固定为 inconclusive。"
                ),
            )
        )
        reasons.append(
            f"有效案例不足（{stats.n_valid_cases}/{plan.min_cases_per_surface}）。"
        )

    if panel.judge_count < plan.min_panel_judges:
        checks.append(
            GateCheck(
                check_id=CHECK_PANEL,
                passed=False,
                detail=(
                    f"正式 panel 有效裁判 {panel.judge_count} 少于 "
                    f"{plan.min_panel_judges}，结论固定为 inconclusive。"
                ),
            )
        )
        reasons.append("正式 panel 裁判数不足。")
    if not panel.panel_gate_ok:
        checks.append(
            GateCheck(
                check_id=CHECK_PANEL,
                passed=False,
                detail=(
                    "预注册 panel 门未通过（裁判数量/多样性/家族排除），"
                    "结论固定为 inconclusive。"
                ),
            )
        )
        reasons.append("panel 门未通过（数量/多样性/家族）。")
    if panel.inconsistent_items:
        checks.append(
            GateCheck(
                check_id=CHECK_BIDIRECTIONAL,
                passed=False,
                detail=(
                    "存在双向一致性失败的 item："
                    + "、".join(dict.fromkeys(panel.inconsistent_items))
                    + "，结论固定为 inconclusive。"
                ),
            )
        )
        reasons.append("双向一致性未满足。")
    if not panel.canary_all_passed:
        checks.append(
            GateCheck(
                check_id=CHECK_CANARY,
                passed=False,
                detail="裁判 canary 硬门未全部通过（100% 要求），结论固定为 inconclusive。",
            )
        )
        reasons.append("裁判 canary 校准未通过。")
    if not panel.drift_all_ok:
        checks.append(
            GateCheck(
                check_id=CHECK_DRIFT,
                passed=False,
                detail="裁判相对冻结基线漂移超过预注册阈值，结论固定为 inconclusive。",
            )
        )
        reasons.append("裁判 canary 漂移超阈值。")
    if not lock_complete:
        checks.append(
            GateCheck(
                check_id=CHECK_LOCK,
                passed=False,
                detail="运行锁不完整，结论固定为 inconclusive。",
            )
        )
        reasons.append("运行锁不完整。")
    if requires_reference and not reference_available:
        checks.append(
            GateCheck(
                check_id=CHECK_REFERENCE,
                passed=False,
                detail=(
                    "Humanizer-zh 参考不可用（缺快照），非劣效门无对照，"
                    "文章结论固定为 inconclusive。"
                ),
            )
        )
        reasons.append("Humanizer-zh 参考不可用。")
    if panel.cannot_judge_ratio > plan.max_cannot_judge_ratio:
        checks.append(
            GateCheck(
                check_id=CHECK_CANNOT_JUDGE,
                passed=False,
                detail=(
                    f"无法判断票占比 {panel.cannot_judge_ratio:.2f} 超过预注册阈值 "
                    f"{plan.max_cannot_judge_ratio:.2f}，结论固定为 inconclusive。"
                ),
            )
        )
        reasons.append("无法判断票占比过高。")
    if panel.aggregation_verdict != "passed":
        checks.append(
            GateCheck(
                check_id=CHECK_PANEL,
                passed=False,
                detail=(
                    "预注册聚合判定非通过（panel 分歧超阈值/无裁决），"
                    "结论固定为 inconclusive，不得沿用自动成功语义。"
                ),
            )
        )
        reasons.append("panel 聚合分歧超阈值。")
    if fidelity.missing_checks or fidelity.missing_cases:
        checks.append(
            GateCheck(
                check_id=CHECK_FIDELITY_COMPLETE,
                passed=False,
                detail="保真检查存在缺失执行项（fail closed），结论固定为 inconclusive。",
            )
        )
        reasons.append("保真检查完整性未满足（fail closed）。")

    # 硬门失败与协议泄漏 → failed（多数票与分数都不能放行）。
    if fidelity.critical_failed_cases:
        checks.append(
            GateCheck(
                check_id=CHECK_FIDELITY_HARD,
                passed=False,
                detail=(
                    "关键保真硬门失败（零容忍，多数票不能放行）："
                    + "、".join(dict.fromkeys(fidelity.critical_failed_cases))
                ),
            )
        )
        reasons.append("关键保真硬门失败。")
    if style.blocked_cases:
        checks.append(
            GateCheck(
                check_id=CHECK_PROTOCOL_LEAK,
                passed=False,
                detail=(
                    "检测到稳定的协议/助手身份泄漏（阻止发布）："
                    + "、".join(dict.fromkeys(style.blocked_cases))
                ),
            )
        )
        reasons.append("协议/助手身份泄漏。")
    if style.fail_closed_cases:
        checks.append(
            GateCheck(
                check_id=CHECK_FIDELITY_COMPLETE,
                passed=False,
                detail="自动风格诊断失败关闭（保真缺失），不得标记通过。",
            )
        )
        reasons.append("自动风格诊断失败关闭。")

    if (
        fidelity.noncritical_check_count
        and fidelity.noncritical_pass_rate < plan.noncritical_fidelity_pass_rate
    ):
        checks.append(
            GateCheck(
                check_id=CHECK_FIDELITY_NONCRITICAL,
                passed=False,
                detail=(
                    f"非关键保真通过率 {fidelity.noncritical_pass_rate:.2%} 低于 "
                    f"预注册硬门 {plan.noncritical_fidelity_pass_rate:.0%}。"
                ),
            )
        )
        reasons.append("非关键保真通过率未达 99%。")
    return checks, reasons


def _statistical_checks(
    *,
    plan: StatisticsPlan,
    stats: PairedStatistics,
    surface: str,
    is_improvement: bool,
    is_noninferiority: bool,
) -> tuple[list[GateCheck], list[str]]:
    """改善门与非劣效门（统计门；样本不足时由前置门负责 inconclusive）。"""
    checks: list[GateCheck] = []
    reasons: list[str] = []
    if is_improvement:
        bound = plan.improvement_ci_lower_bound
        passed = stats.ci_low > bound
        checks.append(
            GateCheck(
                check_id=CHECK_IMPROVEMENT,
                passed=passed,
                detail=(
                    f"[{surface}] candidate 相对 current production 偏好分 "
                    f"{stats.preference_mean:.3f}（95% CI {stats.ci_low:.3f}~"
                    f"{stats.ci_high:.3f}）"
                    + (
                        f"，CI 下界大于 {bound:.0%}，声称显著改善。"
                        if passed
                        else f"，CI 下界未超过 {bound:.0%}，不满足显著改善门。"
                    )
                ),
            )
        )
        if not passed:
            reasons.append("相对 current production 未达到显著改善门。")
    if is_noninferiority:
        bound = plan.noninferiority_ci_lower_bound
        passed = stats.ci_low >= bound
        checks.append(
            GateCheck(
                check_id=CHECK_NONINFERIORITY,
                passed=passed,
                detail=(
                    f"[{surface}] candidate 相对 Humanizer-zh 偏好分 "
                    f"{stats.preference_mean:.3f}（95% CI {stats.ci_low:.3f}~"
                    f"{stats.ci_high:.3f}），非劣效边界 {plan.noninferiority_margin_pp:g}pp"
                    f"（CI 下界 {bound:.0%}）"
                    + ("，通过。" if passed else "，不通过。")
                ),
            )
        )
        if not passed:
            reasons.append("相对 Humanizer-zh 未达到 5pp 非劣效门。")
    return checks, reasons


def _slice_rates(
    plan: StatisticsPlan, cases: list[CaseSliceInput]
) -> tuple[dict[str, float], list[str]]:
    """计算关键切片偏好率，返回 (rates, 明显退化切片清单)。

    切片偏好率与主偏好分公式一致：win 计 1、tie 计 0.5、loss 计 0
    （AC-3）；该切片 case 数 ≥ 预注册最小样本且偏好率 < 预注册退化
    下限时视为明显退化（AC-9）。
    """
    by_slice: dict[str, list[float]] = {}
    for case in cases:
        if case.preference is JudgePreference.CANNOT_JUDGE:
            continue
        score = (
            1.0
            if case.preference is JudgePreference.A
            else 0.5
            if case.preference is JudgePreference.TIE
            else 0.0
        )
        for slice_name, value in case.slices.items():
            if slice_name not in plan.key_slices:
                continue
            by_slice.setdefault(f"{slice_name}:{value}", []).append(score)
    rates: dict[str, float] = {}
    regression: list[str] = []
    for key, values in sorted(by_slice.items()):
        if len(values) < plan.min_slice_cases:
            continue
        rate = sum(values) / len(values)
        rates[key] = rate
        if rate < plan.slice_regression_ci_lower_bound:
            regression.append(key)
    return rates, regression


def _decide(surface: str, checks: list[GateCheck], reasons: list[str]) -> str:
    """按检查结果裁决面级 verdict。

    - 硬门失败（保真/协议泄漏/非关键保真率）与统计门失败（改善门/
      非劣效门/关键切片退化）→ failed：明确未达标，不能发布；
    - 证据/条件不足（样本、panel、一致性、canary、漂移、锁、参考、
      无法判断占比、保真完整性）→ inconclusive：不得沿用自动成功语义；
    - 全部通过 → passed。
    """
    failed_classes = {
        CHECK_FIDELITY_HARD,
        CHECK_PROTOCOL_LEAK,
        CHECK_FIDELITY_NONCRITICAL,
        CHECK_IMPROVEMENT,
        CHECK_NONINFERIORITY,
        CHECK_SLICES,
    }
    for check in checks:
        if not check.passed and not check.skipped and check.check_id in failed_classes:
            return "failed"
    # skipped 的检查不参与裁决（样本不足的统计门已由前置检查判 inconclusive）。
    failed_checks = [
        check for check in checks
        if not check.passed and not check.skipped
    ]
    if failed_checks:
        return "inconclusive"
    return "passed"


def evaluate_humanize_gate(
    *,
    plan: StatisticsPlan,
    surface_inputs: dict[str, SurfaceInput],
    panel: PanelHealth,
    judge_versions: dict[str, str],
    disagreement_items: list[str],
    missing_lock_items: list[str],
    failed_case_ids: list[str],
    cost_latency: dict[str, Any] | None = None,
    dimensions: dict[str, dict[str, dict[str, DimensionStatistics]]] | None = None,
) -> HumanizeGateReport:
    """评估发布质量门并生成完整报告。"""
    report_surfaces: dict[str, SurfaceGateResult] = {}
    surface_verdicts: dict[str, str] = {}
    # 汇总各面的保真硬门失败 case（报告 failed_case_ids 单一事实源）。
    surface_failed_cases: list[str] = []
    for surface_input in surface_inputs.values():
        surface_failed_cases.extend(surface_input.fidelity.critical_failed_cases)
    for surface, surface_input in surface_inputs.items():
        stats_by_peer = surface_input.paired_statistics
        checks: list[GateCheck] = []
        reasons: list[str] = []
        slice_rates: dict[str, float] = {}
        regression_slices: list[str] = []
        # 面级逐 peer 前置 + 统计检查（每个 peer 独立评估）。
        # 文章面必须存在 Humanizer-zh 对照配对：缺失时非劣效门无数据，
        # 文章结论固定为 inconclusive（不得用聊天结果代替）。
        if (
            surface == SURFACE_ARTICLE
            and "humanizer-zh-reference" not in stats_by_peer
        ):
            checks.append(
                GateCheck(
                    check_id=CHECK_REFERENCE,
                    passed=False,
                    detail=(
                        "缺少 Humanizer-zh 对照配对（非劣效门无数据），"
                        "文章结论固定为 inconclusive。"
                    ),
                )
            )
            reasons.append("缺少 Humanizer-zh 对照配对。")
        for peer_id, stats in stats_by_peer.items():
            requires_reference = (
                surface == SURFACE_ARTICLE
                and peer_id == "humanizer-zh-reference"
            )
            base_checks, base_reasons = _base_checks(
                plan=plan,
                stats=stats,
                panel=panel,
                fidelity=surface_input.fidelity,
                style=surface_input.style,
                lock_complete=not missing_lock_items,
                reference_available=surface_input.reference_available,
                requires_reference=requires_reference,
            )
            checks.extend(base_checks)
            reasons.extend(base_reasons)
            if stats.n_valid_cases >= plan.min_cases_per_surface:
                stat_checks, stat_reasons = _statistical_checks(
                    plan=plan,
                    stats=stats,
                    surface=surface,
                    is_improvement=True,
                    is_noninferiority=(
                        surface == SURFACE_ARTICLE
                        and peer_id == "humanizer-zh-reference"
                    ),
                )
            else:
                # 样本不足：统计门标记 skipped（不参与裁决，不产生误导性
                # 文案），整体结论由 min_cases 前置检查固定为 inconclusive。
                stat_checks = [
                    GateCheck(
                        check_id=CHECK_IMPROVEMENT,
                        passed=False,
                        skipped=True,
                        detail="有效样本不足，改善门未判定（inconclusive）。",
                    )
                ]
                if surface == SURFACE_ARTICLE and peer_id == "humanizer-zh-reference":
                    stat_checks.append(
                        GateCheck(
                            check_id=CHECK_NONINFERIORITY,
                            passed=False,
                            skipped=True,
                            detail="有效样本不足，非劣效门未判定（inconclusive）。",
                        )
                    )
                stat_reasons = []
            checks.extend(stat_checks)
            reasons.extend(stat_reasons)
            if surface_input.slice_cases:
                slice_rates, regression_slices = _slice_rates(
                    plan, surface_input.slice_cases
                )
        if regression_slices:
            checks.append(
                GateCheck(
                    check_id=CHECK_SLICES,
                    passed=False,
                    detail=(
                        "预注册关键切片明显退化（直接失败或 inconclusive）："
                        + "、".join(regression_slices)
                    ),
                )
            )
            reasons.append("关键切片明显退化。")
        # 去重 reasons，保持稳定顺序。
        reasons = list(dict.fromkeys(reasons))
        verdict = _decide(surface, checks, reasons)
        surface_verdicts[surface] = verdict
        report_surfaces[surface] = SurfaceGateResult(
            surface=surface,
            verdict=verdict,
            checks=checks,
            reasons=reasons,
            paired_statistics=stats_by_peer,
            slice_rates=slice_rates,
            regression_slices=regression_slices,
        )
    return HumanizeGateReport(
        plan_digest=plan.digest(),
        surface_verdicts=surface_verdicts,
        surfaces=report_surfaces,
        judge_versions=judge_versions,
        judge_disagreement_items=disagreement_items,
        missing_items=missing_lock_items,
        dimensions=dimensions or {},
        failed_case_ids=list(
            dict.fromkeys([*surface_failed_cases, *failed_case_ids])
        ),
        cost_latency=cost_latency or {},
    )


@dataclass(frozen=True)
class SurfaceInput:
    """一个语料面的全部门输入。"""

    paired_statistics: dict[str, PairedStatistics]
    fidelity: FidelityGateInput
    style: StyleGateInput
    slice_cases: list[CaseSliceInput] = field(default_factory=list)
    reference_available: bool = True


def format_gate_blockers(report: HumanizeGateReport) -> list[str]:
    """稳定中文阻断原因清单（CLI 非零退出时输出）。"""
    blockers: list[str] = []
    for surface, result in report.surfaces.items():
        for reason in result.reasons:
            blockers.append(f"[{surface}] {reason}")
    return list(dict.fromkeys(blockers))


__all__ = [
    "CaseSliceInput",
    "FidelityGateInput",
    "GateCheck",
    "HumanizeGateReport",
    "PanelHealth",
    "StyleGateInput",
    "SurfaceGateResult",
    "SurfaceInput",
    "evaluate_humanize_gate",
    "format_gate_blockers",
    "SURFACE_CHAT",
    "SURFACE_ARTICLE",
]
