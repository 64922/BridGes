"""工单 42：配对评测纯函数离线验证（不跑真实模型）。"""

from __future__ import annotations

from typing import Any

from scripts import run_issue42_workflow_pairing as pairing


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
            for spec in pairing.SCENARIOS
            if spec.case_id == case_id
            for check in spec.checks
        },
    }


def _fake_tree_result(*, model_ids: list[str]) -> dict[str, Any]:
    cases = [
        _fake_case(spec.case_id, spec.scenario_id) for spec in pairing.SCENARIOS
    ]
    return {
        "tree": "fake-tree",
        "commit": "abc123",
        "dirty": False,
        "corpus_sha256": pairing.corpus_sha256(),
        "repeats": 1,
        "started_at": "2026-10-06T00:00:00+00:00",
        "finished_at": "2026-10-06T00:01:00+00:00",
        "cases": cases,
        "model_ids": model_ids,
    }


def test_corpus_sha256_is_stable() -> None:
    assert pairing.corpus_sha256() == pairing.corpus_sha256()
    assert len(pairing.corpus_sha256()) == 64
    assert [spec.scenario_id for spec in pairing.SCENARIOS] == [
        "A01",
        "A02",
        "A03",
        "A11",
        "R07",
    ]


def test_percentile_uses_nearest_rank() -> None:
    values = list(range(1, 101))
    assert pairing.percentile(values, 0.5) == 50
    assert pairing.percentile(values, 0.95) == 95
    assert pairing.percentile([7], 0.95) == 7
    assert pairing.percentile([], 0.5) is None


def test_projection_empty_handles_shapes() -> None:
    assert pairing.projection_empty(None)
    assert pairing.projection_empty([])
    assert pairing.projection_empty({})
    assert not pairing.projection_empty([{"id": 1}])


def test_evaluate_case_checks_a01_and_r07() -> None:
    a01 = _fake_case("A01.lightweight", "A01")
    a01_checks = pairing.evaluate_case_checks(a01)
    assert a01_checks == {
        "answer_nonempty": True,
        "single_chat_call": True,
        "no_external_model_capability": True,
        "no_task_created": True,
        "no_unnecessary_search": True,
    }
    a01["turns"][0]["search_planned"] = True
    assert pairing.evaluate_case_checks(a01)["no_unnecessary_search"] is False

    r07 = _fake_case("R07.stop_continue", "R07")
    r07["turns"] = [
        {"run_id": "r1", "answer": "第一次", "capability_calls": {}, "paper_search_empty": True},
        {
            "run_id": "r2",
            "answer": "",
            "status": "stopped",
            "capability_calls": {},
            "paper_search_empty": True,
        },
        {"run_id": "r3", "answer": "第三次", "capability_calls": {}, "paper_search_empty": True},
    ]
    r07.update(
        stopped_run_id="r2",
        stopped_run_status="stopped",
        idle_new_runs=0,
        continued_run_id="r3",
    )
    r07_checks = pairing.evaluate_case_checks(r07)
    assert r07_checks["answer_nonempty"] is True
    assert r07_checks["stop_no_auto_continue"] is True
    assert r07_checks["continue_creates_new_run"] is True
    r07["idle_new_runs"] = 1
    assert pairing.evaluate_case_checks(r07)["stop_no_auto_continue"] is False

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
    assert pairing.evaluate_case_checks(a11) == {
        "no_paper_results": True,
        "hard_condition_blocked": True,
    }
    a11["turns"][0]["error_code"] = None
    a11["turns"][0]["error_message"] = None
    assert pairing.evaluate_case_checks(a11)["hard_condition_blocked"] is False


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
        "lightweight": {"total_budget_ms": 120000, "verify_deliver_reserve_ms": 0},
        "normal": {"total_budget_ms": 60000, "verify_deliver_reserve_ms": 15000},
    }
    markdown = pairing.render_markdown(report, budgets)
    assert "09 预算初值" in markdown
    assert "保留（余量" in markdown
    assert "A01" in markdown and "R07" in markdown
