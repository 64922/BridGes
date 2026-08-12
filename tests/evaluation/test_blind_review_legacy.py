"""旧盲评 legacy 兼容（Issue 10 Test plan 8）。

旧人工评审集只读标记为 legacy：不进入新的隔离多模型自动统计，也不阻塞
全自动流程；旧 blind review CLI/API 合同保持可用（test_blind_review.py
继续回归）。
"""

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


def _result(*, sut_id: str, case_id: str, answer: str) -> CaseResult:
    return CaseResult(
        case_result_id=f"{sut_id}-{case_id}",
        lock_id="lock-1",
        sut_id=sut_id,
        task_id="task-science",
        case_id=case_id,
        seed=1,
        execution_index=0,
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


def _review_set() -> BlindReviewSet:
    primary = [_result(sut_id="bridges_full", case_id="case-a", answer="A")]
    peer = [_result(sut_id="qwen_baseline", case_id="case-a", answer="B")]
    return build_review_set(
        review_set_id="set-legacy",
        lock_id="lock-1",
        results_by_sut={"bridges_full": primary, "qwen_baseline": peer},
        order_seed=2026,
    )


def test_legacy_flag_defaults_false():
    """新建盲评集默认非 legacy（新流程不标记）。"""
    review_set = _review_set()
    assert review_set.legacy is False


def test_legacy_set_rejects_new_submissions():
    """legacy 集只读：拒绝新提交（旧人工评审不再采集）。"""
    review_set = _review_set().model_copy(update={"legacy": True})
    with pytest.raises(BlindReviewError, match="legacy"):
        record_submission(
            review_set,
            reviewer_id="reviewer-1",
            item_id=review_set.items[0].item_id,
            chosen="label_a",
        )
    assert review_set.submission_count() == 0


def test_consensus_summary_marks_legacy():
    """摘要标注 legacy（只读展示，不进入新统计）。"""
    summary = consensus_summary(_review_set())
    assert summary["legacy"] is False
    legacy = _review_set().model_copy(update={"legacy": True})
    assert consensus_summary(legacy)["legacy"] is True


def test_legacy_serialization_round_trip():
    """legacy 标记持久化往返（repository 存读不丢失）。"""
    review_set = _review_set().model_copy(update={"legacy": True})
    restored = BlindReviewSet.model_validate_json(review_set.model_dump_json())
    assert restored.legacy is True
    assert restored.review_set_id == "set-legacy"


def test_old_api_compatible():
    """旧 blind review API 合同保持可用（build/record/consensus）。"""
    review_set = _review_set()
    record_submission(
        review_set,
        reviewer_id="reviewer-1",
        item_id=review_set.items[0].item_id,
        chosen="label_b",
    )
    summary = consensus_summary(review_set)
    assert summary["reviewer_count"] == 1
    assert summary["submission_count"] == 1
    assert summary["auto_judge_primary"] is False


def test_repository_reads_human_review_sets_as_legacy(tmp_path):
    """repository 读取视图：含人工提交的旧盲评集自动标 legacy（只读）。"""
    from bridges.evaluation.repository import EvaluationRepository
    from bridges.storage.database import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = EvaluationRepository(database)
    review_set = _review_set()
    record_submission(
        review_set,
        reviewer_id="reviewer-1",
        item_id=review_set.items[0].item_id,
        chosen="label_a",
    )
    repository.save_review_set(review_set)
    restored = repository.get_review_set("set-legacy")
    assert restored is not None
    # 人工提交集读取时标记 legacy：不进入新的自动统计。
    assert restored.legacy is True
    listed = repository.list_review_sets("lock-1")
    assert listed and listed[0].legacy is True
    # 存储内容不被修改（保存的是 legacy=False 的原始集）。
    row = database.connection.execute(
        "SELECT set_json FROM eval_blind_reviews WHERE review_set_id = ?",
        ("set-legacy",),
    ).fetchone()
    stored = BlindReviewSet.model_validate_json(str(row["set_json"]))
    assert stored.legacy is False
