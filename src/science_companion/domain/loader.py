"""领域包 Manifest 预检与安全加载器。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from science_companion.ai.capability_registry import (
    CapabilityRegistry,
    CapabilityRegistryError,
)
from science_companion.contracts.ai import CapabilityKind, CapabilityStatus
from science_companion.contracts.domain import (
    DomainCheckStatus,
    DomainPackManifest,
    DomainPackPreflightReport,
    DomainPackUpgradeReport,
    DomainValidationCheck,
    PackDependencyLock,
)
from science_companion.domain.protocol import (
    DomainPack,
    DomainPackLoadError,
    LoadedDomainPack,
)

_SEMVER_RE = re.compile(
    r"^(?:v)?(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?"
    r"(?:\.(0|[1-9]\d*))?(?:-[0-9A-Za-z.-]+)?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)
_SUPPORTED_MANIFEST_SCHEMA = "domain-pack-manifest/v1"
_SUPPORTED_PACK_API = "domain-pack/v1"

class DomainPackLoader:
    """Validate and load a candidate domain-pack implementation."""

    def __init__(
        self,
        *,
        platform_api_version: str = "1.0.0",
        supported_manifest_schema: str = _SUPPORTED_MANIFEST_SCHEMA,
        supported_pack_api: str = _SUPPORTED_PACK_API,
        capability_registry: CapabilityRegistry | None = None,
        require_capability_contracts: bool = True,
        available_dependencies: Mapping[str, PackDependencyLock] | None = None,
    ) -> None:
        self.platform_api_version = platform_api_version
        self.supported_manifest_schema = supported_manifest_schema
        self.supported_pack_api = supported_pack_api
        self.capability_registry = capability_registry
        self.require_capability_contracts = require_capability_contracts
        self.available_dependencies = available_dependencies or {}

    def validate_manifest(
        self,
        manifest: DomainPackManifest,
        *,
        implementation: DomainPack | None = None,
    ) -> DomainPackPreflightReport:
        """Return an explainable preflight report without executing the pack."""
        # 统一收敛 model_copy 或外部反序列化留下的未校验嵌套值。
        manifest = DomainPackManifest.model_validate(manifest.model_dump(mode="python"))
        digest = self.manifest_digest(manifest)
        checks: list[DomainValidationCheck] = []

        self._check(
            checks,
            "manifest_schema",
            "Manifest Schema",
            manifest.manifest_schema == self.supported_manifest_schema,
            f"不支持的 Manifest Schema：{manifest.manifest_schema}。",
        )
        self._check(
            checks,
            "pack_version",
            "包版本",
            _parse_version(manifest.version) is not None,
            f"包版本不是合法语义版本：{manifest.version}。",
        )
        self._check(
            checks,
            "platform_api",
            "平台兼容范围",
            _version_satisfies(self.platform_api_version, manifest.platform_api),
            f"包要求的平台 API {manifest.platform_api} 与当前版本 "
            f"{self.platform_api_version} 不兼容。",
        )
        self._check(
            checks,
            "pack_api",
            "领域包协议版本",
            manifest.pack_api == self.supported_pack_api,
            f"不支持的领域包协议：{manifest.pack_api}。",
        )
        declared_digest = manifest.content_digest
        self._check(
            checks,
            "content_digest",
            "包内容摘要",
            declared_digest is not None
            and _is_digest(declared_digest)
            and _normalize_digest(declared_digest) == digest,
            "Manifest 声明的内容摘要与规范化包摘要不一致。",
            details={"computed_digest": digest},
        )
        self._check_signature_coverage(checks, manifest, digest)
        self._check(
            checks,
            "lifecycle_status",
            "包治理状态",
            manifest.lifecycle_status.value
            not in {"revoked", "expired", "suspended"},
            "已撤销、过期或暂停的包不能进入候选验证运行时。",
        )
        self._check(
            checks,
            "manifest_sections",
            "Manifest 必备学科内容",
            bool(manifest.scope)
            and bool(manifest.exclusions)
            and bool(manifest.risk_tiers)
            and bool(manifest.question_types)
            and bool(manifest.source_policies)
            and bool(manifest.claim_schemas)
            and bool(manifest.wording_policy)
            and bool(manifest.rules)
            and bool(manifest.validators)
            and bool(manifest.fixtures),
            "Manifest 必须声明范围、排除项、风险、问题类型、来源、规则、校验器和夹具。",
        )
        self._check_unique_ids(checks, manifest)
        self._check_references(checks, manifest)
        self._check_domain_coverage(checks, manifest)
        self._check_dependency_locks(checks, manifest)
        self._check_platform_floor(checks, manifest)
        self._check_content_paths(checks, manifest)
        self._check_capabilities(checks, manifest)

        if implementation is not None:
            self._check(
                checks,
                "implementation_protocol",
                "领域包协议实现",
                isinstance(implementation, DomainPack),
                "领域包实现必须完整实现稳定协议中的全部方法。",
            )
            implementation_manifest = getattr(implementation, "manifest", None)
            same_identity = isinstance(implementation_manifest, DomainPackManifest) and (
                implementation_manifest.id == manifest.id
                and implementation_manifest.version == manifest.version
            )
            self._check(
                checks,
                "implementation_manifest",
                "实现与 Manifest 一致",
                same_identity,
                "领域包实现携带的 Manifest 与候选 Manifest 标识或版本不一致。",
            )

        blocked_reasons = [
            check.reason for check in checks if check.status == DomainCheckStatus.BLOCKED
        ]
        return DomainPackPreflightReport(
            accepted=not blocked_reasons,
            manifest_digest=digest,
            checks=checks,
            blocked_reasons=blocked_reasons,
        )

    def validate(
        self,
        manifest: DomainPackManifest,
        *,
        implementation: DomainPack | None = None,
    ) -> DomainPackPreflightReport:
        """Alias for callers that treat the loader as a validator."""
        return self.validate_manifest(manifest, implementation=implementation)

    def load(
        self,
        candidate: DomainPackManifest | DomainPack,
        implementation: DomainPack | None = None,
    ) -> LoadedDomainPack:
        """Validate and return a candidate that is safe to execute."""
        manifest: DomainPackManifest | None
        if isinstance(candidate, DomainPackManifest):
            manifest = candidate
            pack = implementation
        else:
            pack = candidate
            manifest = getattr(pack, "manifest", None)

        if not isinstance(manifest, DomainPackManifest) or pack is None:
            raise DomainPackLoadError(
                "加载领域包需要 DomainPackManifest 和实现对象。"
            )

        manifest = DomainPackManifest.model_validate(manifest.model_dump(mode="python"))
        report = self.validate_manifest(manifest, implementation=pack)
        if not report.accepted:
            reason = "；".join(report.blocked_reasons)
            raise DomainPackLoadError(f"领域包候选被阻断：{reason}", report)

        return LoadedDomainPack(
            manifest=manifest,
            implementation=pack,
            manifest_digest=report.manifest_digest,
            checks=tuple(report.checks),
        )

    def load_candidate(
        self,
        candidate: DomainPackManifest | DomainPack,
        implementation: DomainPack | None = None,
    ) -> LoadedDomainPack:
        """Explicit alias for the candidate-loading seam."""
        return self.load(candidate, implementation)

    def validate_upgrade(
        self,
        previous: DomainPackManifest,
        candidate: DomainPackManifest,
    ) -> DomainPackUpgradeReport:
        """检查版本递增、兼容、迁移、失效、撤权和受信回滚闭锁。"""
        # model_copy(update=...) 不会重新校验更新字段；在跨版本边界再次解析，
        # 防止未经验证的字典绕过迁移和撤权策略检查。
        previous = DomainPackManifest.model_validate(previous.model_dump(mode="python"))
        candidate = DomainPackManifest.model_validate(candidate.model_dump(mode="python"))
        issues: list[str] = []
        old_version = _parse_version(previous.version)
        new_version = _parse_version(candidate.version)
        if previous.id != candidate.id:
            issues.append("升级前后领域包标识不一致")
        if old_version is None or new_version is None:
            issues.append("升级版本必须是合法语义版本")
        elif new_version <= old_version:
            issues.append("新版本必须严格高于旧版本")

        migration_required = bool(
            old_version is not None
            and new_version is not None
            and new_version[0] != old_version[0]
        )
        migration = next(
            (
                migration
                for migration in candidate.migrations
                if migration.from_version == previous.version
                and migration.to_version == candidate.version
            ),
            None,
        )
        migration_present = migration is not None
        if migration_required and not migration_present:
            issues.append("major 升级必须提供可重放迁移")
        if migration is not None:
            if not migration.preserves_history:
                issues.append("迁移不得丢弃历史运行和审计记录")
            if not migration.requires_fixture_replay:
                issues.append("迁移必须要求夹具重放")
            if migration.rollback_version not in {None, previous.version}:
                issues.append("迁移声明的回滚版本必须指向受信旧版本")

        compatibility_declared = previous.version in {
            *candidate.compatibility.compatible_with,
            "*",
        }
        if not compatibility_declared:
            issues.append("升级必须明确声明与旧版本的兼容范围")
        if not candidate.compatibility.preserves_runtime_contract:
            issues.append("升级不得削弱领域包运行时协议")

        trusted_rollbacks = set(candidate.rollback_versions)
        trusted_rollbacks.update(candidate.rollback_policy.trusted_versions)
        rollback_trusted = previous.version in trusted_rollbacks
        if not rollback_trusted:
            issues.append("升级必须把当前受信旧版本加入回滚白名单")
        if not candidate.rollback_policy.requires_fixture_replay:
            issues.append("回滚必须要求夹具重放")
        if not candidate.rollback_policy.preserves_history:
            issues.append("回滚不得丢弃历史运行和审计记录")

        invalidation_closed = candidate.invalidation_policy.preserve_history
        if not invalidation_closed:
            issues.append("失效传播不得清除历史记录")

        revocation_closed = (
            candidate.revocation_policy.new_runs == "block"
            and candidate.revocation_policy.existing_runs == "preserve_and_mark"
            and candidate.revocation_policy.requires_revalidation
        )
        if not revocation_closed:
            issues.append("撤权必须阻断新运行、标记历史并要求重新验证")

        return DomainPackUpgradeReport(
            compatible=not issues,
            from_version=previous.version,
            to_version=candidate.version,
            migration_required=migration_required,
            migration_present=migration_present,
            compatibility_declared=compatibility_declared,
            invalidation_closed=invalidation_closed,
            rollback_trusted=rollback_trusted,
            issues=issues,
        )

    @staticmethod
    def manifest_digest(manifest: DomainPackManifest) -> str:
        """返回包签名所覆盖的规范化 SHA-256 摘要。

        摘要排除 content_digest 自身与 signatures（避免循环依赖）；
        声明值与重算值不一致由预检 check 判定，这里只负责重算。
        """
        payload = manifest.model_dump(
            mode="json",
            exclude={"content_digest", "signatures"},
        )
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _check(
        checks: list[DomainValidationCheck],
        check_id: str,
        name: str,
        passed: bool,
        reason: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        checks.append(
            DomainValidationCheck(
                check_id=check_id,
                name=name,
                status=DomainCheckStatus.PASS if passed else DomainCheckStatus.BLOCKED,
                reason="" if passed else reason,
                details=details or {},
            )
        )

    def _check_signature_coverage(
        self,
        checks: list[DomainValidationCheck],
        manifest: DomainPackManifest,
        digest: str,
    ) -> None:
        """校验签名存在性、摘要绑定和覆盖范围。"""
        errors: list[str] = []
        if manifest.lifecycle_status.value in {"signed", "active"} and not manifest.signatures:
            errors.append("已签名或已激活领域包必须提供 Manifest 签名")
        for signature in manifest.signatures:
            if not signature.signature:
                errors.append(f"签名 {signature.signer_id} 缺少签名值")
            if _normalize_digest(signature.signed_digest) != digest:
                errors.append(f"签名 {signature.signer_id} 未绑定当前 Manifest 摘要")
            if not set(signature.scope).intersection(
                {"manifest", "manifest_digest", "content_digest"}
            ):
                errors.append(f"签名 {signature.signer_id} 未覆盖 Manifest 摘要")
        self._check(
            checks,
            "signature_coverage",
            "Manifest 签名覆盖",
            not errors,
            "；".join(errors),
        )

    def _check_unique_ids(
        self,
        checks: list[DomainValidationCheck],
        manifest: DomainPackManifest,
    ) -> None:
        groups = {
            "rules": [rule.rule_id for rule in manifest.rules],
            "validators": [validator.validator_id for validator in manifest.validators],
            "fixtures": [fixture.fixture_id for fixture in manifest.fixtures],
            "dependencies": [dependency.pack_id for dependency in manifest.dependencies],
        }
        duplicates = [
            f"{group}={item}"
            for group, values in groups.items()
            for item in sorted({value for value in values if values.count(value) > 1})
        ]
        self._check(
            checks,
            "stable_ids",
            "稳定 ID 唯一性",
            not duplicates,
            f"发现重复稳定 ID：{', '.join(duplicates)}。",
        )

    def _check_references(
        self,
        checks: list[DomainValidationCheck],
        manifest: DomainPackManifest,
    ) -> None:
        rule_ids = {rule.rule_id for rule in manifest.rules}
        validator_ids = {validator.validator_id for validator in manifest.validators}
        fixture_ids = {fixture.fixture_id for fixture in manifest.fixtures}
        references: list[str] = []
        for rule in manifest.rules:
            references.extend(
                f"规则 {rule.rule_id} -> 夹具 {fixture_id}"
                for fixture_id in rule.fixture_ids
                if fixture_id not in fixture_ids
            )
        for validator in manifest.validators:
            references.extend(
                f"校验器 {validator.validator_id} -> 夹具 {fixture_id}"
                for fixture_id in validator.fixture_ids
                if fixture_id not in fixture_ids
            )
        for fixture in manifest.fixtures:
            references.extend(
                f"夹具 {fixture.fixture_id} -> 规则 {rule_id}"
                for rule_id in fixture.expected_rule_ids
                if rule_id not in rule_ids
            )
            references.extend(
                f"夹具 {fixture.fixture_id} -> 校验器 {validator_id}"
                for validator_id in fixture.expected_validator_ids
                if validator_id not in validator_ids
            )
        for question_type in manifest.question_types:
            if not any(fixture.question_type == question_type for fixture in manifest.fixtures):
                references.append(f"问题类型 {question_type} 没有对应夹具")
        self._check(
            checks,
            "references",
            "Manifest 引用完整性",
            not references,
            f"发现悬空夹具引用：{'；'.join(references)}。",
        )

    def _check_dependency_locks(
        self,
        checks: list[DomainValidationCheck],
        manifest: DomainPackManifest,
    ) -> None:
        errors: list[str] = []
        graph: dict[str, list[str]] = {manifest.id: []}
        for dependency in manifest.dependencies:
            if dependency.pack_id == manifest.id:
                errors.append("包不能依赖自身")
            if _parse_version(dependency.version) is None:
                errors.append(f"依赖 {dependency.pack_id} 版本不是精确语义版本")
            if not dependency.digest:
                errors.append(f"依赖 {dependency.pack_id} 缺少摘要")
            graph[manifest.id].append(dependency.pack_id)
            graph[dependency.pack_id] = list(dependency.depends_on)
            available = self.available_dependencies.get(dependency.pack_id)
            if available is not None and (
                available.version != dependency.version
                or _normalize_digest(available.digest) != _normalize_digest(dependency.digest)
            ):
                errors.append(f"依赖 {dependency.pack_id} 与可用锁不一致")

        cycle = _find_cycle(graph)
        if cycle:
            errors.append(f"依赖锁存在循环：{' -> '.join(cycle)}")
        self._check(
            checks,
            "dependency_locks",
            "依赖锁与闭包",
            not errors,
            "；".join(errors),
        )

    def _check_domain_coverage(
        self,
        checks: list[DomainValidationCheck],
        manifest: DomainPackManifest,
    ) -> None:
        """确保每种问题类型都有来源、Claim、措辞和规则闭环。"""
        errors: list[str] = []
        for question_type in manifest.question_types:
            if not any(
                question_type in policy.applies_to
                for policy in manifest.source_policies
            ):
                errors.append(f"问题类型 {question_type} 缺少来源策略")
            if not any(
                question_type in schema.applies_to
                for schema in manifest.claim_schemas
            ):
                errors.append(f"问题类型 {question_type} 缺少 Claim Schema")
            if not any(
                question_type in policy.applies_to
                for policy in manifest.wording_policy
            ):
                errors.append(f"问题类型 {question_type} 缺少措辞策略")
            if not any(
                question_type in rule.applies_to for rule in manifest.rules
            ):
                errors.append(f"问题类型 {question_type} 缺少领域规则")

        declared_risks = set(manifest.risk_tiers)
        errors.extend(
            f"夹具 {fixture.fixture_id} 使用未声明风险层级 {fixture.risk_tier}"
            for fixture in manifest.fixtures
            if fixture.risk_tier not in declared_risks
        )
        self._check(
            checks,
            "domain_coverage",
            "来源、Claim、措辞和规则覆盖",
            not errors,
            "；".join(errors),
        )

    def _check_platform_floor(
        self,
        checks: list[DomainValidationCheck],
        manifest: DomainPackManifest,
    ) -> None:
        violations = _floor_violations(manifest.platform_floor)
        for rule in manifest.rules:
            violations.extend(
                f"规则 {rule.rule_id}: {violation}"
                for violation in _floor_violations(rule.platform_floor)
            )
        self._check(
            checks,
            "platform_safety_floor",
            "平台安全下限",
            not violations,
            f"领域包不能放宽平台安全下限：{'；'.join(violations)}。",
        )

    def _check_content_paths(
        self,
        checks: list[DomainValidationCheck],
        manifest: DomainPackManifest,
    ) -> None:
        unsafe = [path for path in manifest.content_files if not _safe_pack_path(path)]
        self._check(
            checks,
            "content_paths",
            "包内容路径",
            not unsafe,
            f"包内容路径包含路径穿越或绝对路径：{', '.join(unsafe)}。",
        )
        for fixture in manifest.fixtures:
            if fixture.source_file and not _safe_pack_path(fixture.source_file):
                self._check(
                    checks,
                    "fixture_paths",
                    "夹具路径",
                    False,
                    f"夹具 {fixture.fixture_id} 的来源路径不安全。",
                )

    def _check_capabilities(
        self,
        checks: list[DomainValidationCheck],
        manifest: DomainPackManifest,
    ) -> None:
        errors: list[str] = []
        for validator in manifest.validators:
            if validator.network_access:
                errors.append(f"校验器 {validator.validator_id} 声明了网络访问")
            if validator.secret_access:
                errors.append(f"校验器 {validator.validator_id} 声明了秘密访问")
            if validator.filesystem_access not in {"none", "pack_read_only", "scratch"}:
                errors.append(
                    f"校验器 {validator.validator_id} 的文件权限不受支持："
                    f"{validator.filesystem_access}"
                )

            if self.capability_registry is None:
                if self.require_capability_contracts:
                    errors.append("未提供能力注册表，无法验证逻辑能力合同")
                continue
            try:
                capability = self.capability_registry.get(
                    validator.capability_name,
                    validator.capability_version,
                )
            except CapabilityRegistryError as exc:
                errors.append(str(exc))
                continue
            if capability.kind != CapabilityKind.TOOL:
                errors.append(
                    f"校验器 {validator.validator_id} 必须绑定确定性工具能力，"
                    f"实际为 {capability.kind.value}。"
                )
            if capability.status != CapabilityStatus.VERIFIED:
                errors.append(
                    f"能力 {capability.name}@{capability.version} 未通过验证。"
                )
            if capability.input_schema_version != validator.input_schema_version:
                errors.append(f"校验器 {validator.validator_id} 输入合同版本不匹配")
            if capability.output_schema_version != validator.output_schema_version:
                errors.append(f"校验器 {validator.validator_id} 输出合同版本不匹配")

        self._check(
            checks,
            "capability_contracts",
            "逻辑能力合同",
            not errors,
            "；".join(errors),
        )

def _parse_version(value: str) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.fullmatch(value.strip())
    if match is None:
        return None
    parts = [int(part or 0) for part in match.groups()[:3]]
    return parts[0], parts[1], parts[2]


def _version_satisfies(version: str, constraint: str) -> bool:
    parsed_version = _parse_version(version)
    if parsed_version is None:
        return False
    normalized = constraint.strip()
    if normalized in {"", "*"}:
        return True
    tokens = [token for token in re.split(r"[,\s]+", normalized) if token]
    for token in tokens:
        match = re.match(r"^(<=|>=|==|<|>|~=|\^)?\s*(.+)$", token)
        if match is None:
            return False
        operator = match.group(1) or "=="
        expected = _parse_version(match.group(2))
        if expected is None:
            return False
        if operator == "==" and parsed_version != expected:
            return False
        if operator == ">=" and parsed_version < expected:
            return False
        if operator == ">" and parsed_version <= expected:
            return False
        if operator == "<=" and parsed_version > expected:
            return False
        if operator == "<" and parsed_version >= expected:
            return False
        if operator == "~=" and not (
            parsed_version >= expected and parsed_version[0] == expected[0]
        ):
            return False
        if operator == "^" and not (
            parsed_version >= expected and parsed_version[0] == expected[0]
        ):
            return False
    return True


def _normalize_digest(value: str) -> str:
    return value.removeprefix("sha256:").lower()


def _is_digest(value: str) -> bool:
    normalized = _normalize_digest(value)
    return len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )


def _safe_pack_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    return (
        bool(normalized)
        and not path.is_absolute()
        and ":" not in normalized.split("/", 1)[0]
        and ".." not in path.parts
    )


def _floor_violations(floor: Any) -> list[str]:
    violations: list[str] = []
    for key, value in floor.model_dump().items():
        if key.endswith("_required") and value is False:
            violations.append(f"{key}=false")
        if key.startswith("allow_") and value is True:
            violations.append(f"{key}=true")
    return violations


def _find_cycle(graph: Mapping[str, Sequence[str]]) -> list[str] | None:
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        if node in visiting:
            try:
                return path[path.index(node) :] + [node]
            except ValueError:
                return [node, node]
        if node in visited:
            return None
        visiting.add(node)
        path.append(node)
        for dependency in graph.get(node, []):
            cycle = visit(dependency)
            if cycle:
                return cycle
        path.pop()
        visiting.remove(node)
        visited.add(node)
        return None

    for node in graph:
        cycle = visit(node)
        if cycle:
            return cycle
    return None
