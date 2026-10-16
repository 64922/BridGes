"""画像工单 17：历史重整进度台账的 SQLite 仓储。

台账是账户级维护审计：运行行（``profile_rebuild_runs``）与逐条进度行
（``profile_rebuild_items``）只保存标识、计数、摘要与失败状态，绝不保存
消息或条目正文。仓储同时提供只读的管道状态查询（抽取运行 / 重试任务 /
队列领取行），供编排层对账逐条状态；不复制任何抽取或提交语义。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from bridges.profiles.history_rebuild_contracts import (
    RebuildBaseline,
    RebuildItemRecord,
    RebuildItemStatus,
    RebuildRunRecord,
    RebuildSeedItem,
    RebuildSkipReason,
    RebuildStatus,
    now_text,
)
from bridges.profiles.transactions import joined_transaction
from bridges.storage.database import BridgesDatabase


@dataclass(frozen=True)
class ExtractionRunSnapshot:
    """抽取运行的只读视图（台账分类用，不含来源快照正文）。"""

    extraction_id: str
    status: str
    outcome: str
    attempts: int
    committed_count: int
    observed_count: int
    last_error: str | None
    run_kind: str


@dataclass(frozen=True)
class ExtractionTaskSnapshot:
    """抽取重试任务的只读视图。"""

    task_id: str
    status: str
    last_error: str | None


@dataclass(frozen=True)
class QueueClaimSnapshot:
    """队列领取行的只读视图（队列机制语义由 ``bridges.runtime.queue`` 定义）。"""

    status: str
    next_retry_at: str | None


class SqliteProfileRebuildRepository:
    """``profile_rebuild_*`` 两张表的仓储；所有写操作显式绑定重整标识。"""

    def __init__(self, database: BridgesDatabase, *, initialize: bool = True) -> None:
        self.database = database
        if initialize:
            self.database.initialize()

    def transaction(self) -> AbstractContextManager[None]:
        """台账写入与调用方已有事务并入同一提交边界。"""

        return joined_transaction(self.database)

    # ------------------------------------------------------------------
    # 运行行
    # ------------------------------------------------------------------

    def create_run(self, record: RebuildRunRecord) -> bool:
        """幂等创建运行行；已存在同标识时返回 ``False`` 且不改写既有进度。"""

        with self.transaction():
            cursor = self.database.connection.execute(
                "INSERT OR IGNORE INTO profile_rebuild_runs"
                " (rebuild_id, account_id, status, policy_version,"
                "  extractor_version, source_digest, state_digest, schema_version,"
                "  window_start_at, window_end_at, baseline_json, window_total,"
                "  eligible_count, extracted_count, committed_count,"
                "  skipped_count, failed_count, last_error, created_at,"
                "  updated_at, completed_at, reconciled_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                "         ?, ?, ?, ?)",
                (
                    record.rebuild_id,
                    record.account_id,
                    record.status.value,
                    record.policy_version,
                    record.extractor_version,
                    record.source_digest,
                    record.state_digest,
                    record.schema_version,
                    record.window_start_at,
                    record.window_end_at,
                    json.dumps(
                        record.baseline.as_dict(), ensure_ascii=False, sort_keys=True
                    ),
                    record.window_total,
                    record.eligible_count,
                    record.extracted_count,
                    record.committed_count,
                    record.skipped_count,
                    record.failed_count,
                    record.last_error,
                    record.created_at,
                    record.updated_at,
                    record.completed_at,
                    record.reconciled_at,
                ),
            )
            return cursor.rowcount == 1

    def get_run(self, rebuild_id: str) -> RebuildRunRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM profile_rebuild_runs WHERE rebuild_id = ?",
            (rebuild_id,),
        ).fetchone()
        return None if row is None else self._run_from_row(row)

    def update_run(
        self,
        rebuild_id: str,
        *,
        status: RebuildStatus | None = None,
        window_total: int | None = None,
        eligible_count: int | None = None,
        last_error: str | None = None,
        clear_last_error: bool = False,
        completed_at: str | None = None,
        reconciled_at: str | None = None,
        now: str | None = None,
    ) -> None:
        """更新运行行的可变字段；计数器由 :meth:`refresh_counters` 统一派生。"""

        assignments = ["updated_at = ?"]
        values: list[Any] = [now or now_text()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status.value)
        if window_total is not None:
            assignments.append("window_total = ?")
            values.append(window_total)
        if eligible_count is not None:
            assignments.append("eligible_count = ?")
            values.append(eligible_count)
        if clear_last_error:
            assignments.append("last_error = NULL")
        elif last_error is not None:
            assignments.append("last_error = ?")
            values.append(last_error)
        if completed_at is not None:
            assignments.append("completed_at = ?")
            values.append(completed_at)
        if reconciled_at is not None:
            assignments.append("reconciled_at = ?")
            values.append(reconciled_at)
        values.append(rebuild_id)
        with self.transaction():
            self.database.connection.execute(
                f"UPDATE profile_rebuild_runs SET {', '.join(assignments)}"
                " WHERE rebuild_id = ?",
                values,
            )

    def delete_run(self, rebuild_id: str) -> None:
        """清理：先删逐条行再删运行行（同事务，幂等）。"""

        with self.transaction():
            self.database.connection.execute(
                "DELETE FROM profile_rebuild_items WHERE rebuild_id = ?",
                (rebuild_id,),
            )
            self.database.connection.execute(
                "DELETE FROM profile_rebuild_runs WHERE rebuild_id = ?",
                (rebuild_id,),
            )

    # ------------------------------------------------------------------
    # 逐条进度行
    # ------------------------------------------------------------------

    def seed_items(
        self, rebuild_id: str, account_id: str, items: Sequence[RebuildSeedItem]
    ) -> int:
        """幂等写入窗口消息的初始判定；已存在的条目行保持原状。"""

        inserted = 0
        now = now_text()
        with self.transaction():
            for item in items:
                cursor = self.database.connection.execute(
                    "INSERT OR IGNORE INTO profile_rebuild_items"
                    " (rebuild_id, message_id, account_id, conversation_id,"
                    "  source_created_at, status, skip_reason, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rebuild_id,
                        item.message_id,
                        account_id,
                        item.conversation_id,
                        item.source_created_at,
                        item.status.value,
                        item.skip_reason.value if item.skip_reason else None,
                        now,
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    def list_items(
        self,
        rebuild_id: str,
        *,
        statuses: Sequence[RebuildItemStatus] | None = None,
    ) -> list[RebuildItemRecord]:
        query = "SELECT * FROM profile_rebuild_items WHERE rebuild_id = ?"
        parameters: list[Any] = [rebuild_id]
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            query += f" AND status IN ({placeholders})"
            parameters.extend(status.value for status in statuses)
        query += " ORDER BY source_created_at, message_id"
        rows = self.database.connection.execute(query, parameters).fetchall()
        return [self._item_from_row(row) for row in rows]

    def update_item(
        self,
        rebuild_id: str,
        message_id: str,
        *,
        status: RebuildItemStatus,
        skip_reason: RebuildSkipReason | None = None,
        source_hash: str | None = None,
        task_key: str | None = None,
        extraction_id: str | None = None,
        committed_count: int | None = None,
        observed_count: int | None = None,
        last_error: str | None = None,
        now: str | None = None,
    ) -> None:
        """整行更新一条进度的可变量；``None`` 表示保留既有值。"""

        assignments = ["status = ?", "skip_reason = ?", "updated_at = ?"]
        values: list[Any] = [
            status.value,
            skip_reason.value if skip_reason else None,
            now or now_text(),
        ]
        for column, value in (
            ("source_hash", source_hash),
            ("task_key", task_key),
            ("extraction_id", extraction_id),
            ("committed_count", committed_count),
            ("observed_count", observed_count),
            ("last_error", last_error),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        values.extend([rebuild_id, message_id])
        with self.transaction():
            self.database.connection.execute(
                "UPDATE profile_rebuild_items SET "
                + ", ".join(assignments)
                + " WHERE rebuild_id = ? AND message_id = ?",
                values,
            )

    def count_items(self, rebuild_id: str) -> dict[str, int]:
        rows = self.database.connection.execute(
            "SELECT status, COUNT(*) AS total FROM profile_rebuild_items"
            " WHERE rebuild_id = ? GROUP BY status",
            (rebuild_id,),
        ).fetchall()
        return {str(row["status"]): int(row["total"]) for row in rows}

    def count_skip_reasons(self, rebuild_id: str) -> dict[str, int]:
        rows = self.database.connection.execute(
            "SELECT skip_reason, COUNT(*) AS total FROM profile_rebuild_items"
            " WHERE rebuild_id = ? AND status = 'skipped' AND skip_reason IS NOT NULL"
            " GROUP BY skip_reason ORDER BY skip_reason",
            (rebuild_id,),
        ).fetchall()
        return {str(row["skip_reason"]): int(row["total"]) for row in rows}

    def refresh_counters(self, rebuild_id: str, *, now: str | None = None) -> None:
        """从逐条行重算运行计数器（分母与失败数始终真实，不手工累加）。"""

        with self.transaction():
            self.database.connection.execute(
                "UPDATE profile_rebuild_runs SET"
                " window_total = (SELECT COUNT(*) FROM profile_rebuild_items"
                "   WHERE rebuild_id = ?),"
                " eligible_count = (SELECT COUNT(*) FROM profile_rebuild_items"
                "   WHERE rebuild_id = ? AND status != 'skipped'),"
                " extracted_count = (SELECT COUNT(*) FROM profile_rebuild_items"
                "   WHERE rebuild_id = ? AND status = 'extracted'),"
                " committed_count = (SELECT COALESCE(SUM(committed_count), 0)"
                "   FROM profile_rebuild_items WHERE rebuild_id = ?),"
                " skipped_count = (SELECT COUNT(*) FROM profile_rebuild_items"
                "   WHERE rebuild_id = ? AND status = 'skipped'),"
                " failed_count = (SELECT COUNT(*) FROM profile_rebuild_items"
                "   WHERE rebuild_id = ? AND status = 'failed'),"
                " updated_at = ?"
                " WHERE rebuild_id = ?",
                (
                    rebuild_id,
                    rebuild_id,
                    rebuild_id,
                    rebuild_id,
                    rebuild_id,
                    rebuild_id,
                    now or now_text(),
                    rebuild_id,
                ),
            )

    # ------------------------------------------------------------------
    # 管道只读状态（分类与恢复判定）
    # ------------------------------------------------------------------

    def read_extraction_run(
        self,
        account_id: str,
        message_id: str,
        extractor_version: str,
        source_hash: str,
    ) -> ExtractionRunSnapshot | None:
        row = self.database.connection.execute(
            "SELECT extraction_id, status, outcome, attempts, record_ids_json,"
            " observed_count, last_error, run_kind"
            " FROM profile_extraction_runs"
            " WHERE account_id = ? AND message_id = ? AND extractor_version = ?"
            " AND source_hash = ?",
            (account_id, message_id, extractor_version, source_hash),
        ).fetchone()
        if row is None:
            return None
        try:
            committed = json.loads(str(row["record_ids_json"]))
        except (TypeError, ValueError):
            committed = []
        return ExtractionRunSnapshot(
            extraction_id=str(row["extraction_id"]),
            status=str(row["status"]),
            outcome=str(row["outcome"]),
            attempts=int(row["attempts"]),
            committed_count=len(committed) if isinstance(committed, list) else 0,
            observed_count=int(row["observed_count"]),
            last_error=(
                str(row["last_error"]) if row["last_error"] is not None else None
            ),
            run_kind=str(row["run_kind"]),
        )

    def read_task(
        self,
        account_id: str,
        message_id: str,
        extractor_version: str,
        source_hash: str,
    ) -> ExtractionTaskSnapshot | None:
        row = self.database.connection.execute(
            "SELECT task_id, status, last_error FROM profile_extraction_tasks"
            " WHERE account_id = ? AND message_id = ? AND extractor_version = ?"
            " AND source_hash = ?",
            (account_id, message_id, extractor_version, source_hash),
        ).fetchone()
        if row is None:
            return None
        return ExtractionTaskSnapshot(
            task_id=str(row["task_id"]),
            status=str(row["status"]),
            last_error=(
                str(row["last_error"]) if row["last_error"] is not None else None
            ),
        )

    def read_claim(self, queue_name: str, task_key: str) -> QueueClaimSnapshot | None:
        row = self.database.connection.execute(
            "SELECT status, next_retry_at FROM task_claims"
            " WHERE queue_name = ? AND task_key = ?",
            (queue_name, task_key),
        ).fetchone()
        if row is None:
            return None
        return QueueClaimSnapshot(
            status=str(row["status"]),
            next_retry_at=(
                str(row["next_retry_at"]) if row["next_retry_at"] is not None else None
            ),
        )

    def count_pending_tasks(self, account_id: str) -> int:
        """账户在管道中未终态的任务数（背压与在途范围的真实分母）。"""

        row = self.database.connection.execute(
            "SELECT COUNT(*) AS total FROM profile_extraction_tasks"
            " WHERE account_id = ? AND status IN ('pending', 'running')",
            (account_id,),
        ).fetchone()
        return 0 if row is None else int(row["total"])

    def count_tombstoned_successes_since(self, account_id: str, *, since: str) -> int:
        """墓碑消息在给定时刻之后的成功抽取数（删除屏障的迟到写入检查）。"""

        row = self.database.connection.execute(
            "SELECT COUNT(*) AS total FROM profile_extraction_runs"
            " WHERE account_id = ? AND status = 'succeeded' AND updated_at >= ?"
            " AND message_id IN (SELECT message_id FROM profile_extraction_tombstones"
            "                    WHERE account_id = ?)",
            (account_id, since, account_id),
        ).fetchone()
        return 0 if row is None else int(row["total"])

    # ------------------------------------------------------------------
    # 行映射
    # ------------------------------------------------------------------

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RebuildRunRecord:
        try:
            baseline_payload = json.loads(str(row["baseline_json"]))
        except (TypeError, ValueError):
            baseline_payload = {}
        return RebuildRunRecord(
            rebuild_id=str(row["rebuild_id"]),
            account_id=str(row["account_id"]),
            status=RebuildStatus(str(row["status"])),
            policy_version=str(row["policy_version"]),
            extractor_version=str(row["extractor_version"]),
            source_digest=str(row["source_digest"]),
            state_digest=str(row["state_digest"]),
            schema_version=int(row["schema_version"]),
            window_start_at=(
                str(row["window_start_at"])
                if row["window_start_at"] is not None
                else None
            ),
            window_end_at=(
                str(row["window_end_at"]) if row["window_end_at"] is not None else None
            ),
            baseline=RebuildBaseline.from_dict(
                baseline_payload if isinstance(baseline_payload, dict) else {}
            ),
            window_total=int(row["window_total"]),
            eligible_count=int(row["eligible_count"]),
            extracted_count=int(row["extracted_count"]),
            committed_count=int(row["committed_count"]),
            skipped_count=int(row["skipped_count"]),
            failed_count=int(row["failed_count"]),
            last_error=(
                str(row["last_error"]) if row["last_error"] is not None else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=(
                str(row["completed_at"]) if row["completed_at"] is not None else None
            ),
            reconciled_at=(
                str(row["reconciled_at"]) if row["reconciled_at"] is not None else None
            ),
        )

    @staticmethod
    def _item_from_row(row: sqlite3.Row) -> RebuildItemRecord:
        raw_reason = row["skip_reason"]
        return RebuildItemRecord(
            rebuild_id=str(row["rebuild_id"]),
            message_id=str(row["message_id"]),
            account_id=str(row["account_id"]),
            conversation_id=str(row["conversation_id"]),
            source_created_at=str(row["source_created_at"]),
            status=RebuildItemStatus(str(row["status"])),
            skip_reason=(
                RebuildSkipReason(str(raw_reason)) if raw_reason is not None else None
            ),
            source_hash=(
                str(row["source_hash"]) if row["source_hash"] is not None else None
            ),
            task_key=str(row["task_key"]) if row["task_key"] is not None else None,
            extraction_id=(
                str(row["extraction_id"]) if row["extraction_id"] is not None else None
            ),
            committed_count=int(row["committed_count"]),
            observed_count=int(row["observed_count"]),
            last_error=(
                str(row["last_error"]) if row["last_error"] is not None else None
            ),
            updated_at=str(row["updated_at"]),
        )


__all__ = [
    "ExtractionRunSnapshot",
    "ExtractionTaskSnapshot",
    "QueueClaimSnapshot",
    "SqliteProfileRebuildRepository",
]
