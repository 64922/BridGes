"""工单 39：验收证据补齐模块的确定性回归（无模型调用）。"""

from __future__ import annotations

import json
import runpy
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from bridges.evaluation.expression_ablation_run import AblationId, SignalCondition
from bridges.evaluation.expression_deployment_thresholds import (
    evaluate_deployment_thresholds,
)
from bridges.evaluation.expression_model_path_receipts import run_composite_receipt
from bridges.evaluation.expression_path_receipts import (
    build_receipt,
    run_deterministic_path_receipts,
    run_fixed_copy_receipts,
    verify_path_receipts,
)
from bridges.evaluation.expression_spec import FORMAL_PATHS

ROOT = Path(__file__).resolve().parents[2]


def _complete_paths_payload() -> dict[str, Any]:
    receipts = []
    for path in FORMAL_PATHS:
        locks = (
            [{"lock_id": "l", "actual_model_id": "m"}]
            if path.render_kind == "model_with_policy" and path.path_id != "composite"
            else []
        )
        receipts.append(
            build_receipt(
                path.path_id, execution_kind="test", output="out", model_locks=locks
            )
        )
    long_runs = [
        {
            "scenario_id": scenario_id,
            "status": "executed",
            "transcript": [{"user": "u", "assistant": "a"}],
            "model_locks": [{"lock_id": "l"}],
        }
        for scenario_id in ("long-derivation", "long-comparison")
    ]
    return {"paths": receipts, "long_task_runs": long_runs}


def _ablation_payload() -> dict[str, Any]:
    entries = []
    for ablation in AblationId:
        entries.append(
            {
                "ablation_id": ablation.value,
                "signal_effective": True,
                "conditions": [
                    {
                        "condition": condition.value,
                        "transcript": [
                            {
                                "user": "u",
                                "assistant": "a",
                                "status": "done",
                                "error_code": None,
                            }
                        ],
                        "model_locks": [{"lock_id": "l"}],
                    }
                    for condition in (SignalCondition.PRESENT, SignalCondition.ABSENT)
                ],
            }
        )
    return {"ablations": entries}


def _minimal_report() -> dict[str, Any]:
    return {
        "environment": {"code_commit": "test-commit"},
        "run_lock_digest": "test-digest",
        "generated_at": "2026-10-06T00:00:00+00:00",
        "blind_review": {
            "item_count": 0,
            "items": [],
            "mapping": {},
            "reviewer_count": 0,
            "submission_count": 0,
            "dimensions": [],
        },
        "hard_gates": {"failures": [], "totals": {}},
        "cost": {
            "arms": {
                "concise-baseline": {
                    "turns": 10,
                    "calls": 10,
                    "output_tokens": 4000,
                    "latency_ms_mean": 8000,
                    "first_token_ms_mean": 7000,
                    "retries": 0,
                    "errors": 0,
                },
                "current-v4": {
                    "turns": 10,
                    "calls": 10,
                    "output_tokens": 4400,
                    "latency_ms_mean": 9000,
                    "first_token_ms_mean": 8400,
                    "retries": 0,
                    "errors": 0,
                },
            },
            "humanization_specific_calls_zero": True,
        },
        "deployment_reference": {"baseline_arm": "concise-baseline"},
        "release": {"candidate_arm": "current-v4", "status": "inconclusive"},
        "validation_scope": {
            "real_ablations_executed": False,
            "formal_paths_executed": False,
            "deployment_thresholds_defined": False,
            "machine_hard_gates": "关键词诊断；须逐项人工核查。",
        },
    }


def test_deterministic_receipts_execute_real_renderers() -> None:
    receipts = run_deterministic_path_receipts()
    assert {entry["path_id"] for entry in receipts} == {
        "commute.result",
        "resources.result",
        "tieba.research",
        "career_plan.result",
    }
    assert all(entry["status"] == "executed" for entry in receipts)
    assert all(entry["output_chars"] > 0 for entry in receipts)


def test_fixed_copy_receipt_renders_all_templates() -> None:
    receipt = run_fixed_copy_receipts()
    assert receipt["status"] == "executed"
    assert receipt["expected_count"] > 0
    assert receipt["rendered_count"] == receipt["expected_count"]
    assert receipt["failures"] == []


def test_composite_receipt_is_executed_deterministic_render() -> None:
    receipt = run_composite_receipt()
    assert receipt["status"] == "executed"
    assert receipt["execution_kind"] == "composite_synthesis_render"
    assert receipt["output_chars"] > 0


