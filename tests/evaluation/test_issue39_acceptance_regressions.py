"""独立验收：评分必须对应冻结回答，人工硬失败不能被自然度掩盖。"""

from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path
from typing import Any

import pytest

from bridges.evaluation.expression_corpus import SCENARIOS
from bridges.evaluation.expression_frozen_review import score_frozen_report
from bridges.evaluation.expression_gates import HardGateId, evaluate_hard_gates
from bridges.evaluation.expression_release import ExpressionReleasePolicy, evaluate_release
from bridges.evaluation.expression_review import (
    BlindPairItem,
    ScenarioTranscript,
    TranscriptTurn,
    aggregate_review,
    build_blind_review,
)
from bridges.evaluation.expression_scale import REVIEW_DIMENSIONS
from bridges.evaluation.expression_submission import (
    ReviewChoice,
    parse_submissions,
    submission_template,
)

ROOT = Path(__file__).resolve().parents[2]


def _materials() -> tuple[list[Any], dict[str, dict[str, str]]]:
    transcripts = {
        (f"scenario-{index}", arm): ScenarioTranscript(
            scenario_id=f"scenario-{index}", title="原创任务", category="venting",
            formal_path="chat.companion", arm_id=arm,
            turns=(TranscriptTurn(user="请完成任务", assistant=f"回答 {arm}"),),
        )
        for index in range(12)
        for arm in ("current-v4", "legacy-v2", "concise-baseline")
    }
    return build_blind_review("frozen-review", transcripts)


def _frozen() -> tuple[dict[str, Any], dict[str, Any]]:
    items, mapping = _materials()
    report = {
        "environment": {"code_commit": "frozen-code"},
        "run_lock_digest": "frozen-lock",
        "blind_review": {"items": [item.to_dict() for item in items], "mapping": mapping},
        "hard_gates": {"failures": []},
        "cost": {"humanization_specific_calls_zero": True},
        "validation_scope": {
            "real_ablations_executed": True, "formal_paths_executed": True,
            "deployment_thresholds_defined": True,
        },
    }
    submission = submission_template(items)
    reviewer = submission["reviewers"][0]
    reviewer["reviewer_id"] = "anonymous-1"
    for item in items:
        info = mapping[item.item_id]
        favored = "label_a" if info["label_a_arm"] == "current-v4" else "label_b"
        reviewer["choices"][item.item_id] = {
            dimension.dimension_id: favored for dimension in REVIEW_DIMENSIONS
        }
        reviewer["hard_gates"][item.item_id] = {
            label: {gate.value: "pass" for gate in HardGateId}
            for label in ("label_a", "label_b")
        }
    return report, submission


def test_rejects_votes_for_another_frozen_set() -> None:
    items, _ = _materials()
    payload = submission_template(items)
    payload["review_set_id"] = "another-run"
    with pytest.raises(ValueError, match="review_set_id"):
        parse_submissions(payload, items=items)


def test_rejects_duplicate_reviewers_and_unknown_items() -> None:
    items, _ = _materials()
    payload = submission_template(items)
    payload["reviewers"].append(copy.deepcopy(payload["reviewers"][0]))
    with pytest.raises(ValueError, match="重复"):
        parse_submissions(payload, items=items)
    payload["reviewers"].pop()
    payload["reviewers"][0]["choices"]["unknown"] = {"help": "label_a"}
    with pytest.raises(ValueError, match="未知"):
        parse_submissions(payload, items=items)


def test_aggregate_rejects_duplicate_votes_and_unknown_items() -> None:
    items, mapping = _materials()
    vote = ReviewChoice("r1", items[0].item_id, "help", "label_a")
    with pytest.raises(ValueError, match="重复"):
        aggregate_review(items, mapping, [vote, vote])
    with pytest.raises(ValueError, match="未知"):
        aggregate_review(items, mapping, [ReviewChoice("r1", "unknown", "help", "tie")])


def test_dimension_reviewer_count_does_not_include_other_dimensions() -> None:
    items, mapping = _materials()
    votes = [ReviewChoice("r1", items[0].item_id, "help", "label_a"),
             ReviewChoice("r2", items[0].item_id, "natural", "label_a")]
    aggregate = aggregate_review(items, mapping, votes)
    result = next(
        row for row in aggregate["dimensions"]
        if row["dimension_id"] == "help"
        and row["comparison_id"] == mapping[items[0].item_id]["comparison_id"]
    )
    assert result["reviewer_count"] == 1
    assert aggregate["reviewer_count"] == 2


