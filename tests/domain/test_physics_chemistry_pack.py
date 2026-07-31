"""T042 物理与化学实验测量领域包的公开接缝测试。"""

from __future__ import annotations

from science_companion.ai.capability_registry import CapabilityRegistry
from science_companion.contracts.ai import CapabilityKind, CapabilityRecord
from science_companion.domain import DomainPackLoader, DomainPackValidationRuntime
from science_companion.domain.physics_chemistry import (
    PhysicsChemistryDomainPack,
)


def _loaded_pack() -> tuple[PhysicsChemistryDomainPack, object]:
    pack = PhysicsChemistryDomainPack()
    loaded = DomainPackLoader(
        capability_registry=_capability_registry(),
        require_capability_contracts=True,
    ).load(pack)
    return pack, loaded


def _capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for name, input_schema in (
        ("unit_dimension_check", "unit-dimension/v1"),
        ("uncertainty_propagation_check", "uncertainty/v1"),
        ("chemical_equation_balance_check", "chemical-equation/v1"),
        ("significant_figures_check", "sigfig/v1"),
    ):
        registry.register(
            CapabilityRecord(
                name=name,
                version="1.0.0",
                kind=CapabilityKind.TOOL,
                vendor="test",
                region="local",
                input_schema_version=input_schema,
                output_schema_version="physics-chemistry-validation/v1",
            )
        )
    return registry


def test_manifest_declares_scope_sources_rules_and_version_boundary() -> None:
    pack = PhysicsChemistryDomainPack()
    manifest = pack.manifest

    assert "物理量与测量" in manifest.scope
    assert manifest.exclusions
    assert {"measurement_result", "unit_conversion", "chemical_term"} <= set(
        manifest.question_types
    )
    assert any("BIPM" in policy.evidence_requirements[0] for policy in manifest.source_policies)
    assert any(rule.rule_id == "physics.unit.dimension" for rule in manifest.rules)
    assert any(rule.rule_id == "physics.safety.dangerous_experiment" for rule in manifest.rules)
    assert manifest.content_digest
    assert manifest.compatibility.preserves_runtime_contract is True


def test_runtime_replays_measurement_and_safety_fixtures() -> None:
    pack, loaded = _loaded_pack()
    fixtures = list(pack.manifest.fixtures)

    report = DomainPackValidationRuntime().run(
        loaded,
        fixtures=fixtures,
        run_id="run-t042-core",
    )

    results = {result.fixture_id: result for result in report.fixture_results}
    assert results["physics.measurement.correct"].passed is True
    assert results["physics.unit.celsius-multiply"].passed is True
    assert results["physics.mass-weight.confusion"].passed is True
    assert results["chemistry.mol-molecule.confusion"].passed is True
    assert results["physics.constant.old-codata"].passed is True
    assert results["chemistry.equation.imbalanced"].passed is True
    assert results["physics.safety.dangerous-experiment"].passed is True
    assert results["chemistry.mechanism.dangerous"].passed is True
    assert results["physics.conflict.evidence"].passed is True
    assert results["chemistry.units.ppm-basis-missing"].passed is True
    assert results["physics.measurement.error-uncertainty.confusion"].passed is True
    assert results["physics.sigfig.overstated"].passed is True
    assert results["physics.uncertainty.missing"].passed is True
    assert report.fixture_results[0].actual_status is not None


def test_validate_claim_preserves_claim_evidence_fact_lock_and_report() -> None:
    pack, _ = _loaded_pack()
    claim = {
        "claim_id": "claim.t042.trace",
        "claim_type": "measurement_result",
        "quantity": "长度",
        "value": 1.23,
        "unit": "m",
        "uncertainty": 0.01,
        "coverage_factor": 2,
        "conditions": {"temperature": "20 °C", "pressure": "101.325 kPa"},
        "sample": "钢棒 A",
        "instrument": "游标卡尺",
        "significant_figures": 3,
        "definition_version": "si-length-v1",
        "fact_lock_set": {"set_id": "platform-lock-set:trace", "locks": []},
    }
    evidence = [
        {
            "evidence_id": "e.measurement.1",
            "relation": "supports",
            "locator": "lab-notebook:page-12",
        }
    ]

    result = pack.validate_claim(claim, evidence)

    assert result["status"] == "verified"
    assert result["details"]["claim"]["claim_id"] == "claim.t042.trace"
    assert result["details"]["evidence"][0]["evidence_id"] == "e.measurement.1"
    assert result["details"]["fact_lock_set"]["set_id"] == "platform-lock-set:trace"
    assert result["details"]["fact_lock_reference"]["owner"] == "platform.science.fact_lock"
    assert result["details"]["validation_report"]["measurement_checked"] is True


def test_unit_conversion_passes_when_dimensionally_consistent() -> None:
    pack, _ = _loaded_pack()

    correct = pack.validate_claim(
        {
            "claim_id": "claim.unit.ok",
            "claim_type": "unit_conversion",
            "value": 1.0,
            "from_unit": "m",
            "to_unit": "cm",
            "expected_value": 100.0,
            "definition_version": "si-units-v1",
        },
        [],
    )
    inconsistent = pack.validate_claim(
        {
            "claim_id": "claim.unit.bad",
            "claim_type": "unit_conversion",
            "value": 1.0,
            "from_unit": "m",
            "to_unit": "s",
            "expected_value": 1.0,
            "definition_version": "si-units-v1",
        },
        [],
    )

    assert correct["status"] == "verified"
    assert inconsistent["status"] == "blocked"
    assert "unit_dimension_mismatch" in inconsistent["reason_codes"]


