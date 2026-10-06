"""工单 42：配对评测纯函数离线验证（不跑真实模型）。"""

from __future__ import annotations

from typing import Any

from bridges.evaluation.workflow_scenarios import REAL_MODEL_PAIRING_COVERAGE
from scripts import issue42_pairing_cases as cases
from scripts import issue42_pairing_report as pairing


def _fake_case(case_id: str, scenario_id: str, *, checks: bool = True) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "scenario_id": scenario_id,
        "repeat": 0,
        "error": None,
        "turns": [
            {
                "run_id": "run-1",
                "status": "done",
                "answer": "示例回答",
                "elapsed_s": 1.0,
                "answer_elapsed_ms": 1000,
                "queue_to_first_token_ms": 200,
                "capability_calls": {"qwen_text_chat": 1},
                "paper_search_empty": True,
            }
        ],
        "tasks": [],
        "cost": {"calls": 1, "prompt_tokens": 100, "completion_tokens": 50},
        "checks": {
            check: checks
            for spec in cases.SCENARIOS
            if spec.case_id == case_id
            for check in spec.checks
        },
    }


def _fake_tree_result(*, model_ids: list[str]) -> dict[str, Any]:
    samples = [_fake_case(spec.case_id, spec.scenario_id) for spec in cases.SCENARIOS]
    return {
        "tree": "fake-tree",
        "commit": "abc123",
        "dirty": False,
        "corpus_sha256": cases.corpus_sha256(),
        "repeats": 1,
        "started_at": "2026-10-06T00:00:00+00:00",
        "finished_at": "2026-10-06T00:01:00+00:00",
        "cases": samples,
        "model_ids": model_ids,
    }


def test_corpus_sha256_is_stable() -> None:
    assert cases.corpus_sha256() == cases.corpus_sha256()
    assert len(cases.corpus_sha256()) == 64
    assert [spec.scenario_id for spec in cases.SCENARIOS] == [
        "A01",
        "A02",
        "A03",
        "A11",
        "R07",
        "C01",
        "C02",
    ]


def test_pairing_corpus_covers_real_model_manifest() -> None:
    pairing_ids = [spec.scenario_id for spec in cases.SCENARIOS]
    assert len(pairing_ids) == len(set(pairing_ids))
    assert set(REAL_MODEL_PAIRING_COVERAGE) <= set(pairing_ids)


def test_percentile_uses_nearest_rank() -> None:
    values = list(range(1, 101))
    assert pairing.percentile(values, 0.5) == 50
    assert pairing.percentile(values, 0.95) == 95
    assert pairing.percentile([7], 0.95) == 7
    assert pairing.percentile([], 0.5) is None


def test_projection_empty_handles_shapes() -> None:
    assert cases.projection_empty(None)
    assert cases.projection_empty([])
    assert cases.projection_empty({})
    assert not cases.projection_empty([{"id": 1}])


def test_evaluate_case_checks_a01_and_r07() -> None:
    a01 = _fake_case("A01.lightweight", "A01")
    a01_checks = cases.evaluate_case_checks(a01)
    assert a01_checks == {
        "answer_nonempty": True,
        "single_chat_call": True,
        "no_external_model_capability": True,
        "no_task_created": True,
        "no_unnecessary_search": True,
    }
    a01["turns"][0]["search_planned"] = True
    assert cases.evaluate_case_checks(a01)["no_unnecessary_search"] is False

    r07 = _fake_case("R07.stop_continue", "R07")
    r07["turns"] = [
        {
            "run_id": "r1",
            "status": "done",
            "answer": "第一次",
            "capability_calls": {},
            "paper_search_empty": True,
        },
        {
            "run_id": "r2",
            "answer": "",
            "status": "stopped",
            "capability_calls": {},
            "paper_search_empty": True,
        },
        {
            "run_id": "r3",
            "status": "done",
            "answer": "第三次",
            "capability_calls": {},
            "paper_search_empty": True,
        },
    ]
    r07.update(
        stopped_run_id="r2",
        stopped_run_status="stopped",
        idle_new_runs=0,
        continued_run_id="r3",
    )
    r07_checks = cases.evaluate_case_checks(r07)
    assert r07_checks["answer_nonempty"] is True
    assert r07_checks["stop_no_auto_continue"] is True
    assert r07_checks["continue_creates_new_run"] is True
    r07["idle_new_runs"] = 1
    assert cases.evaluate_case_checks(r07)["stop_no_auto_continue"] is False

    a11 = _fake_case("A11.hard_condition", "A11")
    a11["turns"] = [
        {
            "run_id": "r1",
            "answer": "",
            "status": "error",
            "error_code": "network_not_allowed",
            "error_message": "当前条件不允许联网搜索。",
            "capability_calls": {},
            "paper_search_empty": True,
        }
    ]
    assert cases.evaluate_case_checks(a11) == {
        "no_paper_results": True,
        "hard_condition_blocked": True,
    }
    a11["turns"][0]["error_code"] = None
    a11["turns"][0]["error_message"] = None
    assert cases.evaluate_case_checks(a11)["hard_condition_blocked"] is False


