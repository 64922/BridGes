"""Stable contracts for versioned scientific domain packs.

The platform owns authorization, safety, provenance and publication gates. A
domain pack contributes only typed scientific rules, validator requirements and
replayable fixtures; these contracts make that boundary explicit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class DomainPackStatus(StrEnum):
    """Governance state of a candidate domain pack."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    SIGNED = "signed"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    EXPIRED = "expired"


class DomainCheckStatus(StrEnum):
    """Outcome of one deterministic pack preflight check."""

    PASS = "pass"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"


class DomainRunStatus(StrEnum):
    """Outcome of validating a candidate pack and its fixtures."""

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"


class FixtureResultStatus(StrEnum):
    """Observed status of one fixture execution."""

    PASS = "pass"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class PlatformSafetyFloor(BaseModel):
    """Non-relaxable guarantees inherited from the platform.

    A domain pack can add stricter controls, but it cannot disable any required
    control or opt into network, secret or ACL-bypass access.
    """

    model_config = ConfigDict(extra="allow")

    authorization_required: bool = True
    tenant_isolation_required: bool = True
    audit_required: bool = True
    sandbox_required: bool = True
    fact_lock_required: bool = True
    publish_gate_required: bool = True
    fail_closed_required: bool = True
    allow_network_access: bool = False
    allow_secret_access: bool = False
    allow_acl_bypass: bool = False
    allow_tenant_bypass: bool = False


class DomainSourcePolicy(BaseModel):
    """可审计的领域来源策略，而不是任意来源配置字典。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    policy_id: str = Field(
        validation_alias=AliasChoices("policy_id", "id"),
        min_length=1,
    )
    applies_to: list[str] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)
    allowed_source_roles: list[str] = Field(default_factory=list)
    on_failure: Literal["block", "revalidate", "quarantine", "needs_human"] = "block"


class DomainClaimSchema(BaseModel):
    """领域 Claim Schema 的最小可验证闭环。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_id: str = Field(
        validation_alias=AliasChoices("schema_id", "id"),
        min_length=1,
    )
    applies_to: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)
    wording_policy_ids: list[str] = Field(default_factory=list)
    unknown_behavior: Literal["block", "preserve_unknown"] = "block"


class DomainWordingPolicy(BaseModel):
    """把证据状态映射到可发布措辞的领域策略。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    policy_id: str = Field(
        validation_alias=AliasChoices("policy_id", "id"),
        min_length=1,
    )
    applies_to: list[str] = Field(default_factory=list)
    allowed_statuses: list[str] = Field(default_factory=list)
    forbidden_strengths: list[str] = Field(default_factory=list)
    required_disclosures: list[str] = Field(default_factory=list)
    requires_human_gate: bool = False


class DomainMigrationSpec(BaseModel):
    """可重放升级迁移的声明，禁止只写一条无语义的版本备注。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    migration_id: str = Field(
        validation_alias=AliasChoices("migration_id", "id"),
        min_length=1,
    )
    from_version: str = Field(min_length=1)
    to_version: str = Field(min_length=1)
    strategy: Literal["replay", "transform", "manual_review"] = "transform"
    preserves_history: bool = True
    requires_fixture_replay: bool = True
    rollback_version: str | None = None


class DomainInvalidationPolicy(BaseModel):
    """来源、依赖或校验器变化时的失效传播策略。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    strategy: Literal["block", "revalidate", "quarantine", "stale"] = "revalidate"
    on_dependency_revoked: Literal["block", "revalidate", "quarantine", "stale"] = "block"
    on_source_stale: Literal["block", "revalidate", "quarantine", "stale"] = "revalidate"
    on_validator_failure: Literal["block", "revalidate", "quarantine", "stale"] = "block"
    preserve_history: bool = True


class DomainRollbackPolicy(BaseModel):
    """受信回滚的版本白名单及其重新验证要求。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    trusted_versions: list[str] = Field(default_factory=list)
    requires_fixture_replay: bool = True
    preserves_history: bool = True


class DomainRevocationPolicy(BaseModel):
    """撤权后的新运行和历史运行处理方式。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    new_runs: Literal["block"] = "block"
    existing_runs: Literal["preserve_and_mark"] = "preserve_and_mark"
    requires_revalidation: bool = True


class DomainCompatibility(BaseModel):
    """升级版本可兼容范围和重新验证要求。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    compatible_with: list[str] = Field(default_factory=list)
    preserves_runtime_contract: bool = True
    requires_revalidation: bool = True


