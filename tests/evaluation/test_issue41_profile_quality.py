"""工单 41 画像质量纵向场景测试（确定性机制层）。"""

from __future__ import annotations

import json

from bridges.evaluation.profile_quality import (
    ACCOUNT_A,
    ACCOUNT_B,
    run_profile_quality_evaluation,
)

EXPECTED_SCENARIOS = {
    "negated_preference",
    "multi_fact_coexistence",
    "mixed_subject_quote_emotion",
    "anaphora",
    "time_anchor",
    "parallel_goals_precise_change",
    "delete_synonym_and_recovery",
    "low_confidence_mirror",
    "stop_recording_forget_and_late_task",
    "self_report_vs_answer_evidence",
    "switches_and_isolation",
    "evidence_feedback_path",
    "sqlite_async_transactions",
}


def test_all_profile_quality_scenarios_pass() -> None:
    report = run_profile_quality_evaluation()
    failures = [
        f"{scenario.scenario_id}/{checkpoint.checkpoint_id}: {checkpoint.detail}"
        for scenario in report.scenarios
        for checkpoint in scenario.checkpoints
        if not checkpoint.passed
    ]
    assert report.passed, failures
    assert report.pass_rate == 1.0
    assert {scenario.scenario_id for scenario in report.scenarios} == EXPECTED_SCENARIOS


def test_hard_gates_cover_safety_and_isolation() -> None:
    report = run_profile_quality_evaluation()
    gates = {checkpoint.checkpoint_id for checkpoint in report.hard_gates}
    assert {
        "no_dangling_reference",
        "account_isolated",
        "late_task_no_revive",
        "usage_off_keeps_data",
        "feedback_does_not_delete",
    } <= gates
    assert all(checkpoint.passed for checkpoint in report.hard_gates)


def test_report_is_json_serializable_and_renders() -> None:
    report = run_profile_quality_evaluation()
    payload = report.to_dict()
    json.dumps(payload, ensure_ascii=False)
    assert payload["scenario_count"] == len(EXPECTED_SCENARIOS)
    assert payload["hard_gate_failures"] == []
    markdown = report.render_markdown()
    for scenario_id in EXPECTED_SCENARIOS:
        assert scenario_id in markdown


def test_two_synthetic_accounts_are_isolated() -> None:
    report = run_profile_quality_evaluation()
    assert ACCOUNT_A != ACCOUNT_B
    assert report.environment["account_model"] == "two synthetic accounts"
