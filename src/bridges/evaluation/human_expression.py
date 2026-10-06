"""工单 39：真实配对的成本汇总与报告（复用既有评测底座）。

报告把确定性机制、真实测量、硬门、盲评与放行结论分开陈述；不做
收益宣称，不放行任何策略——放行由预注册策略与人工盲评共同决定。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.evaluation.expression_corpus import scenario_distribution
from bridges.evaluation.expression_deterministic import DeterministicReport
from bridges.evaluation.expression_policy_arms import ARM_TITLES, StrategyArm
from bridges.evaluation.expression_provenance import SUITE_ID, SUITE_VERSION
from bridges.evaluation.expression_real_run import ArmRunResult, TurnMeasurement


def summarize_costs(results: list[ArmRunResult]) -> dict[str, Any]:
    """按臂汇总真实测量；人味专属新增调用必须为零。"""

    by_arm: dict[str, dict[str, Any]] = {}
    measurements_by_key: dict[tuple[str, str, int], TurnMeasurement] = {}
    for result in results:
        arm = result.arm.value
        summary = by_arm.setdefault(
            arm,
            {
                "turns": 0,
                "calls": 0,
                "chat_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "latency_ms": [],
                "first_token_ms": [],
                "answer_chars": 0,
                "retries": 0,
                "errors": 0,
            },
        )
        for measurement in result.measurements:
            summary["turns"] += 1
            summary["calls"] += measurement.total_calls
            summary["chat_calls"] += measurement.chat_calls
            summary["input_tokens"] += measurement.input_tokens or 0
            summary["output_tokens"] += measurement.output_tokens or 0
            summary["latency_ms"].append(measurement.latency_ms)
            if measurement.first_token_ms is not None:
                summary["first_token_ms"].append(measurement.first_token_ms)
            summary["answer_chars"] += measurement.answer_chars
            summary["retries"] += measurement.retry_count
            if measurement.status != "done":
                summary["errors"] += 1
            measurements_by_key[
                (measurement.scenario_id, arm, measurement.turn_index)
            ] = measurement
    for summary in by_arm.values():
        for key in ("latency_ms", "first_token_ms"):
            values = sorted(summary[key])
            summary[f"{key}_mean"] = round(sum(values) / len(values)) if values else None
            summary[f"{key}_p50"] = values[len(values) // 2] if values else None
            summary[key] = len(values)
    extra_calls: list[dict[str, Any]] = []
    baseline_keys = {
        key for key in measurements_by_key if key[1] == StrategyArm.BASELINE.value
    }
    for scenario_id, arm, turn_index in sorted(baseline_keys):
        baseline = measurements_by_key[(scenario_id, arm, turn_index)]
        for other_arm in (StrategyArm.CURRENT.value, StrategyArm.LEGACY.value):
            other = measurements_by_key.get((scenario_id, other_arm, turn_index))
            if other is None:
                continue
            delta = other.total_calls - baseline.total_calls
            if delta > 0:
                extra_calls.append(
                    {
                        "scenario_id": scenario_id,
                        "arm": other_arm,
                        "turn_index": turn_index,
                        "extra_calls": delta,
                    }
                )
    return {
        "arms": by_arm,
        "humanization_specific_calls_zero": not extra_calls,
        "extra_calls": extra_calls,
    }


def build_real_report(
    *,
    deterministic: DeterministicReport,
    results: list[ArmRunResult],
    environment: dict[str, Any],
    review_items: list[Any],
    review_mapping: dict[str, dict[str, str]],
    aggregate: dict[str, Any],
    release: dict[str, Any],
) -> dict[str, Any]:
    """汇总真实配对、硬门、盲评与放行结论（脱敏，不含密钥）。"""

    hard_gate_failures: list[dict[str, Any]] = []
    hard_gate_totals: dict[str, dict[str, int]] = {}
    for result in results:
        for gate in result.gates:
            key = gate.gate.value
            totals = hard_gate_totals.setdefault(
                key, {"passed": 0, "failed": 0}
            )
            if gate.passed:
                totals["passed"] += 1
            else:
                totals["failed"] += 1
                hard_gate_failures.append(
                    {
                        "arm": result.arm.value,
                        "scenario_id": result.scenario.scenario_id,
                        "turn_index": gate.turn_index,
                        "gate": key,
                        "detail": gate.detail,
                    }
                )
    cost = summarize_costs(results)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": environment,
        "suite": {
            "suite_id": SUITE_ID,
            "suite_version": SUITE_VERSION,
            "corpus_digest": deterministic.corpus_digest,
        },
        "deterministic": deterministic.to_dict(),
        "arm_truths": {
            arm.value: ARM_TITLES[arm] for arm in StrategyArm
        },
        "runs": [result.to_dict() for result in results],
        "cost": cost,
        "deployment_reference": {
            "baseline_arm": StrategyArm.BASELINE.value,
            "measured": cost["arms"].get(StrategyArm.BASELINE.value),
            "note": (
                "部署门槛由消费本报告的发布票按本测量设定：人味专属新增调用"
                "必须为零；帮助/分寸非劣与自然度优势按预注册放行策略；"
                "延迟与 token 限额以简洁基线实测为参照。"
            ),
        },
        "hard_gates": {
            "totals": hard_gate_totals,
            "failures": hard_gate_failures,
            "separate_from_warmth": True,
        },
        "blind_review": {
            "item_count": len(review_items),
            "items": [item.to_dict() for item in review_items],
            "mapping": dict(review_mapping),
            "reviewer_count": aggregate.get("reviewer_count", 0),
            "submission_count": aggregate.get("submission_count", 0),
            "dimensions": aggregate.get("dimensions", []),
        },
        "scenario_distribution": [
            {
                **entry,
                "executed": len({
                    result.scenario.scenario_id for result in results
                    if result.scenario.category.value == entry["category"]
                }),
            }
            for entry in scenario_distribution()
        ],
        "validation_scope": {
            "real_ablations_executed": False,
            "formal_paths_executed": False,
            "deployment_thresholds_defined": False,
            "machine_hard_gates": "关键词诊断；通过不证明语义正确，须逐项人工核查。",
        },
        "release": release,
        "rollback": {
            "production_prompt_change": (
                "无：本票只新增评测模块与脚本，不修改生产提示或策略快照。"
            ),
            "expression_strategy": (
                "既有表达策略按运行快照持久化（21/22），可回滚到安全基线或旧"
                "快照；评测运行使用独立内存会话，不写生产会话。"
            ),
            "deterministic_fixes_locked": (
                "工单 05/06 的事实保护与流式追加协议属于确定性缺陷修复，"
                "不在提示策略回滚范围内。"
            ),
        },
    }


__all__ = [
    "build_real_report",
    "summarize_costs",
]