def test_dangerous_experiment_suggestion_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        {
            "claim_id": "claim.dangerous",
            "claim_type": "experimental_comparison",
            "description": "在家中用高锰酸钾和甘油制备少量氧气",
            "hazard_flags": ["explosive", "toxic"],
            "definition_version": "lab-safety-v1",
        },
        [],
    )

    assert result["status"] == "blocked"
    assert "dangerous_experiment" in result["reason_codes"]


def test_chemical_equation_imbalance_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        {
            "claim_id": "claim.unbalanced",
            "claim_type": "mechanism_under_conditions",
            "equation": "H2 + O2 -> H2O",
            "conditions": {"temperature": "25 °C", "pressure": "101.325 kPa"},
            "definition_version": "stoichiometry-v1",
        },
        [],
    )

    assert result["status"] == "blocked"
    assert "chemical_equation_unbalanced" in result["reason_codes"]


def test_dangerous_mechanism_with_balanced_equation_still_enters_safety_gate() -> None:
    """平衡方程 + 危险条件：安全门必须覆盖 mechanism_under_conditions，不能放行。"""
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        {
            "claim_id": "claim.dangerous-mechanism",
            "claim_type": "mechanism_under_conditions",
            "equation": "2H2 + O2 -> 2H2O",
            "conditions": {"temperature": "25 °C", "pressure": "101.325 kPa"},
            "hazard_flags": ["explosive"],
            "description": "在封闭容器中点燃氢氧混合气验证爆炸极限",
            "definition_version": "lab-safety-v1",
        },
        [],
    )

    assert result["status"] == "blocked"
    assert "dangerous_experiment" in result["reason_codes"]


def test_conflicting_evidence_produces_conflicted_state() -> None:
    """支持与反驳证据并存必须进入 conflicted，而不是任意选边。"""
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        {
            "claim_id": "claim.conflict",
            "claim_type": "measurement_result",
            "quantity": "长度",
            "value": 1.23,
            "unit": "m",
            "uncertainty": 0.01,
            "coverage_factor": 2,
            "conditions": {"temperature": "20 °C"},
            "sample": "钢棒 D",
            "instrument": "游标卡尺",
            "significant_figures": 3,
            "definition_version": "si-length-v1",
        },
        [
            {"evidence_id": "e1", "relation": "supports", "locator": "lab-1"},
            {"evidence_id": "e2", "relation": "refutes", "locator": "lab-2"},
        ],
    )

    assert result["status"] == "conflicted"
    assert "evidence_conflict" in result["reason_codes"]


def test_ppm_without_basis_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        {
            "claim_id": "claim.ppm",
            "claim_type": "measurement_result",
            "quantity": "铜离子浓度",
            "value": 5.0,
            "unit": "ppm",
            "uncertainty": 0.5,
            "coverage_factor": 2,
            "conditions": {"temperature": "25 °C"},
            "sample": "水样 C",
            "instrument": "ICP-OES",
            "significant_figures": 1,
            "definition_version": "solution-concentration-v1",
            "description": "水样中铜离子浓度为 5.0 ppm",
        },
        [],
    )

    assert result["status"] == "blocked"
    assert "ppm_basis_missing" in result["reason_codes"]


def test_error_confused_with_uncertainty_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        {
            "claim_id": "claim.error-as-uncertainty",
            "claim_type": "measurement_result",
            "quantity": "长度",
            "value": 1.23,
            "unit": "m",
            "conditions": {"temperature": "20 °C"},
            "sample": "钢棒 E",
            "instrument": "游标卡尺",
            "significant_figures": 3,
            "definition_version": "si-length-v1",
            "description": "测量误差为 0.01 m",
        },
        [],
    )

    assert result["status"] == "blocked"
    assert "error_uncertainty_confused" in result["reason_codes"]


def test_old_codata_constant_is_stale_or_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        {
            "claim_id": "claim.old-constant",
            "claim_type": "constant_value",
            "constant_name": "真空光速",
            "value": 299792458.0,
            "unit": "m/s",
            "codata_year": 2002,
            "definition_version": "codata-v1",
        },
        [],
    )

    assert result["status"] in {"blocked", "stale_or_updated"}
    assert (
        "source_stale" in result["reason_codes"]
        or "codata_year_outdated" in result["reason_codes"]
    )


def test_uncertainty_required_for_measurement_claim() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        {
            "claim_id": "claim.no-uncertainty",
            "claim_type": "measurement_result",
            "quantity": "质量",
            "value": 5.0,
            "unit": "g",
            "conditions": {"temperature": "20 °C"},
            "sample": "未知",
            "instrument": "天平",
            "significant_figures": 1,
            "definition_version": "si-mass-v1",
        },
        [],
    )

    assert result["status"] == "blocked"
    assert "measurement_uncertainty_missing" in result["reason_codes"]
