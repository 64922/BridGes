"""T040 public-seam tests for domain-pack loading and fixture validation."""

from __future__ import annotations

from typing import Any

import pytest

from science_companion.ai.capability_registry import CapabilityRegistry
from science_companion.contracts.ai import CapabilityKind, CapabilityRecord
from science_companion.contracts.domain import (
    DomainClaimSchema,
    DomainCompatibility,
    DomainEvaluationResult,
    DomainInvalidationPolicy,
    DomainManifestSignature,
    DomainPackManifest,
    DomainPackStatus,
    DomainRollbackPolicy,
    DomainRule,
    DomainRunStatus,
    DomainSourcePolicy,
    DomainWordingPolicy,
    FixtureCase,
    PlatformSafetyFloor,
    ValidatorRequirement,
)
from science_companion.domain import (
    DomainPackLoader,
    DomainPackLoadError,
    DomainPackRegistry,
    DomainPackValidationRuntime,
)


class DemoDomainPack:
    """Small deterministic pack implementation used through the public protocol."""

    def __init__(self, manifest: DomainPackManifest, *, result_status: str | None = None) -> None:
        self.manifest = manifest
        self.result_status = result_status
        self.calls: list[str] = []

    def classify_question(self, question_context: Any) -> str:
        self.calls.append("classify_question")
        return str(question_context.get("question_type", "measurement"))

    def plan_sources(self, question_type: str, risk_tier: str) -> dict[str, Any]:
        self.calls.append("plan_sources")
        return {"question_type": question_type, "risk_tier": risk_tier}

    def normalize_metadata(self, adapter_record: Any) -> dict[str, Any]:
        self.calls.append("normalize_metadata")
        return dict(adapter_record or {})

    def resolve_version_status(self, records: Any) -> dict[str, Any]:
        self.calls.append("resolve_version_status")
        return {"status": "active", "records": list(records or [])}

    def parse_domain_structure(self, document: Any) -> dict[str, Any]:
        self.calls.append("parse_domain_structure")
        return dict(document or {})

    def extract_claim_schema(self, content: Any) -> dict[str, Any]:
        self.calls.append("extract_claim_schema")
        return {"content": content}

    def assess_evidence(self, claim: Any, evidence_set: Any) -> dict[str, Any]:
        self.calls.append("assess_evidence")
        return {"claim": claim, "evidence": list(evidence_set or [])}

    def detect_conflicts(self, claim_set: Any, evidence_set: Any) -> list[Any]:
        self.calls.append("detect_conflicts")
        return []

    def constrain_wording(self, assessment: Any, audience: str, genre: str) -> dict[str, Any]:
        self.calls.append("constrain_wording")
        return {"assessment": assessment, "audience": audience, "genre": genre}

    def validate_claim(self, claim: Any, evidence_set: Any) -> dict[str, Any]:
        self.calls.append("validate_claim")
        return {"status": "verified", "claim": claim, "evidence": list(evidence_set or [])}

    def evaluate(
        self, run_artifacts: dict[str, Any], fixture_set: list[FixtureCase]
    ) -> DomainEvaluationResult:
        self.calls.append("evaluate")
        fixture = fixture_set[0]
        return DomainEvaluationResult(
            status=self.result_status or fixture.expected_status,
            reason_codes=list(fixture.expected_reason_codes),
            rule_ids=list(fixture.expected_rule_ids),
            validator_ids=list(fixture.expected_validator_ids),
            details={"artifact_keys": sorted(run_artifacts)},
        )


