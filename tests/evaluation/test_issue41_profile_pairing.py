"""工单 41 真实配对底座测试（确定性替身，不调用真实模型）。"""

from __future__ import annotations

import json
from pathlib import Path

from bridges.evaluation.profile_pairing import (
    PAIRING_TASKS,
    PairingCondition,
    PairingResponse,
    blind_review_bundle,
    run_pairing,
    write_reports,
)

_ANSWERS = {
    PairingCondition.NONE: (
        "贝叶斯定理描述如何在获得新证据后更新概率判断。它先给出先验，再乘以似然并归一化。"
    ),
    PairingCondition.CORRECT: (
        "先看一个例子：某种疾病发病率 1%，检测阳性率 99%……"
        "理解了这个例子，再看贝叶斯定理的公式 $$P(A|B)=...$$ 就很自然。"
        "贝叶斯定理的核心是概率。"
    ),
    PairingCondition.WRONG: (
        "贝叶斯定理的严格证明如下：由条件概率定义出发……该定理是概率论的基础结论。"
    ),
    PairingCondition.OUTDATED: ("贝叶斯定理是概率论中的更新规则：先验乘以似然后归一化。"),
}

_PLAN_ANSWERS = {
    PairingCondition.NONE: "两周线性代数复习计划：第一周矩阵与行列式，第二周特征值。",
    PairingCondition.CORRECT: ("按你每天 30 分钟的安排，线性代数复习分两周：第一周……"),
    PairingCondition.WRONG: "每天 8 小时高强度复习线性代数，两周可以完成三轮。",
    PairingCondition.OUTDATED: "两周线性代数复习计划：先梳理矩阵，再复习特征值。",
}


class _FakeSender:
    def __init__(self, overrides: dict[tuple[str, PairingCondition], str] | None = None):
        self.overrides = overrides or {}

    def __call__(self, task, condition):
        answer = self.overrides.get(
            (task.task_id, condition),
            (_ANSWERS[condition] if task.task_id == "bayes-explain" else _PLAN_ANSWERS[condition]),
        )
        return PairingResponse(
            status="done",
            answer=answer,
            latency_ms=1200 if condition == PairingCondition.CORRECT else 900,
            model_id="qwen3.7-plus-2026-05-26",
            input_tokens=500 + len(answer),
            output_tokens=len(answer),
            profile_item_count=0 if condition == PairingCondition.NONE else 1,
            cost_estimate={"total": 0.0},
        )


def test_all_conditions_pass_with_compliant_answers() -> None:
    report = run_pairing(_FakeSender())
    assert report.passed, [f"{run.task_id}/{run.condition.value}" for run in report.failed_runs]
    assert len(report.runs) == len(PAIRING_TASKS) * len(PairingCondition)

    correct = [run for run in report.runs if run.condition == PairingCondition.CORRECT]
    assert all(run.measurements["example_before_formula"] for run in correct[:1])
    assert all(
        run.measurements.get("correct_constraint_reflected")
        for run in correct
        if run.task_id == "study-plan"
    )


def test_correct_profile_without_concrete_improvement_fails_gate() -> None:
    sender = _FakeSender(
        {
            ("bayes-explain", PairingCondition.CORRECT): (
                "贝叶斯定理是概率论中的更新规则，先验乘以似然后归一化。"
            )
        }
    )
    report = run_pairing(sender)
    assert not report.passed
    failed = {(run.task_id, run.condition.value) for run in report.failed_runs}
    assert ("bayes-explain", "correct_profile") in failed


def test_outdated_fact_leak_fails_gate() -> None:
    sender = _FakeSender(
        {
            ("bayes-explain", PairingCondition.OUTDATED): (
                "你下周要考英语六级，先放一放，贝叶斯定理是概率更新规则。"
            )
        }
    )
    report = run_pairing(sender)
    assert not report.passed
    run = next(
        run
        for run in report.failed_runs
        if run.condition == PairingCondition.OUTDATED and run.task_id == "bayes-explain"
    )
    assert "六级" in run.measurements["outdated_fact_echoed"]


def test_cost_latency_and_condition_summary_recorded() -> None:
    report = run_pairing(_FakeSender())
    payload = report.to_dict()
    assert payload["run_count"] == 8
    summary = payload["conditions"]["correct_profile"]
    assert summary["input_tokens"] > 0
    assert summary["latency_ms_avg"] == 1200
    for run_payload in payload["runs"]:
        assert run_payload["output_tokens"] is not None
        assert run_payload["cost_estimate"] == {"total": 0.0}


def test_blind_review_and_reports_written(tmp_path: Path) -> None:
    report = run_pairing(_FakeSender())
    markdown, mapping = blind_review_bundle(report)
    assert len(mapping) == len(report.runs)
    assert all(label.startswith("case-") for label in mapping)
    assert "盲评" in markdown
    for value in mapping.values():
        assert "/" in value

    written = write_reports(report, tmp_path)
    assert written["pairing-report.md"] is True
    assert (tmp_path / "pairing-report.md").exists()
    assert (tmp_path / "blind-review.md").exists()
    payload = json.loads((tmp_path / "pairing-report.json").read_text("utf-8"))
    assert payload["blind_review_map"] == mapping


def test_identical_keyword_answers_do_not_prove_pairing_gain() -> None:
    def sender(task, condition):
        return PairingResponse("done", "贝叶斯例如错误。公式错误。线性代数每天8小时，30是页码。", 0)

    assert not run_pairing(sender).passed


def test_time_budget_mention_does_not_allow_an_infeasible_plan() -> None:
    sender = _FakeSender(
        {
            ("study-plan", PairingCondition.CORRECT): "线性代数每天30分钟，周末学习8小时。",
        }
    )
    report = run_pairing(sender)
    assert not report.passed
    assert any(
        c.checkpoint_id == "time_feasible" and not c.passed
        for run in report.failed_runs
        for c in run.checkpoints
    )


def test_multiple_daily_sessions_must_fit_total_time_budget() -> None:
    sender = _FakeSender({("study-plan", PairingCondition.CORRECT):
                          "线性代数每天上午30分钟，下午30分钟。"})
    assert not run_pairing(sender).passed


def test_two_week_total_is_not_a_daily_session() -> None:
    sender = _FakeSender({("study-plan", PairingCondition.CORRECT):
                          "线性代数每天30分钟，两周总共7小时；15分钟看例题+15分钟计算。"})
    assert run_pairing(sender).passed
