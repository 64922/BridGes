"""工单 42：证据汇总器的离线验证（构造 JUnit XML，不执行真实测试）。"""

from __future__ import annotations

from xml.sax.saxutils import escape

from bridges.evaluation.workflow_evidence import (
    build_evidence_report,
    expected_junit_key,
    manifest_nodes,
    render_markdown,
)
from bridges.evaluation.workflow_scenarios import WORKFLOW_SCENARIOS


def _xml(cases: list[tuple[str, str, str]]) -> str:
    body = "".join(
        f'<testcase classname="{escape(classname)}" name="{escape(name)}">{child}</testcase>'
        for classname, name, child in cases
    )
    return f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>'


def _passed_cases(nodes: tuple[str, ...] | None = None) -> list[tuple[str, str, str]]:
    return [
        (*expected_junit_key(node), "")
        for node in (nodes or manifest_nodes())
    ]


def test_full_pass_report_has_no_problems_and_covers_all_scenarios() -> None:
    xml = _xml(_passed_cases())
    report = build_evidence_report(xml, pytest_exit_code=0)
    assert report["problems"] == []
    assert report["scenario_count"] == 39
    assert report["summary"]["scenarios_ok"] == 39
    assert all(guard["ok"] for guard in report["zero_tolerance"])


def test_failed_node_marks_scenario_and_reports_problem() -> None:
    failing = "tests/chat/test_improvement09_run_budget_ledger.py::test_budget_class_derivation"
    cases = []
    for node in manifest_nodes():
        key = expected_junit_key(node)
        if node == failing:
            cases.append((*key, '<failure message="boom" />'))
        else:
            cases.append((*key, ""))
    report = build_evidence_report(_xml(cases), pytest_exit_code=1)
    entries = {entry["scenario_id"]: entry for entry in report["scenarios"]}
    assert entries["A01"]["ok"] is False
    assert failing in entries["A01"]["failed"]
    assert any("A01" in problem for problem in report["problems"])


def test_missing_and_skipped_nodes_are_not_acceptable_evidence() -> None:
    nodes = manifest_nodes()
    dropped = nodes[0]
    skipped = nodes[1]
    cases = []
    for node in nodes:
        if node == dropped:
            continue
        key = expected_junit_key(node)
        if node == skipped:
            cases.append((*key, '<skipped message="env" />'))
        else:
            cases.append((*key, ""))
    report = build_evidence_report(_xml(cases))
    assert any("缺 1" in problem for problem in report["problems"])
    assert any("跳过" in problem or "失败 1" in problem for problem in report["problems"])


def test_zero_tolerance_guard_failure_is_a_blocking_problem() -> None:
    guard_node = "tests/kernel/test_node_kernel.py::test_account_isolation_for_artifacts"
    cases = []
    for node in manifest_nodes():
        key = expected_junit_key(node)
        if node == guard_node:
            cases.append((*key, '<failure message="crossed" />'))
        else:
            cases.append((*key, ""))
    report = build_evidence_report(_xml(cases), pytest_exit_code=1)
    guard = next(
        entry for entry in report["zero_tolerance"] if entry["kind"] == "cross_account"
    )
    assert guard["ok"] is False
    assert any("cross_account" in problem for problem in report["problems"])


def test_parametrized_node_requires_all_expansions_to_pass() -> None:
    node = (
        "tests/chat/test_improvement12_hybrid_entry.py"
        "::test_review_stage_gate_negation_quote_and_pause"
    )
    classname, name = expected_junit_key(node)
    cases = []
    for manifest_node in manifest_nodes():
        key = expected_junit_key(manifest_node)
        if manifest_node == node:
            cases.append((*key, ""))
            cases.append((f"{classname}", f"{name}[case-2]", '<failure message="x" />'))
        else:
            cases.append((*key, ""))
    report = build_evidence_report(_xml(cases), pytest_exit_code=1)
    entries = {entry["scenario_id"]: entry for entry in report["scenarios"]}
    assert entries["L05"]["ok"] is False
    assert node in entries["L05"]["failed"]


def test_markdown_lists_every_scenario() -> None:
    report = build_evidence_report(_xml(_passed_cases()), pytest_exit_code=0)
    markdown = render_markdown(report)
    for scenario in WORKFLOW_SCENARIOS:
        assert scenario.scenario_id in markdown