class DomainManifestSignature(BaseModel):
    """Manifest 签名及其覆盖范围声明。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    signer_id: str = Field(
        validation_alias=AliasChoices("signer_id", "signer"),
        min_length=1,
    )
    algorithm: str = Field(default="ed25519", min_length=1)
    signature: str = Field(default="")
    signed_digest: str = Field(
        validation_alias=AliasChoices("signed_digest", "manifest_digest", "digest"),
        min_length=1,
    )
    scope: list[str] = Field(default_factory=list)


class DomainRule(BaseModel):
    """One executable domain rule and its review metadata."""

    model_config = ConfigDict(populate_by_name=True)

    rule_id: str = Field(
        validation_alias=AliasChoices("rule_id", "id"),
        min_length=1,
        description="Stable rule identifier.",
    )
    rule_schema_version: str = Field(default="1", min_length=1)
    applies_to: list[str] = Field(default_factory=list)
    priority: int = Field(default=100)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    unknown_behavior: str = Field(default="block")
    conditions: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    rejection_code: str = Field(default="domain_rule_rejected", min_length=1)
    explanation: str = Field(default="")
    source_refs: list[str] = Field(default_factory=list)
    fixture_ids: list[str] = Field(default_factory=list)
    execution_mode: str = Field(default="deterministic")
    human_gate: str | None = Field(default=None)
    platform_floor: PlatformSafetyFloor = Field(default_factory=PlatformSafetyFloor)

    @field_validator("unknown_behavior")
    @classmethod
    def _validate_unknown_behavior(cls, value: str) -> str:
        if value not in {"block", "preserve_unknown"}:
            raise ValueError("unknown_behavior 必须是 block 或 preserve_unknown。")
        return value


class ValidatorRequirement(BaseModel):
    """Logical validator capability required by a domain pack."""

    model_config = ConfigDict(populate_by_name=True)

    validator_id: str = Field(
        validation_alias=AliasChoices("validator_id", "id"),
        min_length=1,
        description="Stable validator requirement identifier.",
    )
    capability_name: str = Field(
        validation_alias=AliasChoices("capability_name", "name"),
        min_length=1,
    )
    capability_version: str = Field(
        validation_alias=AliasChoices("capability_version", "version"),
        default="1",
        min_length=1,
    )
    input_schema_version: str = Field(default="1", min_length=1)
    output_schema_version: str = Field(default="1", min_length=1)
    isolation_level: str = Field(default="sandbox", min_length=1)
    network_access: bool = False
    secret_access: bool = False
    filesystem_access: str = "none"
    deterministic: bool = True
    required: bool = True
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    fixture_ids: list[str] = Field(default_factory=list)


class FixtureCase(BaseModel):
    """Replayable positive, negative, boundary or governance fixture."""

    model_config = ConfigDict(populate_by_name=True)

    fixture_id: str = Field(
        validation_alias=AliasChoices("fixture_id", "id"),
        min_length=1,
        description="Stable fixture identifier.",
    )
    name: str | None = Field(default=None)
    kind: str = Field(default="positive", min_length=1)
    question_type: str = Field(default="", min_length=0)
    risk_tier: str = Field(default="general_education", min_length=1)
    input_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("input_snapshot", "input"),
    )
    expected_status: str = Field(default="verified", min_length=1)
    expected_reason_codes: list[str] = Field(default_factory=list)
    expected_rule_ids: list[str] = Field(default_factory=list)
    expected_validator_ids: list[str] = Field(default_factory=list)
    expected_wording: dict[str, Any] = Field(default_factory=dict)
    requires_human: bool = False
    source_file: str | None = Field(default=None)
    rationale: str = Field(default="")


class PackDependencyLock(BaseModel):
    """Exact dependency entry resolved for a domain-pack candidate."""

    model_config = ConfigDict(populate_by_name=True)

    pack_id: str = Field(
        validation_alias=AliasChoices("pack_id", "domain_pack_id", "id"),
        min_length=1,
    )
    version: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    dependency_type: str = Field(default="domain_pack", min_length=1)
    depends_on: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("depends_on", "dependencies"),
    )
    source: str | None = Field(default=None)


class DomainPackManifest(BaseModel):
    """Machine-readable manifest for a versioned domain pack."""

    model_config = ConfigDict(populate_by_name=True)

    manifest_schema: str = Field(default="domain-pack-manifest/v1", min_length=1)
    id: str = Field(
        validation_alias=AliasChoices("id", "pack_id"),
        min_length=1,
        description="Globally stable domain-pack identifier.",
    )
    version: str = Field(min_length=1)
    platform_api: str = Field(default=">=1.0,<2.0", min_length=1)
    pack_api: str = Field(default="domain-pack/v1", min_length=1)
    scope: list[str] | dict[str, Any] = Field(default_factory=list)
    exclusions: list[str] | dict[str, Any] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    disciplines: list[str] = Field(default_factory=list)
    risk_tiers: list[str] = Field(default_factory=list)
    question_types: list[str] = Field(default_factory=list)
    source_policies: list[DomainSourcePolicy] = Field(default_factory=list)
    source_adapters: list[dict[str, Any]] = Field(default_factory=list)
    identifier_rules: list[dict[str, Any]] = Field(default_factory=list)
    publication_stage_rules: list[dict[str, Any]] = Field(default_factory=list)
    evidence_dimensions: list[dict[str, Any]] = Field(default_factory=list)
    certainty_mappings: list[dict[str, Any]] = Field(default_factory=list)
    wording_policy: list[DomainWordingPolicy] = Field(default_factory=list)
    claim_schemas: list[DomainClaimSchema] = Field(default_factory=list)
    unit_and_formula_rules: list[dict[str, Any]] = Field(default_factory=list)
    ontologies: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    rules: list[DomainRule] = Field(
        default_factory=list,
        validation_alias=AliasChoices("rules", "domain_rules"),
    )
    validators: list[ValidatorRequirement] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "validators", "validator_requirements", "capabilities"
        ),
    )
    conflict_rules: list[dict[str, Any]] = Field(default_factory=list)
    fixtures: list[FixtureCase] = Field(
        default_factory=list,
        validation_alias=AliasChoices("fixtures", "fixture_cases"),
    )
    evaluation_sets: list[dict[str, Any]] = Field(default_factory=list)
    migrations: list[DomainMigrationSpec] = Field(default_factory=list)
    dependencies: list[PackDependencyLock] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "dependencies", "dependency_lock", "dependency_locks"
        ),
    )
    maintainers: list[dict[str, Any]] = Field(default_factory=list)
    reviewers: list[dict[str, Any]] = Field(default_factory=list)
    signatures: list[DomainManifestSignature] = Field(default_factory=list)
    build_provenance: dict[str, Any] = Field(default_factory=dict)
    content_files: dict[str, str] = Field(default_factory=dict)
    content_digest: str | None = Field(default=None)
    compatibility: DomainCompatibility = Field(default_factory=DomainCompatibility)
    invalidation_policy: DomainInvalidationPolicy = Field(
        default_factory=DomainInvalidationPolicy
    )
    rollback_versions: list[str] = Field(default_factory=list)
    rollback_policy: DomainRollbackPolicy = Field(default_factory=DomainRollbackPolicy)
    revocation_policy: DomainRevocationPolicy = Field(default_factory=DomainRevocationPolicy)
    lifecycle_status: DomainPackStatus = Field(default=DomainPackStatus.DRAFT)
    platform_floor: PlatformSafetyFloor = Field(default_factory=PlatformSafetyFloor)

    @property
    def pack_id(self) -> str:
        """Compatibility name used by pack registries and run locks."""
        return self.id

    @property
    def validator_requirements(self) -> list[ValidatorRequirement]:
        """Return the logical validator requirements."""
        return self.validators

    @property
    def fixture_cases(self) -> list[FixtureCase]:
        """Return replayable fixtures under their contract name."""
        return self.fixtures

    @property
    def dependency_locks(self) -> list[PackDependencyLock]:
        """Return exact dependency locks."""
        return self.dependencies


class DomainEvaluationResult(BaseModel):
    """Typed result returned by a pack's production validation seam."""

    status: str = Field(min_length=1)
    reason_codes: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    validator_ids: list[str] = Field(default_factory=list)
    wording: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class DomainValidationCheck(BaseModel):
    """One explainable preflight or runtime check."""

    check_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: DomainCheckStatus
    reason: str = Field(default="")
    details: dict[str, Any] = Field(default_factory=dict)


