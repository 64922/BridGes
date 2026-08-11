"""profile-auto-v2 回放所需的画像仓库读写边界。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from bridges.chat.repository import ConversationRepository
from bridges.contracts.profile_extraction import (
    ProfileExtractionRetryTask,
    ProfileExtractionRun,
)
from bridges.contracts.profiles import FourDimensionMigrationReport
from bridges.profiles.automatic import (
    PROFILE_REPLAY_QUEUE,
    SqliteAutomaticProfileRepository,
)
from bridges.profiles.four_dimensions import (
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.signals import ProfileSignalCategory, ProfileSignalClassifier
from bridges.runtime.queue import TaskQueue
from bridges.storage.database import BridgesDatabase


@dataclass(frozen=True)
class ProfileReplayMessage:
    """回放门控所需的最小消息视图。"""

    conversation_id: str
    role: str
    status: str
    content: str


@dataclass(frozen=True)
class ProfileReplayCandidate:
    """一条待回放的 v1 run 及其当前消息快照。"""

    extraction_id: str
    account_id: str
    message_id: str
    conversation_id: str
    content: str
    source_hash: str
    bucket: str


@dataclass(frozen=True)
class ProfileReplayRunStatus:
    """回放 run 的安全状态摘要，不返回消息正文。"""

    extraction_id: str
    record_ids: tuple[str, ...]
    status: str


def _source_hash(account_id: str, message_id: str, content: str) -> str:
    return hashlib.sha256(
        "|".join((account_id, message_id, content)).encode("utf-8")
    ).hexdigest()


def _normalize(value: str) -> str:
    return " ".join(value.strip(" \t\r\n，。；：:、,;.!！？?").split())


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


class SqliteProfileReplayRepository:
    """把回放编排器与 profile_*、messages 的直接 SQL 隔离开。"""

    def __init__(
        self,
        database: BridgesDatabase | None = None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if database is None and connection is None:
            raise ValueError("database or connection is required")
        self.database = database
        self._connection = connection or database.connection  # type: ignore[union-attr]
        self._automatic = (
            SqliteAutomaticProfileRepository(database, initialize=False)
            if database is not None
            else None
        )
        self._dimensions = (
            SqliteFourDimensionProfileRepository(database, initialize=False)
            if database is not None
            else None
        )
        self._conversations = (
            ConversationRepository(database) if database is not None else None
        )
        self._queue = (
            TaskQueue(database, default_lease_seconds=60)
            if database is not None
            else None
        )

    @property
    def automatic_repository(self) -> SqliteAutomaticProfileRepository:
        if self._automatic is None:
            raise RuntimeError("read-only replay repository has no write adapter")
        return self._automatic

    @property
    def dimension_repository(self) -> SqliteFourDimensionProfileRepository:
        if self._dimensions is None:
            raise RuntimeError("read-only replay repository has no write adapter")
        return self._dimensions

    def list_candidates(self) -> list[ProfileReplayCandidate]:
        if self._automatic is not None and self._conversations is not None:
            runs = self._automatic.list_runs()
            candidates: list[ProfileReplayCandidate] = []
            for run in runs:
                if not run.extractor_version.startswith("profile-auto-v1"):
                    continue
                if not (
                    (
                        run.status.value == "exhausted"
                        and str(run.last_error or "").startswith("client_error_400")
                    )
                    or (
                        run.status.value == "succeeded"
                        and run.attempts == 0
                    )
                ):
                    continue
                message = self.get_message(run.account_id, run.message_id)
                content = "" if message is None else message.content
                candidates.append(
                    self._candidate(
                        run.extraction_id,
                        run.account_id,
                        run.message_id,
                        "" if message is None else message.conversation_id,
                        content,
                        (
                            "eligible_exhausted_client_error_400"
                            if str(run.last_error or "").startswith("client_error_400")
                            else "eligible_succeeded_attempts_0"
                        ),
                    )
                )
            return candidates

        rows = self._connection.execute(
            "SELECT r.extraction_id, r.account_id, r.message_id, r.last_error, "
            "m.conversation_id, m.content "
            "FROM profile_extraction_runs AS r "
            "LEFT JOIN messages AS m "
            "ON m.account_id = r.account_id AND m.message_id = r.message_id "
            "WHERE r.extractor_version LIKE 'profile-auto-v1%' "
            "AND ((r.status = 'exhausted' AND r.last_error LIKE 'client_error_400%') "
            "OR (r.status = 'succeeded' AND r.attempts = 0)) "
            "ORDER BY r.created_at, r.extraction_id"
        ).fetchall()
        return [
            self._candidate(
                str(row["extraction_id"]),
                str(row["account_id"]),
                str(row["message_id"]),
                str(row["conversation_id"] or ""),
                "" if row["content"] is None else str(row["content"]),
                (
                    "eligible_exhausted_client_error_400"
                    if str(row["last_error"] or "").startswith("client_error_400")
                    else "eligible_succeeded_attempts_0"
                ),
            )
            for row in rows
        ]

    @staticmethod
    def _candidate(
        extraction_id: str,
        account_id: str,
        message_id: str,
        conversation_id: str,
        content: str,
        bucket: str,
    ) -> ProfileReplayCandidate:
        return ProfileReplayCandidate(
            extraction_id=extraction_id,
            account_id=account_id,
            message_id=message_id,
            conversation_id=conversation_id,
            content=content,
            source_hash=_source_hash(account_id, message_id, content),
            bucket=bucket,
        )

    def get_message(
        self, account_id: str, message_id: str
    ) -> ProfileReplayMessage | None:
        if self._conversations is not None:
            message = self._conversations.get_message(account_id, message_id)
            if message is None:
                return None
            return ProfileReplayMessage(
                conversation_id=message.conversation_id,
                role=_value(message.role),
                status=_value(message.status),
                content=message.content,
            )
        row = self._connection.execute(
            "SELECT conversation_id, role, status, content FROM messages "
            "WHERE account_id = ? AND message_id = ?",
            (account_id, message_id),
        ).fetchone()
        if row is None:
            return None
        return ProfileReplayMessage(
            conversation_id=str(row["conversation_id"]),
            role=str(row["role"]),
            status=str(row["status"]),
            content=str(row["content"]),
        )

    def gate(
        self,
        candidate: ProfileReplayCandidate,
        classifier: ProfileSignalClassifier,
    ) -> str:
        message = self.get_message(candidate.account_id, candidate.message_id)
        if message is None:
            return "missing_message"
        if message.role != "user":
            return "other"
        if message.status == "stopped":
            return "stopped"
        if message.status != "done":
            return "other"
        if self.is_tombstoned(candidate.account_id, candidate.message_id):
            return "tombstone"
        if self.is_privacy_blocked(candidate.account_id, message.content):
            return "privacy_block"
        if self.has_withdrawn_record(
            candidate.account_id,
            candidate.message_id,
            _normalize(message.content),
        ):
            return "withdrawn"
        classification = classifier.classify(message.content)
        if classification.category == ProfileSignalCategory.FORBIDDEN:
            return "forbidden"
        if classification.category == ProfileSignalCategory.NO_SIGNAL:
            return "no_signal"
        if not classification.should_process:
            return "other"
        return "eligible"

    def is_tombstoned(self, account_id: str, message_id: str) -> bool:
        if self._automatic is not None:
            return self._automatic.is_message_tombstoned(account_id, message_id)
        return (
            self._connection.execute(
                "SELECT 1 FROM profile_extraction_tombstones "
                "WHERE account_id = ? AND message_id = ?",
                (account_id, message_id),
            ).fetchone()
            is not None
        )

    def is_privacy_blocked(self, account_id: str, content: str) -> bool:
        if self._automatic is not None:
            return self._automatic.is_recording_blocked(account_id, content)
        normalized = _normalize(content)
        return (
            self._connection.execute(
                "SELECT 1 FROM profile_extraction_privacy_blocks "
                "WHERE (account_id = ? AND scope = 'account') "
                "OR (account_id = ? AND scope = 'content' "
                "AND (normalized_value = ? OR instr(?, normalized_value) > 0 "
                "OR instr(normalized_value, ?) > 0)) LIMIT 1",
                (
                    account_id,
                    account_id,
                    normalized,
                    normalized,
                    normalized,
                ),
            ).fetchone()
            is not None
        )

    def has_withdrawn_record(
        self,
        account_id: str,
        message_id: str,
        normalized_content: str,
    ) -> bool:
        if self._dimensions is not None:
            return any(
                record.status.value == "withdrawn"
                and (
                    record.evidence_message_id == message_id
                    or _normalize(record.content) == normalized_content
                )
                for record in self._dimensions.list_records(
                    account_id,
                    include_withdrawn=True,
                )
            )
        return (
            self._connection.execute(
                "SELECT 1 FROM profile_four_dimension_records "
                "WHERE account_id = ? AND status = 'withdrawn' "
                "AND (evidence_message_id = ? OR content = ?) LIMIT 1",
                (account_id, message_id, normalized_content),
            ).fetchone()
            is not None
        )

    def get_run(
        self,
        account_id: str,
        message_id: str,
        version: str,
        source_hash: str,
    ) -> ProfileExtractionRun | None:
        return self.automatic_repository.get_run(
            account_id,
            message_id,
            version,
            source_hash,
        )

    def get_task(
        self,
        account_id: str,
        message_id: str,
        version: str,
        source_hash: str,
    ) -> ProfileExtractionRetryTask | None:
        return self.automatic_repository.get_task(
            account_id,
            message_id,
            version,
            source_hash,
        )

    def save_run(self, run: ProfileExtractionRun) -> None:
        self.automatic_repository.save_run(run)

    def save_task(self, task: ProfileExtractionRetryTask) -> None:
        self.automatic_repository.save_task(task)

    def enqueue(self, task_id: str, payload: dict[str, Any]) -> None:
        if self._queue is None:
            raise RuntimeError("read-only replay repository has no queue")
        self._queue.enqueue(PROFILE_REPLAY_QUEUE, task_id, payload=payload)

    def queue_has_runnable(self) -> bool:
        if self._queue is None:
            raise RuntimeError("read-only replay repository has no queue")
        return self._queue.has_runnable(PROFILE_REPLAY_QUEUE)

    def pending_v2_tasks(self) -> bool:
        return any(
            task.status.value in {"pending", "running"}
            and task.extractor_version.startswith("profile-auto-v2")
            for task in self.automatic_repository.list_tasks()
        )

    def v2_run_status_counts(self) -> dict[str, int]:
        """汇总 v2 运行终态，供 worker 报告永久失败计数。"""

        succeeded = 0
        failed = 0
        for run in self.automatic_repository.list_runs():
            if not run.extractor_version.startswith("profile-auto-v2"):
                continue
            if run.status.value == "succeeded":
                succeeded += 1
            elif run.status.value == "exhausted":
                failed += 1
        return {
            "succeeded": succeeded,
            "failed": failed,
            "permanent_failed": failed,
        }

    def run_statuses(
        self, extraction_ids: list[str]
    ) -> list[ProfileReplayRunStatus]:
        if not extraction_ids:
            return []
        if self._automatic is not None:
            wanted = set(extraction_ids)
            return [
                ProfileReplayRunStatus(
                    extraction_id=run.extraction_id,
                    record_ids=tuple(run.committed_record_ids),
                    status=run.status.value,
                )
                for run in self._automatic.list_runs()
                if run.extraction_id in wanted
            ]
        placeholders = ",".join("?" for _ in extraction_ids)
        rows = self._connection.execute(
            "SELECT extraction_id, status, record_ids_json "
            f"FROM profile_extraction_runs WHERE extraction_id IN ({placeholders})",
            tuple(extraction_ids),
        ).fetchall()
        return [
            ProfileReplayRunStatus(
                extraction_id=str(row["extraction_id"]),
                record_ids=tuple(
                    str(value)
                    for value in json.loads(str(row["record_ids_json"]))
                ),
                status=str(row["status"]),
            )
            for row in rows
        ]

    def has_backup_audit(self, backup_id: str) -> bool:
        if self._dimensions is not None:
            return self._dimensions.has_migration_version_prefix(
                f"profile-auto-v2-replay:backup={backup_id}:"
            )
        return (
            self._connection.execute(
                "SELECT 1 FROM profile_four_dimension_migrations "
                "WHERE migration_version LIKE ? LIMIT 1",
                (f"profile-auto-v2-replay:backup={backup_id}:%",),
            ).fetchone()
            is not None
        )

    def save_migration_report(self, report: FourDimensionMigrationReport) -> None:
        self.dimension_repository.save_migration_report(report)


__all__ = [
    "ProfileReplayCandidate",
    "ProfileReplayMessage",
    "ProfileReplayRunStatus",
    "SqliteProfileReplayRepository",
]
