"""一次性重跑画像抽取历史 400 失败记录。

用法：

    .venv/Scripts/python.exe scripts/retry_exhausted_profile_extractions.py --db bridges.db

脚本只处理 ``exhausted`` 且 ``last_error`` 以 ``client_error_400`` 开头的
记录。非墓碑消息会把 runs/tasks 的 ``attempts`` 重置为 0，重新入队到统一的
``profile-extraction`` 队列，由 ``run_retry_tick`` 使用修复后的网关重处理；
墓碑消息保持 exhausted 并记录跳过原因。重跑后再次失败时，运行时会把新的
供应商错误写回 ``last_error``，作为可复查备注。脚本不会静默迁移 schema。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bridges.runtime.queue import TaskQueue  # noqa: E402
from bridges.storage import BridgesDatabase  # noqa: E402

_QUEUE_NAME = "profile-extraction"
_ERROR_PREFIX = "client_error_400"
_TOMBSTONE_NOTE = "画像抽取历史 400 清理：已跳过，原消息已撤回或被墓碑阻止。"
_MISSING_RUN_NOTE = "画像抽取历史 400 清理：已跳过，缺少对应的 run 记录。"
_REQUEUE_NOTE = "画像抽取历史 400 清理：已重新入队，等待 run_retry_tick 重处理。"


@dataclass
class CleanupSummary:
    """一次清理的可复查计数。"""

    requeued_runs: int = 0
    requeued_tasks: int = 0
    skipped_runs: int = 0
    skipped_tasks: int = 0
    created_tasks: int = 0
    queued_tasks: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "requeued_runs": self.requeued_runs,
            "requeued_tasks": self.requeued_tasks,
            "requeued_rows": self.requeued_runs + self.requeued_tasks,
            "skipped_runs": self.skipped_runs,
            "skipped_tasks": self.skipped_tasks,
            "skipped_rows": self.skipped_runs + self.skipped_tasks,
            "created_tasks": self.created_tasks,
            "queued_tasks": self.queued_tasks,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _task_id(
    account_id: str, message_id: str, extractor_version: str, source_hash: str
) -> str:
    digest = hashlib.sha256(
        "|".join((account_id, message_id, extractor_version, source_hash)).encode("utf-8")
    ).hexdigest()[:32]
    return f"profile-retry-cleanup-{digest}"


def _note(prefix: str, original: Any) -> str:
    original_text = str(original or _ERROR_PREFIX)
    return f"{prefix} 原错误：{original_text[:400]}"[:500]


def _is_retryable(row: Any | None) -> bool:
    return (
        row is not None
        and str(row["status"]) == "exhausted"
        and str(row["last_error"] or "").startswith(_ERROR_PREFIX)
    )


def cleanup_exhausted_profile_extractions(
    database: BridgesDatabase,
) -> CleanupSummary:
    """重置可安全重跑的历史 400 行，并将其放回统一任务队列。"""

    database.initialize()
    queue = TaskQueue(database, default_lease_seconds=60)
    queue.set_lease_seconds(_QUEUE_NAME, 60)
    summary = CleanupSummary()
    candidates = database.connection.execute(
        """
        SELECT account_id, message_id, extractor_version, source_hash
        FROM profile_extraction_runs
        WHERE status = 'exhausted' AND last_error LIKE ?
        UNION
        SELECT account_id, message_id, extractor_version, source_hash
        FROM profile_extraction_tasks
        WHERE status = 'exhausted' AND last_error LIKE ?
        ORDER BY account_id, message_id
        """,
        (f"{_ERROR_PREFIX}%", f"{_ERROR_PREFIX}%"),
    ).fetchall()

    with database.transaction():
        for candidate in candidates:
            identity = (
                str(candidate["account_id"]),
                str(candidate["message_id"]),
                str(candidate["extractor_version"]),
                str(candidate["source_hash"]),
            )
            account_id, message_id, extractor_version, source_hash = identity
            run = database.connection.execute(
                """
                SELECT extraction_id, status, last_error
                FROM profile_extraction_runs
                WHERE account_id = ? AND message_id = ?
                  AND extractor_version = ? AND source_hash = ?
                """,
                identity,
            ).fetchone()
            task = database.connection.execute(
                """
                SELECT task_id, status, last_error
                FROM profile_extraction_tasks
                WHERE account_id = ? AND message_id = ?
                  AND extractor_version = ? AND source_hash = ?
                """,
                identity,
            ).fetchone()
            tombstoned = database.connection.execute(
                """
                SELECT 1
                FROM profile_extraction_tombstones
                WHERE account_id = ? AND message_id = ?
                """,
                (account_id, message_id),
            ).fetchone() is not None

            if tombstoned:
                if _is_retryable(run):
                    database.connection.execute(
                        "UPDATE profile_extraction_runs "
                        "SET status = 'exhausted', last_error = ?, updated_at = ? "
                        "WHERE extraction_id = ?",
                        (
                            _note(_TOMBSTONE_NOTE, run["last_error"]),
                            _now(),
                            run["extraction_id"],
                        ),
                    )
                    summary.skipped_runs += 1
                if _is_retryable(task):
                    database.connection.execute(
                        "UPDATE profile_extraction_tasks "
                        "SET status = 'exhausted', last_error = ?, updated_at = ? "
                        "WHERE task_id = ?",
                        (
                            _note(_TOMBSTONE_NOTE, task["last_error"]),
                            _now(),
                            task["task_id"],
                        ),
                    )
                    summary.skipped_tasks += 1
                continue

            if run is None:
                if _is_retryable(task):
                    database.connection.execute(
                        "UPDATE profile_extraction_tasks "
                        "SET status = 'exhausted', last_error = ?, updated_at = ? "
                        "WHERE task_id = ?",
                        (
                            _note(_MISSING_RUN_NOTE, task["last_error"]),
                            _now(),
                            task["task_id"],
                        ),
                    )
                    summary.skipped_tasks += 1
                continue

            if not _is_retryable(run):
                if _is_retryable(task):
                    summary.skipped_tasks += 1
                continue
            if task is not None and not _is_retryable(task):
                summary.skipped_runs += 1
                continue

            database.connection.execute(
                "UPDATE profile_extraction_runs "
                "SET status = 'pending', attempts = 0, last_error = ?, updated_at = ? "
                "WHERE extraction_id = ?",
                (
                    _note(_REQUEUE_NOTE, run["last_error"]),
                    _now(),
                    run["extraction_id"],
                ),
            )
            summary.requeued_runs += 1

            task_id = str(task["task_id"]) if task is not None else _task_id(*identity)
            if task is None:
                timestamp = _now()
                database.connection.execute(
                    """
                    INSERT INTO profile_extraction_tasks (
                        task_id, account_id, message_id, extractor_version, source_hash,
                        status, attempts, last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
                    """,
                    (
                        task_id,
                        account_id,
                        message_id,
                        extractor_version,
                        source_hash,
                        _note(_REQUEUE_NOTE, run["last_error"]),
                        timestamp,
                        timestamp,
                    ),
                )
                summary.created_tasks += 1
            else:
                database.connection.execute(
                    "UPDATE profile_extraction_tasks "
                    "SET status = 'pending', attempts = 0, last_error = ?, updated_at = ? "
                    "WHERE task_id = ?",
                    (
                        _note(_REQUEUE_NOTE, task["last_error"]),
                        _now(),
                        task_id,
                    ),
                )
            summary.requeued_tasks += 1
            queue.enqueue(
                _QUEUE_NAME,
                task_id,
                payload={
                    "account_id": account_id,
                    "message_id": message_id,
                    "extractor_version": extractor_version,
                    "source_hash": source_hash,
                },
            )
            summary.queued_tasks += 1

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重跑画像抽取历史 400 失败记录")
    parser.add_argument("--db", required=True, help="要处理的 bridges.db 路径")
    args = parser.parse_args(argv)
    database_path = Path(args.db)
    if not database_path.is_file():
        parser.error(f"数据库文件不存在：{database_path}")
    database = BridgesDatabase(database_path)
    try:
        summary = cleanup_exhausted_profile_extractions(database)
    finally:
        database.close()
    print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
