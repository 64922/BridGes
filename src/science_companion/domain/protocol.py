"""领域包实现协议和已加载候选的运行锁。

协议模块只放稳定接口和加载后不可变快照，避免把控制面校验与执行面混在一起。
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from science_companion.contracts.domain import (
    DomainEvaluationResult,
    DomainPackLock,
    DomainPackManifest,
    DomainPackPreflightReport,
    DomainValidationCheck,
    FixtureCase,
)


@runtime_checkable
class DomainPack(Protocol):
    """Stable implementation seam owned by a scientific domain pack."""

    manifest: DomainPackManifest

    def classify_question(self, question_context: Any) -> str:
        """Classify a task into a manifest question type."""

    def plan_sources(self, question_type: str, risk_tier: str) -> Any:
        """Return a source plan without directly performing network access."""

    def normalize_metadata(self, adapter_record: Any) -> Any:
        """Normalize an adapter record into a domain-owned shape."""

    def resolve_version_status(self, records: Any) -> Any:
        """Resolve lifecycle/version state while preserving unknown values."""

    def parse_domain_structure(self, document: Any) -> Any:
        """Parse domain structure without changing platform control data."""

    def extract_claim_schema(self, content: Any) -> Any:
        """Extract candidate domain claims under a fixed schema."""

    def assess_evidence(self, claim: Any, evidence_set: Any) -> Any:
        """Assess evidence dimensions for the domain question type."""

    def detect_conflicts(self, claim_set: Any, evidence_set: Any) -> Any:
        """Return explicit domain conflicts; never silently select a side."""

    def constrain_wording(self, assessment: Any, audience: str, genre: str) -> Any:
        """Return a wording ceiling and mandatory limitations."""

    def validate_claim(self, claim: Any, evidence_set: Any) -> Any:
        """Run deterministic/domain-specific claim validation."""

    def evaluate(
        self,
        run_artifacts: dict[str, Any],
        fixture_set: list[FixtureCase],
    ) -> DomainEvaluationResult | Mapping[str, Any]:
        """Evaluate the production workflow artifacts for fixture replay."""


@dataclass(frozen=True)
class LoadedDomainPack:
    """A candidate that passed loader preflight and may enter the runtime."""

    manifest: DomainPackManifest
    implementation: DomainPack
    manifest_digest: str
    checks: tuple[DomainValidationCheck, ...]

    @property
    def pack_id(self) -> str:
        """Stable pack identifier."""
        return self.manifest.id

    @property
    def pack_version(self) -> str:
        """Exact pack version."""
        return self.manifest.version

    def lock(self) -> DomainPackLock:
        """Create the immutable run snapshot used by a workflow."""
        return DomainPackLock(
            lock_id=f"domain-pack-lock-{secrets.token_urlsafe(12)}",
            pack_id=self.manifest.id,
            pack_version=self.manifest.version,
            manifest_digest=self.manifest_digest,
            dependency_digests={
                dependency.pack_id: dependency.digest
                for dependency in self.manifest.dependencies
            },
            validator_capabilities={
                validator.validator_id: (
                    f"{validator.capability_name}@{validator.capability_version}"
                )
                for validator in self.manifest.validators
            },
            created_at=datetime.now(UTC),
        )


class DomainPackLoadError(ValueError):
    """Raised when a candidate cannot pass deterministic pack preflight."""

    def __init__(self, message: str, report: DomainPackPreflightReport | None = None) -> None:
        super().__init__(message)
        self.report = report


class DomainPackRegistryError(ValueError):
    """Raised when a versioned pack cannot be registered or selected."""
