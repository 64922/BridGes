"""Issue 14：四维画像合同、确定性迁移和账户隔离实现。

Expand/migrate 过渡期仍以旧画像仓库为事实来源。本模块增加账户级目标仓库、
确定性分类、乐观锁用户操作和事务迁移报告，整个过程不调用语言模型。
"""

from __future__ import annotations

import copy
import hashlib
import secrets
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from bridges.contracts.profiles import (
    AssertionStatus,
    FourDimension,
    FourDimensionConfidence,
    FourDimensionLearningRecord,
    FourDimensionLegacyRecord,
    FourDimensionMigrationReport,
    FourDimensionMigrationStatus,
    FourDimensionProfileModifyRequest,
    FourDimensionProfileRecord,
    FourDimensionRecordStatus,
    ProfileAssertion,
    ProfileDimension,
    ProfileSensitivityClass,
    ProfileSlice,
    ProfileSliceItem,
    UnusedSliceItem,
)
from bridges.contracts.teaching_progress import PlanAdjustmentTrigger
from bridges.profiles.adapters import ProfileError
from bridges.profiles.ports import ProfileRepository
from bridges.storage.database import BridgesDatabase

MIGRATION_VERSION = "profile-four-dimensions-v1"
_MAX_SLICE_ITEMS_PER_DIMENSION = 2
_MAX_SLICE_ITEMS_TOTAL = 6
_CHAT_MODE_DIMENSIONS: dict[str, frozenset[FourDimension]] = {
    "companion": frozenset(
        {
            FourDimension.ACADEMIC_STATUS,
            FourDimension.KNOWLEDGE_INTEREST,
            FourDimension.HOBBY,
            FourDimension.STAGE_GOAL,
        }
    ),
    "study": frozenset(
        {
            FourDimension.ACADEMIC_STATUS,
            FourDimension.KNOWLEDGE_INTEREST,
            FourDimension.STAGE_GOAL,
        }
    ),
    "reminder": frozenset(
        {
            FourDimension.ACADEMIC_STATUS,
            FourDimension.HOBBY,
            FourDimension.STAGE_GOAL,
        }
    ),
}

_CONFIDENCE_RANK: dict[FourDimensionConfidence, int] = {
    FourDimensionConfidence.LOW: 0,
    FourDimensionConfidence.MEDIUM: 1,
    FourDimensionConfidence.HIGH: 2,
}


def confidence_rank(confidence: FourDimensionConfidence) -> int:
    """返回把握度排序，供写入和召回共同使用。"""

    return _CONFIDENCE_RANK[confidence]


def is_recallable_confidence(confidence: FourDimensionConfidence) -> bool:
    """只有中、高把握度且未被连续纠错降级的记录可进入聊天切片。"""

    return confidence in {
        FourDimensionConfidence.MEDIUM,
        FourDimensionConfidence.HIGH,
    }


def downgraded_confidence(confidence: FourDimensionConfidence) -> FourDimensionConfidence:
    """按一个档位降低把握度，低档位保持不变。"""

    if confidence == FourDimensionConfidence.HIGH:
        return FourDimensionConfidence.MEDIUM
    return FourDimensionConfidence.LOW


class FourDimensionProfileError(ProfileError):
    """四维画像领域错误。"""


@dataclass(frozen=True)
class FourDimensionMigrationPreflight:
    """收缩前的账户级硬门检查结果。"""

    account_id: str
    migration_version: str
    legacy_record_count: int
    unprocessed_legacy_count: int
    legacy_write_callers: tuple[str, ...]
    backup_id: str | None
    can_contract: bool


class FourDimensionMigrationGateError(FourDimensionProfileError):
    """迁移门未满足时阻止收缩。"""


class FourDimensionContractGate:
    """执行四维迁移的预检、停写和幂等收缩门。"""

    def __init__(self, service: "FourDimensionProfileService") -> None:
        self._service = service
        self._legacy_writes_stopped = False
        self._contracted_accounts: dict[str, FourDimensionMigrationPreflight] = {}

    @property
    def legacy_writes_stopped(self) -> bool:
        return self._legacy_writes_stopped

    def preflight(
        self,
        account_id: str,
        *,
        legacy_write_callers: Iterable[str] = (),
        backup_id: str | None = None,
    ) -> FourDimensionMigrationPreflight:
        """清点旧记录和写调用方；任何未处理项都会阻止收缩。"""
        callers = tuple(sorted({caller for caller in legacy_write_callers if caller}))
        legacy_records = self._service.list_legacy_records(account_id)
        source_records = self._service._source_repository.list_assertions(account_id)  # noqa: SLF001
        unprocessed = sum(
            1
            for source in source_records
            if _classify(source)[0] == "legacy"
            and not any(
                record.source_record_id == source.assertion_id
                for record in legacy_records
            )
        )
        result = FourDimensionMigrationPreflight(
            account_id=account_id,
            migration_version=MIGRATION_VERSION,
            legacy_record_count=len(legacy_records),
            unprocessed_legacy_count=unprocessed,
            legacy_write_callers=callers,
            backup_id=backup_id,
            can_contract=not callers and unprocessed == 0 and backup_id is not None,
        )
        return result

    def contract(
        self,
        account_id: str,
        *,
        backup: Callable[[], str],
        legacy_write_callers: Iterable[str] = (),
    ) -> FourDimensionMigrationPreflight:
        """先备份、再停写和迁移；重复调用返回同一收缩结果。"""
        existing = self._contracted_accounts.get(account_id)
        if existing is not None:
            return existing
        initial = self.preflight(
            account_id,
            legacy_write_callers=legacy_write_callers,
            backup_id="preflight-ready",
        )
        if initial.legacy_write_callers:
            raise FourDimensionMigrationGateError("仍有旧画像写入调用方")
        backup_id = backup()
        if not backup_id:
            raise FourDimensionMigrationGateError("备份未返回可追踪标识")
        self._legacy_writes_stopped = True
        report = self._service.migrate_account(account_id)
        if report.retryable:
            raise FourDimensionMigrationGateError("四维迁移失败，收缩阶段已阻止")
        result = self.preflight(
            account_id,
            legacy_write_callers=legacy_write_callers,
            backup_id=backup_id,
        )
        if not result.can_contract:
            raise FourDimensionMigrationGateError("迁移后仍存在未封存的 legacy 记录")
        self._contracted_accounts[account_id] = result
        return result


