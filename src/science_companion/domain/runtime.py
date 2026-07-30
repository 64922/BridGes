"""领域包验证运行时。

该模块保留历史导入路径；加载器、协议和版本目录分别位于相邻模块。
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from science_companion.contracts.domain import (
    DomainCheckStatus,
    DomainEvaluationResult,
    DomainPackManifest,
    DomainPackValidationRun,
    DomainRunStatus,
    DomainValidationCheck,
    FixtureCase,
    FixtureResult,
    FixtureResultStatus,
)
from science_companion.domain.loader import DomainPackLoader
from science_companion.domain.protocol import (
    DomainPack,
    DomainPackLoadError,
    DomainPackRegistryError,
    LoadedDomainPack,
)
from science_companion.domain.registry import DomainPackRegistry

__all__ = [
    "DomainPack",
    "DomainPackLoadError",
    "DomainPackLoader",
    "DomainPackRegistry",
    "DomainPackRegistryError",
    "DomainPackValidationRuntime",
    "LoadedDomainPack",
]

class DomainPackValidationRuntime:
    """Replay candidate fixtures through the stable domain-pack protocol."""

    def __init__(self, loader: DomainPackLoader | None = None) -> None:
        self._loader = loader or DomainPackLoader()

    def run(
        self,
        candidate: LoadedDomainPack | DomainPack,
        *,
        fixtures: Sequence[FixtureCase] | None = None,
        run_id: str | None = None,
    ) -> DomainPackValidationRun:
        """Run all or selected fixtures and return a safe, explainable report."""
        started = datetime.now(UTC)
        if isinstance(candidate, LoadedDomainPack):
            loaded = candidate
        else:
            try:
                loaded = self._loader.load(candidate)
            except DomainPackLoadError as exc:
                report = exc.report
                manifest = getattr(candidate, "manifest", None)
                if isinstance(manifest, DomainPackManifest) and report is not None:
                    return DomainPackValidationRun(
                        run_id=run_id or f"domain-pack-run-{secrets.token_urlsafe(10)}",
                        pack_id=manifest.id,
                        pack_version=manifest.version,
                        manifest_digest=report.manifest_digest,
                        status=DomainRunStatus.BLOCKED,
                        checks=report.checks,
                        blocked_reasons=report.blocked_reasons,
                        created_at=started,
                        completed_at=datetime.now(UTC),
                    )
                raise

        selected_fixtures = list(fixtures or loaded.manifest.fixtures)
        results: list[FixtureResult] = []
        checks = list(loaded.checks)
        for fixture in selected_fixtures:
            result = self._run_fixture(loaded.implementation, fixture)
            results.append(result)
            check_status = DomainCheckStatus.PASS if result.passed else DomainCheckStatus.BLOCKED
            if result.status == FixtureResultStatus.NEEDS_HUMAN and result.passed:
                check_status = DomainCheckStatus.NEEDS_HUMAN
            checks.append(
                DomainValidationCheck(
                    check_id=f"fixture:{fixture.fixture_id}",
                    name=f"夹具 {fixture.fixture_id}",
                    status=check_status,
                    reason=result.reason,
                    details={"observed_status": result.actual_status},
                )
            )

        blocked_reasons = [result.reason for result in results if not result.passed]
        if blocked_reasons:
            status = DomainRunStatus.FAILED
        elif any(
            result.status == FixtureResultStatus.NEEDS_HUMAN for result in results
        ):
            status = DomainRunStatus.NEEDS_HUMAN
        else:
            status = DomainRunStatus.PASSED
        return DomainPackValidationRun(
            run_id=run_id or f"domain-pack-run-{secrets.token_urlsafe(10)}",
            pack_id=loaded.pack_id,
            pack_version=loaded.pack_version,
            manifest_digest=loaded.manifest_digest,
            status=status,
            checks=checks,
            fixture_results=results,
            blocked_reasons=blocked_reasons,
            created_at=started,
            completed_at=datetime.now(UTC),
        )

    def _run_fixture(self, pack: DomainPack, fixture: FixtureCase) -> FixtureResult:
        try:
            artifacts = self._execute_protocol(pack, fixture)
            evaluation = _coerce_evaluation(
                pack.evaluate(artifacts, [fixture])
            )
            return _compare_fixture(fixture, evaluation, artifacts)
        except Exception as exc:  # noqa: BLE001
            return FixtureResult(
                fixture_id=fixture.fixture_id,
                status=FixtureResultStatus.FAILED,
                passed=False,
                expected_status=fixture.expected_status,
                reason=f"夹具运行失败：{type(exc).__name__}。",
                details={"error_type": type(exc).__name__},
            )

    @staticmethod
    def _execute_protocol(pack: DomainPack, fixture: FixtureCase) -> dict[str, Any]:
        """Run the typed protocol stages with fixture data only."""
        data = dict(fixture.input_snapshot)
        question_context = data.get("question_context", data)
        question_type = pack.classify_question(question_context)
        risk_tier = str(data.get("risk_tier", fixture.risk_tier))
        source_plan = pack.plan_sources(question_type, risk_tier)
        metadata = pack.normalize_metadata(data.get("metadata", {}))
        lifecycle = pack.resolve_version_status(data.get("source_records", []))
        structure = pack.parse_domain_structure(data.get("document", {}))
        claim_schema = pack.extract_claim_schema(data.get("content", data))
        claim = data.get("claim", claim_schema)
        evidence_set = data.get("evidence_set", [])
        assessment = pack.assess_evidence(claim, evidence_set)
        conflicts = pack.detect_conflicts(data.get("claim_set", [claim]), evidence_set)
        wording = pack.constrain_wording(
            assessment,
            str(data.get("audience", "general")),
            str(data.get("genre", "explanation")),
        )
        validation = pack.validate_claim(claim, evidence_set)
        return {
            "question_type": question_type,
            "source_plan": source_plan,
            "metadata": metadata,
            "lifecycle": lifecycle,
            "structure": structure,
            "claim_schema": claim_schema,
            "assessment": assessment,
            "conflicts": conflicts,
            "wording": wording,
            "validation": validation,
        }


def _coerce_evaluation(value: Any) -> DomainEvaluationResult:
    if isinstance(value, DomainEvaluationResult):
        return value
    if isinstance(value, Mapping):
        return DomainEvaluationResult.model_validate(value)
    if hasattr(value, "model_dump"):
        return DomainEvaluationResult.model_validate(value.model_dump())
    status = getattr(value, "status", None)
    if isinstance(status, str) and status:
        return DomainEvaluationResult(
            status=status,
            reason_codes=list(getattr(value, "reason_codes", [])),
            rule_ids=list(getattr(value, "rule_ids", [])),
            validator_ids=list(getattr(value, "validator_ids", [])),
        )
    raise TypeError("领域包 evaluate 必须返回带 status 的结构化结果。")


def _compare_fixture(
    fixture: FixtureCase,
    evaluation: DomainEvaluationResult,
    artifacts: Mapping[str, Any],
) -> FixtureResult:
    mismatches: list[str] = []
    if evaluation.status != fixture.expected_status:
        mismatches.append(
            f"expected_status={fixture.expected_status}, actual_status={evaluation.status}"
        )
    missing_reasons = set(fixture.expected_reason_codes) - set(evaluation.reason_codes)
    if missing_reasons:
        mismatches.append(f"缺少原因码：{', '.join(sorted(missing_reasons))}")
    missing_rules = set(fixture.expected_rule_ids) - set(evaluation.rule_ids)
    if missing_rules:
        mismatches.append(f"缺少规则：{', '.join(sorted(missing_rules))}")
    missing_validators = set(fixture.expected_validator_ids) - set(evaluation.validator_ids)
    if missing_validators:
        mismatches.append(f"缺少校验器：{', '.join(sorted(missing_validators))}")
    wording_mismatches = {
        key: expected
        for key, expected in fixture.expected_wording.items()
        if evaluation.wording.get(key) != expected
    }
    if wording_mismatches:
        mismatches.append(f"预期措辞未满足：{sorted(wording_mismatches)}")
    if fixture.requires_human and evaluation.status not in {"needs_human", "conflicted"}:
        mismatches.append("夹具要求人工门但实际结果未进入人工门")

    actual_status = evaluation.status
    if actual_status in {"needs_human", "conflicted"}:
        observed = FixtureResultStatus.NEEDS_HUMAN
    elif actual_status in {"blocked", "quarantined", "stale_or_updated", "failed"}:
        observed = FixtureResultStatus.BLOCKED
    elif actual_status in {"verified", "qualified", "partial", "pass", "passed"}:
        observed = FixtureResultStatus.PASS
    else:
        observed = FixtureResultStatus.FAILED
    return FixtureResult(
        fixture_id=fixture.fixture_id,
        status=observed,
        passed=not mismatches,
        expected_status=fixture.expected_status,
        actual_status=actual_status,
        reason="；".join(mismatches),
        reason_codes=list(evaluation.reason_codes),
        rule_ids=list(evaluation.rule_ids),
        validator_ids=list(evaluation.validator_ids),
            details={
                "artifact_keys": sorted(artifacts),
                "expected_wording": fixture.expected_wording,
                "actual_wording": evaluation.wording,
            },
        )
