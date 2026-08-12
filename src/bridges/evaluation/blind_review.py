"""盲评（Issue 40 AC-9 / Verification-4）。

盲评隐藏系统身份并随机化顺序：输出只标注随机化的 A/B 标签，评审者
不知道哪一段来自哪个被测系统。顺序随机化使用固定种子（进入运行锁），
保证可复现。评审提交按评审者与项目记录，简单一致率与低一致性项目
（多位评审者选择不一致）被标记复核而不是强行合并。

评审者身份（reviewer_id）由调用方提供（真实姓名或代号），本模块不
做身份认证——盲评的保密性由流程保证：评审者只接触匿名化输出。
"""

from __future__ import annotations

import random
from typing import Any

from bridges.contracts.evaluation_suite import (
    BlindReviewItem,
    BlindReviewSet,
    BlindReviewSubmission,
    EvaluationDimension,
    now_iso,
)


class BlindReviewError(Exception):
    """盲评领域错误。"""


def _extract_output_text(result: Any) -> str:
    """从案例结果中提取用于盲评的最终输出文本。"""
    outputs = getattr(result, "outputs", {}) or {}
    for key in ("final_answer", "final_text", "answer"):
        text = outputs.get(key)
        if isinstance(text, str) and text.strip():
            return text
    return str(outputs)


def build_review_set(
    *,
    review_set_id: str,
    lock_id: str,
    results_by_sut: dict[str, list[Any]],
    dimension: EvaluationDimension | None = None,
    order_seed: int = 2026,
) -> BlindReviewSet:
    """按「完整系统 vs 基线/参考」配对构建匿名盲评集。

    ``results_by_sut`` 键为 SUT 标识，值是该 SUT 的案例结果列表（两个
    SUT 需按同一 (case_id, seed, execution) 顺序对齐）。配对时用固定
    种子随机化 A/B 标签与展示顺序，输出剥离一切系统标识。
    """
    if "bridges_full" not in results_by_sut:
        raise BlindReviewError("盲评配对必须包含 bridges_full 系统。")
    primary = results_by_sut["bridges_full"]
    peers = {
        key: value
        for key, value in results_by_sut.items()
        if key != "bridges_full"
    }
    if not peers:
        raise BlindReviewError("盲评配对至少需要一个对比系统。")

    items: list[BlindReviewItem] = []
    rng = random.Random(order_seed)
    for peer_id, peer_results in peers.items():
        peer_by_key = {_result_key(result): result for result in peer_results}
        for primary_result in primary:
            key = _result_key(primary_result)
            peer_result = peer_by_key.get(key)
            if peer_result is None:
                continue
            output_a = _extract_output_text(primary_result)
            output_b = _extract_output_text(peer_result)
            flip = rng.random() < 0.5
            label_a, output_a_shown = (
                ("b", output_b) if flip else ("a", output_a)
            )
            label_b, output_b_shown = (
                ("a", output_a) if flip else ("b", output_b)
            )
            item = BlindReviewItem(
                item_id=f"{key}-{peer_id}",
                review_set_id=review_set_id,
                dimension=_result_dimension(primary_result),
                task_id=str(getattr(primary_result, "task_id", "")),
                case_id=str(getattr(primary_result, "case_id", "")),
                label_a=label_a,
                label_b=label_b,
                output_a=output_a_shown,
                output_b=output_b_shown,
                order_seed=order_seed,
            )
            if dimension is None or item.dimension == dimension:
                items.append(item)
    return BlindReviewSet(
        review_set_id=review_set_id,
        lock_id=lock_id,
        items=items,
        created_at=now_iso(),
    )


def _result_key(result: Any) -> str:
    case_id = getattr(result, "case_id", "")
    seed = getattr(result, "seed", "")
    execution = getattr(result, "execution_index", "")
    return f"{case_id}@{seed}@{execution}"


def _result_dimension(result: Any) -> EvaluationDimension:
    """从结果指标取真实维度（CaseResult 无 dimension 字段）。"""
    metrics = getattr(result, "metrics", [])
    if metrics:
        dimension = getattr(metrics[0], "dimension", None)
        if isinstance(dimension, EvaluationDimension):
            return dimension
    return EvaluationDimension.SCIENCE


def record_submission(
    review_set: BlindReviewSet,
    *,
    reviewer_id: str,
    item_id: str,
    chosen: str,
    rationale: str | None = None,
    submission_id: str | None = None,
) -> BlindReviewSubmission:
    """记录一位评审者的盲评提交；选择必须是 label_a/label_b/tie。

    legacy 标记的旧人工评审集只读：拒绝新提交（不进入新的自动统计）。
    """
    if review_set.legacy:
        raise BlindReviewError(
            f"盲评集 {review_set.review_set_id} 已标记为 legacy（旧人工评审集），"
            "只读展示，不接受新提交。"
        )
    review_set.item(item_id)
    if chosen not in {"label_a", "label_b", "tie"}:
        raise BlindReviewError(
            f"评审选择不合法：{chosen}（应为 label_a/label_b/tie）。"
        )
    # 防重复提交：同评审者对同一项只能提交一次。
    for existing in review_set.submissions:
        if existing.reviewer_id == reviewer_id and existing.item_id == item_id:
            raise BlindReviewError(f"评审者 {reviewer_id} 已提交过 {item_id}。")
    submission = BlindReviewSubmission(
        submission_id=submission_id or f"sub-{len(review_set.submissions) + 1}",
        review_set_id=review_set.review_set_id,
        reviewer_id=reviewer_id,
        item_id=item_id,
        chosen=chosen,
        rationale=rationale,
        submitted_at=now_iso(),
    )
    review_set.submissions.append(submission)
    return submission


def consensus_summary(review_set: BlindReviewSet) -> dict[str, Any]:
    """盲评一致性摘要：评审数、一致率、低一致性项目与自动裁判标注。

    含 ``legacy`` 标记：旧人工评审集只读展示，不进入新的隔离多模型
    自动统计（统计入口见 bridges.humanize_eval.aggregator）。
    """
    return {
        "reviewer_count": len({s.reviewer_id for s in review_set.submissions}),
        "item_count": len(review_set.items),
        "submission_count": len(review_set.submissions),
        "agreement": review_set.pair_agreement(),
        "low_consistency_items": review_set.low_consistency_items(),
        "auto_judge_primary": review_set.submission_count() == 0,
        "legacy": review_set.legacy,
    }


__all__ = [
    "build_review_set",
    "record_submission",
    "consensus_summary",
    "BlindReviewError",
    "BlindReviewSet",
]