class FixtureResult(BaseModel):
    """Observed result of one replayable domain-pack fixture."""

    fixture_id: str = Field(min_length=1)
    status: FixtureResultStatus
    passed: bool
    expected_status: str
    actual_status: str | None = None
    reason: str = Field(default="")
    reason_codes: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    validator_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class DomainPackLock(BaseModel):
    """Immutable run snapshot of the selected pack and exact dependencies."""

    lock_id: str = Field(min_length=1)
    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    manifest_digest: str = Field(min_length=1)
    dependency_digests: dict[str, str] = Field(default_factory=dict)
    validator_capabilities: dict[str, str] = Field(default_factory=dict)
    created_at: datetime


class DomainPackValidationRun(BaseModel):
    """Complete, auditable result of candidate preflight and fixture replay."""

    run_id: str = Field(min_length=1)
    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    manifest_digest: str = Field(min_length=1)
    status: DomainRunStatus
    checks: list[DomainValidationCheck] = Field(default_factory=list)
    fixture_results: list[FixtureResult] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    created_at: datetime
    completed_at: datetime | None = None


class DomainPackPreflightReport(BaseModel):
    """Loader result before a candidate is allowed into the runtime."""

    accepted: bool
    manifest_digest: str = Field(min_length=1)
    checks: list[DomainValidationCheck] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)


