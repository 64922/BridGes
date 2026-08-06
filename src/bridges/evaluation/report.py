"""评测报告聚合与统计（Issue 40 AC-10）。

报告展示样本量、点估计、95% 置信区间、显著性/不确定性、成本、时延、
失败率、逐切片结果和代表性失败案例。统计全部使用确定性公式（t 分布
临界值表 + Welch 检验），不引入随机抽样，保证同一锁生成的报告可复现。

报告版本化：同一运行锁产生的新报告版本不覆盖旧版本（append-only）。
"""

from __future__ import annotations

import math
import statistics

from bridges.contracts.evaluation_suite import (
    BlindReviewSummary,
    CaseResult,
    Comparison,
    EvaluationDimension,
    EvaluationReport,
    FailureCaseInfo,
    FailureStats,
    PointEstimate,
    ReleaseGateVerdict,
    ReportSlice,
    SuiteRunLock,
    now_iso,
)

#: t 分布双侧 95% 临界值（自由度 1..30，之后用 1.96 近似）。
_T_CRIT: dict[int, float] = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042,
}


def _t_crit(degrees_freedom: int) -> float:
    if degrees_freedom <= 0:
        return 0.0
    return _T_CRIT.get(degrees_freedom, 1.96)


def confidence_interval(values: list[float]) -> tuple[float, float, float]:
    """返回 (均值, 下界, 上界)；样本量不足 2 时区间退化为点估计。"""
    if not values:
        return (0.0, 0.0, 0.0)
    mean = statistics.fmean(values)
    if len(values) < 2:
        return (mean, mean, mean)
    sample_std = statistics.stdev(values)
    margin = _t_crit(len(values) - 1) * (sample_std / math.sqrt(len(values)))
    return (mean, mean - margin, mean + margin)


def welch_p_value(a: list[float], b: list[float]) -> float:
    """Welch t 检验的双侧 p 值（确定性的正态近似，n≥2 且方差>0）。"""
    if len(a) < 2 or len(b) < 2:
        return 1.0
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    var_a, var_b = statistics.variance(a), statistics.variance(b)
    se = math.sqrt(var_a / len(a) + var_b / len(b))
    if se == 0:
        return 0.0 if mean_a != mean_b else 1.0
    t = (mean_a - mean_b) / se
    df = (var_a / len(a) + var_b / len(b)) ** 2 / (
        (var_a / len(a)) ** 2 / (len(a) - 1)
        + (var_b / len(b)) ** 2 / (len(b) - 1)
    )
    if df <= 0:
        return 1.0
    # 标准正态近似（双侧）：p = 2 * (1 - Φ(|t|))，Φ 用有理近似。
    z = abs(t)
    phi = 1.0 - _normal_cdf(z)
    return min(1.0, 2.0 * phi)


def _normal_cdf(z: float) -> float:
    """标准正态 CDF（Abramowitz-Stegun 有理近似，确定性）。"""
    if z < 0:
        return 1.0 - _normal_cdf(-z)
    t = 1.0 / (1.0 + 0.2316419 * z)
    poly = t * (
        0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429)))
    )
    return 1.0 - 0.3989422804014327 * math.exp(-z * z / 2.0) * poly


def build_report(
    *,
    lock: SuiteRunLock,
    results: list[CaseResult],
    blind_summary: BlindReviewSummary,
    threshold_verdict: ReleaseGateVerdict | None = None,
    report_id: str | None = None,
    report_version: str = "1",
) -> EvaluationReport:
    """从运行锁与案例结果聚合版本化报告。"""
    estimates: list[PointEstimate] = []
    comparisons: list[Comparison] = []
    failure_stats: list[FailureStats] = []
    slices: list[ReportSlice] = []
    failure_cases: list[FailureCaseInfo] = []

    results_by = _group_results(results)
    for dimension in EvaluationDimension:
        dim_results = results_by.get(dimension, {})
        for sut_id, group in sorted(dim_results.items()):
            _aggregate_sut(dimension, sut_id, group, estimates, slices, failure_stats)
        # 两两对比：bridges_full vs 其他 SUT（同指标）。
        full = dim_results.get("bridges_full", [])
        for sut_id, group in sorted(dim_results.items()):
            if sut_id == "bridges_full":
                continue
            comparisons.extend(
                _compare_suts(dimension, full, group, "bridges_full", sut_id)
            )

    for result in results:
        if result.failure_case is not None:
            failure_cases.append(result.failure_case)

    total_runs = len(results)
    # ERROR 也是执行失败：失败率与失败案例统计必须包含（避免低估发布门风险）。
    failed_runs = sum(
        1 for r in results if r.status.value in {"failed", "error"}
    )
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    cost_estimates = [r.cost_estimate for r in results if r.cost_estimate]

    return EvaluationReport(
        report_id=report_id or f"report-{lock.lock_id}",
        report_version=report_version,
        lock_id=lock.lock_id,
        suite_id=lock.suite_id,
        suite_version=lock.suite_version,
        generated_at=now_iso(),
        estimates=estimates,
        comparisons=comparisons,
        failure_stats=failure_stats,
        slices=slices,
        blind_review_summary=blind_summary,
        failure_cases=failure_cases,
        cost_latency={
            "total_runs": total_runs,
            "failed_runs": failed_runs,
            "failure_rate": failed_runs / total_runs if total_runs else 0.0,
            "median_latency_ms": statistics.median(latencies) if latencies else None,
            "mean_latency_ms": statistics.fmean(latencies) if latencies else None,
            "cost_estimate_count": len(cost_estimates),
        },
        threshold_verdict=threshold_verdict,
    )


