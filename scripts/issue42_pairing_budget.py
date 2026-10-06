"""工单 42：配对运行的真实预算账本读取、重放与汇总（只读评测）。

预算初值与账本由生产 ``run_budget_ledger`` 冻结；本模块只读生产表并重放
并发占位，不改运行时路径。
"""

from __future__ import annotations

import math
from typing import Any


def percentile(values: list[int], fraction: float) -> int | None:
    """最近秩法分位数（与工作流性能摘要口径一致）。"""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


_LATENCY_FIELDS = (
    ("first_progress", "queue_to_first_progress_ms"),
    ("first_usable", "queue_to_first_usable_ms"),
    ("complete", "queue_to_complete_ms"),
)

_COST_COUNTERS = (
    ("model_calls_used", "model_call_limit"),
    ("transient_retries_used", "transient_retry_max"),
    ("adjustment_rounds_used", "adjustment_rounds_max"),
)

_LIMIT_FIELDS = {
    "total_budget_ms",
    "verify_deliver_reserve_ms",
    "model_call_limit",
    "external_parallel_max",
    "candidate_screen_max",
    "deep_read_max",
    "adjustment_rounds_max",
    "transient_retry_max",
}

_BUDGET_FIELDS = (
    "budget_class",
    "total_budget_ms",
    "verify_deliver_reserve_ms",
    "model_call_limit",
    "external_parallel_max",
    "candidate_screen_max",
    "deep_read_max",
    "adjustment_rounds_max",
    "transient_retry_max",
    "model_calls_used",
    "external_calls_used",
    "external_calls_active",
    "transient_retries_used",
    "adjustment_rounds_used",
    "input_tokens_used",
    "output_tokens_used",
    "status",
    "exhausted_reason",
)


