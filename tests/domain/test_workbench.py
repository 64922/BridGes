"""T046 领域包专家工作台的公开接缝测试。

建议接缝：维护者、独立复核者和发行者分别完成同一包版本的编辑、语义
Diff、夹具、签名和灰度，职责冲突被拒绝。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bridges.contracts.domain import (
    AttestationConclusion,
    DomainClaimSchema,
    DomainPackManifest,
    DomainPackStatus,
    DomainRule,
    DomainSourcePolicy,
    DomainWordingPolicy,
    FixtureCase,
    GrayReleaseStatus,
    QualificationRecord,
    ReviewRole,
    SemanticDiffChange,
    SemanticSignificance,
    ValidatorRequirement,
    WorkbenchStage,
)
from bridges.domain import (
    DomainPackLoader,
    DomainPackRegistry,
    DomainPackValidationRuntime,
)
from bridges.domain.workbench import (
    DomainPackWorkbenchError,
    DomainPackWorkbenchService,
    compute_semantic_diff_between,
)


class MinimalDomainPack:
    """Deterministic pack implementation for workbench tests."""

    def __init__(self, manifest: DomainPackManifest) -> None:
        self.manifest = manifest

    def classify_question(self, question_context: Any) -> str:
        return str(question_context.get("question_type", "measurement"))

    def plan_sources(self, question_type: str, risk_tier: str) -> dict[str, Any]:
        return {"question_type": question_type, "risk_tier": risk_tier}

    def normalize_metadata(self, adapter_record: Any) -> dict[str, Any]:
        return dict(adapter_record or {})

    def resolve_version_status(self, records: Any) -> dict[str, Any]:
        return {"status": "active", "records": list(records or [])}

    def parse_domain_structure(self, document: Any) -> dict[str, Any]:
        return dict(document or {})

    def extract_claim_schema(self, content: Any) -> dict[str, Any]:
        return {"content": content}

    def assess_evidence(self, claim: Any, evidence_set: Any) -> dict[str, Any]:
        return {"claim": claim, "evidence": list(evidence_set or [])}

    def detect_conflicts(self, claim_set: Any, evidence_set: Any) -> list[Any]:
        return []

    def constrain_wording(self, assessment: Any, audience: str, genre: str) -> dict[str, Any]:
        return {"assessment": assessment, "audience": audience, "genre": genre}

    def validate_claim(self, claim: Any, evidence_set: Any) -> dict[str, Any]:
        return {"status": "verified", "claim": claim}

    def evaluate(
        self, run_artifacts: dict[str, Any], fixture_set: list[FixtureCase]
    ) -> dict[str, Any]:
        fixture = fixture_set[0]
        return {
            "status": fixture.expected_status,
            "reason_codes": list(fixture.expected_reason_codes),
            "rule_ids": list(fixture.expected_rule_ids),
            "validator_ids": list(fixture.expected_validator_ids),
        }


def _fixture(
    fixture_id: str,
    expected_status: str,
    *,
    kind: str = "positive",
    requires_human: bool = False,
) -> FixtureCase:
    return FixtureCase(
        fixture_id=fixture_id,
        kind=kind,
        question_type="measurement",
        risk_tier="general_education",
        input_snapshot={
            "question_type": "measurement",
            "content": "1 m = 100 cm",
            "claim": {"text": "1 m = 100 cm"},
            "evidence_set": [{"relation": "supports"}],
        },
        expected_status=expected_status,
        expected_rule_ids=["rule.units"],
        requires_human=requires_human,
        rationale="工作台测试夹具",
    )


def build_manifest(
    *,
    version: str = "1.0.0",
    human_gate: str | None = None,
    rule_conditions: dict[str, Any] | None = None,
    wording_forbidden: list[str] | None = None,
    fixture_statuses: list[tuple[str, str, dict[str, bool]]] | None = None,
    source_failure: str = "block",
) -> DomainPackManifest:
    """构造通过加载器预检的最小包 Manifest。"""
    fixtures = []
    if fixture_statuses is None:
        fixture_statuses = [
            ("fx.pass", "verified", {}),
            ("fx.blocked", "blocked", {}),
            ("fx.human", "needs_human", {"requires_human": True}),
        ]
    for fixture_id, status, options in fixture_statuses:
        fixtures.append(
            _fixture(
                fixture_id,
                status,
                kind="boundary" if status != "verified" else "positive",
                requires_human=bool(options.get("requires_human", False)),
            )
        )
    manifest = DomainPackManifest(
        manifest_schema="domain-pack-manifest/v1",
        id="workbench.test-pack",
        version=version,
        platform_api=">=1.0,<2.0",
        pack_api="domain-pack/v1",
        scope=["测量与单位"],
        exclusions=["危险实验指导"],
        risk_tiers=["general_education"],
        question_types=["measurement"],
        source_policies=[
            DomainSourcePolicy(
                policy_id="source.measurement",
                applies_to=["measurement"],
                allowed_source_roles=["standard", "handbook"],
                on_failure=source_failure,
            )
        ],
        claim_schemas=[
            DomainClaimSchema(
                schema_id="claim.measurement",
                applies_to=["measurement"],
                required_fields=["value", "unit"],
            )
        ],
        wording_policy=[
            DomainWordingPolicy(
                policy_id="wording.measurement",
                applies_to=["measurement"],
                allowed_statuses=["verified", "qualified"],
                forbidden_strengths=wording_forbidden or ["精确等于"],
                required_disclosures=["不确定度"],
            )
        ],
        validators=[
            ValidatorRequirement(
                validator_id="validator.units",
                capability_name="unit_dimension_check",
                capability_version="1",
                input_schema_version="1",
                output_schema_version="1",
            )
        ],
        rules=[
            DomainRule(
                rule_id="rule.units",
                applies_to=["measurement"],
                conditions=rule_conditions or {"dimension": "length"},
                outputs={"status": "verified"},
                rejection_code="unit_mismatch",
                explanation="单位必须量纲一致",
                fixture_ids=[fixture.fixture_id for fixture in fixtures],
                human_gate=human_gate,
            )
        ],
        fixtures=fixtures,
        lifecycle_status="draft",
        platform_floor={"authorization_required": True, "allow_network_access": False},
        compatibility={
            "compatible_with": ["1.0.0"],
            "preserves_runtime_contract": True,
            "requires_revalidation": True,
        },
        rollback_versions=["1.0.0"],
        rollback_policy={
            "trusted_versions": ["1.0.0"],
            "requires_fixture_replay": True,
            "preserves_history": True,
        },
    )
    digest = DomainPackLoader.manifest_digest(manifest)
    return manifest.model_copy(update={"content_digest": digest})


@pytest.fixture
def workbench() -> tuple[DomainPackWorkbenchService, DomainPackManifest]:
    """登记一个测试包的工作台环境。"""
    loader = DomainPackLoader(require_capability_contracts=False)
    registry = DomainPackRegistry(loader)
    pack = MinimalDomainPack(build_manifest())
    loaded = registry.register(pack)
    service = DomainPackWorkbenchService(
        registry, runtime=DomainPackValidationRuntime(loader), loader=loader
    )
    return service, loaded.manifest


def _qualify(
    service: DomainPackWorkbenchService, person_id: str, manifest: DomainPackManifest
) -> None:
    service.register_qualification(
        QualificationRecord(
            qualification_id=f"q-{person_id}",
            person_id=person_id,
            qualification_type="domain_expert",
            verifier_id="verifier",
            disciplines=list(manifest.disciplines),
            verified_at=datetime.now(UTC),
            valid_until=datetime.now(UTC) + timedelta(days=365),
        )
    )


def _declare_all(
    service: DomainPackWorkbenchService,
    manifest: DomainPackManifest,
    persons: list[str],
) -> None:
    for person in persons:
        service.declare_conflict_of_interest(
            person, manifest.id, manifest.version, ["无相关利益"]
        )


def _complete_signatures(
    service: DomainPackWorkbenchService,
    manifest: DomainPackManifest,
    maintainer: str,
    reviewer: str,
    releaser: str,
) -> None:
    """维护者、复核者、发行者走完三签与灰度。"""
    service.submit_content_signature(
        maintainer,
        manifest.id,
        manifest.version,
        conclusion=AttestationConclusion.APPROVE,
        opinion="内容完整",
    )
    service.assign_reviewer(maintainer, manifest.id, manifest.version, reviewer)
    service.assign_releaser(maintainer, manifest.id, manifest.version, releaser)
    service.submit_independent_signature(
        reviewer,
        manifest.id,
        manifest.version,
        conclusion=AttestationConclusion.APPROVE,
        opinion="独立复核通过",
    )
    service.prepare_gray_release(reviewer, manifest.id, manifest.version)


class TestH3JointGate:
    """H3 高风险变更必须由安全治理责任人联合确认后才能发行。"""

    def _complete_h3_flow(
        self,
        service: DomainPackWorkbenchService,
        manifest: DomainPackManifest,
    ) -> None:
        service.register_pack("maintainer", service.get_loaded(manifest.id, manifest.version))
        _qualify(service, "reviewer", manifest)
        _declare_all(service, manifest, ["maintainer", "reviewer", "releaser"])
        _complete_signatures(service, manifest, "maintainer", "reviewer", "releaser")

    def test_h3_declared_pack_release_requires_security_confirmation(
        self,
    ) -> None:
        loader = DomainPackLoader(require_capability_contracts=False)
        registry = DomainPackRegistry(loader)
        manifest = build_manifest(human_gate="H3")
        registry.register(MinimalDomainPack(manifest))
        service = DomainPackWorkbenchService(
            registry, runtime=DomainPackValidationRuntime(loader), loader=loader
        )
        self._complete_h3_flow(service, manifest)
        with pytest.raises(DomainPackWorkbenchError) as exc_info:
            service.release("releaser", manifest.id, manifest.version)
        assert exc_info.value.code == "h3_security_required"
        # 未确认前治理记录仍是 DRAFT，未激活。
        assert service.get_record(manifest.id, manifest.version).lifecycle_status == (
            DomainPackStatus.DRAFT
        )

    def test_h3_confirmation_enables_release(self) -> None:
        loader = DomainPackLoader(require_capability_contracts=False)
        registry = DomainPackRegistry(loader)
        manifest = build_manifest(human_gate="H3")
        registry.register(MinimalDomainPack(manifest))
        service = DomainPackWorkbenchService(
            registry, runtime=DomainPackValidationRuntime(loader), loader=loader
        )
        self._complete_h3_flow(service, manifest)
        confirmation = service.confirm_h3_joint_gate(
            "sec-admin", manifest.id, manifest.version, opinion="安全门已复核"
        )
        assert confirmation.confirmed_by == "sec-admin"
        release = service.release("releaser", manifest.id, manifest.version)
        assert release.canonical_digest == service.get_record(
            manifest.id, manifest.version
        ).canonical_digest
        assert service.get_record(manifest.id, manifest.version).lifecycle_status == (
            DomainPackStatus.ACTIVE
        )

    def test_confirmation_rejected_for_non_h3_pack(self) -> None:
        loader = DomainPackLoader(require_capability_contracts=False)
        registry = DomainPackRegistry(loader)
        manifest = build_manifest()  # 无 H3 规则
        loaded = registry.register(MinimalDomainPack(manifest))
        service = DomainPackWorkbenchService(
            registry, runtime=DomainPackValidationRuntime(loader), loader=loader
        )
        service.register_pack("maintainer", loaded)
        with pytest.raises(DomainPackWorkbenchError) as exc_info:
            service.confirm_h3_joint_gate(
                "sec-admin", manifest.id, manifest.version, opinion="不适用"
            )
        assert exc_info.value.code == "h3_not_declared"

    def test_duplicate_h3_confirmation_rejected(self) -> None:
        loader = DomainPackLoader(require_capability_contracts=False)
        registry = DomainPackRegistry(loader)
        manifest = build_manifest(human_gate="H3")
        loaded = registry.register(MinimalDomainPack(manifest))
        service = DomainPackWorkbenchService(
            registry, runtime=DomainPackValidationRuntime(loader), loader=loader
        )
        service.register_pack("maintainer", loaded)
        service.confirm_h3_joint_gate("sec-admin", manifest.id, manifest.version)
        with pytest.raises(DomainPackWorkbenchError) as exc_info:
            service.confirm_h3_joint_gate("sec-admin", manifest.id, manifest.version)
        assert exc_info.value.code == "already_confirmed"


class TestThreeSignatureChain:
    def test_three_signatures_bind_same_canonical_digest(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        service, manifest = workbench
        service.register_pack("maintainer", service.get_loaded(manifest.id, manifest.version))
        _qualify(service, "reviewer", manifest)
        _declare_all(service, manifest, ["maintainer", "reviewer", "releaser"])
        _complete_signatures(service, manifest, "maintainer", "reviewer", "releaser")

        release = service.release("releaser", manifest.id, manifest.version)
        record = service.get_record(manifest.id, manifest.version)
        assert record.stage == WorkbenchStage.RELEASED
        assert record.lifecycle_status.value == "active"
        assert all(
            item.canonical_digest == release.canonical_digest
            for item in release.attestations
        )
        assert release.platform_attestation.canonical_digest == release.canonical_digest
        roles = {item.role for item in release.attestations}
        assert roles == {ReviewRole.CONTENT, ReviewRole.INDEPENDENT, ReviewRole.PLATFORM}

    def test_content_change_invalidates_old_signatures(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        """任一内容变化都会产生新摘要，使旧签名只适用于登记时内容。"""
        service, manifest = workbench
        service.register_pack("maintainer", service.get_loaded(manifest.id, manifest.version))
        _qualify(service, "reviewer", manifest)
        _declare_all(service, manifest, ["maintainer", "reviewer", "releaser"])
        _complete_signatures(service, manifest, "maintainer", "reviewer", "releaser")

        # 内容不同的新版本必须重新走完整个工作台流程，旧签名不适用于它。
        changed = build_manifest(version="1.0.1", rule_conditions={"dimension": "time"})
        changed_loaded = service._registry.register(MinimalDomainPack(changed))
        service.register_pack("maintainer", changed_loaded)
        old_record = service.get_record(manifest.id, manifest.version)
        new_record = service.get_record(manifest.id, changed.version)
        assert old_record.canonical_digest != new_record.canonical_digest
        assert old_record.attestations  # 旧签名保留在旧版本
        assert not new_record.attestations  # 新版本尚未签名

    def test_release_requires_gray_candidate(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        service, manifest = workbench
        service.register_pack("maintainer", service.get_loaded(manifest.id, manifest.version))
        _qualify(service, "reviewer", manifest)
        _declare_all(service, manifest, ["maintainer", "reviewer", "releaser"])
        service.submit_content_signature(
            "maintainer",
            manifest.id,
            manifest.version,
            conclusion=AttestationConclusion.APPROVE,
            opinion="内容完整",
        )
        service.assign_reviewer("maintainer", manifest.id, manifest.version, "reviewer")
        service.assign_releaser("maintainer", manifest.id, manifest.version, "releaser")
        service.submit_independent_signature(
            "reviewer",
            manifest.id,
            manifest.version,
            conclusion=AttestationConclusion.APPROVE,
            opinion="独立复核通过",
        )
        with pytest.raises(DomainPackWorkbenchError) as exc:
            service.release("releaser", manifest.id, manifest.version)
        assert exc.value.code == "gray_required"

    def test_gray_does_not_activate(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        """灰度只生成可发行候选，不能自动激活。"""
        service, manifest = workbench
        service.register_pack("maintainer", service.get_loaded(manifest.id, manifest.version))
        _qualify(service, "reviewer", manifest)
        _declare_all(service, manifest, ["maintainer", "reviewer", "releaser"])
        _complete_signatures(service, manifest, "maintainer", "reviewer", "releaser")

        record = service.get_record(manifest.id, manifest.version)
        assert record.gray_candidate is not None
        assert record.gray_candidate.status == GrayReleaseStatus.READY_TO_RELEASE
        assert record.stage == WorkbenchStage.GRAY_RELEASE_READY
        assert record.lifecycle_status.value == "draft"  # 未自动激活


class TestActivationPersistence:
    def test_release_persists_active_manifest_with_transparent_record(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        service, manifest = workbench
        service.register_pack("maintainer", service.get_loaded(manifest.id, manifest.version))
        _qualify(service, "reviewer", manifest)
        _declare_all(service, manifest, ["maintainer", "reviewer", "releaser"])
        _complete_signatures(service, manifest, "maintainer", "reviewer", "releaser")

        service.release("releaser", manifest.id, manifest.version)
        active = service.get_active_manifest(manifest.id, manifest.version)
        assert active is not None
        assert active.lifecycle_status.value == "active"
        assert len(active.signatures) == 3
        assert all(
            signature.signed_digest
            == DomainPackLoader.manifest_digest(active)
            for signature in active.signatures
        )
        record = service.get_record(manifest.id, manifest.version)
        assert record.release is not None
        assert record.release.transparent_record["supersedes"] is None
        assert record.release.transparent_record["revalidation_required"] is True
        assert (
            record.release.transparent_record["active_manifest_digest"]
            == DomainPackLoader.manifest_digest(active)
        )


class TestRoleConflict:
    def test_same_person_cannot_be_maintainer_and_reviewer(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        service, manifest = workbench
        loaded = service.get_loaded(manifest.id, manifest.version)
        service.register_pack("maintainer", loaded)
        _qualify(service, "reviewer", manifest)
        _declare_all(service, manifest, ["maintainer", "reviewer", "releaser"])
        service.submit_content_signature(
            "maintainer",
            manifest.id,
            manifest.version,
            conclusion=AttestationConclusion.APPROVE,
            opinion="内容完整",
        )
        with pytest.raises(DomainPackWorkbenchError) as exc:
            service.assign_reviewer("maintainer", manifest.id, manifest.version, "maintainer")
        assert exc.value.code == "role_conflict"
        # 同人直接提交独立验证也被拒绝。
        with pytest.raises(DomainPackWorkbenchError) as exc:
            service.submit_independent_signature(
                "maintainer",
                manifest.id,
                manifest.version,
                conclusion=AttestationConclusion.APPROVE,
                opinion="冒充复核",
            )
        assert exc.value.code == "role_conflict"

    def test_releaser_cannot_also_be_maintainer(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        service, manifest = workbench
        loaded = service.get_loaded(manifest.id, manifest.version)
        service.register_pack("maintainer", loaded)
        _declare_all(service, manifest, ["maintainer", "reviewer", "releaser"])
        service.submit_content_signature(
            "maintainer",
            manifest.id,
            manifest.version,
            conclusion=AttestationConclusion.APPROVE,
            opinion="内容完整",
        )
        service.assign_reviewer("maintainer", manifest.id, manifest.version, "reviewer")
        with pytest.raises(DomainPackWorkbenchError) as exc:
            service.assign_releaser("maintainer", manifest.id, manifest.version, "maintainer")
        assert exc.value.code == "role_conflict"

    def test_reviewer_cannot_also_be_releaser_when_releaser_assigned_first(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        """先分配发行者再分配复核者时，同人双向都被拒绝。"""
        service, manifest = workbench
        service.register_pack("maintainer", service.get_loaded(manifest.id, manifest.version))
        _declare_all(service, manifest, ["maintainer", "personX"])
        service.submit_content_signature(
            "maintainer",
            manifest.id,
            manifest.version,
            conclusion=AttestationConclusion.APPROVE,
            opinion="内容完整",
        )
        service.assign_releaser("maintainer", manifest.id, manifest.version, "personX")
        with pytest.raises(DomainPackWorkbenchError) as exc:
            service.assign_reviewer("maintainer", manifest.id, manifest.version, "personX")
        assert exc.value.code == "role_conflict"

    def test_reviewer_needs_qualification(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        service, manifest = workbench
        service.register_pack("maintainer", service.get_loaded(manifest.id, manifest.version))
        _declare_all(service, manifest, ["maintainer", "reviewer", "releaser"])
        service.submit_content_signature(
            "maintainer",
            manifest.id,
            manifest.version,
            conclusion=AttestationConclusion.APPROVE,
            opinion="内容完整",
        )
        service.assign_reviewer("maintainer", manifest.id, manifest.version, "reviewer")
        with pytest.raises(DomainPackWorkbenchError) as exc:
            service.submit_independent_signature(
                "reviewer",
                manifest.id,
                manifest.version,
                conclusion=AttestationConclusion.APPROVE,
                opinion="无资质复核",
            )
        assert exc.value.code == "qualification_required"

    def test_signing_requires_conflict_declaration(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        service, manifest = workbench
        service.register_pack("maintainer", service.get_loaded(manifest.id, manifest.version))
        with pytest.raises(DomainPackWorkbenchError) as exc:
            service.submit_content_signature(
                "maintainer",
                manifest.id,
                manifest.version,
                conclusion=AttestationConclusion.APPROVE,
                opinion="未声明",
            )
        assert exc.value.code == "conflict_required"


class TestContentChangeInvalidation:
    def test_content_change_rejects_all_stale_gates(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        """登记后内容变化使旧摘要失效：签名、灰度与发行全部拒绝。"""
        service, manifest = workbench
        service.register_pack("maintainer", service.get_loaded(manifest.id, manifest.version))
        _qualify(service, "reviewer", manifest)
        _declare_all(service, manifest, ["maintainer", "reviewer", "releaser"])
        _complete_signatures(service, manifest, "maintainer", "reviewer", "releaser")

        # 模拟登记后内容变化（registry 中 Manifest 被修改）。
        loaded = service.get_loaded(manifest.id, manifest.version)
        loaded.manifest.rules[0].conditions = {"dimension": "time"}

        with pytest.raises(DomainPackWorkbenchError) as exc:
            service.submit_independent_signature(
                "reviewer",
                manifest.id,
                manifest.version,
                conclusion=AttestationConclusion.APPROVE,
                opinion="重签",
            )
        assert exc.value.code == "digest_mismatch"
        with pytest.raises(DomainPackWorkbenchError) as exc:
            service.prepare_gray_release("reviewer", manifest.id, manifest.version)
        assert exc.value.code == "digest_mismatch"
        with pytest.raises(DomainPackWorkbenchError) as exc:
            service.release("releaser", manifest.id, manifest.version)
        assert exc.value.code == "digest_mismatch"


class TestSemanticDiff:
    def test_diff_shows_rule_wording_human_gate_source_fixture_changes(self) -> None:
        previous = build_manifest(
            version="1.0.0",
            human_gate=None,
            wording_forbidden=["精确等于"],
            fixture_statuses=[
                ("fx.pass", "verified", {}),
                ("fx.blocked", "blocked", {}),
                ("fx.human", "needs_human", {"requires_human": True}),
            ],
        )
        current = build_manifest(
            version="1.1.0",
            human_gate="h2",
            rule_conditions={"dimension": "time"},
            wording_forbidden=["精确等于", "一定正确"],
            fixture_statuses=[
                ("fx.pass", "verified", {}),
                ("fx.blocked", "verified", {}),  # BLOCKED 翻转
                ("fx.human", "needs_human", {"requires_human": True}),
                ("fx.extra", "blocked", {}),
            ],
            source_failure="needs_human",
        )
        diff = compute_semantic_diff_between(previous, current)
        categories = {entry.category for entry in diff.entries}
        assert "rule" in categories
        assert "wording" in categories
        assert "human_gate" in categories
        assert "source" in categories
        assert "fixture" in categories

        by_item = {(entry.category, entry.item_id): entry for entry in diff.entries}
        gate = by_item[("human_gate", "rule.units")]
        assert gate.change == SemanticDiffChange.MODIFIED
        assert "人工门升高" in gate.impact
        assert gate.significance == SemanticSignificance.HIGH

        flip = by_item[("fixture", "fx.blocked")]
        assert flip.change == SemanticDiffChange.MODIFIED
        assert flip.old_value == "blocked"
        assert flip.new_value == "verified"
        assert "BLOCKED 变为可发布" in flip.impact
        assert flip.significance == SemanticSignificance.HIGH

        added = by_item[("fixture", "fx.extra")]
        assert added.change == SemanticDiffChange.ADDED

    def test_diff_shows_claim_schema_changes(self) -> None:
        previous = build_manifest(version="1.0.0")
        changed_schemas = [
            DomainClaimSchema(
                schema_id="claim.measurement",
                applies_to=["measurement", "unit_conversion"],
                required_fields=["value", "unit", "dimension"],
            )
        ]
        current = build_manifest(
            version="1.1.0", fixture_statuses=[("fx.pass", "verified", {})]
        )
        current = current.model_copy(update={"claim_schemas": changed_schemas})
        diff = compute_semantic_diff_between(previous, current)
        by_item = {(entry.category, entry.item_id): entry for entry in diff.entries}
        schema_change = by_item[("claim_schema", "claim.measurement")]
        assert schema_change.change == SemanticDiffChange.MODIFIED
        assert "Claim Schema" in schema_change.impact

    def test_workbench_serves_diff_between_registered_versions(
        self, workbench: tuple[DomainPackWorkbenchService, DomainPackManifest]
    ) -> None:
        service, manifest = workbench
        service.register_pack("maintainer", service.get_loaded(manifest.id, manifest.version))
        diff = service.semantic_diff_for(manifest.id, manifest.version)
        assert diff.pack_id == manifest.id
        assert diff.from_version == "(none)"
        assert diff.to_version == manifest.version
        assert diff.digest


class TestFixtureCoverageGate:
    def test_missing_failure_and_human_gate_fixtures_block_independent_review(
        self,
    ) -> None:
        loader = DomainPackLoader(require_capability_contracts=False)
        registry = DomainPackRegistry(loader)
        manifest = build_manifest(
            fixture_statuses=[("fx.pass", "verified", {})]
        )
        loaded = registry.register(MinimalDomainPack(manifest))
        service = DomainPackWorkbenchService(
            registry, runtime=DomainPackValidationRuntime(loader), loader=loader
        )
        service.register_pack("maintainer", loaded)
        _qualify(service, "reviewer", manifest)
        _declare_all(service, manifest, ["maintainer", "reviewer", "releaser"])
        service.submit_content_signature(
            "maintainer",
            manifest.id,
            manifest.version,
            conclusion=AttestationConclusion.APPROVE,
            opinion="内容完整",
        )
        service.assign_reviewer("maintainer", manifest.id, manifest.version, "reviewer")
        with pytest.raises(DomainPackWorkbenchError) as exc:
            service.submit_independent_signature(
                "reviewer",
                manifest.id,
                manifest.version,
                conclusion=AttestationConclusion.APPROVE,
                opinion="缺少覆盖",
            )
        assert exc.value.code == "fixture_coverage"
