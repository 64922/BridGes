"""工单 39：盲评匿名化、提交与放行统计测试（无真实模型调用）。"""

from __future__ import annotations

import re

import pytest

from bridges.evaluation.expression_corpus import SCENARIOS
from bridges.evaluation.expression_policy_arms import StrategyArm
from bridges.evaluation.expression_release import (
    RELEASE_POLICY_VERSION,
    evaluate_release,
)
from bridges.evaluation.expression_review import (
    CURRENT_ARM_ID,
    DEFAULT_COMPARISONS,
    BlindPairItem,
    ScenarioTranscript,
    TranscriptTurn,
    aggregate_review,
    build_blind_review,
)
from bridges.evaluation.expression_scale import REVIEW_CHOICES, REVIEW_DIMENSIONS
from bridges.evaluation.expression_submission import (
    ReviewChoice,
    parse_submissions,
    render_blind_material,
    submission_template,
)

# ---------------------------------------------------------------------------
# 盲评构建、渲染与提交
# ---------------------------------------------------------------------------


def _transcripts() -> dict[tuple[str, str], ScenarioTranscript]:
    transcripts: dict[tuple[str, str], ScenarioTranscript] = {}
    for scenario in SCENARIOS[:3]:
        for arm in StrategyArm:
            transcripts[(scenario.scenario_id, arm.value)] = ScenarioTranscript(
                scenario_id=scenario.scenario_id,
                title=scenario.title,
                category=scenario.category.value,
                formal_path=scenario.formal_path,
                arm_id=arm.value,
                turns=(
                    TranscriptTurn(
                        user=scenario.turns[0],
                        assistant="先具体承接，再完成任务。",
                    ),
                    TranscriptTurn(
                        user=scenario.turns[-1],
                        assistant="第二轮继续承接并完成任务。",
                    ),
                ),
            )
    return transcripts


def test_blind_review_hides_identity_and_keeps_multi_turn_context() -> None:
    items, mapping = build_blind_review("review-1", _transcripts(), order_seed=39)
    assert len(items) == len(SCENARIOS[:3]) * len(DEFAULT_COMPARISONS)
    material = render_blind_material(items)
    for arm in StrategyArm:
        assert arm.value not in material
    for comparison in DEFAULT_COMPARISONS:
        assert comparison.comparison_id not in material
    for scenario in SCENARIOS[:3]:
        assert scenario.scenario_id not in material
    assert all(re.fullmatch(r"item-\d{3}", item.item_id) for item in items)
    first = items[0]
    assert first.label_a_text.startswith("用户（第 1 轮）：")
    assert "用户（第 2 轮）：" in first.label_a_text
    assert mapping[first.item_id]["label_a_arm"] in {arm.value for arm in StrategyArm}


def test_blind_review_order_is_reproducible_with_same_seed() -> None:
    first_items, _ = build_blind_review("review-1", _transcripts(), order_seed=39)
    second_items, _ = build_blind_review("review-1", _transcripts(), order_seed=39)
    assert [(item.item_id, item.label_a_text) for item in first_items] == [
        (item.item_id, item.label_a_text) for item in second_items
    ]


def test_submission_template_allows_ties_and_neither() -> None:
    items, _ = build_blind_review("review-1", _transcripts(), order_seed=39)
    template = submission_template(items)
    assert REVIEW_CHOICES == ("label_a", "label_b", "tie", "neither")
    choices = template["reviewers"][0]["choices"]
    assert set(choices[items[0].item_id]) == {
        dimension.dimension_id for dimension in REVIEW_DIMENSIONS
    }


def test_parse_submissions_rejects_invalid_choice() -> None:
    with pytest.raises(ValueError):
        parse_submissions(
            {
                "reviewers": [
                    {
                        "reviewer_id": "r1",
                        "choices": {"item-001": {"understand": "maybe"}},
                    }
                ]
            }
        )


# ---------------------------------------------------------------------------
# 统计与放行
# ---------------------------------------------------------------------------