def _manifest(
    *,
    fixture: FixtureCase | None = None,
    platform_floor: PlatformSafetyFloor | None = None,
    validator: ValidatorRequirement | None = None,
) -> DomainPackManifest:
    fixture = fixture or FixtureCase(
        fixture_id="fixture.measurement.pass",
        question_type="measurement",
        input_snapshot={
            "question_type": "measurement",
            "metadata": {"source": "fixture"},
            "source_records": [{"version": "1"}],
            "document": {"section": "result"},
            "content": "1 m = 100 cm",
            "claim": {"text": "1 m = 100 cm"},
            "evidence_set": [{"relation": "supports"}],
            "audience": "student",
            "genre": "explanation",
        },
        expected_status="verified",
        expected_rule_ids=["rule.measurement.units"],
        expected_validator_ids=["validator.units"],
    )
    validator = validator or ValidatorRequirement(
        validator_id="validator.units",
        capability_name="unit_dimension_check",
        capability_version="1",
        input_schema_version="1",
        output_schema_version="1",
        fixture_ids=[fixture.fixture_id],
    )
    manifest = DomainPackManifest(
        id="physics.measurement",
        version="1.0.0",
        platform_api=">=1.0,<2.0",
        pack_api="domain-pack/v1",
        scope=["physical measurement education"],
        exclusions=["dangerous laboratory operation"],
        languages=["zh-CN"],
        disciplines=["physics"],
        risk_tiers=["general_education"],
        question_types=["measurement"],
        source_policies=[
            DomainSourcePolicy(
                policy_id="physics.sources",
                applies_to=["measurement"],
                evidence_requirements=["supporting_source"],
            )
        ],
        claim_schemas=[
            DomainClaimSchema(
                schema_id="physics.measurement.claim",
                applies_to=["measurement"],
                required_fields=["text"],
                evidence_requirements=["supporting_source"],
            )
        ],
        wording_policy=[
            DomainWordingPolicy(
                policy_id="physics.measurement.wording",
                applies_to=["measurement"],
                allowed_statuses=["verified", "needs_human"],
            )
        ],
        rules=[
            DomainRule(
                rule_id="rule.measurement.units",
                rule_schema_version="1",
                applies_to=["measurement"],
                explanation="单位必须可归一化并保持量纲一致。",
                fixture_ids=[fixture.fixture_id],
            )
        ],
        validators=[validator],
        fixtures=[fixture],
        dependencies=[],
        platform_floor=platform_floor or PlatformSafetyFloor(),
    )
    manifest.content_digest = DomainPackLoader.manifest_digest(manifest)
    return manifest


def _registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="unit_dimension_check",
            version="1",
            kind=CapabilityKind.TOOL,
            vendor="local",
            region="local",
            input_schema_version="1",
            output_schema_version="1",
        )
    )
    return registry


def test_loader_accepts_candidate_and_validates_registered_capability() -> None:
    manifest = _manifest()
    pack = DemoDomainPack(manifest)

    loaded = DomainPackLoader(
        capability_registry=_registry(),
        require_capability_contracts=True,
    ).load(manifest, pack)

    assert loaded.manifest.id == "physics.measurement"
    assert len(loaded.manifest_digest) == 64
    assert loaded.implementation is pack


def test_loader_rejects_pack_that_weakens_platform_safety_floor() -> None:
    manifest = _manifest(
        platform_floor=PlatformSafetyFloor(
            authorization_required=False,
        )
    )

    with pytest.raises(DomainPackLoadError, match="平台安全下限"):
        DomainPackLoader(capability_registry=_registry()).load(
            manifest,
            DemoDomainPack(manifest),
        )


def test_runtime_runs_protocol_pipeline_and_passes_expected_fixture() -> None:
    manifest = _manifest()
    pack = DemoDomainPack(manifest)
    loaded = DomainPackLoader(
        capability_registry=_registry(),
        require_capability_contracts=True,
    ).load(manifest, pack)

    report = DomainPackValidationRuntime().run(loaded)

    assert report.status == DomainRunStatus.PASSED
    assert report.fixture_results[0].passed is True
    assert report.fixture_results[0].actual_status == "verified"
    assert "validate_claim" in pack.calls
    assert "evaluate" in pack.calls


def test_runtime_exposes_blocked_fixture_reason_when_candidate_disagrees() -> None:
    fixture = FixtureCase(
        fixture_id="fixture.measurement.blocked",
        question_type="measurement",
        input_snapshot={"question_type": "measurement"},
        expected_status="blocked",
        expected_reason_codes=["missing_evidence"],
    )
    manifest = _manifest(fixture=fixture)
    pack = DemoDomainPack(manifest, result_status="verified")
    loaded = DomainPackLoader(capability_registry=_registry()).load(manifest, pack)

    report = DomainPackValidationRuntime().run(loaded)

    assert report.status == DomainRunStatus.FAILED
    assert report.fixture_results[0].passed is False
    assert "expected_status=blocked" in report.fixture_results[0].reason


def test_runtime_reports_human_gate_without_treating_it_as_silent_success() -> None:
    fixture = FixtureCase(
        fixture_id="fixture.measurement.human",
        question_type="measurement",
        input_snapshot={"question_type": "measurement"},
        expected_status="needs_human",
        requires_human=True,
    )
    manifest = _manifest(fixture=fixture)
    pack = DemoDomainPack(manifest, result_status="needs_human")
    loaded = DomainPackLoader(capability_registry=_registry()).load(manifest, pack)

    report = DomainPackValidationRuntime().run(loaded)

    assert report.status == DomainRunStatus.NEEDS_HUMAN
    assert report.fixture_results[0].status.value == "needs_human"
    assert report.fixture_results[0].passed is True


