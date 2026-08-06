"""报告统计与发布阈值测试（Issue 40 AC-10 / Verification-6）。"""

from __future__ import annotations

from bridges.contracts.evaluation_suite import (
    BlindReviewSummary,
    CaseResult,
    CaseRunStatus,
    EvaluationDimension,
    MetricValue,
)
from bridges.evaluation.gates import (
    build_default_threshold,
    evaluate_release_gate,
)
from bridges.evaluation.report import build_report, confidence_interval, welch_p_value


def _result(
    *,
    sut_id: str,
    case_id: str,
    metric_id: str,
    value: float,
    seed: int = 1,
    status: CaseRunStatus = CaseRunStatus.SUCCEEDED,
    dimension: EvaluationDimension = EvaluationDimension.SCIENCE,
) -> CaseResult:
    return CaseResult(
        case_result_id=f"{sut_id}-{case_id}-{seed}",
        lock_id="lock-1",
        sut_id=sut_id,
        task_id="task-science",
        case_id=case_id,
        seed=seed,
        execution_index=0,
        status=status,
        outputs={},
        artifacts=[],
        tool_records=[],
        state_trajectory=["done"],
        auto_assertions=[],
        metrics=[
            MetricValue(
                metric_id=metric_id,
                dimension=dimension,
                name="指标",
                value=value,
                scale_id="scale-science-5",
            )
        ],
        judge_scores=[],
        failure_case=None,
        reproduction_command="replay",
        latency_ms=10,
        cost_estimate=None,
        created_at="2026-08-06T00:00:00+00:00",
    )


def _lock() -> object:
    from bridges.contracts.evaluation_suite import SuiteRunLock, now_iso

    return SuiteRunLock(
        lock_id="lock-1",
        suite_id="science-baseline",
        suite_version="1.0.0",
        suite_digest="d",
        code_commit_or_build_digest="c",
        runtime_identifier="eval-harness",
        os_hardware_summary="test",
        database_migration_version="26",
        config_digest="cfg",
        dataset_versions={"ds": "1"},
        domain_pack_versions={},
        model_run_locks=[],
        prompt_versions={},
        schema_versions={},
        tool_adapter_versions={},
        judge_versions={},
        scoring_scale_versions={},
        random_seeds=[1],
        execution_count=1,
        network_cache_policy="frozen",
        created_at=now_iso(),
    )


def test_confidence_interval_small_sample() -> None:
    mean, low, high = confidence_interval([4.0, 5.0, 5.0, 5.0, 4.0])
    assert mean == 4.6
    assert low <= mean <= high
    assert low > 0
    # 单样本退化为点估计。
    mean2, low2, high2 = confidence_interval([5.0])
    assert mean2 == low2 == high2 == 5.0


def test_welch_p_value_deterministic() -> None:
    assert welch_p_value([5.0, 5.0, 5.0], [5.0, 5.0, 5.0]) == 1.0
    p = welch_p_value([5.0, 5.0, 5.0, 5.0, 5.0], [0.0, 0.0, 0.0, 0.0, 0.0])
    assert p < 0.05
    # 双跑确定性：同一输入两次计算相同。
    assert welch_p_value([5.0, 4.0, 4.5], [3.0, 3.5, 3.0]) == welch_p_value(
        [5.0, 4.0, 4.5], [3.0, 3.5, 3.0]
    )


def test_report_contains_required_statistics() -> None:
    """AC-10：报告展示样本量、点估计、区间、显著性、失败率与逐切片。"""
    lock = _lock()
    results = [
        _result(
            sut_id="bridges_full", case_id=f"c{i}",
            metric_id="fact_accuracy", value=5.0 if i % 2 else 4.0,
        )
        for i in range(6)
    ] + [
        _result(sut_id="qwen_baseline", case_id=f"c{i}", metric_id="fact_accuracy", value=2.0)
        for i in range(6)
    ] + [
        _result(
            sut_id="bridges_full",
            case_id="c-fail",
            metric_id="fact_accuracy",
            value=0.0,
            status=CaseRunStatus.FAILED,
        )
    ]
    report = build_report(
        lock=lock,
        results=results,
        blind_summary=BlindReviewSummary(reviewer_count=2, agreement=0.8),
    )
    assert report.lock_id == "lock-1"
    assert report.estimates
    full_estimates = [e for e in report.estimates if e.sut_id == "bridges_full"]
    assert all(e.n == 7 for e in full_estimates)
    assert all(e.ci_low <= e.mean <= e.ci_high for e in full_estimates)
    # 对比（Welch）：完整 vs 基线。
    comparisons = [c for c in report.comparisons if c.metric_id == "fact_accuracy"]
    assert comparisons
    assert all(c.method == "Welch t 检验（正态近似）" for c in comparisons)
    assert any(c.sut_a == "bridges_full" and c.sut_b == "qwen_baseline" for c in comparisons)
    # 失败率。
    assert report.cost_latency["total_runs"] == 13
    assert report.cost_latency["failed_runs"] == 1
    # 盲评摘要。
    assert report.blind_review_summary.agreement == 0.8