def _synthetic_review(
    *,
    current_wins: bool = True,
    tie_rate: float = 0.0,
    neither_rate: float = 0.0,
    item_count: int = 8,
    reviewers: int = 2,
    flip_all: bool = False,
) -> tuple[list[BlindPairItem], dict[str, dict[str, str]], list[ReviewChoice]]:
    items: list[BlindPairItem] = []
    mapping: dict[str, dict[str, str]] = {}
    for comparison in DEFAULT_COMPARISONS:
        for index in range(item_count):
            item_id = f"{comparison.comparison_id}-item-{index:02d}"
            items.append(
                BlindPairItem(
                    item_id=item_id,
                    review_set_id="review-1",
                    scenario_id=f"scenario-{index}",
                    title=f"场景 {index}",
                    category="venting",
                    formal_path="chat.companion",
                    comparison_id=comparison.comparison_id,
                    label_a_text="A",
                    label_b_text="B",
                    order_seed=39,
                )
            )
            mapping[item_id] = {
                "comparison_id": comparison.comparison_id,
                "arm_x": comparison.arm_x,
                "arm_y": comparison.arm_y,
                "label_a_arm": (
                    comparison.arm_y if flip_all else comparison.arm_x
                ),
                "label_b_arm": (
                    comparison.arm_x if flip_all else comparison.arm_y
                ),
                "scenario_id": f"scenario-{index}",
            }
    choices: list[ReviewChoice] = []
    for reviewer_index in range(reviewers):
        reviewer_id = f"r{reviewer_index + 1}"
        for flat_index, item in enumerate(items):
            info = mapping[item.item_id]
            if "current-v4" in (info["arm_x"], info["arm_y"]):
                if current_wins:
                    favored = CURRENT_ARM_ID
                else:
                    favored = next(
                        arm
                        for arm in (info["arm_x"], info["arm_y"])
                        if arm != CURRENT_ARM_ID
                    )
            else:
                favored = info["arm_x"]
            for dimension in REVIEW_DIMENSIONS:
                if tie_rate and flat_index % max(1, round(1 / tie_rate)) == 0:
                    chosen = "tie"
                elif neither_rate and flat_index % max(1, round(1 / neither_rate)) == 0:
                    chosen = "neither"
                else:
                    chosen = (
                        "label_a" if info["label_a_arm"] == favored else "label_b"
                    )
                choices.append(
                    ReviewChoice(
                        reviewer_id=reviewer_id,
                        item_id=item.item_id,
                        dimension_id=dimension.dimension_id,
                        chosen=chosen,
                    )
                )
    return items, mapping, choices


def test_aggregate_counts_arm_oriented_wins_ties_and_neither() -> None:
    items, mapping, choices = _synthetic_review(tie_rate=0.25, neither_rate=0.2)
    aggregate = aggregate_review(items, mapping, choices)
    assert aggregate["reviewer_count"] == 2
    assert aggregate["candidate_arm"] == CURRENT_ARM_ID
    result = next(
        entry
        for entry in aggregate["dimensions"]
        if entry["comparison_id"] == "current-vs-baseline"
        and entry["dimension_id"] == "help"
    )
    assert result["candidate_wins"] > 0
    assert 0.0 <= result["candidate_win_rate"] <= 1.0
    assert result["ties"] > 0
    assert result["neither"] > 0


def test_aggregate_counts_candidate_wins_despite_label_flip() -> None:
    """A/B 顺序随机交换后，候选胜率按臂取向统计，不随洗牌漂移。"""

    items, mapping, choices = _synthetic_review(flip_all=True, current_wins=True)
    aggregate = aggregate_review(items, mapping, choices)
    result = next(
        entry
        for entry in aggregate["dimensions"]
        if entry["comparison_id"] == "current-vs-baseline"
        and entry["dimension_id"] == "help"
    )
    assert result["candidate_arm"] == CURRENT_ARM_ID
    assert result["candidate_win_rate"] == 1.0
    assert result["candidate_wilson_low"] > 0.5


def test_release_inconclusive_without_human_submissions() -> None:
    items, mapping, _ = _synthetic_review()
    aggregate = aggregate_review(items, mapping, [])
    release = evaluate_release(aggregate)
    assert release["status"] == "inconclusive"
    assert release["released"] is False
    assert release["policy"]["version"] == RELEASE_POLICY_VERSION


def test_release_blocks_on_candidate_hard_gate_failure() -> None:
    items, mapping, choices = _synthetic_review()
    aggregate = aggregate_review(items, mapping, choices)
    release = evaluate_release(
        aggregate, hard_gate_failures=("current-v4/fact-thing#1:事实漂移",)
    )
    assert release["status"] == "not_released"


def test_release_requires_help_non_inferiority_before_naturalness() -> None:
    items, mapping, choices = _synthetic_review(current_wins=False)
    aggregate = aggregate_review(items, mapping, choices)
    release = evaluate_release(aggregate)
    assert release["status"] == "not_released"
    assert any("帮助" in reason for reason in release["reasons"])
    assert not any("自然度" in reason for reason in release["reasons"])


def test_release_can_pass_with_sufficient_synthetic_evidence() -> None:
    items, mapping, choices = _synthetic_review(item_count=12, reviewers=2)
    aggregate = aggregate_review(items, mapping, choices)
    release = evaluate_release(aggregate)
    assert release["status"] == "released"
    assert release["released"] is True


def test_release_blocks_on_high_neither_rate() -> None:
    items, mapping, choices = _synthetic_review(neither_rate=0.5)
    aggregate = aggregate_review(items, mapping, choices)
    release = evaluate_release(aggregate)
    assert release["status"] == "not_released"
    assert any("拒选率" in reason for reason in release["reasons"])
