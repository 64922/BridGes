"""工单 42：把 pytest 真实执行结果汇总为 39 场景证据报告。

清单（``workflow_scenarios``）给出每个场景与零容忍守卫的测试节点；
本模块解析 pytest JUnit XML，生成「哪些场景真的执行且通过」的报告，
缺节点、失败、跳过都视为证据不成立。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bridges.evaluation.workflow_scenarios import (
    WORKFLOW_SCENARIOS,
    ZERO_TOLERANCE_GUARDS,
    Scenario,
    ZeroToleranceGuard,
)


@dataclass(frozen=True)
class NodeOutcome:
    """一个测试节点的执行结论。"""

    node: str
    status: str
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "passed"


def expected_junit_key(node: str) -> tuple[str, str]:
    """由清单节点推导 JUnit 的 ``(classname, name)``。"""
    path_part, _, remainder = node.partition("::")
    classname = path_part.removesuffix(".py").replace("/", ".").replace("\\", ".")
    segments = remainder.split("::")
    if len(segments) == 1:
        return classname, segments[0]
    return f"{classname}.{segments[0]}", segments[1]


def manifest_nodes() -> tuple[str, ...]:
    """全部场景与守卫涉及的测试节点（去重、稳定排序）。"""
    nodes = {node for scenario in WORKFLOW_SCENARIOS for node in scenario.tests}
    nodes.update(node for guard in ZERO_TOLERANCE_GUARDS for node in guard.tests)
    return tuple(sorted(nodes))


def parse_junit(xml_text: str) -> dict[tuple[str, str], list[NodeOutcome]]:
    """解析 JUnit XML；参数化用例归并到基础名，同名用例全部保留。"""
    root = ET.fromstring(xml_text)
    outcomes: dict[tuple[str, str], list[NodeOutcome]] = {}
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        if not classname or not name:
            continue
        status = "passed"
        message = ""
        for child in case:
            tag = child.tag.lower()
            if tag == "failure":
                status = "failed"
                message = (child.get("message") or "").strip()
                break
            if tag == "error":
                status = "error"
                message = (child.get("message") or "").strip()
                break
            if tag == "skipped":
                status = "skipped"
                message = (child.get("message") or "").strip()
                break
        base_name = name.partition("[")[0]
        key = (classname, base_name)
        outcomes.setdefault(key, []).append(
            NodeOutcome(node=f"{classname}::{name}", status=status, message=message)
        )
    return outcomes


_STATUS_SEVERITY = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}


def _node_evidence(
    nodes: tuple[str, ...], junit: dict[tuple[str, str], list[NodeOutcome]]
) -> tuple[dict[str, str], list[str], list[str]]:
    evidence: dict[str, str] = {}
    missing: list[str] = []
    failed: list[str] = []
    for node in nodes:
        outcomes = junit.get(expected_junit_key(node))
        if not outcomes:
            missing.append(node)
            evidence[node] = "missing"
            continue
        worst = max(outcomes, key=lambda outcome: _STATUS_SEVERITY[outcome.status])
        evidence[node] = worst.status
        if worst.status != "passed":
            failed.append(node)
    return evidence, missing, failed


def build_evidence_report(
    xml_text: str, *, pytest_exit_code: int | None = None, pytest_command: str = ""
) -> dict[str, Any]:
    """生成完整的场景证据报告（纯函数，可离线测试）。"""
    junit = parse_junit(xml_text)
    scenarios: list[dict[str, Any]] = []
    problems: list[str] = []
    for scenario in WORKFLOW_SCENARIOS:
        scenarios.append(_scenario_entry(scenario, junit, problems))
    guards: list[dict[str, Any]] = []
    for guard in ZERO_TOLERANCE_GUARDS:
        guards.append(_guard_entry(guard, junit, problems))
    passed_nodes = sum(
        1
        for entry in scenarios
        for status in entry["evidence"].values()
        if status == "passed"
    )
    return {
        "kind": "workflow-evidence",
        "generated_at": datetime.now(UTC).isoformat(),
        "pytest_exit_code": pytest_exit_code,
        "pytest_command": pytest_command,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "zero_tolerance": guards,
        "summary": {
            "passed_nodes": passed_nodes,
            "problems": len(problems),
            "scenarios_ok": sum(1 for entry in scenarios if entry["ok"]),
        },
        "problems": problems,
    }


def _scenario_entry(
    scenario: Scenario, junit: dict[tuple[str, str], list[NodeOutcome]], problems: list[str]
) -> dict[str, Any]:
    evidence, missing, failed = _node_evidence(scenario.tests, junit)
    ok = not missing and not failed
    if not ok:
        problems.append(
            f"{scenario.scenario_id} 证据不成立：缺 {len(missing)}，失败 {len(failed)}。"
        )
    return {
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "layers": [layer.value for layer in scenario.layers],
        "external_gates": [gate.value for gate in scenario.external_gates],
        "zero_tolerance": [kind.value for kind in scenario.zero_tolerance],
        "evidence": evidence,
        "missing": missing,
        "failed": failed,
        "ok": ok,
    }


def _guard_entry(
    guard: ZeroToleranceGuard,
    junit: dict[tuple[str, str], list[NodeOutcome]],
    problems: list[str],
) -> dict[str, Any]:
    evidence, missing, failed = _node_evidence(guard.tests, junit)
    ok = not missing and not failed
    if not ok:
        problems.append(
            f"零容忍守卫 {guard.kind.value} 不成立：缺 {len(missing)}，失败 {len(failed)}。"
        )
    return {
        "kind": guard.kind.value,
        "description": guard.description,
        "evidence": evidence,
        "missing": missing,
        "failed": failed,
        "ok": ok,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """把证据报告渲染为便于审查的 Markdown。"""
    lines = [
        "# 工单 42 场景执行证据",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- pytest 退出码：{report.get('pytest_exit_code')}",
        f"- 场景：{report['summary']['scenarios_ok']}/{report['scenario_count']} 通过",
        f"- 问题数：{report['summary']['problems']}",
        "",
        "| 场景 | 结论 | 失败/缺失 |",
        "| --- | --- | --- |",
    ]
    for entry in report["scenarios"]:
        detail = "、".join(entry["missing"] + entry["failed"]) or "—"
        lines.append(
            f"| {entry['scenario_id']} {entry['title']} | "
            f"{'通过' if entry['ok'] else '不成立'} | {detail} |"
        )
    lines.extend(
        [
            "",
            "## 零容忍守卫",
            "",
            "| 类型 | 结论 | 失败/缺失 |",
            "| --- | --- | --- |",
        ]
    )
    for guard in report["zero_tolerance"]:
        detail = "、".join(guard["missing"] + guard["failed"]) or "—"
        lines.append(
            f"| {guard['kind']} | {'通过' if guard['ok'] else '不成立'} | {detail} |"
        )
    if report["problems"]:
        lines.extend(["", "## 问题", ""])
        lines.extend(f"- {problem}" for problem in report["problems"])
    return "\n".join(lines) + "\n"


__all__ = [
    "NodeOutcome",
    "build_evidence_report",
    "expected_junit_key",
    "manifest_nodes",
    "parse_junit",
    "render_markdown",
]
