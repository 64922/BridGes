"""工单 39：人味表达多轮盲评的匿名化、量表与统计（无模型调用）。

职责：

- 把三个策略臂的完整多轮会话匿名化：随机交换 A/B 顺序（固定种子进入
  运行锁），只暴露匿名标签；映射单独保存，评审者只见盲评材料。
- 五个独立维度（是否听懂、帮助、自然度、分寸、连续性）分别选择
  A / B / 平局 / 都不选；不用套话计数代替收益判断。
- 统计胜/平/拒选、评审一致性与 Wilson 区间；按预注册策略先判
  「帮助/分寸非劣」，通过后再看自然度，样本不足时明确不放行。
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 量表版本进入运行锁与报告，任何问题/选项变化都必须升版本。
SCALE_VERSION = "human-expression-scale-v1"
#: 预注册放行策略版本。
RELEASE_POLICY_VERSION = "human-expression-release-v1"


@dataclass(frozen=True)
class ReviewDimension:
    dimension_id: str
    label: str
    question: str
    anchors: str


#: 五个独立维度；自然度只在帮助/分寸非劣之后参与放行判断。
REVIEW_DIMENSIONS: tuple[ReviewDimension, ...] = (
    ReviewDimension(
        "understand",
        "听懂",
        "回答是否理解并回应了用户在本轮的真实请求？",
        "0=误解或答非所问；3=基本听懂；5=准确抓住意图与约束",
    ),
    ReviewDimension(
        "help",
        "帮助",
        "回答是否实际完成了任务或推进了问题？",
        "0=没有帮助；3=部分推进；5=明确完成或给出可执行下一步",
    ),
    ReviewDimension(
        "natural",
        "自然度",
        "表达是否像有分寸的伙伴，而不是套话或机械模板？",
        "0=明显机械或堆砌温暖词；3=自然可读；5=自然且贴合语境",
    ),
    ReviewDimension(
        "boundary",
        "分寸",
        "是否尊重用户本轮明确边界（不要建议/安慰/追问，只给答案，要求详细等）？",
        "0=明显越界；3=基本尊重；5=完全尊重且不丢失必要信息",
    ),
    ReviewDimension(
        "continuity",
        "连续性",
        "多轮对话中是否承接了前文任务、纠正或未完成事项，而不是只看最后一句？",
        "0=断片或重复；3=基本承接；5=准确接回前文并推进",
    ),
)

REVIEW_CHOICES: tuple[str, ...] = ("label_a", "label_b", "tie", "neither")
#: 主对照（涉及候选策略臂 `current-v4`）与其他配对分开统计。
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

    def completed_turns(self) -> int:
        return sum(1 for turn in self.turns if turn.status == "done")

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

    @property
    def primary(self) -> bool:
        return self.arm_x == CURRENT_ARM_ID or self.arm_y == CURRENT_ARM_ID


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
    """构建匿名单个对照项与 de-blind 映射；固定种子保证可复现。"""

    items: list[BlindPairItem] = []
    mapping: dict[str, dict[str, str]] = {}
    scenario_ids = {key[0] for key in transcripts}
    for scenario_id in sorted(scenario_ids):
        for comparison in comparisons:
            left = transcripts.get((scenario_id, comparison.arm_x))
            right = transcripts.get((scenario_id, comparison.arm_y))
            if left is None or right is None:
                continue
            key = f"{scenario_id}-{comparison.comparison_id}"
            rng = random.Random(f"{order_seed}:{key}")
            flip = rng.random() < 0.5
            text_x = left.render() if left.turns else ""
            text_y = right.render() if right.turns else ""
            item_id = key
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
            label_a_arm = comparison.arm_y if flip else comparison.arm_x
            label_b_arm = comparison.arm_x if flip else comparison.arm_y
            mapping[item_id] = {
                "comparison_id": comparison.comparison_id,
                "arm_x": comparison.arm_x,
                "arm_y": comparison.arm_y,
                "label_a_arm": label_a_arm,
                "label_b_arm": label_b_arm,
                "scenario_id": scenario_id,
            }
    return items, mapping


def render_blind_material(
    items: list[BlindPairItem],
    *,
    comparison_labels: dict[str, str] | None = None,
) -> str:
    """渲染评审者可见的盲评材料（只有匿名标签，无系统身份）。"""

    labels = comparison_labels or {}
    lines = [
        "# 人味表达多轮盲评材料",
        "",
        "同一模型的两种表达策略分别生成了同一组连续多轮对话，顺序已随机交换。",
        "请对每一项的五个维度分别选择：A、B、平局、都不选；不看系统身份，",
        "只判断回答本身。事实/状态/边界硬失败由确定性检查单列，不进入下面的选择。",
        "",
    ]
    for dimension in REVIEW_DIMENSIONS:
        lines.append(f"- {dimension.label}：{dimension.question}（{dimension.anchors}）")
    lines.extend(["", "---", ""])
    for item in items:
        label = labels.get(item.comparison_id, item.comparison_id)
        lines.extend(
            [
                f"## {item.item_id}（场景：{item.title}；对照：{label}）",
                "",
                "### 会话 A",
                "",
                item.label_a_text or "（无内容）",
                "",
                "### 会话 B",
                "",
                item.label_b_text or "（无内容）",
                "",
                "评审表（把选择填入下方，可加理由）：",
                "",
                "| 维度 | A | B | 平局 | 都不选 | 理由（可选） |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for dimension in REVIEW_DIMENSIONS:
            lines.append(f"| {dimension.label} |  |  |  |  |  |")
        lines.append("")
    return "\n".join(lines)


def submission_template(items: list[BlindPairItem]) -> dict[str, Any]:
    """供评审者填写的 JSON 模板（含全部对照项与维度）。"""

    return {
        "review_set_id": items[0].review_set_id if items else "",
        "instructions": "每个 item 的五个维度各填 label_a / label_b / tie / neither。",
        "reviewers": [
            {
                "reviewer_id": "请填写评审者标识",
                "choices": {
                    item.item_id: {
                        dimension.dimension_id: "" for dimension in REVIEW_DIMENSIONS
                    }
                    for item in items
                },
                "comments": {},
            }
        ],
    }


@dataclass(frozen=True)
class ReviewChoice:
    reviewer_id: str
    item_id: str
    dimension_id: str
    chosen: str
    rationale: str | None = None


def parse_submissions(payload: dict[str, Any]) -> list[ReviewChoice]:
    """解析评审提交 JSON；非法选择抛 ValueError（不静默丢弃）。"""

    choices: list[ReviewChoice] = []
    valid_dimensions = {dimension.dimension_id for dimension in REVIEW_DIMENSIONS}
    for reviewer in payload.get("reviewers", []):
        reviewer_id = str(reviewer.get("reviewer_id", "")).strip()
        if not reviewer_id:
            raise ValueError("评审提交缺少 reviewer_id。")
        for item_id, dimension_choices in (reviewer.get("choices") or {}).items():
            for dimension_id, chosen in (dimension_choices or {}).items():
                if dimension_id not in valid_dimensions:
                    raise ValueError(f"未登记的评审维度：{dimension_id}。")
                if not chosen:
                    continue
                if chosen not in REVIEW_CHOICES:
                    raise ValueError(
                        f"评审选择不合法：{item_id}/{dimension_id}={chosen}。"
                    )
                choices.append(
                    ReviewChoice(
                        reviewer_id=reviewer_id,
                        item_id=str(item_id),
                        dimension_id=dimension_id,
                        chosen=str(chosen),
                    )
                )
    return choices


def load_submissions(path: str | Path) -> list[ReviewChoice]:
    return parse_submissions(json.loads(Path(path).read_text(encoding="utf-8")))


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
    comparison_id: str
    dimension_id: str
    item_count: int
    decisive: int
    label_a_wins: int
    label_b_wins: int
    ties: int
    neither: int
    win_rate_a: float
    wilson_low: float
    wilson_high: float
    neither_rate: float
    reviewer_count: int
    agreement: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparison_id": self.comparison_id,
            "dimension_id": self.dimension_id,
            "item_count": self.item_count,
            "decisive": self.decisive,
            "label_a_wins": self.label_a_wins,
            "label_b_wins": self.label_b_wins,
            "ties": self.ties,
            "neither": self.neither,
            "win_rate_a": round(self.win_rate_a, 4),
            "wilson_low": round(self.wilson_low, 4),
            "wilson_high": round(self.wilson_high, 4),
            "neither_rate": round(self.neither_rate, 4),
            "reviewer_count": self.reviewer_count,
            "agreement": round(self.agreement, 4),
        }


def aggregate_review(
    items: list[BlindPairItem],
    mapping: dict[str, dict[str, str]],
    choices: list[ReviewChoice],
) -> dict[str, Any]:
    """按对照 × 维度统计胜/平/拒选与不确定性；保留评审者一致率。"""

    by_item: dict[str, list[ReviewChoice]] = {}
    for choice in choices:
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
        for dimension in REVIEW_DIMENSIONS:
            a_wins = b_wins = ties = neither = 0
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
                        if arm == info.get("arm_x"):
                            a_wins += 1
                        else:
                            b_wins += 1
                if len(votes) >= 2:
                    agreement_denominator += 1
                    if len(set(votes)) == 1:
                        agreement_hits += 1
            decisive = a_wins + b_wins
            total_votes = decisive + ties + neither
            results.append(
                DimensionResult(
                    comparison_id=comparison_id,
                    dimension_id=dimension.dimension_id,
                    item_count=len(comparison_items),
                    decisive=decisive,
                    label_a_wins=a_wins,
                    label_b_wins=b_wins,
                    ties=ties,
                    neither=neither,
                    win_rate_a=(a_wins / decisive) if decisive else 0.0,
                    wilson_low=wilson_interval(a_wins, decisive)[0],
                    wilson_high=wilson_interval(a_wins, decisive)[1],
                    neither_rate=(neither / total_votes) if total_votes else 0.0,
                    reviewer_count=len(reviewer_ids),
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
        "dimensions": [result.to_dict() for result in results],
    }


@dataclass(frozen=True)
class ExpressionReleasePolicy:
    """预注册放行策略：帮助/分寸非劣先过门，再判断自然度。"""

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
            "status": "inconclusive",
            "released": False,
            "candidate_arm": candidate_arm,
            "policy": active.to_dict(),
            "reasons": ["尚无人工盲评提交：不放行，也不宣称自然度提升。"],
            "blockers": [],
            "hard_gate_failures": list(hard_gate_failures),
        }
    if aggregate["reviewer_count"] < active.min_reviewers:
        blockers.append("评审人数不足。")
    if hard_gate_failures:
        blockers.append(f"候选策略存在硬门失败：{'；'.join(hard_gate_failures)}。")

    for comparison_id in ("current-vs-baseline", "current-vs-legacy"):
        help_result = _result(aggregate, comparison_id, "help")
        boundary_result = _result(aggregate, comparison_id, "boundary")
        if help_result is None or boundary_result is None:
            blockers.append(f"{comparison_id} 缺少帮助/分寸维度。")
            continue
        for label, result in (("帮助", help_result), ("分寸", boundary_result)):
            if result["decisive"] < active.min_decisive_votes:
                blockers.append(
                    f"{comparison_id}/{label} 有效投票不足"
                    f"（{result['decisive']} < {active.min_decisive_votes}）。"
                )
            elif result["win_rate_a"] < 0.5:
                blockers.append(f"{comparison_id}/{label} 未达到非劣（胜率 < 0.5）。")
            if result["neither_rate"] > active.max_neither_rate:
                blockers.append(
                    f"{comparison_id}/{label} 拒选率过高"
                    f"（{result['neither_rate']:.0%}）。"
                )
    # 帮助/分寸非劣通过后才读取自然度；任何自然度优势都以实际投票为准。
    help_blocked = any("帮助" in blocker or "分寸" in blocker for blocker in blockers)
    if not help_blocked:
        for comparison_id in ("current-vs-baseline", "current-vs-legacy"):
            natural = _result(aggregate, comparison_id, "natural")
            if natural is None:
                blockers.append(f"{comparison_id} 缺少自然度维度。")
                continue
            if natural["decisive"] < active.min_decisive_votes:
                blockers.append(f"{comparison_id}/自然度 有效投票不足。")
            elif natural["wilson_low"] <= active.naturalness_min_win_rate:
                blockers.append(
                    f"{comparison_id}/自然度 未显示统计上可区分的优势"
                    f"（Wilson 下界 {natural['wilson_low']:.2f}）。"
                )
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


__all__ = [
    "BlindPairItem",
    "ComparisonSpec",
    "CURRENT_ARM_ID",
    "DEFAULT_COMPARISONS",
    "DimensionResult",
    "ExpressionReleasePolicy",
    "RELEASE_POLICY_VERSION",
    "REVIEW_CHOICES",
    "REVIEW_DIMENSIONS",
    "ReviewChoice",
    "ReviewDimension",
    "SCALE_VERSION",
    "ScenarioTranscript",
    "TranscriptTurn",
    "aggregate_review",
    "build_blind_review",
    "evaluate_release",
    "load_submissions",
    "parse_submissions",
    "render_blind_material",
    "submission_template",
    "wilson_interval",
]