def test_release_gate_blocks_low_scores() -> None:
    """Verification-6：完整系统未达最低事实/安全阈值时阻止发行。"""
    lock = _lock()
    results = [
        _result(
            sut_id="bridges_full",
            case_id=f"c{i}",
            metric_id="fact_accuracy",
            value=1.0,
        )
        for i in range(5)
    ] + [
        _result(
            sut_id="bridges_full",
            case_id=f"s{i}",
            metric_id="hallucination",
            value=0.0,
        )
        for i in range(5)
    ]
    report = build_report(
        lock=lock, results=results, blind_summary=BlindReviewSummary()
    )
    verdict = evaluate_release_gate(report, build_default_threshold())
    assert not verdict.passed
    assert any("fact_accuracy" in blocker for blocker in verdict.blockers)
    assert any("hallucination" in blocker for blocker in verdict.blockers)


def test_release_gate_passes_strong_scores() -> None:
    lock = _lock()
    results = [
        _result(sut_id="bridges_full", case_id=f"c{i}", metric_id=metric_id, value=5.0)
        for metric_id in ("fact_accuracy", "hallucination", "calibration")
        for i in range(5)
    ] + [
        _result(
            sut_id="bridges_full",
            case_id=f"r{i}",
            metric_id="risk_identification",
            value=5.0,
            dimension=EvaluationDimension.SECURITY,
        )
        for i in range(5)
    ] + [
        _result(
            sut_id="bridges_full",
            case_id=f"b{i}",
            metric_id="boundary_response",
            value=5.0,
            dimension=EvaluationDimension.SECURITY,
        )
        for i in range(5)
    ] + [
        _result(
            sut_id="bridges_full",
            case_id=f"k{i}",
            metric_id="risk_boundary",
            value=5.0,
            dimension=EvaluationDimension.CAREER,
        )
        for i in range(5)
    ] + [
        _result(
            sut_id="bridges_full",
            case_id=f"l{i}",
            metric_id="learning_outcome",
            value=5.0,
            dimension=EvaluationDimension.TEACHING,
        )
        for i in range(5)
    ]
    report = build_report(lock=lock, results=results, blind_summary=BlindReviewSummary())
    verdict = evaluate_release_gate(report, build_default_threshold())
    assert verdict.passed, verdict.blockers


def test_release_gate_blocks_high_risk_failures() -> None:
    lock = _lock()
    results = [
        _result(
            sut_id="bridges_full", case_id=f"c{i}",
            metric_id="fact_accuracy", value=5.0,
        )
        for i in range(5)
    ]
    # 模拟一个高风险失败案例。
    from bridges.contracts.evaluation_suite import FailureCaseInfo

    high_risk = _result(
        sut_id="bridges_full", case_id="c-risky", metric_id="fact_accuracy", value=0.0
    )
    high_risk = high_risk.model_copy(
        update={
            "status": CaseRunStatus.FAILED,
            "failure_case": FailureCaseInfo(
                failure_id="fail-1",
                dimension=EvaluationDimension.SCIENCE,
                task_id="task-science",
                case_id="c-risky",
                sut_id="bridges_full",
                severity="high",
                description="高风险失败",
                trace_refs=[],
                reproduction_command="replay",
                created_at="2026-08-06T00:00:00+00:00",
            ),
        }
    )
    report = build_report(
        lock=lock, results=results + [high_risk], blind_summary=BlindReviewSummary()
    )
    verdict = evaluate_release_gate(report, build_default_threshold())
    assert not verdict.passed
    assert any("高风险失败" in blocker for blocker in verdict.blockers)


def test_default_threshold_has_all_hard_metrics() -> None:
    threshold = build_default_threshold()
    metric_ids = {m.metric_id for m in threshold.minimums}
    assert {
        "fact_accuracy",
        "hallucination",
        "risk_identification",
        "boundary_response",
    } <= metric_ids
    assert threshold.max_high_risk_failures == 0