def _group_results(
    results: list[CaseResult],
) -> dict[EvaluationDimension, dict[str, list[CaseResult]]]:
    grouped: dict[EvaluationDimension, dict[str, list[CaseResult]]] = {}
    for result in results:
        for metric in result.metrics:
            grouped.setdefault(metric.dimension, {}).setdefault(result.sut_id, []).append(result)
            break
        else:
            grouped.setdefault(EvaluationDimension.SCIENCE, {}).setdefault(
                result.sut_id, []
            ).append(result)
    return grouped


def _aggregate_sut(
    dimension: EvaluationDimension,
    sut_id: str,
    group: list[CaseResult],
    estimates: list[PointEstimate],
    slices: list[ReportSlice],
    failure_stats: list[FailureStats],
) -> None:
    metric_values: dict[str, list[float]] = {}
    slice_values: dict[tuple[str, str], list[float]] = {}
    for result in group:
        for metric in result.metrics:
            if metric.dimension != dimension:
                continue
            metric_values.setdefault(metric.metric_id, []).append(metric.value)
            slice_key = (metric.slice_tag or "normal", metric.metric_id)
            slice_values.setdefault(slice_key, []).append(metric.value)

    for metric_id, values in sorted(metric_values.items()):
        mean, low, high = confidence_interval(values)
        estimates.append(
            PointEstimate(
                dimension=dimension,
                sut_id=sut_id,
                metric_id=metric_id,
                n=len(values),
                mean=mean,
                ci_low=low,
                ci_high=high,
                unit="0-5",
            )
        )
    for (slice_tag, metric_id), values in sorted(slice_values.items()):
        slices.append(
            ReportSlice(
                slice_tag=slice_tag,
                dimension=dimension,
                sut_id=sut_id,
                metric_id=metric_id,
                n=len(values),
                mean=statistics.fmean(values),
            )
        )

    failed = sum(1 for r in group if r.status.value == "failed")
    high_risk = sum(
        1
        for r in group
        if r.failure_case is not None and r.failure_case.severity == "high"
    )
    failure_stats.append(
        FailureStats(
            dimension=dimension,
            sut_id=sut_id,
            failure_count=failed,
            failure_rate=failed / len(group) if group else 0.0,
            high_risk_count=high_risk,
        )
    )


def _compare_suts(
    dimension: EvaluationDimension,
    full: list[CaseResult],
    peer: list[CaseResult],
    sut_a: str,
    sut_b: str,
) -> list[Comparison]:
    """同案例配对比较 bridges_full 与对比系统（按 case/seed/execution 对齐）。"""
    peer_by_key = {
        (r.case_id, r.seed, r.execution_index): r for r in peer
    }
    comparisons: list[Comparison] = []
    metric_ids: list[str] = []
    for result in full:
        for metric in result.metrics:
            if metric.dimension == dimension and metric.metric_id not in metric_ids:
                metric_ids.append(metric.metric_id)
    for metric_id in metric_ids:
        a_values: list[float] = []
        b_values: list[float] = []
        for result in full:
            peer_result = peer_by_key.get((result.case_id, result.seed, result.execution_index))
            if peer_result is None:
                continue
            a_value = next(
                (m.value for m in result.metrics if m.metric_id == metric_id), None
            )
            b_value = next(
                (m.value for m in peer_result.metrics if m.metric_id == metric_id), None
            )
            if a_value is not None and b_value is not None:
                a_values.append(a_value)
                b_values.append(b_value)
        if not a_values or not b_values:
            continue
        mean_a = statistics.fmean(a_values)
        mean_b = statistics.fmean(b_values)
        p_value = welch_p_value(a_values, b_values)
        comparisons.append(
            Comparison(
                dimension=dimension,
                metric_id=metric_id,
                sut_a=sut_a,
                sut_b=sut_b,
                mean_diff=mean_a - mean_b,
                p_value=p_value,
                significant=p_value < 0.05,
                method="Welch t 检验（正态近似）",
                direction=f"{sut_a} 减 {sut_b}",
            )
        )
    return comparisons


__all__ = [
    "build_report",
    "confidence_interval",
    "welch_p_value",
    "_t_crit",
]