def test_verify_path_receipts_requires_model_locks_and_long_runs() -> None:
    assert verify_path_receipts(_complete_paths_payload()) == []

    missing_lock = _complete_paths_payload()
    for entry in missing_lock["paths"]:
        if entry["path_id"] == "study.tutoring":
            entry["model_locks"] = []
    problems = verify_path_receipts(missing_lock)
    assert any("study.tutoring" in problem for problem in problems)

    missing_runs = _complete_paths_payload()
    missing_runs["long_task_runs"] = missing_runs["long_task_runs"][:1]
    problems = verify_path_receipts(missing_runs)
    assert any("长任务真实执行材料不足" in problem for problem in problems)


def test_deployment_thresholds_measure_candidate_against_baseline() -> None:
    passed = evaluate_deployment_thresholds(_minimal_report())
    assert passed["passed"], passed["problems"]
    assert passed["measured"]["candidate"]["calls_per_turn"] == 1.0

    slow = _minimal_report()
    slow["cost"]["arms"]["current-v4"]["latency_ms_mean"] = 20000
    failed = evaluate_deployment_thresholds(slow)
    assert not failed["passed"]
    assert any(
        check["check_id"] == "mean_latency_ratio" and not check["passed"]
        for check in failed["checks"]
    )
    assert any("成本门" in problem for problem in failed["problems"])


def test_derive_report_only_updates_validation_scope(tmp_path: Path) -> None:
    script = runpy.run_path(
        str(ROOT / "scripts" / "run_issue39_evidence_completion.py")
    )
    output = tmp_path / "out"
    output.mkdir()
    (output / "ablation-report.json").write_text(
        json.dumps(_ablation_payload()), encoding="utf-8"
    )
    (output / "formal-path-receipts.json").write_text(
        json.dumps(_complete_paths_payload()), encoding="utf-8"
    )
    report = _minimal_report()
    report_path = tmp_path / "real-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    assert script["derive_report"](output, report_path) == 0
    derived = json.loads(
        (output / "derived-report" / "report-v2.json").read_text(encoding="utf-8")
    )
    assert derived["validation_scope"] == {
        **report["validation_scope"],
        "real_ablations_executed": True,
        "formal_paths_executed": True,
        "deployment_thresholds_defined": True,
    }
    for key in ("blind_review", "cost", "release", "run_lock_digest", "hard_gates"):
        assert derived[key] == report[key]
    assert derived["evidence_completion"]["deployment_thresholds"]["passed"] is True


def test_derive_report_keeps_flags_false_when_evidence_incomplete(
    tmp_path: Path,
) -> None:
    script = runpy.run_path(
        str(ROOT / "scripts" / "run_issue39_evidence_completion.py")
    )
    output = tmp_path / "out"
    output.mkdir()
    (output / "ablation-report.json").write_text(
        json.dumps({"ablations": []}), encoding="utf-8"
    )
    receipts = deepcopy(_complete_paths_payload())
    for entry in receipts["paths"]:
        if entry["path_id"] == "paper.summary":
            entry["model_locks"] = []
    (output / "formal-path-receipts.json").write_text(
        json.dumps(receipts), encoding="utf-8"
    )
    report_path = tmp_path / "real-report.json"
    report_path.write_text(json.dumps(_minimal_report()), encoding="utf-8")

    assert script["derive_report"](output, report_path) == 1
    derived = json.loads(
        (output / "derived-report" / "report-v2.json").read_text(encoding="utf-8")
    )
    assert derived["validation_scope"]["real_ablations_executed"] is False
    assert derived["validation_scope"]["formal_paths_executed"] is False
    assert derived["validation_scope"]["deployment_thresholds_defined"] is True


def test_derive_report_refuses_missing_evidence_without_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = runpy.run_path(
        str(ROOT / "scripts" / "run_issue39_evidence_completion.py")
    )

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("派生报告不得构造真实网关。")

    monkeypatch.setitem(script, "_composition", forbidden)
    empty = tmp_path / "empty"
    empty.mkdir()
    report_path = tmp_path / "real-report.json"
    report_path.write_text(json.dumps(_minimal_report()), encoding="utf-8")
    argv = ["--derive-report", "--output-dir", str(empty), "--report", str(report_path)]
    assert script["main"](argv) == 2


def test_retrying_gateway_raises_after_exhausted_attempts() -> None:
    from types import SimpleNamespace

    from bridges.evaluation.expression_real_gateway import RetryingStructuredGateway

    calls: list[tuple[Any, ...]] = []

    class _Gateway:
        def invoke(self, *args: Any, **kwargs: Any) -> Any:
            calls.append(args)
            return SimpleNamespace(
                error_code="structured_output_parse_failed", output=None
            )

    gateway = RetryingStructuredGateway(_Gateway(), max_attempts=3)
    with pytest.raises(RuntimeError):
        gateway.invoke(
            "qwen_structured_output", "1", payload={"task": "study.tutor"}
        )
    assert len(calls) == 3
    assert [entry["succeeded"] for entry in gateway.retry_log] == [False, False, False]
