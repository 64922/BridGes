"""配对统计（Issue 11）。

统计单位是 case，不是把同一 case 的 seed、重复执行、双向顺序或多个
系统裁判误当成独立样本。聚合分三层（AC-2）：

1. seed/execution 内聚：同一裁判对同一 case 的多个 item（重复执行）
   多数决出一个偏好；平票视为该裁判对该 case 无效（重试不稳定）。
2. 同裁判 A/B 与 B/A 聚：judge_pair 已做双向一致性校验，矛盾裁决
   标记 invalid；本模块按 sealed mapping 把槽位视角还原成 candidate
   视角后再次核对双向一致（防御性双保险）。
3. 预注册规则聚合有效系统裁判：多数票；分裂（无明确多数）保守计 tie。

主偏好分 = win + 0.5 × tie（AC-3）；CANNOT_JUDGE 不计胜负但计入
有效性报告。置信区间使用固定种子的 case-cluster bootstrap 百分位
方法（对 case 有放回重采样，保持 case 为独立统计单位）。
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from bridges.humanize_eval.judges import (
    JUDGE_DIMENSIONS,
    JudgeOrder,
    JudgePreference,
    JudgeVerdict,
)

#: candidate 视角的偏好标签（还原后与槽位无关）。
CANDIDATE_WIN = JudgePreference.A
PEER_WIN = JudgePreference.B


@dataclass(frozen=True)
class ItemPreference:
    """一个 (judge, item) 还原到 candidate 视角的有效偏好。

    preference：A=candidate 胜 / B=peer 胜 / TIE / CANNOT_JUDGE。
    """

    judge_id: str
    item_id: str
    case_id: str
    preference: JudgePreference
    valid: bool = True
    invalid_reason: str = ""


def candidate_preference(
    verdict: JudgeVerdict, candidate_in_a: bool
) -> JudgePreference:
    """把槽位视角的偏好还原成 candidate 视角（A=candidate 胜）。

    AB 顺序：候选一是 output_a；BA 顺序：候选一是 output_b。偏好恒指
    "候选一更好"，因此 BA 顺序下偏好指向的槽位取反。
    """
    if verdict.preference in (JudgePreference.TIE, JudgePreference.CANNOT_JUDGE):
        return verdict.preference
    if verdict.order is JudgeOrder.AB:
        cand_wins = (
            (candidate_in_a and verdict.preference is JudgePreference.A)
            or (not candidate_in_a and verdict.preference is JudgePreference.B)
        )
    else:
        cand_wins = (
            (candidate_in_a and verdict.preference is JudgePreference.B)
            or (not candidate_in_a and verdict.preference is JudgePreference.A)
        )
    return CANDIDATE_WIN if cand_wins else PEER_WIN


def resolve_judge_outcome(
    outcome: Any,
    *,
    case_by_item: dict[str, str],
    candidate_in_a_by_item: dict[str, bool],
) -> ItemPreference:
    """把一个 (judge, item) 的双向裁决还原成 candidate 视角偏好。

    ``outcome`` 需具备 judge_id / item_id / verdict_ab / verdict_ba
    （runner.JudgeOutcome 或测试替身）。任一方向无效、双向还原后
    矛盾（位置偏差）或缺少 mapping 记录时判为无效裁决。
    """
    entry = (
        candidate_in_a_by_item.get(outcome.item_id),
        case_by_item.get(outcome.item_id),
    )
    candidate_in_a, case_id = entry
    if candidate_in_a is None or case_id is None:
        return ItemPreference(
            judge_id=outcome.judge_id,
            item_id=outcome.item_id,
            case_id=case_id or "",
            preference=JudgePreference.CANNOT_JUDGE,
            valid=False,
            invalid_reason=f"缺少 item {outcome.item_id} 的 sealed mapping 记录。",
        )
    ab, ba = outcome.verdict_ab, outcome.verdict_ba
    if not (ab.is_valid and ba.is_valid):
        return ItemPreference(
            judge_id=outcome.judge_id,
            item_id=outcome.item_id,
            case_id=case_id,
            preference=JudgePreference.CANNOT_JUDGE,
            valid=False,
            invalid_reason=ab.invalid_reason or ba.invalid_reason,
        )
    pref_ab = candidate_preference(ab, candidate_in_a)
    pref_ba = candidate_preference(ba, candidate_in_a)
    if pref_ab != pref_ba:
        return ItemPreference(
            judge_id=outcome.judge_id,
            item_id=outcome.item_id,
            case_id=case_id,
            preference=JudgePreference.CANNOT_JUDGE,
            valid=False,
            invalid_reason="双向裁决还原后偏好矛盾（位置偏差），裁决无效。",
        )
    return ItemPreference(
        judge_id=outcome.judge_id,
        item_id=outcome.item_id,
        case_id=case_id,
        preference=pref_ab,
    )


def aggregate_case_preferences(
    preferences: list[ItemPreference],
) -> dict[str, JudgePreference]:
    """按 case 聚合 panel 偏好（AC-2 三层聚合）。

    每 (judge, case)：多个 item（seed/execution）多数决，平票视为无效；
    per case：有效裁判偏好多数票，无明确多数（分裂）保守计 TIE，
    CANNOT_JUDGE 不计胜负。返回 case_id -> 偏好
    （A=candidate 胜 / B=peer 胜 / TIE=平局或分裂）。
    """
    by_case_judge: dict[tuple[str, str], list[JudgePreference]] = defaultdict(list)
    for item in preferences:
        if item.preference is JudgePreference.CANNOT_JUDGE:
            continue
        by_case_judge[(item.case_id, item.judge_id)].append(item.preference)

    # seed/execution 内聚：同裁判同 case 多个 item 多数决；平票无效。
    case_judge_vote: dict[str, list[JudgePreference]] = defaultdict(list)
    for (case_id, judge_id), votes in sorted(by_case_judge.items()):
        winner = _majority(votes)
        if winner is not None:
            case_judge_vote[case_id].append(winner)

    # 裁判聚合：多数票；分裂计 tie。
    aggregated: dict[str, JudgePreference] = {}
    for case_id, votes in sorted(case_judge_vote.items()):
        winner = _majority(votes)
        aggregated[case_id] = winner if winner is not None else JudgePreference.TIE
    return aggregated


def _majority(votes: list[JudgePreference]) -> JudgePreference | None:
    """严格多数票（> 半数）；平票返回 None。"""
    counts: dict[JudgePreference, int] = defaultdict(int)
    for vote in votes:
        counts[vote] += 1
    top = max(counts.items(), key=lambda pair: pair[1])
    if top[1] > len(votes) / 2:
        return top[0]
    return None


class PairedStatistics(BaseModel):
    """candidate 相对一个 peer 的配对统计（case 为统计单位）。"""

    peer_sut_id: str = Field(description="对照系统（current-production / humanizer-zh-reference）。")
    n_cases_total: int = Field(description="参与该配对的 case 总数。")
    n_valid_cases: int = Field(description="有效统计 case 数（排除无法判断）。")
    wins: int = Field(description="candidate 胜 case 数。")
    ties: int = Field(description="平局 case 数（含 panel 分裂保守计 tie）。")
    losses: int = Field(description="candidate 败 case 数。")
    cannot_judge_cases: int = Field(description="无法判断 case 数（不计胜负）。")
    invalid_judge_cases: int = Field(description="裁判内聚无效的 case 数（平票）。")
    preference_mean: float = Field(description="主偏好分 = (win + 0.5×tie) / n_valid。")
    ci_low: float = Field(description="95% CI 下界（case-cluster bootstrap）。")
    ci_high: float = Field(description="95% CI 上界。")
    effect_size: float = Field(description="效应量 = preference_mean - 0.5（相对随机基线）。")
    per_case: dict[str, str] = Field(
        default_factory=dict, description="case_id -> win/tie/loss/cannot_judge/invalid。"
    )


def compute_paired_statistics(
    *,
    peer_sut_id: str,
    preferences: list[ItemPreference],
    case_ids: list[str] | None = None,
    bootstrap_seed: int = 2026,
    bootstrap_iterations: int = 2000,
    ci_level: float = 0.95,
) -> PairedStatistics:
    """从 (judge, item) 偏好计算配对统计与 bootstrap CI。

    ``preferences`` 为该配对的全部裁决（含无效与 CANNOT_JUDGE）；
    ``case_ids`` 为该配对应覆盖的 case 清单（用于报告完整覆盖）。
    case 状态分类：
    - 聚合出结论 → win/tie/loss（候选视角）；
    - 全部有效裁决为 CANNOT_JUDGE → cannot_judge（不计胜负）；
    - 无有效裁决或裁判内聚平票被丢弃 → invalid；
    - 在 case_ids 中但没有任何裁决 → no_verdict。
    """
    aggregated = aggregate_case_preferences(preferences)
    by_case: dict[str, list[ItemPreference]] = defaultdict(list)
    for item in preferences:
        by_case[item.case_id].append(item)
    cannot_judge_cases: list[str] = []
    invalid_judge_cases: list[str] = []
    for case_id, items in sorted(by_case.items()):
        valid = [item for item in items if item.valid]
        if not valid:
            invalid_judge_cases.append(case_id)
            continue
        if all(
            item.preference is JudgePreference.CANNOT_JUDGE for item in valid
        ):
            cannot_judge_cases.append(case_id)
        elif case_id not in aggregated:
            # 有有效裁决但聚合后被丢弃（裁判内聚平票）。
            invalid_judge_cases.append(case_id)

    wins = ties = losses = 0
    per_case: dict[str, str] = {}
    for case_id, preference in sorted(aggregated.items()):
        if preference is CANDIDATE_WIN:
            wins += 1
            per_case[case_id] = "win"
        elif preference is JudgePreference.TIE:
            ties += 1
            per_case[case_id] = "tie"
        else:
            losses += 1
            per_case[case_id] = "loss"
    for case_id in cannot_judge_cases:
        per_case[case_id] = "cannot_judge"
    for case_id in invalid_judge_cases:
        per_case[case_id] = "invalid"
    if case_ids is not None:
        for case_id in case_ids:
            if case_id not in per_case:
                per_case[case_id] = "no_verdict"

    scores = [1.0] * wins + [0.5] * ties + [0.0] * losses
    n_valid = len(scores)
    mean = statistics.fmean(scores) if scores else 0.0
    ci_low, ci_high = bootstrap_preference_ci(
        scores,
        seed=bootstrap_seed,
        iterations=bootstrap_iterations,
        ci_level=ci_level,
    )
    return PairedStatistics(
        peer_sut_id=peer_sut_id,
        n_cases_total=n_valid + len(cannot_judge_cases) + len(invalid_judge_cases),
        n_valid_cases=n_valid,
        wins=wins,
        ties=ties,
        losses=losses,
        cannot_judge_cases=len(cannot_judge_cases),
        invalid_judge_cases=len(invalid_judge_cases),
        preference_mean=mean,
        ci_low=ci_low,
        ci_high=ci_high,
        effect_size=mean - 0.5,
        per_case=per_case,
    )


def bootstrap_preference_ci(
    scores: list[float],
    *,
    seed: int,
    iterations: int = 2000,
    ci_level: float = 0.95,
) -> tuple[float, float]:
    """固定种子的 case-cluster bootstrap 百分位区间（确定性）。

    对 case 级偏好分列表有放回重采样（样本量与 case 数相同），每次
    重采样计算均值，取 (1-ci_level)/2 与 (1+ci_level)/2 分位数。
    样本量不足 2 时区间退化为点估计（门层用最小样本拦截）。
    """
    if len(scores) < 2:
        mean = statistics.fmean(scores) if scores else 0.0
        return (mean, mean)
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(iterations):
        sample = [rng.choice(scores) for _ in scores]
        means.append(statistics.fmean(sample))
    means.sort()
    alpha = (1.0 - ci_level) / 2.0
    low = means[int(len(means) * alpha)]
    high = means[int(len(means) * (1.0 - alpha))]
    return (low, high)


class DimensionStatistics(BaseModel):
    """一个评维度的成对统计（AC-6：均值/中位数、成对差、CI、裁判分歧）。

    统计单位与主偏好分一致：case 为独立样本；同一 case 的重复执行与
    多裁判先聚合（同 (case, 维度) 的裁判分数取均值），再跨 case 统计。
    """

    dimension: str
    candidate_mean: float = Field(description="candidate 维度分均值（1-5）。")
    peer_mean: float = Field(description="peer 维度分均值（1-5）。")
    candidate_median: float = Field(description="candidate 维度分中位数。")
    peer_median: float = Field(description="peer 维度分中位数。")
    mean_diff: float = Field(description="成对差均值（candidate - peer）。")
    ci_low: float = Field(description="成对差 95% CI 下界（case-cluster bootstrap）。")
    ci_high: float = Field(description="成对差 95% CI 上界。")
    judge_disagreement_count: int = Field(
        description="裁判间分歧数：同 case 同维度裁判分差 ≥2 的 case 数。"
    )
    n_cases: int = Field(description="参与该维度统计的 case 数。")


def compute_dimension_statistics(
    *,
    outcomes: list[Any],
    case_by_item: dict[str, str],
    candidate_in_a_by_item: dict[str, bool],
    bootstrap_seed: int = 2026,
    bootstrap_iterations: int = 2000,
    ci_level: float = 0.95,
) -> dict[str, DimensionStatistics]:
    """按维度计算 candidate 与 peer 的成对分统计（AC-6）。

    裁判给"展示顺序第一个候选"评分：AB 顺序的 scores 评 output_a，
    BA 顺序的 scores 评 output_b，组合出同一 item 的 (candidate, peer)
    成对维度分。同 (case, 维度) 的裁判分数先取均值（case 为统计单位），
    成对差 = candidate - peer，CI 用与主偏好分相同的 case-cluster
    bootstrap（同种子，报告哈希稳定）。
    """
    pairs: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for outcome in outcomes:
        ab, ba = outcome.verdict_ab, outcome.verdict_ba
        if not (ab.is_valid and ba.is_valid):
            continue
        case_id = case_by_item.get(ab.item_id)
        candidate_in_a = candidate_in_a_by_item.get(ab.item_id)
        if case_id is None or candidate_in_a is None:
            continue
        score_ab = {s.dimension: s.score for s in ab.scores}
        score_ba = {s.dimension: s.score for s in ba.scores}
        for dimension in JUDGE_DIMENSIONS:
            score_a = score_ab.get(dimension)
            score_b = score_ba.get(dimension)
            if score_a is None or score_b is None:
                continue
            candidate_score, peer_score = (
                (score_a, score_b) if candidate_in_a else (score_b, score_a)
            )
            pairs[(case_id, dimension)].append(
                (float(candidate_score), float(peer_score))
            )

    stats: dict[str, DimensionStatistics] = {}
    for dimension in JUDGE_DIMENSIONS:
        diffs: list[float] = []
        candidate_values: list[float] = []
        peer_values: list[float] = []
        disagreement = 0
        for case_id, dim in pairs:
            if dim != dimension:
                continue
            judge_pairs = pairs[(case_id, dim)]
            # 同 case 多裁判分数取均值（case 为统计单位）。
            candidate_mean = statistics.fmean(
                score for score, _ in judge_pairs
            )
            peer_mean = statistics.fmean(peer for _, peer in judge_pairs)
            candidate_values.append(candidate_mean)
            peer_values.append(peer_mean)
            diffs.append(candidate_mean - peer_mean)
            spread = max(score for score, _ in judge_pairs) - min(
                score for score, _ in judge_pairs
            )
            if spread >= 2:
                disagreement += 1
        if not diffs:
            continue
        ci_low, ci_high = bootstrap_preference_ci(
            diffs,
            seed=bootstrap_seed,
            iterations=bootstrap_iterations,
            ci_level=ci_level,
        )
        stats[dimension] = DimensionStatistics(
            dimension=dimension,
            candidate_mean=statistics.fmean(candidate_values),
            peer_mean=statistics.fmean(peer_values),
            candidate_median=statistics.median(candidate_values),
            peer_median=statistics.median(peer_values),
            mean_diff=statistics.fmean(diffs),
            ci_low=ci_low,
            ci_high=ci_high,
            judge_disagreement_count=disagreement,
            n_cases=len(diffs),
        )
    return stats


__all__ = [
    "DimensionStatistics",
    "ItemPreference",
    "PairedStatistics",
    "aggregate_case_preferences",
    "bootstrap_preference_ci",
    "candidate_preference",
    "compute_dimension_statistics",
    "compute_paired_statistics",
    "resolve_judge_outcome",
]
