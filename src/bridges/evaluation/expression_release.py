"""工单 39：预注册放行门（先帮助/分寸，再自然度）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bridges.evaluation.expression_review import (
    CURRENT_ARM_ID,
    DEFAULT_COMPARISONS,
    ComparisonSpec,
)

RELEASE_POLICY_VERSION = "human-expression-release-v2"


@dataclass(frozen=True)
class ExpressionReleasePolicy:
    """预注册放行策略：帮助/分寸非劣先过门，再判断自然度。

    「非劣」口径：在决定性投票中候选臂胜率不低于 0.5（平局与拒选单列，
    不摊入胜率）；自然度要求候选臂胜率的 Wilson 下界高于 0.5，即显示
    统计上可区分的优势。阈值在任何盲评提交之前固定。
    """

    policy_id: str = "human-expression-release-policy"
    version: str = RELEASE_POLICY_VERSION
    min_reviewers: int = 1
    min_decisive_votes: int = 10
    max_neither_rate: float = 0.34
    naturalness_min_win_rate: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "min_reviewers": self.min_reviewers,
            "min_decisive_votes": self.min_decisive_votes,
            "max_neither_rate": self.max_neither_rate,
            "naturalness_min_win_rate": self.naturalness_min_win_rate,
        }


def _result(
    aggregate: dict[str, Any], comparison_id: str, dimension_id: str
) -> dict[str, Any] | None:
    for entry in aggregate.get("dimensions", []):
        if (
            entry["comparison_id"] == comparison_id
            and entry["dimension_id"] == dimension_id
        ):
            return dict(entry)
    return None


def primary_comparisons(candidate_arm: str) -> list[ComparisonSpec]:
    """涉及候选臂的预注册对照（放行判断只看这些）。"""

    return [
        spec
        for spec in DEFAULT_COMPARISONS
        if candidate_arm in (spec.arm_x, spec.arm_y)
    ]


def evaluate_release(
    aggregate: dict[str, Any],
    *,
    policy: ExpressionReleasePolicy | None = None,
    candidate_arm: str = CURRENT_ARM_ID,
    hard_gate_failures: tuple[str, ...] = (),
) -> dict[str, Any]:
    """按预注册策略给出放行结论；不足证据或硬失败明确不放行。"""

    active = policy or ExpressionReleasePolicy()
    blockers: list[str] = []
    if aggregate.get("submission_count", 0) == 0 or aggregate.get("reviewer_count", 0) == 0:
        return {
            "status": "not_released" if hard_gate_failures else "inconclusive",
            "released": False,
            "candidate_arm": candidate_arm,
            "policy": active.to_dict(),
            "reasons": ["尚无人工盲评提交：不放行，也不宣称自然度提升。",
                        *[f"硬门阻塞：{failure}" for failure in hard_gate_failures]],
            "blockers": list(hard_gate_failures),
            "hard_gate_failures": list(hard_gate_failures),
        }
    if aggregate["reviewer_count"] < active.min_reviewers:
        blockers.append("评审人数不足。")
    if hard_gate_failures:
        blockers.append(f"候选策略存在硬门失败：{'；'.join(hard_gate_failures)}。")

    comparisons = primary_comparisons(candidate_arm)
    if not comparisons:
        blockers.append(f"预注册对照缺少候选臂：{candidate_arm}。")
    for comparison in comparisons:
        for dimension_id, label in (("help", "帮助"), ("boundary", "分寸")):
            result = _result(aggregate, comparison.comparison_id, dimension_id)
            if result is None or result.get("candidate_win_rate") is None:
                blockers.append(f"{comparison.comparison_id} 缺少候选臂{label}统计。")
                continue
            if result["decisive"] < active.min_decisive_votes:
                blockers.append(
                    f"{comparison.comparison_id}/{label} 有效投票不足"
                    f"（{result['decisive']} < {active.min_decisive_votes}）。"
                )
            elif result["candidate_win_rate"] < 0.5:
                blockers.append(
                    f"{comparison.comparison_id}/{label} 未达到非劣"
                    f"（候选胜率 {result['candidate_win_rate']:.2f} < 0.5）。"
                )
            if result["reviewer_count"] < active.min_reviewers:
                blockers.append(f"{comparison.comparison_id}/{label} 评审人数不足。")
            if result["neither_rate"] > active.max_neither_rate:
                blockers.append(
                    f"{comparison.comparison_id}/{label} 拒选率过高"
                    f"（{result['neither_rate']:.0%}）。"
                )
    # 帮助/分寸非劣通过后才读取自然度；任何自然度优势都以实际投票为准。
    if not blockers:
        for comparison in comparisons:
            natural = _result(aggregate, comparison.comparison_id, "natural")
            if natural is None or natural.get("candidate_win_rate") is None:
                blockers.append(f"{comparison.comparison_id} 缺少候选臂自然度统计。")
                continue
            if natural["decisive"] < active.min_decisive_votes:
                blockers.append(f"{comparison.comparison_id}/自然度 有效投票不足。")
            elif (
                natural["candidate_wilson_low"] is None
                or natural["candidate_wilson_low"] <= active.naturalness_min_win_rate
            ):
                blockers.append(
                    f"{comparison.comparison_id}/自然度 未显示统计上可区分的优势"
                    f"（Wilson 下界 {natural['candidate_wilson_low']}）。"
                )
            if natural["reviewer_count"] < active.min_reviewers:
                blockers.append(f"{comparison.comparison_id}/自然度 评审人数不足。")
            if natural["neither_rate"] > active.max_neither_rate:
                blockers.append(f"{comparison.comparison_id}/自然度 拒选率过高。")
    status = "released" if not blockers else "not_released"
    reasons = blockers or ["帮助/分寸非劣且自然度优势通过预注册门。"]
    return {
        "status": status,
        "released": not blockers,
        "candidate_arm": candidate_arm,
        "policy": active.to_dict(),
        "reasons": reasons,
        "blockers": blockers,
        "hard_gate_failures": list(hard_gate_failures),
    }
