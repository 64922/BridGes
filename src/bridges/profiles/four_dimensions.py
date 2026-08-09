"""Issue 14: expanded four-dimension profile contract and migration.

The legacy profile repository remains the source of truth during the
expand/migrate window. This module adds an account-scoped target repository,
deterministic classification, optimistic-lock user operations and a
transactional migration report. It never calls a language model.
"""

from __future__ import annotations

import copy
import hashlib
import secrets
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Literal

from bridges.contracts.profiles import (
    AssertionStatus,
    FourDimension,
    FourDimensionLearningRecord,
    FourDimensionLegacyRecord,
    FourDimensionMigrationReport,
    FourDimensionMigrationStatus,
    FourDimensionProfileModifyRequest,
    FourDimensionProfileRecord,
    FourDimensionRecordStatus,
    ProfileAssertion,
    ProfileDimension,
)
from bridges.profiles.adapters import ProfileError
from bridges.profiles.ports import ProfileRepository
from bridges.storage.database import BridgesDatabase

MIGRATION_VERSION = "profile-four-dimensions-v1"


class FourDimensionProfileError(ProfileError):
    """Safe domain error for the expanded profile contract."""


class FourDimensionProfileRepository(ABC):
    """Persistence port for expanded records and internal migration artifacts."""

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """Open an all-or-nothing target migration or user mutation."""

    @abstractmethod
    def save_record(self, record: FourDimensionProfileRecord) -> FourDimensionProfileRecord:
        """Insert or replace one account-owned target record."""

    @abstractmethod
    def get_record(self, owner_id: str, record_id: str) -> FourDimensionProfileRecord:
        """Return a target record or raise a non-leaking error."""

    @abstractmethod
    def find_record_by_source(
        self, owner_id: str, source_record_id: str, dimension: FourDimension
    ) -> FourDimensionProfileRecord | None:
        """Find a target record by its stable legacy source and target dimension."""

    @abstractmethod
    def list_records(
        self, owner_id: str, include_withdrawn: bool = False
    ) -> list[FourDimensionProfileRecord]:
        """List target records within one account."""

    @abstractmethod
    def save_learning_record(
        self, record: FourDimensionLearningRecord
    ) -> FourDimensionLearningRecord:
        """Persist an internal teaching-domain handoff."""

    @abstractmethod
    def find_learning_record_by_source(
        self, owner_id: str, source_record_id: str
    ) -> FourDimensionLearningRecord | None:
        """Find a teaching handoff by its legacy source id."""

    @abstractmethod
    def list_learning_records(self, owner_id: str) -> list[FourDimensionLearningRecord]:
        """List internal teaching handoffs for tests and audit tooling."""

    @abstractmethod
    def save_legacy_record(self, record: FourDimensionLegacyRecord) -> FourDimensionLegacyRecord:
        """Persist an immutable legacy archive entry."""

    @abstractmethod
    def find_legacy_record_by_source(
        self, owner_id: str, source_record_id: str
    ) -> FourDimensionLegacyRecord | None:
        """Find a legacy archive entry by source id."""

    @abstractmethod
    def list_legacy_records(self, owner_id: str) -> list[FourDimensionLegacyRecord]:
        """List legacy archives for internal audit tooling."""

    @abstractmethod
    def save_migration_report(
        self, report: FourDimensionMigrationReport
    ) -> FourDimensionMigrationReport:
        """Persist an account-scoped report without profile正文."""

    @abstractmethod
    def get_latest_migration_report(
        self, owner_id: str
    ) -> FourDimensionMigrationReport | None:
        """Return the newest report for one account."""


class InMemoryFourDimensionProfileRepository(FourDimensionProfileRepository):
    """Deterministic repository used by the domain and API tests."""

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
    """SQLite target repository; every statement is account-scoped."""

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
            "write_origin, migration_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(record_id) DO UPDATE SET dimension = excluded.dimension, "
            "label = excluded.label, content = excluded.content, "
            "first_stable_recorded_at = excluded.first_stable_recorded_at, "
            "updated_at = excluded.updated_at, version = excluded.version, status = excluded.status, "
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
    """Classify only from explicit, deterministic lexical evidence."""
    content = source.value_or_rule.strip()
    if not content:
        raise FourDimensionProfileError("画像内容不合法，迁移可重试。")
    if source.status in {AssertionStatus.DELETED}:
        return "skip", None, "deleted_source"
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
    """Account-scoped four-dimension CRUD and deterministic migration service."""

    def __init__(
        self,
        source_repository: ProfileRepository,
        repository: FourDimensionProfileRepository,
    ) -> None:
        self._source_repository = source_repository
        self._repository = repository

    def list_records(self, account_id: str) -> list[FourDimensionProfileRecord]:
        return self._repository.list_records(account_id)

    def get_record(self, account_id: str, record_id: str) -> FourDimensionProfileRecord:
        return self._repository.get_record(account_id, record_id)

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
            record.content = request.content.strip()
            if not record.content:
                raise FourDimensionProfileError("画像内容不合法。")
            record.version += 1
            record.updated_at = datetime.now(UTC)
            record.content_hash = hashlib.sha256(record.content.encode("utf-8")).hexdigest()
            record.write_origin = "user"
            return self._repository.save_record(record)

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
            return self._repository.save_record(record)

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
                            and existing.content_hash == source_hash
                        ):
                            counts["skip"] += 1
                            continue
                        existing.content = source.value_or_rule
                        existing.source_version = source.version
                        existing.content_hash = hashlib.sha256(
                            existing.content.encode("utf-8")
                        ).hexdigest()
                        existing.updated_at = now
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
                        source_record_id=source.assertion_id,
                        source_version=source.version,
                        content_hash=hashlib.sha256(
                            source.value_or_rule.encode("utf-8")
                        ).hexdigest(),
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