def _budget_ledger(app: Any, run_id: str) -> dict[str, Any] | None:
    """只读正式预算账本行；旧 V2 树无此表时返回 None。"""
    try:
        row = app.state.bridges_database.connection.execute(
            f"SELECT {', '.join(_BUDGET_FIELDS)} FROM run_budget_ledger WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    except Exception:  # noqa: BLE001 - 旧树没有账本表，按无账本记录
        return None
    if row is None:
        return None
    return dict(zip(_BUDGET_FIELDS, row, strict=True))


def _budget_entry_counts(app: Any, run_id: str) -> dict[str, int]:
    try:
        rows = app.state.bridges_database.connection.execute(
            "SELECT kind, COUNT(*) FROM run_budget_entries WHERE run_id = ? GROUP BY kind",
            (run_id,),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {str(kind): int(count) for kind, count in rows}


def _external_peak(app: Any, run_id: str) -> int | None:
    """按流水重放真实外部调用并发峰值；旧树无账本表返回 None。"""
    try:
        rows = app.state.bridges_database.connection.execute(
            "SELECT kind FROM run_budget_entries WHERE run_id = ?"
            " AND kind IN ('external_call', 'external_call_result') ORDER BY seq",
            (run_id,),
        ).fetchall()
    except Exception:  # noqa: BLE001 - 旧树没有账本表
        return None
    active = 0
    peak = 0
    for (kind,) in rows:
        if kind == "external_call":
            active += 1
            peak = max(peak, active)
        else:
            active = max(0, active - 1)
    return peak


def _paper_observation(projected: dict[str, Any]) -> dict[str, int]:
    """论文投影里的真实候选/深读观测；非论文任务全为 0。"""
    paper = projected.get("paper_search")
    if not isinstance(paper, dict):
        return {"candidate_screens": 0, "queries": 0, "papers": 0, "deep_reads": 0}
    queries = [item for item in paper.get("queries", []) if isinstance(item, dict)]
    papers = [item for item in paper.get("papers", []) if isinstance(item, dict)]
    return {
        "candidate_screens": sum(int(item.get("evidence_count") or 0) for item in queries),
        "queries": len(queries),
        "papers": len(papers),
        "deep_reads": sum(
            1 for item in papers if item.get("read_scope") in {"partial", "full_text"}
        ),
    }


def _budget_summary(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按运行实际预算类别汇总真实消耗与延迟；无账本样本的树返回空。"""
    groups: dict[str, dict[str, Any]] = {}
    for case in cases:
        for turn in case.get("turns", []):
            budget = turn.get("budget")
            if not isinstance(budget, dict):
                continue
            name = str(budget.get("budget_class") or "unknown")
            group = groups.setdefault(
                name,
                {
                    "samples": 0,
                    "limits": {},
                    "observed_max": {},
                    "latency": {key: [] for key, _ in _LATENCY_FIELDS},
                    "paper": {"candidate_screens": 0, "deep_reads": 0, "papers": 0},
                    "statuses": {},
                    "exhausted": [],
                    "entry_kinds": {},
                },
            )
            group["samples"] += 1
            for field in _LIMIT_FIELDS:
                value = budget.get(field)
                if isinstance(value, int):
                    group["limits"][field] = value
            for field in (
                "model_calls_used",
                "external_calls_used",
                "external_calls_active",
                "transient_retries_used",
                "adjustment_rounds_used",
                "input_tokens_used",
                "output_tokens_used",
            ):
                value = budget.get(field)
                if isinstance(value, int):
                    group["observed_max"][field] = max(
                        group["observed_max"].get(field, 0), value
                    )
            status = str(budget.get("status") or "")
            group["statuses"][status] = group["statuses"].get(status, 0) + 1
            if budget.get("exhausted_reason"):
                group["exhausted"].append(str(budget["exhausted_reason"]))
            external_peak = turn.get("external_peak")
            if not isinstance(external_peak, int):
                external_peak = budget.get("external_calls_active")
            if isinstance(external_peak, int):
                group["observed_max"]["external_peak"] = max(
                    group["observed_max"].get("external_peak", 0), external_peak
                )
            for key, field in _LATENCY_FIELDS:
                value = turn.get(field)
                if isinstance(value, int) and value >= 0:
                    group["latency"][key].append(value)
            observation = turn.get("paper_observation")
            if isinstance(observation, dict):
                for field in ("candidate_screens", "deep_reads", "papers"):
                    value = observation.get(field)
                    if isinstance(value, int):
                        group["paper"][field] = max(group["paper"].get(field, 0), value)
            for kind, count in (turn.get("budget_entries") or {}).items():
                group["entry_kinds"][kind] = group["entry_kinds"].get(kind, 0) + int(count)
    for group in groups.values():
        group["latency_p50"] = {
            key: percentile(values, 0.5) for key, values in group["latency"].items()
        }
        group["latency_p95"] = {
            key: percentile(values, 0.95) for key, values in group["latency"].items()
        }
    return groups


def _budget_problems(summary: dict[str, dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    for name, group in summary.items():
        observed = group["observed_max"]
        limits = group["limits"]
        for counter, limit in _COST_COUNTERS:
            used = observed.get(counter)
            allowed = limits.get(limit)
            if isinstance(used, int) and isinstance(allowed, int) and used > allowed:
                problems.append(f"{name} 预算超限：{counter}={used} > {limit}={allowed}。")
        deep_reads = group["paper"].get("deep_reads", 0)
        allowed_reads = limits.get("deep_read_max")
        if isinstance(allowed_reads, int) and deep_reads > allowed_reads:
            problems.append(f"{name} 深读超限：deep_reads={deep_reads} > {allowed_reads}。")
        screens = group["paper"].get("candidate_screens", 0)
        allowed_screens = limits.get("candidate_screen_max")
        if isinstance(allowed_screens, int) and screens > allowed_screens:
            problems.append(
                f"{name} 候选初筛超限：candidate_screens={screens} > {allowed_screens}。"
            )
        peak = observed.get("external_peak")
        parallel_max = limits.get("external_parallel_max")
        if isinstance(peak, int) and isinstance(parallel_max, int) and peak > parallel_max:
            problems.append(
                f"{name} 并发超限：external_peak={peak} > external_parallel_max={parallel_max}。"
            )
        complete = group["latency_p95"].get("complete")
        total = limits.get("total_budget_ms")
        if isinstance(complete, int) and isinstance(total, int) and complete > total:
            problems.append(f"{name} 完整结果 P95={complete} ms 超出总预算 {total} ms。")
    return problems


def _p50p95(group: dict[str, Any], key: str) -> str:
    p50 = group["latency_p50"].get(key)
    p95 = group["latency_p95"].get(key)
    if p50 is None and p95 is None:
        return "无样本"
    return f"{p50}/{p95}"


def _status_text(group: dict[str, Any]) -> str:
    text = "、".join(
        f"{status or 'unknown'}×{count}" for status, count in sorted(group["statuses"].items())
    )
    if group["exhausted"]:
        text += "（耗尽：" + "；".join(sorted(group["exhausted"])) + "）"
    return text or "—"