def test_frozen_score_preserves_original_and_rejects_changed_answer() -> None:
    report, submission = _frozen()
    original = copy.deepcopy(report)
    result = score_frozen_report(report, submission)
    assert result["release"]["released"] is True  # 仅合成数据验证门禁，不作真人证据。
    assert report == original
    report["blind_review"]["items"][0]["label_a_text"] += "回答被替换"
    with pytest.raises(ValueError, match="摘要"):
        score_frozen_report(report, submission)


@pytest.mark.parametrize("gate", list(HardGateId))
def test_manual_candidate_hard_failure_blocks_release(gate: HardGateId) -> None:
    report, submission = _frozen()
    item_id, info = next(iter(report["blind_review"]["mapping"].items()))
    label = "label_a" if info["label_a_arm"] == "current-v4" else "label_b"
    submission["reviewers"][0]["hard_gates"][item_id][label][gate.value] = "fail"
    result = score_frozen_report(report, submission)
    assert result["release"]["released"] is False
    assert result["manual_hard_gate_failures"]


def test_missing_manual_gates_and_missing_real_evidence_block_release() -> None:
    report, submission = _frozen()
    submission["reviewers"][0]["hard_gates"] = {}
    assert not score_frozen_report(report, submission)["release"]["released"]
    report, submission = _frozen()
    report.pop("validation_scope")
    result = score_frozen_report(report, submission)
    assert not result["release"]["released"]
    assert any("真实三项消融" in text for text in result["release"]["blockers"])


def test_unvoted_item_still_requires_manual_hard_review() -> None:
    report, submission = _frozen()
    reviewer = submission["reviewers"][0]
    item_id = next(iter(reviewer["choices"]))
    reviewer["choices"][item_id] = {}
    reviewer["hard_gates"].pop(item_id)
    result = score_frozen_report(report, submission)
    assert len(result["manual_hard_gate_missing"]) == 12
    assert not result["release"]["released"]


def test_natural_rejections_are_not_ignored_after_help_passes() -> None:
    report, submission = _frozen()
    items = [BlindPairItem(**item) for item in report["blind_review"]["items"]]
    choices = parse_submissions(submission, items=items)
    aggregate = aggregate_review(items, report["blind_review"]["mapping"], choices)
    for result in aggregate["dimensions"]:
        if result["dimension_id"] == "natural":
            result["neither_rate"] = 0.8
    assert not evaluate_release(aggregate)["released"]
    for result in aggregate["dimensions"]:
        result["reviewer_count"] = 1
    aggregate["reviewer_count"] = 2  # 全局人数足够，单项人数仍不足。
    assert not evaluate_release(aggregate, policy=ExpressionReleasePolicy(min_reviewers=2))[
        "released"
    ]


def test_hard_failure_is_explicit_even_without_preference_votes() -> None:
    verdict = evaluate_release({"submission_count": 0, "reviewer_count": 0},
                               hard_gate_failures=("候选任务未完成",))
    assert verdict["status"] == "not_released"
    assert verdict["blockers"] == ["候选任务未完成"]


def test_detailed_task_cannot_pass_with_only_acknowledgement() -> None:
    scenario = next(s for s in SCENARIOS if s.scenario_id == "long-derivation")
    gates = evaluate_hard_gates(scenario, turn_index=len(scenario.turns), answer="好的")
    assert not next(gate for gate in gates if gate.gate is HardGateId.TASK_INCOMPLETE).passed


def test_secret_rejection_never_echoes_value() -> None:
    script = runpy.run_path(str(ROOT / "scripts/run_issue39_human_expression_evaluation.py"))
    with pytest.raises(SystemExit) as error:
        script["_ensure_redacted"]({"api_key": "ABCDEFGH1234"})
    assert "ABCDEFGH1234" not in str(error.value)


def test_cli_scores_frozen_answers_without_constructing_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = runpy.run_path(str(ROOT / "scripts/run_issue39_human_expression_evaluation.py"))
    report, submission = _frozen()
    report_path = tmp_path / "report.json"
    submission_path = tmp_path / "submission.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    submission_path.write_text(json.dumps(submission), encoding="utf-8")

    def forbidden_gateway(*args: Any, **kwargs: Any) -> None:
        pytest.fail("冻结评分不允许调用真实模型。")

    monkeypatch.setitem(script["main"].__globals__, "build_real_gateway", forbidden_gateway)
    assert script["main"]([
        "--review-report", str(report_path), "--submissions", str(submission_path),
        "--output-dir", str(tmp_path / "result"),
    ]) == 0
    assert json.loads((tmp_path / "result/review-result.json").read_text(encoding="utf-8"))[
        "source_run_lock_digest"
    ] == "frozen-lock"
    with pytest.raises(SystemExit) as error:
        script["main"](["--real-probes", "--submissions", str(submission_path)])
    assert error.value.code == 2
