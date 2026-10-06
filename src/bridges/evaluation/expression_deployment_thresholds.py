"""工单 39：部署限额定义与候选成本门核查（以冻结主报告实测为参照）。

限额依据简洁基线的独立测量加固定余量：调用数不增加、延迟／首字／输出
token 与重试不显著恶化、人味专属新增调用为零。放行策略的帮助/分寸非劣
与自然度优势仍由预注册放行策略判定，这里只负责成本与调用门。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

THRESHOLD_VERSION = "human-expression-deployment-thresholds-v1"

#: 以简洁基线实测为参照的固定余量；任何调整必须升版本并记录理由。
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "threshold_version": THRESHOLD_VERSION,
    "basis": (
        "简洁基线臂在冻结主报告中的独立测量；延迟余量 25%、首字余量 50%、"
        "输出 token 余量 25%、每回合重试余量 0.2、错误率上限 0.1。"
    ),
    "max_calls_per_turn_increase": 0.0,
    "max_mean_latency_ratio": 1.25,
    "max_mean_first_token_ratio": 1.5,
    "max_mean_output_tokens_per_call_ratio": 1.25,
    "max_retries_per_turn_increase": 0.2,
    "max_errors_per_turn": 0.1,
    "require_zero_humanization_specific_calls": True,
}


def _per_turn(arm: dict[str, Any], field: str) -> float:
    turns = int(arm.get("turns") or 0)
    if turns <= 0:
        return 0.0
    return float(arm.get(field) or 0) / turns


def _output_tokens_per_call(arm: dict[str, Any]) -> float:
    calls = int(arm.get("calls") or 0)
    if calls <= 0:
        return 0.0
    return float(arm.get("output_tokens") or 0) / calls


def evaluate_deployment_thresholds(report: dict[str, Any]) -> dict[str, Any]:
    """按本模块固定限额核查候选臂；返回可写入证据文件的完整产物。"""

    limits = dict(DEFAULT_THRESHOLDS)
    arms = (report.get("cost") or {}).get("arms") or {}
    baseline_id = str(
        (report.get("deployment_reference") or {}).get("baseline_arm")
        or "concise-baseline"
    )
    candidate_id = str(
        (report.get("release") or {}).get("candidate_arm") or "current-v4"
    )
    baseline = arms.get(baseline_id)
    candidate = arms.get(candidate_id)
    problems: list[str] = []
    checks: list[dict[str, Any]] = []
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        problems.append("冻结报告缺少基线臂或候选臂的成本测量。")
        return {
            "threshold_version": limits["threshold_version"],
            "generated_at": datetime.now(UTC).isoformat(),
            "passed": False,
            "problems": problems,
            "thresholds": limits,
            "measured": {},
            "checks": [],
        }

    measured = {
        "baseline_arm": baseline_id,
        "candidate_arm": candidate_id,
        "baseline": {
            "calls_per_turn": _per_turn(baseline, "calls"),
            "mean_latency_ms": baseline.get("latency_ms_mean"),
            "mean_first_token_ms": baseline.get("first_token_ms_mean"),
            "output_tokens_per_call": _output_tokens_per_call(baseline),
            "retries_per_turn": _per_turn(baseline, "retries"),
            "errors_per_turn": _per_turn(baseline, "errors"),
        },
        "candidate": {
            "calls_per_turn": _per_turn(candidate, "calls"),
            "mean_latency_ms": candidate.get("latency_ms_mean"),
            "mean_first_token_ms": candidate.get("first_token_ms_mean"),
            "output_tokens_per_call": _output_tokens_per_call(candidate),
            "retries_per_turn": _per_turn(candidate, "retries"),
            "errors_per_turn": _per_turn(candidate, "errors"),
        },
        "humanization_specific_calls_zero": bool(
            (report.get("cost") or {}).get("humanization_specific_calls_zero")
        ),
        "release_status": (report.get("release") or {}).get("status"),
    }

    def ratio(field: str) -> float | None:
        base = measured["baseline"].get(field)
        cand = measured["candidate"].get(field)
        if not base or base <= 0 or cand is None:
            return None
        return float(cand) / float(base)

    def check(check_id: str, limit: Any, actual: Any, passed: bool) -> None:
        checks.append(
            {
                "check_id": check_id,
                "limit": limit,
                "actual": actual,
                "passed": bool(passed),
            }
        )

    base_calls = measured["baseline"]["calls_per_turn"]
    cand_calls = measured["candidate"]["calls_per_turn"]
    check(
        "calls_per_turn_increase",
        limits["max_calls_per_turn_increase"],
        round(cand_calls - base_calls, 4),
        cand_calls - base_calls <= limits["max_calls_per_turn_increase"],
    )
    latency_ratio = ratio("mean_latency_ms")
    check(
        "mean_latency_ratio",
        limits["max_mean_latency_ratio"],
        None if latency_ratio is None else round(latency_ratio, 4),
        latency_ratio is not None and latency_ratio <= limits["max_mean_latency_ratio"],
    )
    first_token_ratio = ratio("mean_first_token_ms")
    check(
        "mean_first_token_ratio",
        limits["max_mean_first_token_ratio"],
        None if first_token_ratio is None else round(first_token_ratio, 4),
        first_token_ratio is not None
        and first_token_ratio <= limits["max_mean_first_token_ratio"],
    )
    output_ratio = ratio("output_tokens_per_call")
    check(
        "mean_output_tokens_per_call_ratio",
        limits["max_mean_output_tokens_per_call_ratio"],
        None if output_ratio is None else round(output_ratio, 4),
        output_ratio is not None
        and output_ratio <= limits["max_mean_output_tokens_per_call_ratio"],
    )
    retries_increase = (
        measured["candidate"]["retries_per_turn"]
        - measured["baseline"]["retries_per_turn"]
    )
    check(
        "retries_per_turn_increase",
        limits["max_retries_per_turn_increase"],
        round(retries_increase, 4),
        retries_increase <= limits["max_retries_per_turn_increase"],
    )
    check(
        "errors_per_turn",
        limits["max_errors_per_turn"],
        round(measured["candidate"]["errors_per_turn"], 4),
        measured["candidate"]["errors_per_turn"] <= limits["max_errors_per_turn"],
    )
    if limits.get("require_zero_humanization_specific_calls"):
        check(
            "humanization_specific_calls_zero",
            True,
            measured["humanization_specific_calls_zero"],
            measured["humanization_specific_calls_zero"],
        )
    failed = [entry["check_id"] for entry in checks if not entry["passed"]]
    if failed:
        problems.append(f"候选臂未通过成本门：{', '.join(failed)}。")
    return {
        "threshold_version": limits["threshold_version"],
        "generated_at": datetime.now(UTC).isoformat(),
        "source_report": {
            "code_commit": (report.get("environment") or {}).get("code_commit"),
            "run_lock_digest": report.get("run_lock_digest"),
            "report_digest": hashlib.sha256(
                json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        },
        "thresholds": limits,
        "measured": measured,
        "checks": checks,
        "passed": not problems,
        "problems": problems,
    }


__all__ = [
    "DEFAULT_THRESHOLDS",
    "THRESHOLD_VERSION",
    "evaluate_deployment_thresholds",
]
