"""画像工单 19：四维路径退出审计与切换恢复的报告合同。

报告只包含标识、计数和校验结论，不包含画像正文、消息内容或凭据。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

PROFILE_CUTOVER_AUDIT_VERSION = "profile-cutover-audit-v1"
PROFILE_CUTOVER_RECOVERY_VERSION = "profile-cutover-recovery-v1"


@dataclass(frozen=True)
class CutoverCheck:
    """一条切换审计或恢复核对结论。"""

    check_id: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ProfileCutoverAudit:
    """切换前只读审计报告；不包含画像正文。"""

    account_id: str
    schema_version: int
    checks: tuple[CutoverCheck, ...]
    counts: Mapping[str, int]
    audit_version: str = PROFILE_CUTOVER_AUDIT_VERSION

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "audit_version": self.audit_version,
            "account_id": self.account_id,
            "schema_version": self.schema_version,
            "passed": self.passed,
            "checks": [check.as_dict() for check in self.checks],
            "counts": dict(self.counts),
        }


@dataclass(frozen=True)
class ProfileCutoverRecovery:
    """恢复库核对报告；只含标识、计数和校验结论。"""

    account_id: str
    backup_id: str
    backup_digest: str
    source_digest: str
    schema_version: int
    checks: tuple[CutoverCheck, ...]
    counts: Mapping[str, int]
    recovery_version: str = PROFILE_CUTOVER_RECOVERY_VERSION

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "recovery_version": self.recovery_version,
            "account_id": self.account_id,
            "backup_id": self.backup_id,
            "backup_digest": self.backup_digest,
            "source_digest": self.source_digest,
            "schema_version": self.schema_version,
            "passed": self.passed,
            "checks": [check.as_dict() for check in self.checks],
            "counts": dict(self.counts),
        }


@dataclass(frozen=True)
class ProfileCutoverRecoverySet:
    """多账户恢复结果；每个账户共用同一恢复目标库。"""

    account_ids: tuple[str, ...]
    recoveries: tuple[ProfileCutoverRecovery, ...]
    recovery_version: str = PROFILE_CUTOVER_RECOVERY_VERSION

    @property
    def passed(self) -> bool:
        return all(recovery.passed for recovery in self.recoveries)

    @property
    def counts(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for recovery in self.recoveries:
            for key, value in recovery.counts.items():
                totals[key] = totals.get(key, 0) + value
        return totals

    def as_dict(self) -> dict[str, object]:
        return {
            "recovery_version": self.recovery_version,
            "account_ids": list(self.account_ids),
            "passed": self.passed,
            "counts": self.counts,
            "recoveries": [recovery.as_dict() for recovery in self.recoveries],
        }
