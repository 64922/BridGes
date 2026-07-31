"""T041 数学与形式证明领域包的公开接缝测试。"""

from __future__ import annotations

from science_companion.ai.capability_registry import CapabilityRegistry
from science_companion.contracts.ai import CapabilityKind, CapabilityRecord
from science_companion.domain import DomainPackLoader, DomainPackValidationRuntime
from science_companion.domain.math_formal_proof import (
    MathFormalProofDomainPack,
)


def _loaded_pack() -> tuple[MathFormalProofDomainPack, object]:
    pack = MathFormalProofDomainPack()
    loaded = DomainPackLoader(
        capability_registry=_capability_registry(),
        require_capability_contracts=True,
    ).load(pack)
    return pack, loaded


def _capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for name, input_schema in (
        ("math_expression_check", "math-expression/v1"),
        ("formal_proof_check", "formal-proof/v1"),
    ):
        registry.register(
            CapabilityRecord(
                name=name,
                version="1.0.0",
                kind=CapabilityKind.TOOL,
                vendor="test",
                region="local",
                input_schema_version=input_schema,
                output_schema_version="math-validation/v1",
            )
        )
    return registry


def test_math_manifest_declares_scope_sources_rules_and_version_boundary() -> None:
    pack = MathFormalProofDomainPack()
    manifest = pack.manifest

    assert "证明有效性" in manifest.scope
    assert manifest.exclusions
    assert {"proof_validity", "symbolic_equivalence"} <= set(manifest.question_types)
    assert any("DLMF" in policy.evidence_requirements[0] for policy in manifest.source_policies)
    assert any(rule.rule_id == "math.proof.step" for rule in manifest.rules)
    assert manifest.content_digest
    assert manifest.compatibility.preserves_runtime_contract is True


def test_runtime_replays_correct_and_degraded_proof_fixtures_with_trace() -> None:
    pack, loaded = _loaded_pack()
    fixtures = {
        fixture.fixture_id: fixture
        for fixture in pack.manifest.fixtures
        if fixture.fixture_id.startswith("math.proof")
    }

    report = DomainPackValidationRuntime().run(
        loaded,
        fixtures=list(fixtures.values()),
        run_id="run-t041-proof-fixtures",
    )

    assert report.status.value == "needs_human"
    results = {result.fixture_id: result for result in report.fixture_results}
    assert results["math.proof.correct"].passed is True
    assert results["math.proof.missing-premise"].passed is True
    assert results["math.proof.circular"].passed is True
    assert results["math.proof.symbol-conflict"].passed is True
    assert results["math.proof.undecidable"].passed is True
    assert results["math.proof.invalid"].passed is True
    assert results["math.proof.correct"].details["actual_wording"]["proof_checked"] is True
    assert "missing_premise" in results["math.proof.missing-premise"].reason_codes
    assert "circular_reasoning" in results["math.proof.circular"].reason_codes
    assert results["math.proof.symbol-conflict"].actual_status == "conflicted"
    assert results["math.proof.undecidable"].actual_status == "needs_human"


def test_validate_claim_preserves_claim_evidence_fact_lock_and_step_report() -> None:
    pack, _ = _loaded_pack()
    claim = {
        "claim_id": "claim.t041.trace",
        "claim_type": "proof_validity",
        "definition_version": "elementary-algebra-v1",
        "quantifiers": [],
        "variables": {"x": "real"},
        "assumptions": ["x != 0"],
        "goal": "x / x = 1",
        "proof_steps": [
            {
                "step_id": "s1",
                "statement": "x / x = 1",
                "reason": "cancel_nonzero",
                "evidence_ids": ["e.step.1"],
            }
        ],
        "fact_lock_set": {"set_id": "platform-lock-set:trace", "locks": []},
    }
    evidence = [{"evidence_id": "e.step.1", "relation": "supports", "locator": "step:s1"}]

    result = pack.validate_claim(claim, evidence)

    assert result["status"] == "verified"
    assert result["details"]["claim"]["claim_id"] == "claim.t041.trace"
    assert result["details"]["evidence"][0]["evidence_id"] == "e.step.1"
    assert result["details"]["fact_lock_set"]["set_id"] == "platform-lock-set:trace"
    assert result["details"]["fact_lock_reference"]["owner"] == "platform.science.fact_lock"
    assert result["details"]["validation_report"]["proof_checked"] is True
    assert result["details"]["validation_report"]["generated_steps_are_not_proof"] is True
    assert result["details"]["step_trace"][0]["step_id"] == "s1"


def test_symbolic_equivalence_only_passes_when_deterministic_rules_can_decide() -> None:
    pack, _ = _loaded_pack()

    equivalent = pack.validate_claim(
        {
            "claim_id": "claim.eq",
            "claim_type": "symbolic_equivalence",
            "left": "a + b",
            "right": "b + a",
            "definition_version": "elementary-algebra-v1",
            "quantifiers": [],
            "assumptions": [],
            "variables": {"a": "real", "b": "real"},
        },
        [],
    )
    undecidable = pack.validate_claim(
        {
            "claim_id": "claim.eq-unknown",
            "claim_type": "symbolic_equivalence",
            "left": "sin(x)",
            "right": "x",
            "definition_version": "elementary-analysis-v1",
            "quantifiers": [],
            "assumptions": [],
            "variables": {"x": "real"},
        },
        [],
    )

    assert equivalent["status"] == "verified"
    assert undecidable["status"] == "needs_human"
    assert "equivalence_undecidable" in undecidable["reason_codes"]


def test_domain_pack_does_not_accept_self_reported_proof_or_nonfinite_bound() -> None:
    pack, _ = _loaded_pack()

    self_reported_counterexample = pack.validate_claim(
        {
            "claim_id": "claim.self-report",
            "claim_type": "counterexample",
            "candidate": "x = 0",
            "definition_version": "algebra-v1",
            "quantifiers": [],
            "assumptions": [],
            "counterexample_verified": True,
        },
        [],
    )
    nonfinite_bound = pack.validate_claim(
        {
            "claim_id": "claim.nan-bound",
            "claim_type": "numeric_bound",
            "value": float("nan"),
            "lower": 0,
            "upper": 1,
            "definition_version": "analysis-v1",
            "quantifiers": [],
            "assumptions": [],
        },
        [],
    )

    assert self_reported_counterexample["status"] == "needs_human"
    assert nonfinite_bound["status"] == "blocked"
    assert "numeric_bound_failed" in nonfinite_bound["reason_codes"]