class FourDimensionProfileRepository(ABC):
    """四维记录及内部迁移产物的持久化端口。"""

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """打开全有或全无的迁移或用户变更事务。"""

    @abstractmethod
    def save_record(self, record: FourDimensionProfileRecord) -> FourDimensionProfileRecord:
        """插入或替换一条账户所有的目标记录。"""

    @abstractmethod
    def get_record(self, owner_id: str, record_id: str) -> FourDimensionProfileRecord:
        """返回目标记录；不存在或越权时使用不泄漏信息的错误。"""

    @abstractmethod
    def find_record_by_source(
        self, owner_id: str, source_record_id: str, dimension: FourDimension
    ) -> FourDimensionProfileRecord | None:
        """按稳定旧来源和目标维度查找目标记录。"""

    @abstractmethod
    def list_records(
        self, owner_id: str, include_withdrawn: bool = False
    ) -> list[FourDimensionProfileRecord]:
        """列出单个账户内的目标记录。"""

    @abstractmethod
    def delete_record(self, owner_id: str, record_id: str) -> FourDimensionProfileRecord:
        """物理删除一条记录；不得写入撤回墓碑。"""

    @abstractmethod
    def save_learning_record(
        self, record: FourDimensionLearningRecord
    ) -> FourDimensionLearningRecord:
        """保存教学域内部交接记录。"""

    @abstractmethod
    def find_learning_record_by_source(
        self, owner_id: str, source_record_id: str
    ) -> FourDimensionLearningRecord | None:
        """按旧来源标识查找教学域交接记录。"""

    @abstractmethod
    def list_learning_records(self, owner_id: str) -> list[FourDimensionLearningRecord]:
        """列出供测试和审计工具使用的教学域内部交接记录。"""

    @abstractmethod
    def save_legacy_record(self, record: FourDimensionLegacyRecord) -> FourDimensionLegacyRecord:
        """保存不可变的 legacy 封存记录。"""

    @abstractmethod
    def find_legacy_record_by_source(
        self, owner_id: str, source_record_id: str
    ) -> FourDimensionLegacyRecord | None:
        """按来源标识查找 legacy 封存记录。"""

    @abstractmethod
    def list_legacy_records(self, owner_id: str) -> list[FourDimensionLegacyRecord]:
        """列出供内部审计工具使用的 legacy 封存记录。"""

    @abstractmethod
    def save_migration_report(
        self, report: FourDimensionMigrationReport
    ) -> FourDimensionMigrationReport:
        """保存账户级迁移报告，不包含画像正文。"""

    @abstractmethod
    def get_latest_migration_report(
        self, owner_id: str
    ) -> FourDimensionMigrationReport | None:
        """返回单个账户最新的迁移报告。"""


class InMemoryFourDimensionProfileRepository(FourDimensionProfileRepository):
    """供领域和 API 测试使用的确定性内存仓库。"""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], FourDimensionProfileRecord] = {}
        self._learning_records: dict[tuple[str, str], FourDimensionLearningRecord] = {}
        self._legacy_records: dict[tuple[str, str], FourDimensionLegacyRecord] = {}
        self._reports: dict[tuple[str, str], FourDimensionMigrationReport] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        snapshot = (
            copy.deepcopy(self._records),
            copy.deepcopy(self._learning_records),
            copy.deepcopy(self._legacy_records),
            copy.deepcopy(self._reports),
        )
        try:
            yield
        except BaseException:
            (
                self._records,
                self._learning_records,
                self._legacy_records,
                self._reports,
            ) = snapshot
            raise

    @staticmethod
    def _key(owner_id: str, object_id: str) -> tuple[str, str]:
        return owner_id, object_id

    def save_record(self, record: FourDimensionProfileRecord) -> FourDimensionProfileRecord:
        self._records[self._key(record.owner_account_id, record.record_id)] = record
        return record

    def get_record(self, owner_id: str, record_id: str) -> FourDimensionProfileRecord:
        record = self._records.get(self._key(owner_id, record_id))
        if record is None:
            raise FourDimensionProfileError("对象不存在或没有访问权限。")
        return record

    def find_record_by_source(
        self, owner_id: str, source_record_id: str, dimension: FourDimension
    ) -> FourDimensionProfileRecord | None:
        return next(
            (
                record
                for record in self._records.values()
                if record.owner_account_id == owner_id
                and record.source_record_id == source_record_id
                and record.dimension == dimension
            ),
            None,
        )

    def list_records(
        self, owner_id: str, include_withdrawn: bool = False
    ) -> list[FourDimensionProfileRecord]:
        records = [
            record
            for record in self._records.values()
            if record.owner_account_id == owner_id
            and (include_withdrawn or record.status == FourDimensionRecordStatus.ACTIVE)
        ]
        records.sort(key=lambda record: (record.dimension.value, record.first_stable_recorded_at))
        return records

    def delete_record(self, owner_id: str, record_id: str) -> FourDimensionProfileRecord:
        record = self.get_record(owner_id, record_id)
        del self._records[self._key(owner_id, record_id)]
        return record

    def save_learning_record(
        self, record: FourDimensionLearningRecord
    ) -> FourDimensionLearningRecord:
        self._learning_records[self._key(record.owner_account_id, record.record_id)] = record
        return record

    def find_learning_record_by_source(
        self, owner_id: str, source_record_id: str
    ) -> FourDimensionLearningRecord | None:
        return next(
            (
                record
                for record in self._learning_records.values()
                if record.owner_account_id == owner_id
                and record.source_record_id == source_record_id
            ),
            None,
        )

    def list_learning_records(self, owner_id: str) -> list[FourDimensionLearningRecord]:
        records = [
            record
            for record in self._learning_records.values()
            if record.owner_account_id == owner_id
        ]
        records.sort(key=lambda record: record.created_at, reverse=True)
        return records

    def save_legacy_record(self, record: FourDimensionLegacyRecord) -> FourDimensionLegacyRecord:
        self._legacy_records[self._key(record.owner_account_id, record.archive_id)] = record
        return record

    def find_legacy_record_by_source(
        self, owner_id: str, source_record_id: str
    ) -> FourDimensionLegacyRecord | None:
        return next(
            (
                record
                for record in self._legacy_records.values()
                if record.owner_account_id == owner_id
                and record.source_record_id == source_record_id
            ),
            None,
        )

    def list_legacy_records(self, owner_id: str) -> list[FourDimensionLegacyRecord]:
        records = [
            record
            for record in self._legacy_records.values()
            if record.owner_account_id == owner_id
        ]
        records.sort(key=lambda record: record.created_at, reverse=True)
        return records

    def save_migration_report(
        self, report: FourDimensionMigrationReport
    ) -> FourDimensionMigrationReport:
        self._reports[self._key(report.owner_account_id, report.report_id)] = report
        return report

    def get_latest_migration_report(
        self, owner_id: str
    ) -> FourDimensionMigrationReport | None:
        reports = [
            report for report in self._reports.values() if report.owner_account_id == owner_id
        ]
        return max(reports, key=lambda report: report.created_at) if reports else None


