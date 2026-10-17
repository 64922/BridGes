"""工单 18：评测资产、统计口径与证据文件的确定性单元测试（不访问网络与模型）。"""

from __future__ import annotations

import json
from pathlib import Path

from bridges.evaluation.profile_loop_eval import evidence
from bridges.evaluation.profile_loop_eval.metrics import (
    check_fact_groups,
    check_forbidden,
    check_negation,
    latency_summary,
    scene_status,
    summarize_resources,
    summarize_results,
    unmatched_items,
)
from bridges.evaluation.profile_loop_eval.scenes import (
    PAIRED_TASKS,
    SCENARIO_SPECS,
    scenario_payload,
)
from bridges.evaluation.profile_loop_eval.types import (
    FactGroup,
    SceneRecord,
    SceneResult,
)


def _result(
    scene_id: str,
    *,
    kind: str = "scenario",
    items: tuple[str, ...] = (),
    checks: tuple[tuple[str, bool], ...] = (),
) -> SceneResult:
    record = SceneRecord(
        scene_id=scene_id,
        kind=kind,
        title=scene_id,
        items_after=[{"text": text, "status": "active"} for text in items],
    )
    result = SceneResult(record=record)
    for check_id, passed in checks:
        result.add(check_id, passed, detail=check_id)
    return result


def test_scenario_matrix_is_frozen_and_complete() -> None:
    ids = [spec.scenario_id for spec in SCENARIO_SPECS]
    assert ids == [f"T{index:02d}" for index in range(1, 21)]
    for spec in SCENARIO_SPECS:
        assert spec.dev_turns, spec.scenario_id
        sample_ids = [sample.sample_id for sample in spec.extra_samples]
        assert len(sample_ids) == len(set(sample_ids)), spec.scenario_id
    payload = scenario_payload()
    assert [row["scenario_id"] for row in payload] == ids
    assert all(isinstance(row["dev_turns"], list) for row in payload)


def test_paired_tasks_cover_correction_arms() -> None:
    assert [task.task_id for task in PAIRED_TASKS] == ["paired-time-budget", "paired-grade"]
    for task in PAIRED_TASKS:
        assert task.correct_seed and task.wrong_seed and task.question
        assert task.correction_text
        assert task.recurrence_must_include
        assert task.recurrence_must_exclude


def test_scene_status_and_summary_counts() -> None:
    passed = _result("pass", checks=(("fact:a", True),))
    partial = _result("partial", checks=(("fact:a", True), ("fact:b", False)))
    failed = _result("fail", checks=(("boundary:x", False),))
    empty = _result("empty")
    assert scene_status(passed.checks) == "pass"
    assert scene_status(partial.checks) == "partial"
    assert scene_status(failed.checks) == "fail"
    assert scene_status(empty.checks) == "no_checks"

    summary = summarize_results([passed, partial, failed, empty])
    assert summary["sample_size"] == {"scenes": 4, "scenarios": 4, "extras": 0}
    assert summary["check_totals"]["fact"] == {
        "passed": 2,
        "failed": 1,
        "total": 3,
        "rate": 0.6667,
    }
    assert summary["check_totals"]["boundary"]["failed"] == 1
    rows = {row["scene_id"]: row for row in summary["matrix"]}
    assert rows["partial"]["failed_checks"] == ["fact:b"]


def test_fact_forbidden_and_negation_checks() -> None:
    result = _result("checks", items=("我对心理学不感兴趣", "我喜欢跑步"))
    check_fact_groups(result, (FactGroup("心理学", ("心理学",)),))
    check_negation(result, ("心理学",))
    check_forbidden(result, ("心理学",))
    results = {check.check_id: check for check in result.checks}
    assert results["fact:心理学"].passed
    assert results["negation:心理学"].passed
    assert not results["forbidden:心理学"].passed

    positive = _result("positive", items=("我对心理学很感兴趣",))
    check_negation(positive, ("心理学",))
    assert not positive.checks[-1].passed

    assert unmatched_items(
        _result("extra", items=("无关条目", "我喜欢跑步")).record.to_dict(),
        (FactGroup("跑步", ("跑步",)),),
    ) == ["无关条目"]