def test_loader_rejects_declared_digest_that_does_not_match_manifest() -> None:
    manifest = _manifest()
    manifest.content_digest = "0" * 64

    with pytest.raises(DomainPackLoadError, match="内容摘要"):
        DomainPackLoader(capability_registry=_registry()).load(
            manifest, DemoDomainPack(manifest)
        )


def test_registry_keeps_two_compatible_versions_and_selects_exact_lock() -> None:
    registry = DomainPackRegistry(
        DomainPackLoader(
            capability_registry=_registry(),
            require_capability_contracts=True,
        )
    )
    first = _manifest()
    registry.register(DemoDomainPack(first))
    second = first.model_copy(
        update={
            "version": "1.1.0",
            "rollback_versions": ["1.0.0"],
            "compatibility": DomainCompatibility(compatible_with=["1.0.0"]),
            "rollback_policy": DomainRollbackPolicy(trusted_versions=["1.0.0"]),
            "invalidation_policy": DomainInvalidationPolicy(strategy="revalidate"),
        }
    )
    second.content_digest = DomainPackLoader.manifest_digest(second)
    registry.register(DemoDomainPack(second))

    assert registry.list_versions(first.id) == ["1.0.0", "1.1.0"]
    assert registry.get(first.id, "1.0.0").pack_version == "1.0.0"
    assert registry.get(first.id).pack_version == "1.1.0"


def test_loader_rejects_dangling_fixture_reference() -> None:
    manifest = _manifest()
    manifest.rules[0].fixture_ids = ["fixture.missing"]
    manifest.content_digest = DomainPackLoader.manifest_digest(manifest)

    with pytest.raises(DomainPackLoadError, match="夹具引用"):
        DomainPackLoader(capability_registry=_registry()).load(
            manifest, DemoDomainPack(manifest)
        )


def test_loader_rejects_question_type_without_claim_evidence_wording_coverage() -> None:
    manifest = _manifest().model_copy(
        update={
            "source_policies": [DomainSourcePolicy(policy_id="physics.sources")],
        }
    )
    manifest.content_digest = DomainPackLoader.manifest_digest(manifest)

    with pytest.raises(DomainPackLoadError, match="来源策略"):
        DomainPackLoader(capability_registry=_registry()).load(
            manifest, DemoDomainPack(manifest)
        )


def test_upgrade_report_requires_compatibility_and_trusted_rollback() -> None:
    previous = _manifest()
    candidate = previous.model_copy(update={"version": "1.1.0"})

    report = DomainPackLoader().validate_upgrade(previous, candidate)

    assert report.compatible is False
    assert report.compatibility_declared is False
    assert report.rollback_trusted is False
    assert any("兼容" in issue for issue in report.issues)
    assert any("回滚" in issue for issue in report.issues)


def test_loader_requires_manifest_digest_and_fail_closed_capability_registry() -> None:
    manifest = _manifest()
    manifest.content_digest = None

    with pytest.raises(DomainPackLoadError, match="内容摘要"):
        DomainPackLoader(capability_registry=_registry()).load(
            manifest, DemoDomainPack(manifest)
        )

    with pytest.raises(DomainPackLoadError, match="能力注册表"):
        DomainPackLoader().load(_manifest(), DemoDomainPack(_manifest()))


def test_loader_requires_signature_coverage_for_active_manifest() -> None:
    manifest = _manifest()
    manifest.lifecycle_status = DomainPackStatus.ACTIVE
    manifest.content_digest = DomainPackLoader.manifest_digest(manifest)

    with pytest.raises(DomainPackLoadError, match="签名"):
        DomainPackLoader(capability_registry=_registry()).load(
            manifest, DemoDomainPack(manifest)
        )

    manifest.signatures = [
        DomainManifestSignature(
            signer_id="reviewer-1",
            signature="signed-value",
            signed_digest=manifest.content_digest or "",
            scope=["manifest_digest"],
        )
    ]
    loaded = DomainPackLoader(capability_registry=_registry()).load(
        manifest, DemoDomainPack(manifest)
    )
    assert loaded.pack_id == manifest.id


def test_runtime_compares_fixture_expected_wording() -> None:
    fixture = FixtureCase(
        fixture_id="fixture.measurement.wording",
        question_type="measurement",
        input_snapshot={"question_type": "measurement"},
        expected_status="verified",
        expected_wording={"strength": "qualified"},
    )
    manifest = _manifest(fixture=fixture)
    loaded = DomainPackLoader(capability_registry=_registry()).load(
        manifest, DemoDomainPack(manifest)
    )

    report = DomainPackValidationRuntime().run(loaded)

    assert report.status == DomainRunStatus.FAILED
    assert "措辞" in report.fixture_results[0].reason