class SqliteFourDimensionProfileRepository(FourDimensionProfileRepository):
    """SQLite 四维目标仓库；每条语句都按账户作用域执行。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database
        self._db.initialize()

    def transaction(self) -> AbstractContextManager[None]:
        return self._db.transaction()

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="seconds")

    @staticmethod
    def _dt(value: str) -> datetime:
        return datetime.fromisoformat(value)

    @staticmethod
    def _record_from_row(row: object) -> FourDimensionProfileRecord:
        return FourDimensionProfileRecord(
            record_id=str(row["record_id"]),  # type: ignore[index]
            owner_account_id=str(row["account_id"]),  # type: ignore[index]
            dimension=FourDimension(str(row["dimension"])),  # type: ignore[index]
            label=str(row["label"]),  # type: ignore[index]
            content=str(row["content"]),  # type: ignore[index]
            first_stable_recorded_at=SqliteFourDimensionProfileRepository._dt(
                str(row["first_stable_recorded_at"])  # type: ignore[index]
            ),
            updated_at=SqliteFourDimensionProfileRepository._dt(str(row["updated_at"])),  # type: ignore[index]
            version=int(row["version"]),  # type: ignore[index]
            status=FourDimensionRecordStatus(str(row["status"])),  # type: ignore[index]
            confidence=FourDimensionConfidence(str(row["confidence"])),  # type: ignore[index]
            evidence_quote=(
                str(row["evidence_quote"])  # type: ignore[index]
                if row["evidence_quote"] is not None  # type: ignore[index]
                else None
            ),
            evidence_message_id=(
                str(row["evidence_message_id"])  # type: ignore[index]
                if row["evidence_message_id"] is not None  # type: ignore[index]
                else None
            ),
            correction_count=int(row["correction_count"]),  # type: ignore[index]
            change_note=(
                str(row["change_note"])  # type: ignore[index]
                if row["change_note"] is not None  # type: ignore[index]
                else None
            ),
            source_record_id=str(row["source_record_id"]),  # type: ignore[index]
            source_version=int(row["source_version"]),  # type: ignore[index]
            content_hash=str(row["content_hash"]),  # type: ignore[index]
            write_origin=str(row["write_origin"]),  # type: ignore[index]
            migration_version=str(row["migration_version"]),  # type: ignore[index]
        )

    def save_record(self, record: FourDimensionProfileRecord) -> FourDimensionProfileRecord:
        self._db.scoped(record.owner_account_id).execute(
            "INSERT INTO profile_four_dimension_records ("
            "record_id, account_id, dimension, label, content, first_stable_recorded_at, "
            "updated_at, version, status, source_record_id, source_version, content_hash, "
            "write_origin, migration_version, confidence, evidence_quote, evidence_message_id, "
            "correction_count, change_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(record_id) DO UPDATE SET dimension = excluded.dimension, "
            "label = excluded.label, content = excluded.content, "
            "first_stable_recorded_at = excluded.first_stable_recorded_at, "
            "updated_at = excluded.updated_at, version = excluded.version, status = excluded.status, "
            "confidence = excluded.confidence, evidence_quote = excluded.evidence_quote, "
            "evidence_message_id = excluded.evidence_message_id, correction_count = excluded.correction_count, "
            "change_note = excluded.change_note, "
            "source_record_id = excluded.source_record_id, source_version = excluded.source_version, "
            "content_hash = excluded.content_hash, write_origin = excluded.write_origin, "
            "migration_version = excluded.migration_version WHERE account_id = excluded.account_id",
            (
                record.record_id,
                record.owner_account_id,
                record.dimension.value,
                record.label,
                record.content,
                self._iso(record.first_stable_recorded_at),
                self._iso(record.updated_at),
                record.version,
                record.status.value,
                record.source_record_id,
                record.source_version,
                record.content_hash,
                record.write_origin,
                record.migration_version,
                record.confidence.value,
                record.evidence_quote,
                record.evidence_message_id,
                record.correction_count,
                record.change_note,
            ),
        )
        return record

    def get_record(self, owner_id: str, record_id: str) -> FourDimensionProfileRecord:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_four_dimension_records "
            "WHERE account_id = ? AND record_id = ?",
            (owner_id, record_id),
        ).fetchone()
        if row is None:
            raise FourDimensionProfileError("对象不存在或没有访问权限。")
        return self._record_from_row(row)

    def find_record_by_source(
        self, owner_id: str, source_record_id: str, dimension: FourDimension
    ) -> FourDimensionProfileRecord | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_four_dimension_records "
            "WHERE account_id = ? AND source_record_id = ? AND dimension = ?",
            (owner_id, source_record_id, dimension.value),
        ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def list_records(
        self, owner_id: str, include_withdrawn: bool = False
    ) -> list[FourDimensionProfileRecord]:
        status_clause = "" if include_withdrawn else " AND status = 'active'"
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_four_dimension_records WHERE account_id = ?"
            + status_clause
            + " ORDER BY dimension, first_stable_recorded_at",
            (owner_id,),
        ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def delete_record(self, owner_id: str, record_id: str) -> FourDimensionProfileRecord:
        record = self.get_record(owner_id, record_id)
        scoped = self._db.scoped(owner_id)
        scoped.execute(
            "DELETE FROM profile_four_dimension_records "
            "WHERE account_id = ? AND record_id = ?",
            (owner_id, record_id),
        )
        # 观察表没有 record_id 外键，按当前记录的维度和值一并清理，
        # 避免删除后的观察在后续复现门槛中重新抬高同一条记录。
        scoped.execute(
            "DELETE FROM profile_extraction_observations "
            "WHERE account_id = ? AND dimension = ? AND normalized_value = ?",
            (owner_id, record.dimension.value, record.content),
        )
        return record

    @staticmethod
    def _learning_from_row(row: object) -> FourDimensionLearningRecord:
        return FourDimensionLearningRecord(
            record_id=str(row["record_id"]),  # type: ignore[index]
            owner_account_id=str(row["account_id"]),  # type: ignore[index]
            source_record_id=str(row["source_record_id"]),  # type: ignore[index]
            source_version=int(row["source_version"]),  # type: ignore[index]
            content_hash=str(row["content_hash"]),  # type: ignore[index]
            created_at=SqliteFourDimensionProfileRepository._dt(str(row["created_at"])),  # type: ignore[index]
        )

    def save_learning_record(
        self, record: FourDimensionLearningRecord
    ) -> FourDimensionLearningRecord:
        self._db.scoped(record.owner_account_id).execute(
            "INSERT INTO profile_four_dimension_learning_records ("
            "record_id, account_id, source_record_id, source_version, content_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(record_id) DO NOTHING",
            (
                record.record_id,
                record.owner_account_id,
                record.source_record_id,
                record.source_version,
                record.content_hash,
                self._iso(record.created_at),
            ),
        )
        return record

    def find_learning_record_by_source(
        self, owner_id: str, source_record_id: str
    ) -> FourDimensionLearningRecord | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_four_dimension_learning_records "
            "WHERE account_id = ? AND source_record_id = ?",
            (owner_id, source_record_id),
        ).fetchone()
        return self._learning_from_row(row) if row is not None else None

    def list_learning_records(self, owner_id: str) -> list[FourDimensionLearningRecord]:
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_four_dimension_learning_records "
            "WHERE account_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()
        return [self._learning_from_row(row) for row in rows]

    @staticmethod
    def _legacy_from_row(row: object) -> FourDimensionLegacyRecord:
        return FourDimensionLegacyRecord(
            archive_id=str(row["archive_id"]),  # type: ignore[index]
            owner_account_id=str(row["account_id"]),  # type: ignore[index]
            source_record_id=str(row["source_record_id"]),  # type: ignore[index]
            source_dimension=str(row["source_dimension"]),  # type: ignore[index]
            content=str(row["content"]),  # type: ignore[index]
            content_hash=str(row["content_hash"]),  # type: ignore[index]
            reason_code=str(row["reason_code"]),  # type: ignore[index]
            created_at=SqliteFourDimensionProfileRepository._dt(str(row["created_at"])),  # type: ignore[index]
        )

    def save_legacy_record(self, record: FourDimensionLegacyRecord) -> FourDimensionLegacyRecord:
        self._db.scoped(record.owner_account_id).execute(
            "INSERT INTO profile_four_dimension_legacy ("
            "archive_id, account_id, source_record_id, source_dimension, content, content_hash, "
            "reason_code, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(archive_id) DO NOTHING",
            (
                record.archive_id,
                record.owner_account_id,
                record.source_record_id,
                record.source_dimension,
                record.content,
                record.content_hash,
                record.reason_code,
                self._iso(record.created_at),
            ),
        )
        return record

    def find_legacy_record_by_source(
        self, owner_id: str, source_record_id: str
    ) -> FourDimensionLegacyRecord | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_four_dimension_legacy "
            "WHERE account_id = ? AND source_record_id = ?",
            (owner_id, source_record_id),
        ).fetchone()
        return self._legacy_from_row(row) if row is not None else None

    def list_legacy_records(self, owner_id: str) -> list[FourDimensionLegacyRecord]:
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_four_dimension_legacy "
            "WHERE account_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()
        return [self._legacy_from_row(row) for row in rows]

    @staticmethod
    def _report_from_row(row: object) -> FourDimensionMigrationReport:
        import json

        return FourDimensionMigrationReport(
            report_id=str(row["report_id"]),  # type: ignore[index]
            owner_account_id=str(row["account_id"]),  # type: ignore[index]
            migration_version=str(row["migration_version"]),  # type: ignore[index]
            status=FourDimensionMigrationStatus(str(row["status"])),  # type: ignore[index]
            four_dimension_migrated=int(row["four_dimension_migrated"]),  # type: ignore[index]
            teaching_records_migrated=int(row["teaching_records_migrated"]),  # type: ignore[index]
            legacy_preserved=int(row["legacy_preserved"]),  # type: ignore[index]
            skipped=int(row["skipped"]),  # type: ignore[index]
            failed=int(row["failed"]),  # type: ignore[index]
            stable_record_ids=[str(value) for value in json.loads(str(row["stable_record_ids_json"]))],  # type: ignore[index]
            failure_codes=[str(value) for value in json.loads(str(row["failure_codes_json"]))],  # type: ignore[index]
            retryable=bool(int(row["retryable"])),  # type: ignore[index]
            created_at=SqliteFourDimensionProfileRepository._dt(str(row["created_at"])),  # type: ignore[index]
        )

    def save_migration_report(
        self, report: FourDimensionMigrationReport
    ) -> FourDimensionMigrationReport:
        import json

        self._db.scoped(report.owner_account_id).execute(
            "INSERT INTO profile_four_dimension_migrations ("
            "report_id, account_id, migration_version, status, four_dimension_migrated, "
            "teaching_records_migrated, legacy_preserved, skipped, failed, stable_record_ids_json, "
            "failure_codes_json, retryable, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report.report_id,
                report.owner_account_id,
                report.migration_version,
                report.status.value,
                report.four_dimension_migrated,
                report.teaching_records_migrated,
                report.legacy_preserved,
                report.skipped,
                report.failed,
                json.dumps(report.stable_record_ids, ensure_ascii=False),
                json.dumps(report.failure_codes, ensure_ascii=False),
                1 if report.retryable else 0,
                self._iso(report.created_at),
            ),
        )
        return report

    def get_latest_migration_report(
        self, owner_id: str
    ) -> FourDimensionMigrationReport | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_four_dimension_migrations "
            "WHERE account_id = ? ORDER BY created_at DESC LIMIT 1",
            (owner_id,),
        ).fetchone()
        return self._report_from_row(row) if row is not None else None


_EDUCATION_TERMS = frozenset(
    {"小学", "初中", "高中", "中学", "大学", "本科", "专科", "研究生", "硕士", "博士", "年级", "专业", "学位", "在校"}
)
_KNOWLEDGE_TERMS = frozenset(
    {"物理", "化学", "生物", "数学", "历史", "哲学", "天文", "地理", "编程", "计算机", "科学", "技术", "语言", "知识", "学习", "科普", "研究"}
)
_HOBBY_TERMS = frozenset(
    {"跑步", "游泳", "运动", "音乐", "乐器", "绘画", "画画", "摄影", "旅行", "旅游", "游戏", "烘焙", "做饭", "园艺", "电影", "追剧", "书法", "手工", "宠物", "球"}
)


_KNOWLEDGE_TERMS = _KNOWLEDGE_TERMS | frozenset(
    {
        "data",
        "analysis",
        "visualization",
        "programming",
        "physics",
        "math",
        "science",
        "knowledge",
        "learning",
        "study",
        "\u6570\u636e",
        "\u5206\u6790",
        "\u53ef\u89c6\u5316",
        "\u79d1\u5b66",
        "\u5b66\u4e60",
        "\u884c\u4e1a",
        "\u91d1\u878d",
    }
)
_HOBBY_TERMS = _HOBBY_TERMS | frozenset(
    {
        "running",
        "travel",
        "music",
        "painting",
        "drawing",
        "photography",
        "games",
        "cooking",
        "hobby",
        "\u8dd1\u6b65",
        "\u65c5\u884c",
        "\u97f3\u4e50",
        "\u7ed8\u753b",
        "\u6444\u5f71",
    }
)


def _content_hash(source: ProfileAssertion) -> str:
    payload = "|".join(
        [source.assertion_id, str(source.version), source.canonical_dimension, source.value_or_rule]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, account_id: str, source_id: str, dimension: str = "") -> str:
    payload = "|".join([MIGRATION_VERSION, prefix, account_id, source_id, dimension])
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


MigrationKind = Literal["target", "teaching", "legacy", "skip"]


def _classify(source: ProfileAssertion) -> tuple[MigrationKind, FourDimension | None, str]:
    """只依据明确且确定性的词法证据分类。"""
    if source.status in {AssertionStatus.DELETED}:
        return "skip", None, "deleted_source"
    if source.sensitivity_class in {
        ProfileSensitivityClass.SENSITIVE,
        ProfileSensitivityClass.PROHIBITED,
    }:
        return "legacy", None, "sensitive_source"
    content = source.value_or_rule.strip()
    if not content:
        raise FourDimensionProfileError("画像内容不合法，迁移可重试。")
    if source.canonical_dimension == ProfileDimension.STAGE_GOAL.value:
        return "target", FourDimension.STAGE_GOAL, "direct_stage_goal"
    if source.canonical_dimension == ProfileDimension.BASIC_INFORMATION.value:
        return (
            ("target", FourDimension.ACADEMIC_STATUS, "explicit_education_evidence")
            if any(term in content for term in _EDUCATION_TERMS)
            else ("legacy", None, "ambiguous_basic_information")
        )
    if source.canonical_dimension == ProfileDimension.KNOWLEDGE_STATE.value:
        return "teaching", None, "knowledge_state_to_teaching"
    if source.canonical_dimension == ProfileDimension.INTEREST_PREFERENCE.value:
        has_knowledge = any(term in content for term in _KNOWLEDGE_TERMS)
        has_hobby = any(term in content for term in _HOBBY_TERMS)
        if has_knowledge == has_hobby:
            return "legacy", None, "ambiguous_interest"
        return (
            ("target", FourDimension.KNOWLEDGE_INTEREST, "explicit_knowledge_interest")
            if has_knowledge
            else ("target", FourDimension.HOBBY, "explicit_hobby")
        )
    return "legacy", None, "retired_dimension"


class FourDimensionProfileService:
    """账户级四维画像读写和确定性迁移服务。"""

    def __init__(
        self,
        source_repository: ProfileRepository,
        repository: FourDimensionProfileRepository,
        learning_adjustment_callback: Callable[
            [str, PlanAdjustmentTrigger, str, str], object
        ]
        | None = None,
        observation_delete_callback: Callable[[str, FourDimension, str], object]
        | None = None,
    ) -> None:
        self._source_repository = source_repository
        self._repository = repository
        self._learning_adjustment_callback = learning_adjustment_callback
        self._observation_delete_callback = observation_delete_callback

    def set_observation_delete_callback(
        self, callback: Callable[[str, FourDimension, str], object] | None
    ) -> None:
        """绑定自动观察删除端口，保持内存实现也遵守真删边界。"""

        self._observation_delete_callback = callback

    def list_records(self, account_id: str) -> list[FourDimensionProfileRecord]:
        return self._repository.list_records(account_id)

    def transaction(self) -> AbstractContextManager[None]:
        """为自动画像批量提交暴露四维记录的事务边界。"""
        return self._repository.transaction()

    def compile_chat_slice(
        self,
        account_id: str,
        *,
        mode: str,
        run_id: str,
        project_id: str | None = None,
    ) -> ProfileSlice:
        """按当前活动四维记录编译本轮最小切片，不读取旧九维正文。"""
        allowed = _CHAT_MODE_DIMENSIONS.get(mode, frozenset())
        per_dimension: dict[FourDimension, int] = {}
        included: list[ProfileSliceItem] = []
        all_records = [
            record
            for record in self._repository.list_records(account_id)
            if record.dimension in allowed
        ]
        records = [
            record for record in all_records if is_recallable_confidence(record.confidence)
        ]
        low_confidence = [
            record
            for record in all_records
            if not is_recallable_confidence(record.confidence)
        ]
        records.sort(
            key=lambda record: (confidence_rank(record.confidence), record.updated_at),
            reverse=True,
        )
        for record in records:
            count = per_dimension.get(record.dimension, 0)
            if count >= _MAX_SLICE_ITEMS_PER_DIMENSION:
                continue
            if len(included) >= _MAX_SLICE_ITEMS_TOTAL:
                break
            included.append(
                ProfileSliceItem(
                    assertion_id=record.record_id,
                    dimension=record.dimension.value,
                    value_or_rule=record.content,
                    inclusion_reason="当前模式下与你当前任务相关的已授权信息",
                    sensitivity_class=ProfileSensitivityClass.PREFERENCE,
                )
            )
            per_dimension[record.dimension] = count + 1
        unused = [
            UnusedSliceItem(
                assertion_id=record.record_id,
                dimension=record.dimension.value,
                value_or_rule=record.content,
                exclusion_reason="可靠程度不足，暂不用于当前回答",
            )
            for record in low_confidence
        ]
        return ProfileSlice(
            slice_id=_stable_id("slice", account_id, run_id, mode),
            owner_account_id=account_id,
            run_id=run_id,
            purpose="chat",
            project_id=project_id,
            included_items=included,
            unused_items=unused,
            sensitivity_classes_allowed=[ProfileSensitivityClass.PREFERENCE],
            compiled_policy_version=MIGRATION_VERSION,
            length_budget=_MAX_SLICE_ITEMS_TOTAL,
            compiled_at=datetime.now(UTC),
        )

    def get_record(self, account_id: str, record_id: str) -> FourDimensionProfileRecord:
        return self._repository.get_record(account_id, record_id)

    def upsert_automatic_record(
        self,
        account_id: str,
        *,
        dimension: FourDimension,
        content: str,
        action: str,
        confidence: FourDimensionConfidence = FourDimensionConfidence.MEDIUM,
        evidence_quote: str | None = None,
        evidence_message_id: str | None = None,
        change_note: str | None = None,
    ) -> FourDimensionProfileRecord:
        """提交一条 Issue 15 自动抽取结果到四维目标表。

        自动记录使用稳定的「账户 + 维度 + 规范化内容」来源键：不同消息
        的同义重复只更新同一行，不会生成副本；消息和抽取器版本由上层
        extraction run、observation 和 retry task 分别承担幂等边界。``update`` 在同维度最近一条
        自动记录上改写，保留首次稳定时间；撤回记录不会被自动复活。
        """
        normalized = content.strip()
        if not normalized or len(normalized) > 1000:
            raise FourDimensionProfileError("画像内容不合法。")
        source_record_id = (
            "auto-"
            + hashlib.sha256(
                f"{account_id}|{dimension.value}|{normalized.casefold()}".encode("utf-8")
            ).hexdigest()[:32]
        )
        existing: FourDimensionProfileRecord | None = None
        try:
            existing = self._repository.get_record(account_id, source_record_id)
        except FourDimensionProfileError:
            existing = None
        if existing is None and action == "update":
            candidates = [
                record
                for record in self._repository.list_records(account_id, include_withdrawn=True)
                if record.dimension == dimension and record.write_origin == "automatic"
            ]
            if candidates:
                existing = max(candidates, key=lambda record: record.updated_at)
        if existing is not None:
            if existing.status == FourDimensionRecordStatus.WITHDRAWN:
                raise FourDimensionProfileError("画像记录已撤回，不能自动复活。")
            if existing.correction_count >= 3:
                return existing
            if existing.content == normalized:
                if confidence_rank(confidence) > confidence_rank(existing.confidence):
                    existing.confidence = confidence
                existing.evidence_quote = evidence_quote or existing.evidence_quote
                existing.evidence_message_id = (
                    evidence_message_id or existing.evidence_message_id
                )
                if change_note is not None:
                    existing.change_note = change_note
                return self._repository.save_record(existing)
            previous_content = existing.content
            existing.content = normalized
            existing.updated_at = datetime.now(UTC)
            existing.version += 1
            existing.source_version += 1
            existing.source_record_id = source_record_id
            existing.content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            existing.write_origin = "automatic"
            existing.migration_version = "profile-auto-v1"
            existing.confidence = confidence
            existing.evidence_quote = evidence_quote
            existing.evidence_message_id = evidence_message_id
            existing.change_note = change_note or "自动记录已根据新的用户证据更新"
            updated = self._repository.save_record(existing)
            if self._observation_delete_callback is not None:
                self._observation_delete_callback(
                    account_id, existing.dimension, previous_content
                )
            return updated

        now = datetime.now(UTC)
        return self._repository.save_record(
            FourDimensionProfileRecord(
                record_id=source_record_id,
                owner_account_id=account_id,
                dimension=dimension,
                label=dimension.label,
                content=normalized,
                first_stable_recorded_at=now,
                updated_at=now,
                version=1,
                status=FourDimensionRecordStatus.ACTIVE,
                confidence=confidence,
                evidence_quote=evidence_quote,
                evidence_message_id=evidence_message_id,
                correction_count=0,
                change_note=change_note,
                source_record_id=source_record_id,
                source_version=1,
                content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                write_origin="automatic",
                migration_version="profile-auto-v1",
            )
        )

    @staticmethod
    def _validate_version(record: FourDimensionProfileRecord, version: int) -> None:
        if record.status != FourDimensionRecordStatus.ACTIVE:
            raise FourDimensionProfileError("画像记录已撤回，不能继续修改。")
        if version != record.version:
            raise FourDimensionProfileError("版本冲突，请刷新后重试。")

    def modify_record(
        self,
        account_id: str,
        record_id: str,
        request: FourDimensionProfileModifyRequest,
    ) -> FourDimensionProfileRecord:
        with self._repository.transaction():
            record = self._repository.get_record(account_id, record_id)
            self._validate_version(record, request.version)
            previous_content = record.content
            record.content = request.content.strip()
            if not record.content:
                raise FourDimensionProfileError("画像内容不合法。")
            record.version += 1
            record.updated_at = datetime.now(UTC)
            record.content_hash = hashlib.sha256(record.content.encode("utf-8")).hexdigest()
            record.write_origin = "user"
            record.correction_count += 1
            record.evidence_quote = record.content
            record.evidence_message_id = None
            if record.correction_count == 1:
                if record.confidence == FourDimensionConfidence.LOW:
                    record.confidence = FourDimensionConfidence.MEDIUM
                record.change_note = "用户纠正后已更新"
            elif record.correction_count == 2:
                record.confidence = downgraded_confidence(record.confidence)
                record.change_note = "反复纠错：可靠程度已降低一档"
            else:
                record.confidence = FourDimensionConfidence.LOW
                record.change_note = "当前不可信：连续收到多次纠正，暂不再使用"
            updated = self._repository.save_record(record)
            if (
                self._observation_delete_callback is not None
                and previous_content != updated.content
            ):
                self._observation_delete_callback(
                    account_id, updated.dimension, previous_content
                )
        self._notify_learning_adjustment(
            account_id,
            PlanAdjustmentTrigger.PROFILE_UPDATED,
            f"profile:{updated.record_id}:v{updated.version}",
            "四维画像记录已修改，下一课重新编排。",
        )
        return updated

    def withdraw_record(
        self, account_id: str, record_id: str, version: int | None = None
    ) -> FourDimensionProfileRecord:
        with self._repository.transaction():
            record = self._repository.get_record(account_id, record_id)
            if record.status != FourDimensionRecordStatus.ACTIVE:
                raise FourDimensionProfileError("画像记录已撤回，不能重复撤回。")
            if version is not None and version != record.version:
                raise FourDimensionProfileError("版本冲突，请刷新后重试。")
            record.status = FourDimensionRecordStatus.WITHDRAWN
            record.version += 1
            record.updated_at = datetime.now(UTC)
            record.write_origin = "user"
            updated = self._repository.save_record(record)
        self._notify_learning_adjustment(
            account_id,
            PlanAdjustmentTrigger.PROFILE_WITHDRAWN,
            f"profile:{updated.record_id}:v{updated.version}",
            "四维画像记录已撤回，下一课移除相关路径。",
        )
        return updated

    def delete_record(self, account_id: str, record_id: str, version: int) -> None:
        """永久删除记录及其当前值对应的观察，不留下撤回墓碑。"""

        with self._repository.transaction():
            record = self._repository.get_record(account_id, record_id)
            self._validate_version(record, version)
            self._repository.delete_record(account_id, record_id)
            if self._observation_delete_callback is not None:
                self._observation_delete_callback(
                    account_id, record.dimension, record.content
                )
        self._notify_learning_adjustment(
            account_id,
            PlanAdjustmentTrigger.PROFILE_WITHDRAWN,
            f"profile:{record_id}:deleted",
            "四维画像记录已永久删除，下一课移除相关路径。",
        )

    def _notify_learning_adjustment(
        self,
        account_id: str,
        trigger: PlanAdjustmentTrigger,
        trigger_key: str,
        reason: str,
    ) -> None:
        if self._learning_adjustment_callback is not None:
            self._learning_adjustment_callback(account_id, trigger, trigger_key, reason)

    def list_learning_records(self, account_id: str) -> list[FourDimensionLearningRecord]:
        return self._repository.list_learning_records(account_id)

    def list_legacy_records(self, account_id: str) -> list[FourDimensionLegacyRecord]:
        return self._repository.list_legacy_records(account_id)

    def latest_migration_report(
        self, account_id: str
    ) -> FourDimensionMigrationReport | None:
        return self._repository.get_latest_migration_report(account_id)

    def migrate_account(self, account_id: str) -> FourDimensionMigrationReport:
        now = datetime.now(UTC)
        counts = {"target": 0, "teaching": 0, "legacy": 0, "skip": 0, "failed": 0}
        stable_ids: list[str] = []
        failure_codes: list[str] = []
        try:
            with self._repository.transaction():
                for source in self._source_repository.list_assertions(account_id):
                    try:
                        kind, dimension, reason = _classify(source)
                    except FourDimensionProfileError:
                        counts["failed"] += 1
                        failure_codes.append("invalid_content")
                        raise

                    source_hash = _content_hash(source)
                    if kind == "skip":
                        counts["skip"] += 1
                        continue
                    if kind == "teaching":
                        if self._repository.find_learning_record_by_source(
                            account_id, source.assertion_id
                        ) is not None:
                            counts["skip"] += 1
                            continue
                        handoff = FourDimensionLearningRecord(
                            record_id=_stable_id("learning", account_id, source.assertion_id),
                            owner_account_id=account_id,
                            source_record_id=source.assertion_id,
                            source_version=source.version,
                            content_hash=source_hash,
                            created_at=now,
                        )
                        self._repository.save_learning_record(handoff)
                        counts["teaching"] += 1
                        stable_ids.append(handoff.record_id)
                        continue
                    if kind == "legacy":
                        if self._repository.find_legacy_record_by_source(
                            account_id, source.assertion_id
                        ) is not None:
                            counts["skip"] += 1
                            continue
                        archive = FourDimensionLegacyRecord(
                            archive_id=_stable_id("legacy", account_id, source.assertion_id),
                            owner_account_id=account_id,
                            source_record_id=source.assertion_id,
                            source_dimension=source.canonical_dimension,
                            content=source.value_or_rule,
                            content_hash=source_hash,
                            reason_code=reason,
                            created_at=now,
                        )
                        self._repository.save_legacy_record(archive)
                        counts["legacy"] += 1
                        stable_ids.append(archive.archive_id)
                        continue

                    assert dimension is not None
                    target_hash = hashlib.sha256(
                        source.value_or_rule.encode("utf-8")
                    ).hexdigest()
                    existing = self._repository.find_record_by_source(
                        account_id, source.assertion_id, dimension
                    )
                    if existing is not None:
                        if (
                            existing.write_origin == "user"
                            or existing.status == FourDimensionRecordStatus.WITHDRAWN
                        ):
                            counts["skip"] += 1
                            continue
                        if (
                            existing.source_version == source.version
                            and existing.content_hash == target_hash
                        ):
                            counts["skip"] += 1
                            continue
                        existing.content = source.value_or_rule
                        existing.source_version = source.version
                        existing.content_hash = target_hash
                        existing.updated_at = now
                        existing.version += 1
                        self._repository.save_record(existing)
                        counts["skip"] += 1
                        continue

                    target = FourDimensionProfileRecord(
                        record_id=_stable_id("profile", account_id, source.assertion_id, dimension.value),
                        owner_account_id=account_id,
                        dimension=dimension,
                        label=dimension.label,
                        content=source.value_or_rule,
                        first_stable_recorded_at=source.created_at,
                        updated_at=now,
                        version=1,
                        status=(
                            FourDimensionRecordStatus.ACTIVE
                            if source.status == AssertionStatus.ACTIVE
                            else FourDimensionRecordStatus.WITHDRAWN
                        ),
                        confidence=FourDimensionConfidence.LOW,
                        evidence_quote=None,
                        evidence_message_id=None,
                        correction_count=0,
                        change_note="由旧信息迁移，等待新的证据确认",
                        source_record_id=source.assertion_id,
                        source_version=source.version,
                        content_hash=target_hash,
                        write_origin="migration",
                        migration_version=MIGRATION_VERSION,
                    )
                    self._repository.save_record(target)
                    counts["target"] += 1
                    stable_ids.append(target.record_id)

                report = FourDimensionMigrationReport(
                    report_id=secrets.token_urlsafe(16),
                    owner_account_id=account_id,
                    migration_version=MIGRATION_VERSION,
                    status=FourDimensionMigrationStatus.COMPLETED,
                    four_dimension_migrated=counts["target"],
                    teaching_records_migrated=counts["teaching"],
                    legacy_preserved=counts["legacy"],
                    skipped=counts["skip"],
                    failed=counts["failed"],
                    stable_record_ids=stable_ids,
                    failure_codes=failure_codes,
                    retryable=False,
                    created_at=now,
                )
                return self._repository.save_migration_report(report)
        except FourDimensionProfileError:
            report = FourDimensionMigrationReport(
                report_id=secrets.token_urlsafe(16),
                owner_account_id=account_id,
                migration_version=MIGRATION_VERSION,
                status=FourDimensionMigrationStatus.RETRYABLE,
                four_dimension_migrated=0,
                teaching_records_migrated=0,
                legacy_preserved=0,
                skipped=counts["skip"],
                failed=max(1, counts["failed"]),
                stable_record_ids=[],
                failure_codes=failure_codes or ["migration_failed"],
                retryable=True,
                created_at=now,
            )
            with self._repository.transaction():
                return self._repository.save_migration_report(report)
