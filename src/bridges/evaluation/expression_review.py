"""工单 39：人味表达多轮盲评的匿名化与统计（无模型调用）。

职责：

- 把策略臂的完整多轮会话匿名化：随机交换 A/B 顺序（固定种子进入
  运行锁），评审者只见到匿名项；de-blind 映射保留在评测侧。
- 统计按策略臂取向（不按随机的 A/B 标签）：每个对照 × 维度记录各臂
  胜场、候选臂胜率、平局与拒选，并给出候选臂胜率的 Wilson 区间。
- 按预注册策略先判「帮助/分寸非劣」，通过后再看自然度；样本不足时
  明确不放行，不用套话计数代替收益判断。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

from bridges.evaluation.expression_scale import REVIEW_CHOICES, REVIEW_DIMENSIONS

#: 候选策略臂标识（主对照涉及它时按候选取向统计与放行）。
CURRENT_ARM_ID = "current-v4"


@dataclass(frozen=True)
class TranscriptTurn:
    """一个已完成的回合（用户输入 + 助手最终回答）。"""

    user: str
    assistant: str
    status: str = "done"
    error_code: str | None = None
    tool_outcome: str = "none"


@dataclass(frozen=True)
class ScenarioTranscript:
    """一个场景在一个策略臂下的完整多轮会话。"""

    scenario_id: str
    title: str
    category: str
    formal_path: str
    arm_id: str
    turns: tuple[TranscriptTurn, ...]

    def render(self) -> str:
        lines: list[str] = []
        for index, turn in enumerate(self.turns, 1):
            lines.append(f"用户（第 {index} 轮）：{turn.user}")
            if turn.status == "done":
                lines.append(f"回答（第 {index} 轮）：{turn.assistant.strip()}")
            else:
                lines.append(
                    f"回答（第 {index} 轮）：【未完成，状态 {turn.status}"
                    + (f"，错误码 {turn.error_code}" if turn.error_code else "")
                    + "】"
                )
        return "\n".join(lines)


@dataclass(frozen=True)
class BlindPairItem:
    """一个匿名对照项：同一场景两个策略臂的完整多轮会话。"""

    item_id: str
    review_set_id: str
    scenario_id: str
    title: str
    category: str
    formal_path: str
    comparison_id: str
    label_a_text: str
    label_b_text: str
    order_seed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "review_set_id": self.review_set_id,
            "scenario_id": self.scenario_id,
            "title": self.title,
            "category": self.category,
            "formal_path": self.formal_path,
            "comparison_id": self.comparison_id,
            "label_a_text": self.label_a_text,
            "label_b_text": self.label_b_text,
            "order_seed": self.order_seed,
        }


@dataclass(frozen=True)
class ComparisonSpec:
    comparison_id: str
    arm_x: str
    arm_y: str


#: 三个策略臂的全部两两配对（三组真实模型配对）。
DEFAULT_COMPARISONS: tuple[ComparisonSpec, ...] = (
    ComparisonSpec("current-vs-legacy", "current-v4", "legacy-v2"),
    ComparisonSpec("current-vs-baseline", "current-v4", "concise-baseline"),
    ComparisonSpec("legacy-vs-baseline", "legacy-v2", "concise-baseline"),
)


def build_blind_review(
    review_set_id: str,
    transcripts: dict[tuple[str, str], ScenarioTranscript],
    *,
    comparisons: tuple[ComparisonSpec, ...] = DEFAULT_COMPARISONS,
    order_seed: int = 39,
) -> tuple[list[BlindPairItem], dict[str, dict[str, str]]]:
    """构建匿名对照项与 de-blind 映射；固定种子保证可复现。

    对照项标识按固定顺序生成匿名编号（item-001…），不包含场景、策略或
    对照名；评审材料因此不泄漏身份，映射只留在评测侧。
    """

    items: list[BlindPairItem] = []
    mapping: dict[str, dict[str, str]] = {}
    scenario_ids = {key[0] for key in transcripts}
    index = 0
    for scenario_id in sorted(scenario_ids):
        for comparison in comparisons:
            left = transcripts.get((scenario_id, comparison.arm_x))
            right = transcripts.get((scenario_id, comparison.arm_y))
            if left is None or right is None:
                continue
            index += 1
            key = f"{scenario_id}-{comparison.comparison_id}"
            rng = random.Random(f"{order_seed}:{key}")
            flip = rng.random() < 0.5
            text_x = left.render() if left.turns else ""
            text_y = right.render() if right.turns else ""
            item_id = f"item-{index:03d}"
            items.append(
                BlindPairItem(
                    item_id=item_id,
                    review_set_id=review_set_id,
                    scenario_id=scenario_id,
                    title=left.title,
                    category=left.category,
                    formal_path=left.formal_path,
                    comparison_id=comparison.comparison_id,
                    label_a_text=text_y if flip else text_x,
                    label_b_text=text_x if flip else text_y,
                    order_seed=order_seed,
                )
            )
            mapping[item_id] = {
                "comparison_id": comparison.comparison_id,
                "arm_x": comparison.arm_x,
                "arm_y": comparison.arm_y,
                "label_a_arm": comparison.arm_y if flip else comparison.arm_x,
                "label_b_arm": comparison.arm_x if flip else comparison.arm_y,
                "scenario_id": scenario_id,
            }
    return items, mapping


# ---------------------------------------------------------------------------
# 统计与放行
# ---------------------------------------------------------------------------


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """二项比例的 Wilson 95% 区间；样本为 0 时返回 (0,1)。"""

    if total <= 0:
        return (0.0, 1.0)
    phat = successes / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))
        / denominator
    )
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass(frozen=True)
class DimensionResult:
    """一个对照 × 维度按策略臂取向的统计结果。

    胜场按真实策略臂归属（`label_a_arm`/`label_b_arm` 经映射还原），
    不按随机的 A/B 标签，避免候选臂胜率随洗牌漂移。
    """

    comparison_id: str
    dimension_id: str
    item_count: int
    decisive: int
    arm_x: str
    arm_y: str
    arm_x_wins: int
    arm_y_wins: int
    ties: int
    neither: int
    arm_x_win_rate: float
    candidate_arm: str | None
    candidate_wins: int | None
    candidate_win_rate: float | None
    candidate_wilson_low: float | None
    candidate_wilson_high: float | None
    neither_rate: float
    reviewer_count: int
    agreement: float

    def to_dict(self) -> dict[str, Any]:
        def rounded(value: float | None) -> float | None:
            return round(value, 4) if value is not None else None

        return {
            "comparison_id": self.comparison_id,
            "dimension_id": self.dimension_id,
            "item_count": self.item_count,
            "decisive": self.decisive,
            "arm_x": self.arm_x,
            "arm_y": self.arm_y,
            "arm_x_wins": self.arm_x_wins,
            "arm_y_wins": self.arm_y_wins,
            "ties": self.ties,
            "neither": self.neither,
            "arm_x_win_rate": rounded(self.arm_x_win_rate),
            "candidate_arm": self.candidate_arm,
            "candidate_wins": self.candidate_wins,
            "candidate_win_rate": rounded(self.candidate_win_rate),
            "candidate_wilson_low": rounded(self.candidate_wilson_low),
            "candidate_wilson_high": rounded(self.candidate_wilson_high),
            "neither_rate": rounded(self.neither_rate),
            "reviewer_count": self.reviewer_count,
            "agreement": rounded(self.agreement),
        }


def aggregate_review(
    items: list[BlindPairItem],
    mapping: dict[str, dict[str, str]],
    choices: list[Any],
    *,
    candidate_arm: str = CURRENT_ARM_ID,
) -> dict[str, Any]:
    """按对照 × 维度统计臂取向胜/平/拒选与不确定性；保留评审者一致率。"""

    item_by_id = {item.item_id: item for item in items}
    seen: set[tuple[str, str, str]] = set()
    valid_dimensions = {dimension.dimension_id for dimension in REVIEW_DIMENSIONS}
    by_item: dict[str, list[Any]] = {}
    for choice in choices:
        item = item_by_id.get(choice.item_id)
        key = (choice.reviewer_id, choice.item_id, choice.dimension_id)
        if item is None or choice.item_id not in mapping:
            raise ValueError(f"评审提交引用未知材料：{choice.item_id}。")
        if choice.review_set_id is not None and choice.review_set_id != item.review_set_id:
            raise ValueError("评审提交与材料的 review_set_id 不一致。")
        if key in seen:
            raise ValueError(f"同一评审者对同一材料维度重复投票：{key}。")
        if choice.dimension_id not in valid_dimensions or choice.chosen not in REVIEW_CHOICES:
            raise ValueError("评审维度或选择不合法。")
        seen.add(key)
        by_item.setdefault(choice.item_id, []).append(choice)
    by_comparison: dict[str, list[BlindPairItem]] = {}
    for item in items:
        info = mapping.get(item.item_id)
        if info is None:
            continue
        by_comparison.setdefault(info["comparison_id"], []).append(item)

    results: list[DimensionResult] = []
    reviewer_ids = {choice.reviewer_id for choice in choices}
    for comparison_id, comparison_items in sorted(by_comparison.items()):
        first_info = mapping[comparison_items[0].item_id]
        arm_x = first_info["arm_x"]
        arm_y = first_info["arm_y"]
        candidate = candidate_arm if candidate_arm in (arm_x, arm_y) else None
        for dimension in REVIEW_DIMENSIONS:
            dimension_reviewers = {
                choice.reviewer_id
                for item in comparison_items
                for choice in by_item.get(item.item_id, [])
                if choice.dimension_id == dimension.dimension_id
            }
            arm_wins: dict[str, int] = {arm_x: 0, arm_y: 0}
            ties = neither = 0
            agreement_hits = 0
            agreement_denominator = 0
            for item in comparison_items:
                info = mapping[item.item_id]
                votes = [
                    choice.chosen
                    for choice in by_item.get(item.item_id, [])
                    if choice.dimension_id == dimension.dimension_id
                ]
                for vote in votes:
                    if vote == "tie":
                        ties += 1
                    elif vote == "neither":
                        neither += 1
                    else:
                        arm = info.get(f"{vote}_arm")
                        if arm in arm_wins:
                            arm_wins[arm] += 1
                if len(votes) >= 2:
                    agreement_denominator += 1
                    if len(set(votes)) == 1:
                        agreement_hits += 1
            decisive = arm_wins[arm_x] + arm_wins[arm_y]
            total_votes = decisive + ties + neither
            candidate_wins = (
                arm_wins[candidate] if candidate is not None else None
            )
            candidate_rate = (
                candidate_wins / decisive
                if candidate_wins is not None and decisive
                else None
            )
            wilson_low: float | None = None
            wilson_high: float | None = None
            if candidate_wins is not None and decisive:
                wilson_low, wilson_high = wilson_interval(candidate_wins, decisive)
            results.append(
                DimensionResult(
                    comparison_id=comparison_id,
                    dimension_id=dimension.dimension_id,
                    item_count=len(comparison_items),
                    decisive=decisive,
                    arm_x=arm_x,
                    arm_y=arm_y,
                    arm_x_wins=arm_wins[arm_x],
                    arm_y_wins=arm_wins[arm_y],
                    ties=ties,
                    neither=neither,
                    arm_x_win_rate=(arm_wins[arm_x] / decisive) if decisive else 0.0,
                    candidate_arm=candidate,
                    candidate_wins=candidate_wins,
                    candidate_win_rate=candidate_rate,
                    candidate_wilson_low=wilson_low,
                    candidate_wilson_high=wilson_high,
                    neither_rate=(neither / total_votes) if total_votes else 0.0,
                    reviewer_count=len(dimension_reviewers),
                    agreement=(
                        agreement_hits / agreement_denominator
                        if agreement_denominator
                        else 0.0
                    ),
                )
            )
    return {
        "reviewer_count": len(reviewer_ids),
        "submission_count": len(choices),
        "item_count": len(items),
        "candidate_arm": candidate_arm,
        "dimensions": [result.to_dict() for result in results],
    }


__all__ = [
    "BlindPairItem",
    "ComparisonSpec",
    "CURRENT_ARM_ID",
    "DEFAULT_COMPARISONS",
    "DimensionResult",
    "ScenarioTranscript",
    "TranscriptTurn",
    "aggregate_review",
    "build_blind_review",
    "wilson_interval",
]