class DomainPackUpgradeReport(BaseModel):
    """Compatibility and migration result for registering a new pack version."""

    compatible: bool
    from_version: str
    to_version: str
    migration_required: bool
    migration_present: bool
    compatibility_declared: bool = False
    invalidation_closed: bool = False
    rollback_trusted: bool = False
    issues: list[str] = Field(default_factory=list)


class WorkbenchStage(StrEnum):
    """复核流程阶段；它描述工作台流程，不替代包治理状态。"""

    DRAFTING = "drafting"
    CONTENT_SIGNED = "content_signed"
    REVIEW_ASSIGNED = "review_assigned"
    REVIEW_COMPLETED = "review_completed"
    CHANGES_REQUESTED = "changes_requested"
    RELEASE_PREFLIGHT = "release_preflight"
    GRAY_RELEASE_READY = "gray_release_ready"
    RELEASED = "released"


class ReviewRole(StrEnum):
    """三签职责分离中的签名角色。"""

    CONTENT = "content"
    INDEPENDENT = "independent"
    PLATFORM = "platform"


class AttestationConclusion(StrEnum):
    """一次签名或复核的结论。"""

    APPROVE = "approve"
    REJECT = "reject"
    NEEDS_CHANGES = "needs_changes"


class QualificationRecord(BaseModel):
    """可核验资质：类型、核验者、有效期和适用范围。"""

    model_config = ConfigDict(populate_by_name=True)

    qualification_id: str = Field(
        validation_alias=AliasChoices("qualification_id", "id"),
        min_length=1,
    )
    person_id: str = Field(min_length=1)
    qualification_type: str = Field(
        min_length=1,
        description="例如 clinical_expert、formal_proof、domain_expert。",
    )
    verifier_id: str = Field(min_length=1)
    disciplines: list[str] = Field(default_factory=list)
    scope: str = Field(default="")
    verified_at: datetime
    valid_until: datetime | None = None

    @property
    def is_expired(self) -> bool:
        return self.valid_until is not None and self.valid_until < datetime.now(UTC)


class ConflictOfInterestDeclaration(BaseModel):
    """逐版本利益冲突声明；签名前必须存在。"""

    model_config = ConfigDict(populate_by_name=True)

    declaration_id: str = Field(
        validation_alias=AliasChoices("declaration_id", "id"),
        min_length=1,
    )
    person_id: str = Field(min_length=1)
    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    disclosures: list[str] = Field(default_factory=list)
    declared_at: datetime


class ReviewAttestation(BaseModel):
    """内容签名、独立验证签名或平台发行签名。

    三种签名必须绑定同一规范化包摘要；任一内容变化都会使旧签名不再适用。
    """

    model_config = ConfigDict(populate_by_name=True)

    attestation_id: str = Field(
        validation_alias=AliasChoices("attestation_id", "id"),
        min_length=1,
    )
    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    canonical_digest: str = Field(
        min_length=1,
        description="被签名的规范化包摘要。",
    )
    role: ReviewRole
    person_id: str = Field(min_length=1)
    qualification_ids: list[str] = Field(default_factory=list)
    conflict_declaration_id: str = Field(min_length=1)
    conclusion: AttestationConclusion
    opinion: str = Field(default="")
    signed_at: datetime


