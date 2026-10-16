"""画像工单 17：历史重整驱动的管道只读视图。

抽取运行 / 重试任务 / 队列领取行的只读查询与账户级计数（在途背压、
墓碑迟到成功）。这里只读在线管道的既有表，不复制任何抽取或提交语义，
也不写入这些表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

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


class ProfileRebuildPipelineView:
    """绑定在工作副本上的管道只读视图（供驱动分类与对账核对）。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self.database = database

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


__all__ = [
    "ExtractionRunSnapshot",
    "ExtractionTaskSnapshot",
    "ProfileRebuildPipelineView",
    "QueueClaimSnapshot",
]
