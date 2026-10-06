"""工单 42：39 场景清单的结构与可执行性校验。"""

from __future__ import annotations

import re
from pathlib import Path

from bridges.evaluation import external_probes as probes
from bridges.evaluation.workflow_scenarios import (
    REAL_MODEL_PAIRING_COVERAGE,
    SCENARIO_BY_ID,
    WORKFLOW_SCENARIOS,
    ZERO_TOLERANCE_GUARDS,
    ExternalGate,
    ZeroTolerance,
    expected_scenario_ids,
    validate_workflow_scenarios,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_declares_exactly_39_fixed_scenarios() -> None:
    assert validate_workflow_scenarios() == []
    assert [scenario.scenario_id for scenario in WORKFLOW_SCENARIOS] == list(
        expected_scenario_ids()
    )


def test_every_scenario_binds_executable_test_evidence() -> None:
    for scenario in WORKFLOW_SCENARIOS:
        assert scenario.tests, scenario.scenario_id
        assert scenario.layers, scenario.scenario_id


def test_every_manifest_test_node_resolves_to_a_real_test() -> None:
    nodes = [node for scenario in WORKFLOW_SCENARIOS for node in scenario.tests]
    nodes += [node for guard in ZERO_TOLERANCE_GUARDS for node in guard.tests]
    for node in sorted(set(nodes)):
        path_part, _, test_name = node.partition("::")
        path = REPO_ROOT / path_part
        assert path.exists(), f"测试文件不存在：{node}"
        text = path.read_text(encoding="utf-8")
        assert re.search(rf"def {re.escape(test_name)}\s*\(", text), (
            f"测试函数不存在：{node}"
        )


def test_external_gates_used_by_scenarios_are_registered_as_probes() -> None:
    used = {
        gate for scenario in WORKFLOW_SCENARIOS for gate in scenario.external_gates
    }
    assert used, "清单未绑定任何外部门"
    assert used <= set(probes.PROBE_REGISTRY), "存在没有探针实现的外部门"


def test_probe_registry_and_product_claims_cover_same_gates() -> None:
    assert set(probes.PROBE_REGISTRY) == set(probes.PRODUCT_CLAIMS)
    assert set(probes.PRODUCT_CLAIMS) <= set(ExternalGate)


def test_zero_tolerance_guards_cover_every_declared_kind() -> None:
    kinds = {guard.kind for guard in ZERO_TOLERANCE_GUARDS}
    assert kinds == set(ZeroTolerance)
    for guard in ZERO_TOLERANCE_GUARDS:
        assert guard.tests, guard.kind
    guarded = {
        kind for scenario in WORKFLOW_SCENARIOS for kind in scenario.zero_tolerance
    }
    assert guarded <= set(ZeroTolerance)


def test_fault_injection_scenarios_are_declared() -> None:
    expected = {"R01", "R02", "R03", "R04", "R07", "L07", "L09", "L10"}
    actual = {
        scenario.scenario_id
        for scenario in WORKFLOW_SCENARIOS
        if scenario.fault_injection_tests
    }
    assert actual == expected


def test_real_model_pairing_coverage_scenarios_exist_and_are_marked() -> None:
    for scenario_id in REAL_MODEL_PAIRING_COVERAGE:
        scenario = SCENARIO_BY_ID[scenario_id]
        assert scenario.real_model, scenario_id