class ConflictDisclosure(BaseModel):
    """少数意见与未决依据；不能迫使少数方签署。"""

    model_config = ConfigDict(populate_by_name=True)

    disclosure_id: str = Field(
        validation_alias=AliasChoices("disclosure_id", "id"),
        min_length=1,
    )
    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    item_ref: str = Field(min_length=1)
    minority_opinion: str = Field(min_length=1)
    basis: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    decision: str = Field(default="")
    created_at: datetime


class SemanticDiffChange(StrEnum):
    """语义差异变化类型。"""

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


class SemanticSignificance(StrEnum):
    """差异的判定意义级别。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SemanticDiffEntry(BaseModel):
    """一条有判定意义的语义差异。"""

    model_config = ConfigDict(populate_by_name=True)

    category: str = Field(
        min_length=1,
        description="rule / wording / human_gate / source / fixture / dependency / validator。",
    )
    change: SemanticDiffChange
    item_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    old_value: Any = None
    new_value: Any = None
    impact: str = Field(
        min_length=1,
        description="对判定的意义，例如“人工门升高”“BLOCKED 变为可发布”。",
    )
    significance: SemanticSignificance = SemanticSignificance.LOW


class SemanticDiff(BaseModel):
    """两个包版本之间的判定差异；原始 YAML Diff 不能替代它。"""

    model_config = ConfigDict(populate_by_name=True)

    diff_id: str = Field(min_length=1)
    pack_id: str = Field(min_length=1)
    from_version: str
    to_version: str
    entries: list[SemanticDiffEntry] = Field(default_factory=list)
    digest: str = Field(min_length=1)


class CanonicalPackSummary(BaseModel):
    """规范化包摘要：所有签名绑定的不可变对象。

    任一组成部分变化都会产生新摘要，并使旧签名失效。
    """

    model_config = ConfigDict(populate_by_name=True)

    canonical_digest: str = Field(min_length=1)
    manifest_digest: str = Field(min_length=1)
    content_tree_digest: str = Field(min_length=1)
    dependency_lock_digest: str = Field(min_length=1)
    fixture_summary_digest: str = Field(min_length=1)
    build_provenance_digest: str = Field(min_length=1)
    semantic_diff_digest: str = Field(min_length=1)


class GrayReleaseStatus(StrEnum):
    """灰度候选状态；READY_TO_RELEASE 不会自动激活。"""

    READY_TO_RELEASE = "ready_to_release"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"


class GrayReleaseCandidate(BaseModel):
    """可发行候选：灰度验证的完整结果，激活需要发行签名。"""

    model_config = ConfigDict(populate_by_name=True)

    candidate_id: str = Field(min_length=1)
    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    canonical_digest: str = Field(min_length=1)
    status: GrayReleaseStatus
    checks: list[DomainValidationCheck] = Field(default_factory=list)
    fixture_results: list[FixtureResult] = Field(default_factory=list)
    fixture_summary: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(min_length=1)
    created_at: datetime


class PackRelease(BaseModel):
    """发行摘要、平台签名和透明记录。"""

    model_config = ConfigDict(populate_by_name=True)

    release_id: str = Field(min_length=1)
    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    canonical_digest: str = Field(min_length=1)
    attestations: list[ReviewAttestation] = Field(default_factory=list)
    platform_attestation: ReviewAttestation
    gray_candidate_id: str = Field(min_length=1)
    released_by: str = Field(min_length=1)
    released_at: datetime
    transparent_record: dict[str, Any] = Field(default_factory=dict)


class WorkbenchPackRecord(BaseModel):
    """专家工作台对一个包版本的完整治理记录。"""

    model_config = ConfigDict(populate_by_name=True)

    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    manifest_digest: str = Field(min_length=1)
    canonical_digest: str = Field(min_length=1)
    stage: WorkbenchStage = WorkbenchStage.DRAFTING
    lifecycle_status: DomainPackStatus = DomainPackStatus.DRAFT
    maintainer_id: str = Field(min_length=1)
    reviewer_id: str | None = None
    releaser_id: str | None = None
    attestations: list[ReviewAttestation] = Field(default_factory=list)
    declarations: list[ConflictOfInterestDeclaration] = Field(default_factory=list)
    disclosures: list[ConflictDisclosure] = Field(default_factory=list)
    gray_candidate: GrayReleaseCandidate | None = None
    release: PackRelease | None = None
    checks: list[DomainValidationCheck] = Field(default_factory=list)
    fixture_results: list[FixtureResult] = Field(default_factory=list)
    updated_at: datetime