def test_resource_and_latency_summaries_report_usage_only() -> None:
    locks = [
        {
            "capability_name": "qwen_text_chat",
            "status": "success",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
        {
            "capability_name": "qwen_text_chat",
            "status": "error",
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        },
    ]
    usage = summarize_resources(locks)
    assert usage["model_calls"] == 2
    assert usage["input_tokens"] == 12
    assert usage["output_tokens"] == 6
    assert usage["by_capability"]["qwen_text_chat"]["failed"] == 1
    assert "不折算费用" in usage["note"]

    result = _result("latency")
    result.record.turns = [
        {
            "generation_run": {"duration_ms": 100},
            "extraction_runs": [
                {
                    "created_at": "2026-10-17T00:00:00+00:00",
                    "updated_at": "2026-10-17T00:00:02+00:00",
                }
            ],
        },
        {"generation_run": {"duration_ms": 300}, "extraction_runs": []},
    ]
    latency = latency_summary([result])
    assert latency["generation_duration_ms"]["count"] == 2
    assert latency["generation_duration_ms"]["median"] == 300
    assert latency["extraction_duration_ms"]["max"] == 2000


def test_evidence_files_and_baseline_comparison(tmp_path: Path) -> None:
    payload = {
        "run_lock": {
            "response_mode": "stub",
            "eval_version": "profile-loop-eval-v1",
            "eval_assets_sha256": "digest",
            "git_commit": "abc",
            "git_dirty": False,
            "model_config": {"model_id": "stub"},
            "credentials": {},
        },
        "generated_at": "2026-10-17T00:00:00+00:00",
        "summary": {
            "matrix": [
                {
                    "scene_id": "T01",
                    "kind": "scenario",
                    "status": "pass",
                    "passed": 1,
                    "checks": 1,
                    "failed_checks": [],
                }
            ],
            "check_totals": {"fact": {"passed": 1, "failed": 0, "rate": 1.0}},
        },
        "resources": {
            "resources": {
                "model_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "note": "只报用量",
            },
            "latency": {
                "generation_duration_ms": {"count": 0},
                "extraction_duration_ms": {"count": 0},
            },
            "tasks_by_status": {},
            "queue_depth_max_observed": 0,
        },
        "baseline_comparison": {"available": False},
        "notice": "判定标准运行前冻结。",
    }
    json_path, md_path = evidence.write_evidence(
        tmp_path,
        stamp="20261017T000000Z",
        payload=payload,
        markdown=evidence.render_markdown(payload),
    )
    assert json_path.name == "loop-eval-stub-20261017T000000Z.json"
    assert (tmp_path / "run-lock-stub-20261017T000000Z.json").is_file()
    assert "判定汇总" in md_path.read_text(encoding="utf-8")

    candidate_dir = tmp_path / "01-journeys"
    candidate_dir.mkdir()
    for stamp, observed in (
        ("20261017T000001Z", "fail"),
        ("20261017T000002Z", "pass"),
    ):
        evidence.write_json(
            candidate_dir / f"baseline-real-{stamp}.json",
            {
                "run_lock": {"git_commit": "abc", "model_config": {"model_id": "m"}},
                "journeys": [
                    {
                        "journey_id": "t01-name",
                        "expected_baseline_status": "pass",
                        "evaluation": {
                            "observed_status": observed,
                            "baseline_match": observed == "pass",
                        },
                    }
                ],
            },
        )
    candidate = evidence.load_baseline_candidate(candidate_dir)
    assert candidate is not None and candidate["journeys"][0]["evaluation"] == {
        "observed_status": "pass",
        "baseline_match": True,
    }
    frozen = json.loads(json.dumps(candidate))
    frozen["journeys"][0]["evaluation"] = {
        "observed_status": "known_failing",
        "baseline_match": False,
    }
    comparison = evidence.compare_baseline(frozen, candidate)
    assert comparison["available"]
    assert comparison["rows"] == [
        {
            "journey_id": "t01-name",
            "baseline_observed": "known_failing",
            "candidate_observed": "pass",
            "candidate_expected": "pass",
            "candidate_match": True,
        }
    ]