def test_summarize_pairing_flags_model_mismatch() -> None:
    matched = pairing.summarize_pairing(
        _fake_tree_result(model_ids=["qwen-a"]),
        _fake_tree_result(model_ids=["qwen-a"]),
    )
    assert matched["problems"] == []
    assert matched["new_totals"]["quality"]["pass_rate"] == 1.0
    assert matched["new_totals"]["latency"]["answer_p95_ms"] == 1000

    mismatched = pairing.summarize_pairing(
        _fake_tree_result(model_ids=["qwen-a"]),
        _fake_tree_result(model_ids=["qwen-b"]),
    )
    assert any("生效模型不一致" in problem for problem in mismatched["problems"])


def test_render_markdown_reports_budget_calibration() -> None:
    report = pairing.summarize_pairing(
        _fake_tree_result(model_ids=["qwen-a"]),
        _fake_tree_result(model_ids=["qwen-a"]),
    )
    budgets = {
        "lightweight": {
            "total_budget_ms": 120000,
            "verify_deliver_reserve_ms": 0,
            "model_call_limit": 6,
            "transient_retry_max": 1,
            "adjustment_rounds_max": 1,
            "candidate_screen_max": 20,
            "deep_read_max": 3,
        },
        "normal": {
            "total_budget_ms": 60000,
            "verify_deliver_reserve_ms": 15000,
            "model_call_limit": 6,
            "transient_retry_max": 1,
            "adjustment_rounds_max": 1,
            "candidate_screen_max": 20,
            "deep_read_max": 3,
        },
    }
    markdown = pairing.render_markdown(report, budgets)
    assert "预算初值与实测消耗对照" in markdown
    assert "语义阈值由生产量表" in markdown
    assert "无预算账本样本" in markdown
    assert "A01" in markdown and "R07" in markdown


def test_budget_summary_flags_ledger_overrun_and_shortfall() -> None:
    sample = _fake_case("C01.deep_paper", "C01")
    sample["turns"][0].update(
        budget={
            "budget_class": "deep",
            "total_budget_ms": 120000,
            "external_parallel_max": 2,
            "model_call_limit": 8,
            "transient_retry_max": 1,
            "adjustment_rounds_max": 1,
            "deep_read_max": 5,
            "model_calls_used": 9,
            "transient_retries_used": 0,
            "adjustment_rounds_used": 0,
            "status": "closed",
        },
        queue_to_complete_ms=130000,
        paper_observation={"candidate_screens": 24, "deep_reads": 6, "papers": 5},
        external_peak=3,
    )
    summary = pairing._budget_summary([sample])
    problems = pairing._budget_problems(summary)
    assert any("model_calls_used=9" in item for item in problems)
    assert any("深读超限" in item for item in problems)
    assert any("并发超限" in item for item in problems)
    assert any("完整结果 P95" in item for item in problems)
    assert summary["deep"]["latency_p95"]["complete"] == 130000
    assert summary["deep"]["observed_max"]["external_peak"] == 3


def test_error_with_partial_text_never_counts_as_answer_success() -> None:
    sample = _fake_case("A01.lightweight", "A01")
    sample["turns"][0].update(status="error", error_code="web_search_citation_invalid")
    assert cases.evaluate_case_checks(sample)["answer_nonempty"] is False
    assert pairing._latency_summary([sample])["answer_samples"] == 0


def test_question_mark_and_network_word_are_not_behavior_evidence() -> None:
    sample = _fake_case("A03.ambiguous", "A03")
    sample["turns"][0]["answer"] = "我找到了三份资料，你觉得如何？"
    assert cases.evaluate_case_checks(sample)["asks_one_clarification"] is False
    sample = _fake_case("A11.hard_condition", "A11")
    sample["turns"][0]["answer"] = "已联网找到论文。"
    assert cases.evaluate_case_checks(sample)["hard_condition_blocked"] is False


def test_failed_check_and_missing_repetition_reject_report() -> None:
    old = _fake_tree_result(model_ids=["qwen-a"])
    new = _fake_tree_result(model_ids=["qwen-a"])
    new["cases"][0]["checks"]["no_unnecessary_search"] = False
    assert any("未通过" in item for item in pairing.summarize_pairing(old, new)["problems"])
    new["repeats"] = 2
    assert any("重复次数" in item for item in pairing.summarize_pairing(old, new)["problems"])


def test_stop_requires_actual_stopped_status() -> None:
    sample = _fake_case("R07.stop_continue", "R07")
    sample.update(stopped_run_id="run-1", stopped_run_status="done", idle_new_runs=0)
    assert cases.evaluate_case_checks(sample)["stop_no_auto_continue"] is False
