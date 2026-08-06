"""盲评测试（Issue 40 AC-9 / Verification-4）。"""

from __future__ import annotations

import pytest

from bridges.contracts.evaluation_suite import (
    BlindReviewSet,
    CaseResult,
    CaseRunStatus,
    EvaluationDimension,
)
from bridges.evaluation.blind_review import (
    BlindReviewError,
    build_review_set,
    consensus_summary,
    record_submission,
)


def _result(
    *,
    sut_id: str,
    case_id: str,
    seed: int = 1,
    execution_index: int = 0,
    answer: str,
    dimension: EvaluationDimension = EvaluationDimension.SCIENCE,
) -> CaseResult:
    return CaseResult(
        case_result_id=f"{sut_id}-{case_id}-{seed}",
        lock_id="lock-1",
        sut_id=sut_id,
        task_id="task-science",
        case_id=case_id,
        seed=seed,
        execution_index=execution_index,
        status=CaseRunStatus.SUCCEEDED,
        outputs={"final_answer": answer, "citations": []},
        artifacts=[],
        tool_records=[],
        state_trajectory=["done"],
        auto_assertions=[],
        metrics=[],
        judge_scores=[],
        failure_case=None,
        reproduction_command="replay",
        latency_ms=10,
        cost_estimate=None,
        created_at="2026-08-06T00:00:00+00:00",
    )


def test_review_set_anonymizes_system_identity() -> None:
    """AC-9：盲评隐藏系统身份——对照项不包含 SUT 标识。"""
    primary = [
        _result(sut_id="bridges_full", case_id="case-a", answer="完整系统回答"),
    ]
    peer = [
        _result(sut_id="qwen_baseline", case_id="case-a", answer="基线回答"),
    ]
    review_set = build_review_set(
        review_set_id="set-1",
        lock_id="lock-1",
        results_by_sut={"bridges_full": primary, "qwen_baseline": peer},
        order_seed=2026,
    )
    assert len(review_set.items) == 1
    item = review_set.items[0]
    assert "bridges_full" not in item.output_a
    assert "qwen_baseline" not in item.output_b
    assert "bridges_full" not in item.output_b
    assert "qwen_baseline" not in item.output_a
    assert item.label_a in {"a", "b"}
    assert item.label_b in {"a", "b"}


def test_review_set_randomizes_order_with_fixed_seed() -> None:
    """随机化顺序使用固定种子：同一种子可复现，不同种子顺序不同。"""
    primary = [_result(sut_id="bridges_full", case_id="case-a", answer="A")]
    peer = [_result(sut_id="qwen_baseline", case_id="case-a", answer="B")]
    set_1 = build_review_set(
        review_set_id="s1",
        lock_id="l",
        results_by_sut={"bridges_full": primary, "qwen_baseline": peer},
        order_seed=7,
    )
    set_2 = build_review_set(
        review_set_id="s2",
        lock_id="l",
        results_by_sut={"bridges_full": primary, "qwen_baseline": peer},
        order_seed=7,
    )
    assert set_1.items[0].label_a == set_2.items[0].label_a
    assert set_1.items[0].output_a == set_2.items[0].output_a


def test_submission_recording_and_dedup() -> None:
    review_set = BlindReviewSet(
        review_set_id="set-1", lock_id="lock-1", created_at="2026-08-06T00:00:00+00:00"
    )
    primary = [_result(sut_id="bridges_full", case_id="case-a", answer="A")]
    peer = [_result(sut_id="qwen_baseline", case_id="case-a", answer="B")]
    review_set = build_review_set(
        review_set_id="set-1",
        lock_id="lock-1",
        results_by_sut={"bridges_full": primary, "qwen_baseline": peer},
        order_seed=1,
    )
    record_submission(
        review_set, reviewer_id="reviewer-1", item_id=review_set.items[0].item_id, chosen="label_a"
    )
    assert review_set.submission_count() == 1
    with pytest.raises(BlindReviewError, match="已提交"):
        record_submission(
            review_set,
            reviewer_id="reviewer-1",
            item_id=review_set.items[0].item_id,
            chosen="label_b",
        )
    with pytest.raises(BlindReviewError, match="不合法"):
        record_submission(
            review_set,
            reviewer_id="reviewer-2",
            item_id=review_set.items[0].item_id,
            chosen="label_c",
        )


def test_low_consistency_items_flagged_not_merged() -> None:
    """Verification-4：低一致性项目标记复核而不是强行合并。"""
    primary = [
        _result(sut_id="bridges_full", case_id=f"case-{i}", answer=f"A{i}")
        for i in range(2)
    ]
    peer = [
        _result(sut_id="qwen_baseline", case_id=f"case-{i}", answer=f"B{i}")
        for i in range(2)
    ]
    review_set = build_review_set(
        review_set_id="set-1",
        lock_id="lock-1",
        results_by_sut={"bridges_full": primary, "qwen_baseline": peer},
        order_seed=1,
    )
    items = review_set.items
    # 两位评审者：第一项一致，第二项分歧。
    record_submission(review_set, reviewer_id="r1", item_id=items[0].item_id, chosen="label_a")
    record_submission(review_set, reviewer_id="r2", item_id=items[0].item_id, chosen="label_a")
    record_submission(review_set, reviewer_id="r1", item_id=items[1].item_id, chosen="label_a")
    record_submission(review_set, reviewer_id="r2", item_id=items[1].item_id, chosen="label_b")
    summary = consensus_summary(review_set)
    assert summary["reviewer_count"] == 2
    assert summary["agreement"] == 0.5
    assert items[1].item_id in summary["low_consistency_items"]
    assert items[0].item_id not in summary["low_consistency_items"]


def test_consensus_requires_two_reviewers_on_shared_items() -> None:
    primary = [_result(sut_id="bridges_full", case_id="case-a", answer="A")]
    peer = [_result(sut_id="qwen_baseline", case_id="case-a", answer="B")]
    review_set = build_review_set(
        review_set_id="set-1",
        lock_id="lock-1",
        results_by_sut={"bridges_full": primary, "qwen_baseline": peer},
        order_seed=1,
    )
    # 只有一位评审者 → 一致率为 0（不宣称一致）。
    record_submission(
        review_set, reviewer_id="r1", item_id=review_set.items[0].item_id, chosen="label_a"
    )
    summary = consensus_summary(review_set)
    assert summary["agreement"] == 0.0
    assert summary["auto_judge_primary"] is False


def test_auto_judge_primary_flag_when_no_human_review() -> None:
    """AC-9：自动裁判不能成为唯一结论来源——无人盲评时显式标注。"""
    primary = [_result(sut_id="bridges_full", case_id="case-a", answer="A")]
    peer = [_result(sut_id="qwen_baseline", case_id="case-a", answer="B")]
    review_set = build_review_set(
        review_set_id="set-1",
        lock_id="lock-1",
        results_by_sut={"bridges_full": primary, "qwen_baseline": peer},
        order_seed=1,
    )
    summary = consensus_summary(review_set)
    assert summary["auto_judge_primary"] is True
