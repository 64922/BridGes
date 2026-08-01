"""领域包专家工作台：三签、语义 Diff 与灰度发行。

本模块实现职责分离的领域包发行流水线。正式发行必须同时满足：
内容签名、独立验证签名与平台发行签名，且三种签名绑定同一规范化包摘要；
同一自然人不能对同一版本同时充当维护者和独立复核者；
灰度只生成 READY_TO_RELEASE 候选，激活必须由平台发行者显式追加发行签名。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.domain import (
    AttestationConclusion,
    CanonicalPackSummary,
    ConflictDisclosure,
    ConflictOfInterestDeclaration,
    DomainManifestSignature,
    DomainPackManifest,
    DomainPackStatus,
    GrayReleaseCandidate,
    GrayReleaseStatus,
    H3SecurityConfirmation,
    PackRelease,
    QualificationRecord,
    ReviewAttestation,
    ReviewRole,
    SemanticDiff,
    SemanticDiffChange,
    SemanticDiffEntry,
    SemanticSignificance,
    WorkbenchPackRecord,
    WorkbenchStage,
)
from bridges.domain.loader import DomainPackLoader, _parse_version
from bridges.domain.protocol import (
    DomainPackRegistryError,
    LoadedDomainPack,
)
from bridges.domain.registry import DomainPackRegistry
from bridges.domain.runtime import DomainPackValidationRuntime

__all__ = [
    "DomainPackWorkbenchError",
    "DomainPackWorkbenchService",
    "compute_semantic_diff_between",
    "canonical_pack_summary",
]


class DomainPackWorkbenchError(ValueError):
    """工作台流程违反职责分离、签名绑定或灰度门。"""

    def __init__(self, message: str, *, code: str = "workbench_rejected") -> None:
        super().__init__(message)
        self.code = code


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fixture_summary(fixtures: Sequence[Any]) -> list[dict[str, Any]]:
    """夹具摘要（用于规范化摘要计算，保持确定性顺序）。"""
    return [
        {
            "fixture_id": fixture.fixture_id,
            "kind": fixture.kind,
            "expected_status": fixture.expected_status,
            "expected_rule_ids": sorted(fixture.expected_rule_ids),
            "expected_validator_ids": sorted(fixture.expected_validator_ids),
            "requires_human": fixture.requires_human,
        }
        for fixture in fixtures
    ]


def _fixture_summary_stats(fixtures: Sequence[Any]) -> dict[str, Any]:
    """夹具覆盖统计（用于灰度候选展示）。"""
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    human_gate = 0
    for fixture in fixtures:
        by_kind[fixture.kind] = by_kind.get(fixture.kind, 0) + 1
        by_status[fixture.expected_status] = by_status.get(fixture.expected_status, 0) + 1
        if fixture.requires_human or fixture.expected_status in {
            "needs_human",
            "conflicted",
        }:
            human_gate += 1
    return {
        "total": len(fixtures),
        "by_kind": by_kind,
        "by_expected_status": by_status,
        "human_gate_count": human_gate,
    }


def canonical_pack_summary(
    manifest: DomainPackManifest,
    previous: DomainPackManifest | None = None,
) -> CanonicalPackSummary:
    """对 Manifest、内容树、依赖锁、夹具摘要、构建出处和语义 Diff 做规范化摘要。

    任一组成部分变化都会产生新摘要，使旧签名失效。
    """
    manifest_digest = DomainPackLoader.manifest_digest(manifest)
    content_tree_digest = _hash_payload(
        {key: manifest.content_files[key] for key in sorted(manifest.content_files)}
    )
    dependency_lock_digest = _hash_payload(
        [dependency.model_dump(mode="json") for dependency in manifest.dependencies]
    )
    fixture_summary_digest = _hash_payload(_fixture_summary(manifest.fixtures))
    build_provenance_digest = _hash_payload(manifest.build_provenance)
    diff_entries = _diff_entries_between(previous, manifest)
    semantic_diff_digest = _hash_payload(
        [entry.model_dump(mode="json") for entry in diff_entries]
    )
    canonical_digest = _hash_payload(
        {
            "manifest": manifest_digest,
            "content_tree": content_tree_digest,
            "dependency_lock": dependency_lock_digest,
            "fixture_summary": fixture_summary_digest,
            "build_provenance": build_provenance_digest,
            "semantic_diff": semantic_diff_digest,
        }
    )
    return CanonicalPackSummary(
        canonical_digest=canonical_digest,
        manifest_digest=manifest_digest,
        content_tree_digest=content_tree_digest,
        dependency_lock_digest=dependency_lock_digest,
        fixture_summary_digest=fixture_summary_digest,
        build_provenance_digest=build_provenance_digest,
        semantic_diff_digest=semantic_diff_digest,
    )


def _diff_entries_between(
    previous: DomainPackManifest | None,
    current: DomainPackManifest,
) -> list[SemanticDiffEntry]:
    """只生成有判定意义的差异：规则、措辞、人工门、来源、依赖和夹具。"""
    entries: list[SemanticDiffEntry] = []

    def _modified(
        category: str,
        item_id: str,
        label: str,
        old_value: Any,
        new_value: Any,
        impact: str,
        significance: SemanticSignificance = SemanticSignificance.MEDIUM,
    ) -> SemanticDiffEntry:
        return SemanticDiffEntry(
            category=category,
            change=SemanticDiffChange.MODIFIED,
            item_id=item_id,
            label=label,
            old_value=old_value,
            new_value=new_value,
            impact=impact,
            significance=significance,
        )

    previous_rules = {rule.rule_id: rule for rule in previous.rules} if previous else {}
    current_rules = {rule.rule_id: rule for rule in current.rules}
    for rule_id in sorted(set(previous_rules) | set(current_rules)):
        if rule_id not in previous_rules:
            entries.append(
                SemanticDiffEntry(
                    category="rule",
                    change=SemanticDiffChange.ADDED,
                    item_id=rule_id,
                    label=f"领域规则 {rule_id}",
                    new_value=current_rules[rule_id].model_dump(mode="json"),
                    impact="新增领域规则",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        if rule_id not in current_rules:
            entries.append(
                SemanticDiffEntry(
                    category="rule",
                    change=SemanticDiffChange.REMOVED,
                    item_id=rule_id,
                    label=f"领域规则 {rule_id}",
                    old_value=previous_rules[rule_id].model_dump(mode="json"),
                    impact="删除领域规则",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        old_rule = previous_rules[rule_id]
        new_rule = current_rules[rule_id]
        if old_rule.human_gate != new_rule.human_gate:
            gate_level = {"H0": 1, "H1": 2, "H2": 3, "H3": 4}
            old_level = gate_level.get(str(old_rule.human_gate).upper(), 0)
            new_level = gate_level.get(str(new_rule.human_gate).upper(), 0)
            entries.append(
                _modified(
                    "human_gate",
                    rule_id,
                    f"规则 {rule_id} 人工门",
                    old_rule.human_gate,
                    new_rule.human_gate,
                    (
                        "人工门升高"
                        if new_level > old_level
                        else "人工门降低"
                        if new_level < old_level
                        else "人工门调整"
                    ),
                    SemanticSignificance.HIGH,
                )
            )
        if old_rule.platform_floor != new_rule.platform_floor:
            entries.append(
                _modified(
                    "rule",
                    rule_id,
                    f"规则 {rule_id} 平台下限",
                    old_rule.platform_floor.model_dump(mode="json"),
                    new_rule.platform_floor.model_dump(mode="json"),
                    "平台安全下限变化",
                    SemanticSignificance.HIGH,
                )
            )
        if (
            old_rule.conditions != new_rule.conditions
            or old_rule.outputs != new_rule.outputs
            or old_rule.rejection_code != new_rule.rejection_code
            or old_rule.applies_to != new_rule.applies_to
        ):
            entries.append(
                _modified(
                    "rule",
                    rule_id,
                    f"规则 {rule_id} 判定条件",
                    old_rule.model_dump(
                        mode="json", include={"conditions", "outputs", "rejection_code"}
                    ),
                    new_rule.model_dump(
                        mode="json", include={"conditions", "outputs", "rejection_code"}
                    ),
                    "规则判定条件或输出变化",
                )
            )

    previous_wording = (
        {policy.policy_id: policy for policy in previous.wording_policy}
        if previous
        else {}
    )
    current_wording = {policy.policy_id: policy for policy in current.wording_policy}
    for policy_id in sorted(set(previous_wording) | set(current_wording)):
        if policy_id not in previous_wording:
            entries.append(
                SemanticDiffEntry(
                    category="wording",
                    change=SemanticDiffChange.ADDED,
                    item_id=policy_id,
                    label=f"措辞策略 {policy_id}",
                    new_value=current_wording[policy_id].model_dump(mode="json"),
                    impact="新增措辞策略",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        if policy_id not in current_wording:
            entries.append(
                SemanticDiffEntry(
                    category="wording",
                    change=SemanticDiffChange.REMOVED,
                    item_id=policy_id,
                    label=f"措辞策略 {policy_id}",
                    old_value=previous_wording[policy_id].model_dump(mode="json"),
                    impact="删除措辞策略",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        old_policy = previous_wording[policy_id]
        new_policy = current_wording[policy_id]
        if (
            old_policy.allowed_statuses != new_policy.allowed_statuses
            or old_policy.forbidden_strengths != new_policy.forbidden_strengths
            or old_policy.required_disclosures != new_policy.required_disclosures
        ):
            entries.append(
                _modified(
                    "wording",
                    policy_id,
                    f"措辞策略 {policy_id} 强度",
                    {
                        "allowed_statuses": old_policy.allowed_statuses,
                        "forbidden_strengths": old_policy.forbidden_strengths,
                    },
                    {
                        "allowed_statuses": new_policy.allowed_statuses,
                        "forbidden_strengths": new_policy.forbidden_strengths,
                    },
                    "措辞强度变化",
                )
            )
        if old_policy.requires_human_gate != new_policy.requires_human_gate:
            entries.append(
                _modified(
                    "human_gate",
                    policy_id,
                    f"措辞策略 {policy_id} 人工门",
                    old_policy.requires_human_gate,
                    new_policy.requires_human_gate,
                    "人工门升高" if new_policy.requires_human_gate else "人工门降低",
                    SemanticSignificance.HIGH,
                )
            )

    previous_sources = (
        {policy.policy_id: policy for policy in previous.source_policies}
        if previous
        else {}
    )
    current_sources = {policy.policy_id: policy for policy in current.source_policies}
    for policy_id in sorted(set(previous_sources) | set(current_sources)):
        if policy_id not in previous_sources:
            entries.append(
                SemanticDiffEntry(
                    category="source",
                    change=SemanticDiffChange.ADDED,
                    item_id=policy_id,
                    label=f"来源策略 {policy_id}",
                    new_value=current_sources[policy_id].model_dump(mode="json"),
                    impact="新增来源策略",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        if policy_id not in current_sources:
            entries.append(
                SemanticDiffEntry(
                    category="source",
                    change=SemanticDiffChange.REMOVED,
                    item_id=policy_id,
                    label=f"来源策略 {policy_id}",
                    old_value=previous_sources[policy_id].model_dump(mode="json"),
                    impact="删除来源策略",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        old_source = previous_sources[policy_id]
        new_source = current_sources[policy_id]
        if (
            old_source.applies_to != new_source.applies_to
            or old_source.allowed_source_roles != new_source.allowed_source_roles
            or old_source.on_failure != new_source.on_failure
            or old_source.evidence_requirements != new_source.evidence_requirements
        ):
            entries.append(
                _modified(
                    "source",
                    policy_id,
                    f"来源策略 {policy_id} 权威范围",
                    old_source.model_dump(
                        mode="json",
                        include={
                            "applies_to",
                            "allowed_source_roles",
                            "evidence_requirements",
                            "on_failure",
                        },
                    ),
                    new_source.model_dump(
                        mode="json",
                        include={
                            "applies_to",
                            "allowed_source_roles",
                            "evidence_requirements",
                            "on_failure",
                        },
                    ),
                    "来源权威范围或失败语义变化",
                )
            )

    previous_schemas = (
        {schema.schema_id: schema for schema in previous.claim_schemas}
        if previous
        else {}
    )
    current_schemas = {schema.schema_id: schema for schema in current.claim_schemas}
    for schema_id in sorted(set(previous_schemas) | set(current_schemas)):
        if schema_id not in previous_schemas:
            entries.append(
                SemanticDiffEntry(
                    category="claim_schema",
                    change=SemanticDiffChange.ADDED,
                    item_id=schema_id,
                    label=f"Claim Schema {schema_id}",
                    new_value=current_schemas[schema_id].model_dump(mode="json"),
                    impact="新增 Claim Schema",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        if schema_id not in current_schemas:
            entries.append(
                SemanticDiffEntry(
                    category="claim_schema",
                    change=SemanticDiffChange.REMOVED,
                    item_id=schema_id,
                    label=f"Claim Schema {schema_id}",
                    old_value=previous_schemas[schema_id].model_dump(mode="json"),
                    impact="删除 Claim Schema",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        old_schema = previous_schemas[schema_id]
        new_schema = current_schemas[schema_id]
        if (
            old_schema.applies_to != new_schema.applies_to
            or old_schema.required_fields != new_schema.required_fields
        ):
            entries.append(
                _modified(
                    "claim_schema",
                    schema_id,
                    f"Claim Schema {schema_id} 证据映射",
                    {
                        "applies_to": old_schema.applies_to,
                        "required_fields": old_schema.required_fields,
                    },
                    {
                        "applies_to": new_schema.applies_to,
                        "required_fields": new_schema.required_fields,
                    },
                    "Claim Schema 适用范围或必填字段变化",
                )
            )

    previous_fixtures = (
        {fixture.fixture_id: fixture for fixture in previous.fixtures}
        if previous
        else {}
    )
    current_fixtures = {fixture.fixture_id: fixture for fixture in current.fixtures}
    for fixture_id in sorted(set(previous_fixtures) | set(current_fixtures)):
        if fixture_id not in previous_fixtures:
            entries.append(
                SemanticDiffEntry(
                    category="fixture",
                    change=SemanticDiffChange.ADDED,
                    item_id=fixture_id,
                    label=f"夹具 {fixture_id}",
                    new_value=current_fixtures[fixture_id].model_dump(mode="json"),
                    impact="新增夹具",
                    significance=SemanticSignificance.LOW,
                )
            )
            continue
        if fixture_id not in current_fixtures:
            entries.append(
                SemanticDiffEntry(
                    category="fixture",
                    change=SemanticDiffChange.REMOVED,
                    item_id=fixture_id,
                    label=f"夹具 {fixture_id}",
                    old_value=previous_fixtures[fixture_id].model_dump(mode="json"),
                    impact="删除夹具",
                    significance=SemanticSignificance.LOW,
                )
            )
            continue
        old_fixture = previous_fixtures[fixture_id]
        new_fixture = current_fixtures[fixture_id]
        if old_fixture.expected_status != new_fixture.expected_status:
            # 翻转方向有判定意义：回归（可发布变 BLOCKED）与放宽
            # （BLOCKED 变可发布）必须区分，不能只按集合判断。
            publishable = {"verified", "qualified", "partial", "pass", "passed"}
            human_gate = {"needs_human", "conflicted"}
            old_status = old_fixture.expected_status
            new_status = new_fixture.expected_status
            impact = "夹具翻转"
            significance = SemanticSignificance.MEDIUM
            if old_status in publishable and new_status == "blocked":
                impact = "可发布变为 BLOCKED"
                significance = SemanticSignificance.HIGH
            elif old_status == "blocked" and new_status in publishable:
                impact = "BLOCKED 变为可发布"
                significance = SemanticSignificance.HIGH
            elif old_status in human_gate and new_status == "blocked":
                impact = "人工门判定变为 BLOCKED"
                significance = SemanticSignificance.HIGH
            elif old_status == "blocked" and new_status in human_gate:
                impact = "变为人工门判定"
                significance = SemanticSignificance.HIGH
            elif old_status in publishable and new_status in human_gate:
                impact = "可发布变为人工门判定"
                significance = SemanticSignificance.HIGH
            elif old_status in human_gate and new_status in publishable:
                impact = "人工门判定变为可发布"
                significance = SemanticSignificance.HIGH
            entries.append(
                _modified(
                    "fixture",
                    fixture_id,
                    f"夹具 {fixture_id} 预期判定",
                    old_fixture.expected_status,
                    new_fixture.expected_status,
                    impact,
                    significance,
                )
            )
        if (
            old_fixture.expected_rule_ids != new_fixture.expected_rule_ids
            or old_fixture.expected_validator_ids != new_fixture.expected_validator_ids
        ):
            entries.append(
                _modified(
                    "fixture",
                    fixture_id,
                    f"夹具 {fixture_id} 规则绑定",
                    sorted(old_fixture.expected_rule_ids),
                    sorted(new_fixture.expected_rule_ids),
                    "夹具规则或校验器绑定变化",
                )
            )

    previous_validators = (
        {validator.validator_id: validator for validator in previous.validators}
        if previous
        else {}
    )
    current_validators = {
        validator.validator_id: validator for validator in current.validators
    }
    for validator_id in sorted(set(previous_validators) | set(current_validators)):
        if validator_id not in previous_validators:
            entries.append(
                SemanticDiffEntry(
                    category="validator",
                    change=SemanticDiffChange.ADDED,
                    item_id=validator_id,
                    label=f"校验器需求 {validator_id}",
                    new_value=current_validators[validator_id].model_dump(mode="json"),
                    impact="新增逻辑能力需求",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        if validator_id not in current_validators:
            entries.append(
                SemanticDiffEntry(
                    category="validator",
                    change=SemanticDiffChange.REMOVED,
                    item_id=validator_id,
                    label=f"校验器需求 {validator_id}",
                    old_value=previous_validators[validator_id].model_dump(mode="json"),
                    impact="删除逻辑能力需求",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        old_validator = previous_validators[validator_id]
        new_validator = current_validators[validator_id]
        if (
            old_validator.capability_name != new_validator.capability_name
            or old_validator.capability_version != new_validator.capability_version
            or old_validator.isolation_level != new_validator.isolation_level
            or old_validator.network_access != new_validator.network_access
            or old_validator.secret_access != new_validator.secret_access
        ):
            entries.append(
                _modified(
                    "validator",
                    validator_id,
                    f"校验器需求 {validator_id} 能力",
                    {
                        "capability": (
                            f"{old_validator.capability_name}"
                            f"@{old_validator.capability_version}"
                        ),
                        "isolation_level": old_validator.isolation_level,
                        "network_access": old_validator.network_access,
                        "secret_access": old_validator.secret_access,
                    },
                    {
                        "capability": (
                            f"{new_validator.capability_name}"
                            f"@{new_validator.capability_version}"
                        ),
                        "isolation_level": new_validator.isolation_level,
                        "network_access": new_validator.network_access,
                        "secret_access": new_validator.secret_access,
                    },
                    "逻辑能力需求变化",
                )
            )

    previous_dependencies = (
        {dependency.pack_id: dependency for dependency in previous.dependencies}
        if previous
        else {}
    )
    current_dependencies = {
        dependency.pack_id: dependency for dependency in current.dependencies
    }
    for pack_id in sorted(set(previous_dependencies) | set(current_dependencies)):
        if pack_id not in previous_dependencies:
            entries.append(
                SemanticDiffEntry(
                    category="dependency",
                    change=SemanticDiffChange.ADDED,
                    item_id=pack_id,
                    label=f"依赖 {pack_id}",
                    new_value=current_dependencies[pack_id].model_dump(mode="json"),
                    impact="新增依赖锁",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        if pack_id not in current_dependencies:
            entries.append(
                SemanticDiffEntry(
                    category="dependency",
                    change=SemanticDiffChange.REMOVED,
                    item_id=pack_id,
                    label=f"依赖 {pack_id}",
                    old_value=previous_dependencies[pack_id].model_dump(mode="json"),
                    impact="删除依赖锁",
                    significance=SemanticSignificance.MEDIUM,
                )
            )
            continue
        old_dependency = previous_dependencies[pack_id]
        new_dependency = current_dependencies[pack_id]
        if (
            old_dependency.version != new_dependency.version
            or old_dependency.digest != new_dependency.digest
        ):
            entries.append(
                _modified(
                    "dependency",
                    pack_id,
                    f"依赖 {pack_id} 版本",
                    f"{old_dependency.version}@{old_dependency.digest[:12]}",
                    f"{new_dependency.version}@{new_dependency.digest[:12]}",
                    "依赖版本或摘要变化",
                    SemanticSignificance.HIGH,
                )
            )
    return entries


def compute_semantic_diff_between(
    previous: DomainPackManifest | None,
    current: DomainPackManifest,
) -> SemanticDiff:
    """计算两个包版本之间的判定差异（纯函数，服务与测试共用）。"""
    entries = _diff_entries_between(previous, current)
    return SemanticDiff(
        diff_id=f"semantic-diff-{secrets.token_urlsafe(10)}",
        pack_id=current.id,
        from_version=previous.version if previous is not None else "(none)",
        to_version=current.version,
        entries=entries,
        digest=_hash_payload([entry.model_dump(mode="json") for entry in entries]),
    )


class DomainPackWorkbenchService:
    """职责分离的领域包发行流水线。"""

    def __init__(
        self,
        registry: DomainPackRegistry,
        runtime: DomainPackValidationRuntime | None = None,
        loader: DomainPackLoader | None = None,
    ) -> None:
        self._registry = registry
        self._loader = loader or DomainPackLoader()
        self._runtime = runtime or DomainPackValidationRuntime(self._loader)
        self._records: dict[tuple[str, str], WorkbenchPackRecord] = {}
        self._qualifications: dict[str, QualificationRecord] = {}
        self._activated: dict[tuple[str, str], DomainPackManifest] = {}
        self._default_active: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 登记与查询
    # ------------------------------------------------------------------

    def register_pack(
        self,
        person_id: str,
        loaded: LoadedDomainPack,
    ) -> WorkbenchPackRecord:
        """登记一个包版本到专家工作台；person_id 成为内容维护者。"""
        key = (loaded.pack_id, loaded.pack_version)
        if key in self._records:
            raise DomainPackWorkbenchError(
                f"工作台已登记该版本：{key[0]}@{key[1]}。",
                code="already_registered",
            )
        # 规范化摘要的语义 Diff 组件与工作台展示的版本对 Diff 一致：
        # 签名绑定的必须是评审者实际看到的判定差异。
        previous = self._previous_loaded(loaded.pack_id, loaded.pack_version)
        summary = canonical_pack_summary(
            loaded.manifest,
            previous.manifest if previous is not None else None,
        )
        run = self._runtime.run(loaded)
        record = WorkbenchPackRecord(
            pack_id=loaded.pack_id,
            pack_version=loaded.pack_version,
            manifest_digest=summary.manifest_digest,
            canonical_digest=summary.canonical_digest,
            stage=WorkbenchStage.DRAFTING,
            lifecycle_status=loaded.manifest.lifecycle_status,
            maintainer_id=person_id,
            checks=list(run.checks),
            fixture_results=list(run.fixture_results),
            updated_at=datetime.now(UTC),
        )
        self._records[key] = record
        return record

    def get_record(self, pack_id: str, version: str) -> WorkbenchPackRecord:
        key = (pack_id, version)
        record = self._records.get(key)
        if record is None:
            raise DomainPackWorkbenchError(
                f"工作台未登记：{pack_id}@{version}。",
                code="not_found",
            )
        return record

    def list_records(self) -> list[WorkbenchPackRecord]:
        return sorted(
            self._records.values(),
            key=lambda record: (record.pack_id, record.pack_version),
        )

    def get_loaded(self, pack_id: str, version: str) -> LoadedDomainPack:
        try:
            return self._registry.get(pack_id, version)
        except DomainPackRegistryError as exc:
            raise DomainPackWorkbenchError(str(exc), code="not_found") from exc

    # ------------------------------------------------------------------
    # 资质与利益冲突
    # ------------------------------------------------------------------

    def register_qualification(self, qualification: QualificationRecord) -> None:
        if qualification.qualification_id in self._qualifications:
            raise DomainPackWorkbenchError(
                f"资质已登记：{qualification.qualification_id}。",
                code="already_registered",
            )
        self._qualifications[qualification.qualification_id] = qualification

    def list_qualifications(self, person_id: str) -> list[QualificationRecord]:
        return [
            qualification
            for qualification in self._qualifications.values()
            if qualification.person_id == person_id
        ]

    def declare_conflict_of_interest(
        self,
        person_id: str,
        pack_id: str,
        version: str,
        disclosures: Sequence[str],
    ) -> ConflictOfInterestDeclaration:
        record = self.get_record(pack_id, version)
        declaration = ConflictOfInterestDeclaration(
            declaration_id=f"coi-{secrets.token_urlsafe(10)}",
            person_id=person_id,
            pack_id=pack_id,
            pack_version=version,
            disclosures=list(disclosures),
            declared_at=datetime.now(UTC),
        )
        self._records[(pack_id, version)] = record.model_copy(
            update={
                "declarations": list(record.declarations) + [declaration],
                "updated_at": datetime.now(UTC),
            }
        )
        return declaration

    def add_conflict_disclosure(
        self,
        pack_id: str,
        version: str,
        item_ref: str,
        minority_opinion: str,
        basis: Sequence[str] = (),
        missing_evidence: Sequence[str] = (),
        decision: str = "",
    ) -> ConflictDisclosure:
        record = self.get_record(pack_id, version)
        disclosure = ConflictDisclosure(
            disclosure_id=f"disclosure-{secrets.token_urlsafe(10)}",
            pack_id=pack_id,
            pack_version=version,
            item_ref=item_ref,
            minority_opinion=minority_opinion,
            basis=list(basis),
            missing_evidence=list(missing_evidence),
            decision=decision,
            created_at=datetime.now(UTC),
        )
        self._records[(pack_id, version)] = record.model_copy(
            update={
                "disclosures": list(record.disclosures) + [disclosure],
                "updated_at": datetime.now(UTC),
            }
        )
        return disclosure

    # ------------------------------------------------------------------
    # 语义 Diff
    # ------------------------------------------------------------------

    def semantic_diff_for(self, pack_id: str, version: str) -> SemanticDiff:
        current = self.get_loaded(pack_id, version)
        previous = self._previous_loaded(pack_id, version)
        return compute_semantic_diff_between(
            previous.manifest if previous is not None else None,
            current.manifest,
        )

    def _previous_loaded(
        self, pack_id: str, version: str
    ) -> LoadedDomainPack | None:
        versions = self._registry.list_versions(pack_id)
        previous = None
        for candidate in versions:
            if candidate == version:
                break
            previous = self._registry.get(pack_id, candidate)
        return previous

    def get_active_manifest(
        self, pack_id: str, version: str
    ) -> DomainPackManifest | None:
        """读取已发行版本的激活副本；未发行时返回 None。"""
        return self._activated.get((pack_id, version))

    # ------------------------------------------------------------------
    # T047：治理状态与受信回滚挂钩
    # ------------------------------------------------------------------

    def list_loaded_packs(self) -> list[LoadedDomainPack]:
        """列出工作台已登记的全部包版本（用于依赖扫描）。"""
        loaded: list[LoadedDomainPack] = []
        for pack_id, version in self._records:
            try:
                loaded.append(self.get_loaded(pack_id, version))
            except DomainPackWorkbenchError:
                continue
        return loaded

    def list_registered_versions(self, pack_id: str) -> list[str]:
        """列出包的全部已登记版本（按语义版本升序）。"""
        return self._registry.list_versions(pack_id)

    def set_lifecycle_status(
        self,
        pack_id: str,
        version: str,
        status: DomainPackStatus,
    ) -> WorkbenchPackRecord:
        """由平台状态机设置包治理状态（撤销、暂停等），不能由包文件自设。

        操作者记录在对应的事件（如 RevocationEvent.revoked_by）中，
        工作台记录本身不保存 actor。
        """
        key = (pack_id, version)
        record = self._records.get(key)
        if record is None:
            raise DomainPackWorkbenchError(
                f"工作台未登记：{pack_id}@{version}。",
                code="not_found",
            )
        updated = record.model_copy(
            update={
                "lifecycle_status": status,
                "updated_at": datetime.now(UTC),
            }
        )
        self._records[key] = updated
        return updated

    def set_default_active(
        self,
        pack_id: str,
        version: str,
        loaded: LoadedDomainPack,
    ) -> None:
        """受信回滚：把仍受信的旧版重新设为项目可选版本。

        只切换默认版本，不修改任何版本的历史治理状态；已撤销版本
        仍保持 REVOKED，不能通过回滚复活。
        """
        active_manifest = self.get_active_manifest(pack_id, version) or loaded.manifest
        self._activated[(pack_id, version)] = active_manifest
        self._default_active[pack_id] = version

    def get_default_active_version(self, pack_id: str) -> str | None:
        """当前项目可选默认版本；无回滚记录时取最高已发行版本。"""
        if pack_id in self._default_active:
            return self._default_active[pack_id]
        released = [
            (record.pack_version, record)
            for (registered_id, _), record in self._records.items()
            if registered_id == pack_id and record.stage == WorkbenchStage.RELEASED
        ]
        if not released:
            return None
        return max(
            released,
            key=lambda item: _parse_version(item[0]) or (0, 0, 0),
        )[0]

    # ------------------------------------------------------------------
    # 三签
    # ------------------------------------------------------------------

    def _require_governance_usable(self, record: WorkbenchPackRecord) -> None:
        """已撤销、暂停或过期的版本不能重新签名、灰度或发行。

        治理状态只能由平台状态机（安全管理员撤销等）改变；工作台流程
        不能把 REVOKED/SUSPENDED/EXPIRED 版本重新激活为 ACTIVE，
        否则撤销记录之外存在第二条复活路径。
        """
        if record.lifecycle_status in {
            DomainPackStatus.REVOKED,
            DomainPackStatus.SUSPENDED,
            DomainPackStatus.EXPIRED,
        }:
            raise DomainPackWorkbenchError(
                f"版本处于 {record.lifecycle_status.value} 治理状态，不能继续签名、灰度或发行。",
                code="governance_blocked",
            )

    def submit_content_signature(
        self,
        person_id: str,
        pack_id: str,
        version: str,
        *,
        conclusion: AttestationConclusion,
        opinion: str,
    ) -> ReviewAttestation:
        """内容维护者对规范化包摘要提交内容签名。"""
        record = self.get_record(pack_id, version)
        self._require_governance_usable(record)
        if record.stage in {WorkbenchStage.RELEASED}:
            raise DomainPackWorkbenchError(
                "已发行版本不能重新签名。",
                code="released",
            )
        if person_id != record.maintainer_id:
            raise DomainPackWorkbenchError(
                "只有内容维护者可以提交内容签名。",
                code="role_required",
            )
        if conclusion != AttestationConclusion.APPROVE:
            raise DomainPackWorkbenchError(
                "内容签名结论必须是 approve。",
                code="invalid_conclusion",
            )
        self._require_declaration(record, person_id)
        loaded = self.get_loaded(pack_id, version)
        summary = self._require_digest(record, loaded)
        attestation = ReviewAttestation(
            attestation_id=f"attestation-{secrets.token_urlsafe(10)}",
            pack_id=pack_id,
            pack_version=version,
            canonical_digest=summary.canonical_digest,
            role=ReviewRole.CONTENT,
            person_id=person_id,
            conflict_declaration_id=self._declaration_id(record, person_id),
            conclusion=conclusion,
            opinion=opinion,
            signed_at=datetime.now(UTC),
        )
        self._records[(pack_id, version)] = record.model_copy(
            update={
                "attestations": list(record.attestations) + [attestation],
                "stage": WorkbenchStage.CONTENT_SIGNED,
                "updated_at": datetime.now(UTC),
            }
        )
        return attestation

    def assign_reviewer(
        self,
        person_id: str,
        pack_id: str,
        version: str,
        reviewer_id: str,
    ) -> WorkbenchPackRecord:
        """内容维护者分配独立复核者；同人双角色被拒绝。"""
        record = self.get_record(pack_id, version)
        if record.stage != WorkbenchStage.CONTENT_SIGNED:
            raise DomainPackWorkbenchError(
                "提交内容签名后才能分配独立复核者。",
                code="stage_required",
            )
        if person_id != record.maintainer_id:
            raise DomainPackWorkbenchError(
                "只有内容维护者可以分配独立复核者。",
                code="role_required",
            )
        if reviewer_id == record.maintainer_id:
            raise DomainPackWorkbenchError(
                "同一自然人不能同时充当维护者和独立复核者。",
                code="role_conflict",
            )
        if reviewer_id == record.releaser_id:
            raise DomainPackWorkbenchError(
                "同一自然人不能同时充当独立复核者和平台发行者。",
                code="role_conflict",
            )
        if record.reviewer_id is not None:
            raise DomainPackWorkbenchError(
                "独立复核者已经分配。",
                code="reviewer_assigned",
            )
        updated = record.model_copy(
            update={
                "reviewer_id": reviewer_id,
                "stage": WorkbenchStage.REVIEW_ASSIGNED,
                "updated_at": datetime.now(UTC),
            }
        )
        self._records[(pack_id, version)] = updated
        return updated

    def assign_releaser(
        self,
        person_id: str,
        pack_id: str,
        version: str,
        releaser_id: str,
    ) -> WorkbenchPackRecord:
        """内容维护者分配平台发行者；发行者不能兼任维护者或复核者。"""
        record = self.get_record(pack_id, version)
        if person_id != record.maintainer_id:
            raise DomainPackWorkbenchError(
                "只有内容维护者可以分配平台发行者。",
                code="role_required",
            )
        if releaser_id in {record.maintainer_id, record.reviewer_id}:
            raise DomainPackWorkbenchError(
                "平台发行者不能兼任维护者或独立复核者。",
                code="role_conflict",
            )
        if record.releaser_id is not None:
            raise DomainPackWorkbenchError(
                "平台发行者已经分配。",
                code="releaser_assigned",
            )
        updated = record.model_copy(
            update={
                "releaser_id": releaser_id,
                "updated_at": datetime.now(UTC),
            }
        )
        self._records[(pack_id, version)] = updated
        return updated

    def submit_independent_signature(
        self,
        person_id: str,
        pack_id: str,
        version: str,
        *,
        conclusion: AttestationConclusion,
        opinion: str,
    ) -> ReviewAttestation:
        """独立复核者在重跑夹具、抽查来源后提交独立验证签名。

        同一自然人不能对同一版本同时充当维护者和独立复核者；
        复核者资质必须覆盖包范围且未过期；
        缺少反例、恶意例或人工门夹具时阻止进入独立复核。
        """
        record = self.get_record(pack_id, version)
        self._require_governance_usable(record)
        if person_id == record.maintainer_id:
            raise DomainPackWorkbenchError(
                "同一自然人不能同时充当维护者和独立复核者。",
                code="role_conflict",
            )
        if person_id != record.reviewer_id:
            raise DomainPackWorkbenchError(
                "只有被分配的独立复核者可以提交独立验证签名。",
                code="role_required",
            )
        self._require_declaration(record, person_id)
        self._require_qualification(record, person_id)
        coverage = self._fixture_coverage_issues(record)
        if coverage:
            raise DomainPackWorkbenchError(
                "；".join(coverage),
                code="fixture_coverage",
            )
        loaded = self.get_loaded(pack_id, version)
        summary = self._require_digest(record, loaded)
        run = self._runtime.run(loaded)
        failed = [result for result in run.fixture_results if not result.passed]
        if failed and conclusion is AttestationConclusion.APPROVE:
            raise DomainPackWorkbenchError(
                "夹具重放失败，独立复核结论不能是 approve。",
                code="fixture_failed",
            )
        attestation = ReviewAttestation(
            attestation_id=f"attestation-{secrets.token_urlsafe(10)}",
            pack_id=pack_id,
            pack_version=version,
            canonical_digest=summary.canonical_digest,
            role=ReviewRole.INDEPENDENT,
            person_id=person_id,
            qualification_ids=self._qualification_ids(record, person_id),
            conflict_declaration_id=self._declaration_id(record, person_id),
            conclusion=conclusion,
            opinion=opinion,
            signed_at=datetime.now(UTC),
        )
        stage = (
            WorkbenchStage.CHANGES_REQUESTED
            if conclusion != AttestationConclusion.APPROVE
            else WorkbenchStage.REVIEW_COMPLETED
        )
        self._records[(pack_id, version)] = record.model_copy(
            update={
                "attestations": list(record.attestations) + [attestation],
                "stage": stage,
                "checks": list(run.checks),
                "fixture_results": list(run.fixture_results),
                "updated_at": datetime.now(UTC),
            }
        )
        return attestation

    # ------------------------------------------------------------------
    # 灰度与发行
    # ------------------------------------------------------------------

    def prepare_gray_release(
        self,
        person_id: str,
        pack_id: str,
        version: str,
    ) -> GrayReleaseCandidate:
        """生成可发行候选；灰度结果不会自动激活领域包。"""
        record = self.get_record(pack_id, version)
        self._require_governance_usable(record)
        if person_id not in {
            record.maintainer_id,
            record.reviewer_id,
            record.releaser_id,
        }:
            raise DomainPackWorkbenchError(
                "只有登记了维护者、复核者或发行者角色的自然人可以准备灰度。",
                code="role_required",
            )
        self._require_approvals(record, (ReviewRole.CONTENT, ReviewRole.INDEPENDENT))
        loaded = self.get_loaded(pack_id, version)
        summary = self._require_digest(record, loaded)
        run = self._runtime.run(loaded)
        failed = [result for result in run.fixture_results if not result.passed]
        candidate = GrayReleaseCandidate(
            candidate_id=f"gray-{secrets.token_urlsafe(10)}",
            pack_id=pack_id,
            pack_version=version,
            canonical_digest=summary.canonical_digest,
            status=(
                GrayReleaseStatus.BLOCKED
                if failed
                else GrayReleaseStatus.READY_TO_RELEASE
            ),
            checks=list(run.checks),
            fixture_results=list(run.fixture_results),
            fixture_summary=_fixture_summary_stats(loaded.manifest.fixtures),
            created_by=person_id,
            created_at=datetime.now(UTC),
        )
        stage = (
            WorkbenchStage.GRAY_RELEASE_READY
            if candidate.status == GrayReleaseStatus.READY_TO_RELEASE
            else WorkbenchStage.RELEASE_PREFLIGHT
        )
        self._records[(pack_id, version)] = record.model_copy(
            update={
                "gray_candidate": candidate,
                "stage": stage,
                "checks": list(run.checks),
                "fixture_results": list(run.fixture_results),
                "updated_at": datetime.now(UTC),
            }
        )
        return candidate

    def get_gray_candidate(
        self, pack_id: str, version: str
    ) -> GrayReleaseCandidate | None:
        return self.get_record(pack_id, version).gray_candidate

    # ------------------------------------------------------------------
    # H3 高风险联合门
    # ------------------------------------------------------------------

    @staticmethod
    def declares_h3(manifest: DomainPackManifest) -> bool:
        """包是否声明 H3 高风险变更（publication_stage_rules 或规则人工门）。"""
        if any(
            str(rule.get("gate_level", "")).upper() == "H3"
            or "h3" in str(rule.get("stage", "")).lower()
            for rule in manifest.publication_stage_rules
        ):
            return True
        return any(rule.human_gate == "H3" for rule in manifest.rules)

    def confirm_h3_joint_gate(
        self,
        security_admin_id: str,
        pack_id: str,
        version: str,
        *,
        opinion: str = "",
    ) -> H3SecurityConfirmation:
        """安全/治理责任人对 H3 高风险变更的联合确认。

        调用方（API 层）必须先验证 security_admin_id 是安全管理员；
        本方法只登记确认，不自行判定身份。
        """
        record = self.get_record(pack_id, version)
        loaded = self.get_loaded(pack_id, version)
        if not self.declares_h3(loaded.manifest):
            raise DomainPackWorkbenchError(
                "该包版本未声明 H3 高风险变更，不需要安全治理联合确认。",
                code="h3_not_declared",
            )
        if any(
            item.confirmed_by == security_admin_id
            for item in record.h3_security_confirmations
        ):
            raise DomainPackWorkbenchError(
                "该安全管理员已经确认过本版本的 H3 联合门。",
                code="already_confirmed",
            )
        confirmation = H3SecurityConfirmation(
            confirmation_id=f"h3-confirm-{secrets.token_urlsafe(10)}",
            pack_id=pack_id,
            pack_version=version,
            confirmed_by=security_admin_id,
            opinion=opinion,
            confirmed_at=datetime.now(UTC),
        )
        self._records[(pack_id, version)] = record.model_copy(
            update={
                "h3_security_confirmations": list(
                    record.h3_security_confirmations
                )
                + [confirmation],
                "updated_at": datetime.now(UTC),
            }
        )
        return confirmation

    def release(self, person_id: str, pack_id: str, version: str) -> PackRelease:
        """平台发行者重新验证全部签名与门后追加发行签名并激活。

        灰度只生成可发行候选；激活必须经过这里的发行签名。
        """
        record = self.get_record(pack_id, version)
        self._require_governance_usable(record)
        if person_id != record.releaser_id:
            raise DomainPackWorkbenchError(
                "只有平台发行者可以追加发行签名。",
                code="role_required",
            )
        if record.stage in {WorkbenchStage.RELEASED}:
            raise DomainPackWorkbenchError(
                "该版本已经发行。",
                code="released",
            )
        self._require_declaration(record, person_id)
        self._require_approvals(record, (ReviewRole.CONTENT, ReviewRole.INDEPENDENT))
        candidate = record.gray_candidate
        if candidate is None or candidate.status != GrayReleaseStatus.READY_TO_RELEASE:
            raise DomainPackWorkbenchError(
                "没有可用的 READY_TO_RELEASE 灰度候选。",
                code="gray_required",
            )
        loaded = self.get_loaded(pack_id, version)
        summary = self._require_digest(record, loaded)
        if candidate.canonical_digest != summary.canonical_digest:
            raise DomainPackWorkbenchError(
                "灰度候选摘要与当前内容不一致；需要重新灰度。",
                code="digest_mismatch",
            )
        # 发行前重新确认：签名摘要、资质、利益冲突、夹具与平台下限仍有效。
        if not record.checks or any(
            check.status.value == "blocked" for check in record.checks
        ):
            raise DomainPackWorkbenchError(
                "预检存在阻塞项，不能发行。",
                code="preflight_blocked",
            )
        # H3 高风险变更：合资格领域专家（独立复核者资质）之外，
        # 还要求安全/治理责任人联合确认；任一缺失即闭锁发行。
        if self.declares_h3(loaded.manifest) and not record.h3_security_confirmations:
            raise DomainPackWorkbenchError(
                "H3 高风险变更要求安全治理责任人联合确认后才能发行。",
                code="h3_security_required",
            )
        platform_attestation = ReviewAttestation(
            attestation_id=f"attestation-{secrets.token_urlsafe(10)}",
            pack_id=pack_id,
            pack_version=version,
            canonical_digest=summary.canonical_digest,
            role=ReviewRole.PLATFORM,
            person_id=person_id,
            conflict_declaration_id=self._declaration_id(record, person_id),
            conclusion=AttestationConclusion.APPROVE,
            opinion="平台发行预检通过：签名、资质、冲突、依赖、夹具与灰度门均有效。",
            signed_at=datetime.now(UTC),
        )
        attestations = list(record.attestations) + [platform_attestation]
        active_manifest = self._validate_active_manifest(loaded, attestations)
        previous_loaded = self._previous_loaded(pack_id, version)
        release = PackRelease(
            release_id=f"release-{secrets.token_urlsafe(10)}",
            pack_id=pack_id,
            pack_version=version,
            canonical_digest=summary.canonical_digest,
            attestations=attestations,
            platform_attestation=platform_attestation,
            gray_candidate_id=candidate.candidate_id,
            released_by=person_id,
            released_at=datetime.now(UTC),
            transparent_record={
                "pack_id": pack_id,
                "pack_version": version,
                "canonical_digest": summary.canonical_digest,
                "manifest_digest": summary.manifest_digest,
                "active_manifest_digest": DomainPackLoader.manifest_digest(
                    active_manifest
                ),
                "supersedes": (
                    previous_loaded.pack_version
                    if previous_loaded is not None
                    else None
                ),
                "revalidation_required": bool(
                    loaded.manifest.compatibility.requires_revalidation
                ),
                "signers": [
                    {
                        "role": item.role.value,
                        "person_id": item.person_id,
                        "conclusion": item.conclusion.value,
                        "signed_at": item.signed_at.isoformat(),
                    }
                    for item in attestations
                ],
                "gray_candidate_id": candidate.candidate_id,
            },
        )
        self._activated[(pack_id, version)] = active_manifest
        self._records[(pack_id, version)] = record.model_copy(
            update={
                "attestations": attestations,
                "release": release,
                "stage": WorkbenchStage.RELEASED,
                "lifecycle_status": DomainPackStatus.ACTIVE,
                "updated_at": datetime.now(UTC),
            }
        )
        return release

    # ------------------------------------------------------------------
    # 内部门检查
    # ------------------------------------------------------------------

    def _require_digest(
        self, record: WorkbenchPackRecord, loaded: LoadedDomainPack
    ) -> CanonicalPackSummary:
        """内容与登记摘要一致时返回当前规范化摘要；任何变化使旧签名失效。"""
        previous = self._previous_loaded(loaded.pack_id, loaded.pack_version)
        summary = canonical_pack_summary(
            loaded.manifest,
            previous.manifest if previous is not None else None,
        )
        if summary.canonical_digest != record.canonical_digest:
            raise DomainPackWorkbenchError(
                "包内容已变化，旧摘要不再适用；请重新登记。",
                code="digest_mismatch",
            )
        return summary

    def _require_declaration(
        self, record: WorkbenchPackRecord, person_id: str
    ) -> None:
        if not any(
            item.person_id == person_id for item in record.declarations
        ):
            raise DomainPackWorkbenchError(
                "签名前必须声明本版本利益冲突。",
                code="conflict_required",
            )

    def _declaration_id(
        self, record: WorkbenchPackRecord, person_id: str
    ) -> str:
        declaration = next(
            (item for item in record.declarations if item.person_id == person_id),
            None,
        )
        if declaration is None:
            raise DomainPackWorkbenchError(
                "签名前必须声明本版本利益冲突。",
                code="conflict_required",
            )
        return declaration.declaration_id

    def _require_qualification(
        self, record: WorkbenchPackRecord, person_id: str
    ) -> None:
        ids = self._qualification_ids(record, person_id)
        if not ids:
            raise DomainPackWorkbenchError(
                "复核者资质必须覆盖包范围且未过期；当前没有可用资质。",
                code="qualification_required",
            )

    def _qualification_ids(
        self, record: WorkbenchPackRecord, person_id: str
    ) -> list[str]:
        loaded = self.get_loaded(record.pack_id, record.pack_version)
        disciplines = set(loaded.manifest.disciplines)
        return [
            qualification.qualification_id
            for qualification in self._qualifications.values()
            if qualification.person_id == person_id
            and not qualification.is_expired
            and (not disciplines or set(qualification.disciplines) & disciplines)
        ]

    def _fixture_coverage_issues(self, record: WorkbenchPackRecord) -> list[str]:
        """决策：缺反例、恶意例或人工门夹具时阻止进入独立复核。

        夹具类别按判定语义识别：通过状态为正例；blocked/恶意或反例
        kind 为失败路径；needs_human/conflicted 或 requires_human 为人工门。
        """
        loaded = self.get_loaded(record.pack_id, record.pack_version)
        fixtures = loaded.manifest.fixtures
        pass_statuses = {"verified", "qualified", "partial", "pass", "passed"}
        fail_statuses = {"blocked", "quarantined", "stale_or_updated", "failed"}
        issues: list[str] = []
        if not any(
            fixture.expected_status in pass_statuses
            or fixture.kind in {"positive", "pass"}
            for fixture in fixtures
        ):
            issues.append("缺少正例夹具")
        if not any(
            fixture.expected_status in fail_statuses
            or fixture.kind in {"negative", "malicious", "failure"}
            for fixture in fixtures
        ):
            issues.append("缺少反例或恶意例夹具")
        if not any(
            fixture.requires_human
            or fixture.expected_status in {"needs_human", "conflicted"}
            for fixture in fixtures
        ):
            issues.append("缺少人工门夹具")
        return issues

    @staticmethod
    def _require_approvals(
        record: WorkbenchPackRecord,
        roles: Sequence[ReviewRole],
    ) -> None:
        for role in roles:
            for attestation in reversed(record.attestations):
                if attestation.role != role:
                    continue
                if attestation.conclusion != AttestationConclusion.APPROVE:
                    raise DomainPackWorkbenchError(
                        f"{role.value} 角色最近一次签名结论不是 approve。",
                        code="signature_missing",
                    )
                if attestation.canonical_digest != record.canonical_digest:
                    raise DomainPackWorkbenchError(
                        f"{role.value} 签名与当前规范化摘要不一致。",
                        code="digest_mismatch",
                    )
                break
            else:
                raise DomainPackWorkbenchError(
                    f"缺少 {role.value} 角色的批准签名。",
                    code="signature_missing",
                )

    def _validate_active_manifest(
        self,
        loaded: LoadedDomainPack,
        attestations: Sequence[ReviewAttestation],
    ) -> DomainPackManifest:
        """构建激活副本：完整三签与治理状态必须通过加载器预检。

        规范化包摘要包含治理状态，因此激活后的摘要重新计算，
        激活副本的内容摘要与签名都绑定激活后的摘要。返回的激活
        副本由调用方持久化，保证发行后包以 ACTIVE 状态可见。
        """
        manifest = loaded.manifest
        active_manifest = manifest.model_copy(
            update={
                "signatures": [
                    DomainManifestSignature(
                        signer_id=f"{item.role.value}:{item.person_id}",
                        signed_digest=loaded.manifest_digest,
                        scope=["manifest", "manifest_digest", "content_digest"],
                        signature=f"attestation:{item.attestation_id}",
                    )
                    for item in attestations
                ],
                "lifecycle_status": DomainPackStatus.ACTIVE,
            }
        )
        active_digest = DomainPackLoader.manifest_digest(active_manifest)
        active_manifest = active_manifest.model_copy(
            update={
                "content_digest": active_digest,
                "signatures": [
                    DomainManifestSignature(
                        signer_id=item.signer_id,
                        signed_digest=active_digest,
                        scope=item.scope,
                        signature=item.signature,
                    )
                    for item in active_manifest.signatures
                ],
            }
        )
        report = self._loader.validate_manifest(active_manifest)
        if not report.accepted:
            raise DomainPackWorkbenchError(
                f"激活校验失败：{'；'.join(report.blocked_reasons)}",
                code="activation_rejected",
            )
        return active_manifest
