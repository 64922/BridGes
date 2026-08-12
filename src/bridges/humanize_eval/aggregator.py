"""预注册聚合器（Issue 10）。

聚合器使用预注册规则处理有效裁决，报告每个裁判的双向结果、证据、
分歧与可靠性。分歧超过阈值时返回 ``inconclusive``，不得调用第四个
"仲裁模型"临时改判。确定性保真硬门优先于所有裁判：任一关键事实、
保护区或虚构亲历失败直接判失败，多数票不能放行。

全流程可无人值守运行；任何异常只产生 ``fail`` 或 ``inconclusive``
及机器可读原因，不产生等待人工评审的状态。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, Field

from bridges.humanize_eval.judges import JudgePreference, JudgeVerdict
from bridges.humanize_eval.registry import JudgeRegistry, panel_gate_issues


class JudgeOutcomeLike(Protocol):
    """一个裁判对一个 item 的双向裁决（runner.JudgeOutcome 或测试替身）。"""

    judge_id: str
    verdict_ab: JudgeVerdict
    verdict_ba: JudgeVerdict

#: 预注册分歧阈值：分歧 item 占有效 item 的比例超过此值即 inconclusive。
DISAGREEMENT_ITEM_RATIO = 1 / 3

#: 预注册无法判断阈值：CANNOT_JUDGE 票占比过高即 inconclusive。
CANNOT_JUDGE_RATIO = 0.3

#: 固定报告标注：系统自动裁判结果，未经真实用户或人工验证。
AUTOMATED_ONLY_NOTE = (
    "本结论由系统自动裁判产生（automated_system_judges_only=true，"
    "human_validated=false），未经真实用户或人工验证。"
)


class JudgeOpinion(BaseModel):
    """一个裁判对一个 item 的还原后有效意见（含 AB/BA 双向结果）。"""

    judge_id: str
    valid: bool = Field(description="双向一致且证据/理由有效。")
    preference: JudgePreference = Field(description="AB 顺序偏好（槽位视角）。")
    reason_code: str = Field(default="")
    evidence_span: str = Field(default="")
    invalid_reason: str = Field(default="")
    # BA 顺序的对应信息（AC-12：报告每个裁判的双向结果与证据）。
    preference_ba: JudgePreference = Field(
        default=JudgePreference.TIE, description="BA 顺序偏好。"
    )
    evidence_span_ba: str = Field(default="", description="BA 顺序证据 span。")
    reason_code_ba: str = Field(default="", description="BA 顺序 reason code。")


class PanelItemConclusion(BaseModel):
    """一个比较项的 panel 结论。"""

    item_id: str
    winner: str | None = Field(
        default=None, description="a / b / tie；无多数时为空。"
    )
    vote_counts: dict[str, int] = Field(
        default_factory=dict, description="偏好 -> 有效票数。"
    )
    opinions: list[JudgeOpinion] = Field(default_factory=list)
    disagreement: bool = Field(default=False, description="多数不存在（分裂）。")
    fidelity_failed: bool = Field(default=False, description="保真硬门失败。")

    @property
    def judge_count(self) -> int:
        return len(self.opinions)

    @property
    def valid_votes(self) -> int:
        return sum(1 for opinion in self.opinions if opinion.valid)


class JudgeReliability(BaseModel):
    """一个裁判在本次运行的可靠性摘要。"""

    judge_id: str
    valid_verdicts: int = Field(description="有效裁决数（双向）。")
    invalid_verdicts: int = Field(description="无效裁决数。")
    position_bias_count: int = Field(description="位置偏差票数。")
    evidence_invalid_count: int = Field(description="证据/理由无效票数。")
    preference_distribution: dict[str, int] = Field(
        default_factory=dict, description="还原后偏好分布（A/B/TIE/CANNOT_JUDGE）。"
    )


class AggregationResult(BaseModel):
    """预注册规则聚合结果。"""

    verdict: str = Field(description="passed / failed / inconclusive。")
    reasons: list[str] = Field(default_factory=list)
    item_conclusions: list[PanelItemConclusion] = Field(default_factory=list)
    per_judge: dict[str, JudgeReliability] = Field(default_factory=dict)
    disagreement_items: list[str] = Field(default_factory=list)
    invalid_verdicts: int = Field(default=0)
    fidelity_failed_items: list[str] = Field(default_factory=list)
    automated_only: bool = True
    note: str = AUTOMATED_ONLY_NOTE


def aggregate_panel(
    *,
    registry: JudgeRegistry,
    outcomes: Sequence[JudgeOutcomeLike],
    fidelity_failed_case_ids: set[str],
    item_case_ids: dict[str, str],
) -> AggregationResult:
    """按预注册规则聚合全部裁判的双向裁决。

    ``outcomes`` 为每裁判×item 的双向裁决对，``fidelity_failed_case_ids``
    为保真硬门失败的 case 集合，``item_case_ids`` 为 item_id -> case_id。
    所有裁判看同一匿名 packet，A/B 槽位对全部裁判语义一致，无需还原即可
    跨裁判比对偏好；sealed mapping 只在 evaluator 侧保存，裁判拿不到。
    """
    reasons: list[str] = []
    panel_issues = panel_gate_issues(registry)
    if panel_issues:
        reasons.extend(panel_issues)
        return AggregationResult(
            verdict="inconclusive",
            reasons=reasons,
        )

    # 按 judge × item 组织裁决对。
    pairings: dict[tuple[str, str], tuple[JudgeVerdict, JudgeVerdict]] = {}
    for outcome in outcomes:
        ab = outcome.verdict_ab
        ba = outcome.verdict_ba
        pairings[(outcome.judge_id, ab.item_id)] = (ab, ba)

    by_item: dict[str, list[tuple[str, tuple[JudgeVerdict, JudgeVerdict]]]] = {}
    for (judge_id, item_id), pair in pairings.items():
        by_item.setdefault(item_id, []).append((judge_id, pair))

    per_judge_opinions: dict[str, list[JudgeOpinion]] = {}
    reliability: dict[str, JudgeReliability] = {}
    conclusions: list[PanelItemConclusion] = []
    disagreement_items: list[str] = []
    fidelity_failed_items: list[str] = []
    invalid_verdicts = 0

    for item_id, entries in sorted(by_item.items()):
        case_id = item_case_ids.get(item_id, "")
        fidelity_failed = case_id in fidelity_failed_case_ids
        opinions: list[JudgeOpinion] = []
        for judge_id, (ab, ba) in entries:
            valid = ab.is_valid and ba.is_valid
            if not valid:
                invalid_verdicts += 1
                invalid_reason = ab.invalid_reason or ba.invalid_reason
            else:
                invalid_reason = ""
            opinion = JudgeOpinion(
                judge_id=judge_id,
                valid=valid,
                preference=ab.preference,
                reason_code=ab.reason_code,
                evidence_span=ab.evidence_span,
                invalid_reason=invalid_reason,
                preference_ba=ba.preference,
                evidence_span_ba=ba.evidence_span,
                reason_code_ba=ba.reason_code,
            )
            opinions.append(opinion)
            per_judge_opinions.setdefault(judge_id, []).append(opinion)
        valid_opinions = [o for o in opinions if o.valid]
        vote_counts: dict[str, int] = {}
        for opinion in valid_opinions:
            vote_counts[opinion.preference.value] = (
                vote_counts.get(opinion.preference.value, 0) + 1
            )
        winner: str | None = None
        disagreement = False
        if valid_opinions:
            top_vote = max(vote_counts.values())
            # 多数票：超过一半有效票即给 winner（2-1 分裂有明确多数）。
            if top_vote > len(valid_opinions) / 2:
                winner = max(vote_counts.items(), key=lambda pair: pair[1])[0]
            # 分歧：任一少数派存在即记录（3-0 全票一致才无分歧）。
            disagreement = top_vote < len(valid_opinions)
        if disagreement:
            disagreement_items.append(item_id)
        conclusion = PanelItemConclusion(
            item_id=item_id,
            winner=winner,
            vote_counts=vote_counts,
            opinions=opinions,
            disagreement=disagreement,
            fidelity_failed=fidelity_failed,
        )
        conclusions.append(conclusion)
        if fidelity_failed:
            fidelity_failed_items.append(case_id)

    for judge_id, opinion_list in per_judge_opinions.items():
        valid_count = sum(1 for o in opinion_list if o.valid)
        invalid_list = [o for o in opinion_list if not o.valid]
        position_bias = sum(
            1 for o in invalid_list if "位置翻转" in o.invalid_reason
        )
        evidence_invalid = len(invalid_list) - position_bias
        distribution: dict[str, int] = {}
        for opinion in opinion_list:
            distribution[opinion.preference.value] = (
                distribution.get(opinion.preference.value, 0) + 1
            )
        reliability[judge_id] = JudgeReliability(
            judge_id=judge_id,
            valid_verdicts=valid_count,
            invalid_verdicts=len(invalid_list),
            position_bias_count=position_bias,
            evidence_invalid_count=evidence_invalid,
            preference_distribution=distribution,
        )

    # 预注册结论规则。
    if not conclusions:
        reasons.append("没有可聚合的裁判裁决。")
        return AggregationResult(
            verdict="inconclusive",
            reasons=reasons,
            per_judge=reliability,
        )

    cannot_judge_total = sum(
        o.preference is JudgePreference.CANNOT_JUDGE
        for conclusion in conclusions
        for o in conclusion.opinions
        if o.valid
    )
    valid_total = sum(
        1
        for conclusion in conclusions
        for o in conclusion.opinions
        if o.valid
    )
    if valid_total and cannot_judge_total / valid_total > CANNOT_JUDGE_RATIO:
        reasons.append(
            f"无法判断票占比过高（{cannot_judge_total}/{valid_total}），"
            "结论固定为 inconclusive。"
        )

    if disagreement_items:
        ratio = len(disagreement_items) / len(conclusions)
        # 分歧存在但未超阈值：只报告（item_conclusions/per_judge），不阻止结论。
        if ratio > DISAGREEMENT_ITEM_RATIO:
            reasons.append(
                f"panel 分歧：{len(disagreement_items)}/{len(conclusions)} item "
                f"存在少数派，占比 {ratio:.2f} 超过预注册阈值 "
                f"{DISAGREEMENT_ITEM_RATIO:.2f}，结论固定为 inconclusive"
                "（未调用任何仲裁模型）。"
            )

    if invalid_verdicts:
        reasons.append(f"无效裁决数：{invalid_verdicts}（不进入统计）。")

    if fidelity_failed_items:
        reasons.append(
            "保真硬门失败（多数票不能放行）："
            + "、".join(dict.fromkeys(fidelity_failed_items))
        )

    verdict = (
        "failed" if fidelity_failed_items else "inconclusive"
    ) if reasons else "passed"
    return AggregationResult(
        verdict=verdict,
        reasons=reasons,
        item_conclusions=conclusions,
        per_judge=reliability,
        disagreement_items=disagreement_items,
        invalid_verdicts=invalid_verdicts,
        fidelity_failed_items=list(dict.fromkeys(fidelity_failed_items)),
    )
