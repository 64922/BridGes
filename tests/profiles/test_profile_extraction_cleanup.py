"""画像抽取历史 400 脏数据清理的公共接缝测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from bridges.storage import BridgesDatabase
from scripts.retry_exhausted_profile_extractions import (
    cleanup_exhausted_profile_extractions,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _insert_run_and_task(
    database: BridgesDatabase,
    *,
    account_id: str,
    message_id: str,
    extraction_id: str,
    task_id: str,
    error: str = "client_error_400: upstream rejected JSON mode",
) -> None:
    timestamp = _now()
    with database.transaction():
        database.connection.execute(
            """
            INSERT INTO profile_extraction_runs (
                extraction_id, account_id, message_id, extractor_version,
                source_hash, source_snapshot, status, attempts, record_ids_json,
                observed_count, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                extraction_id,
                account_id,
                message_id,
                "profile-auto-v1",
                f"hash-{message_id}",
                "我想学习物理",
                "exhausted",
                3,
                "[]",
                0,
                error,
                timestamp,
                timestamp,
            ),
        )
        database.connection.execute(
            """
            INSERT INTO profile_extraction_tasks (
                task_id, account_id, message_id, extractor_version,
                source_hash, status, attempts, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                account_id,
                message_id,
                "profile-auto-v1",
                f"hash-{message_id}",
                "exhausted",
                3,
                error,
                timestamp,
                timestamp,
            ),
        )


def test_cleanup_requeues_live_rows_and_skips_tombstoned_rows(tmp_path) -> None:
    database = BridgesDatabase(tmp_path / "cleanup.db")
    database.initialize()
    _insert_run_and_task(
        database,
        account_id="account-live",
        message_id="message-live",
        extraction_id="run-live",
        task_id="task-live",
    )
    _insert_run_and_task(
        database,
        account_id="account-deleted",
        message_id="message-deleted",
        extraction_id="run-deleted",
        task_id="task-deleted",
    )
    _insert_run_and_task(
        database,
        account_id="account-mixed",
        message_id="message-mixed",
        extraction_id="run-mixed",
        task_id="task-mixed",
    )
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_extraction_tombstones "
            "(account_id, message_id, created_at) VALUES (?, ?, ?)",
            ("account-deleted", "message-deleted", _now()),
        )
        database.connection.execute(
            "UPDATE profile_extraction_runs SET status = 'succeeded', attempts = 1, "
            "last_error = NULL "
            "WHERE extraction_id = 'run-mixed'"
        )

    summary = cleanup_exhausted_profile_extractions(database)

    assert summary.requeued_runs == 1
    assert summary.requeued_tasks == 1
    assert summary.skipped_runs == 1
    assert summary.skipped_tasks == 2
    queue_row = database.connection.execute(
        "SELECT status, payload_json FROM task_claims "
        "WHERE queue_name = 'profile-extraction' AND task_key = ?",
        ("task-live",),
    ).fetchone()
    assert queue_row is not None
    assert queue_row["status"] == "queued"
    assert json.loads(queue_row["payload_json"])["message_id"] == "message-live"

    live_run = database.connection.execute(
        "SELECT status, attempts, last_error FROM profile_extraction_runs "
        "WHERE extraction_id = 'run-live'"
    ).fetchone()
    assert live_run is not None
    assert (live_run["status"], live_run["attempts"]) == ("pending", 0)
    assert "重新入队" in live_run["last_error"]

    deleted_task = database.connection.execute(
        "SELECT status, last_error FROM profile_extraction_tasks WHERE task_id = 'task-deleted'"
    ).fetchone()
    assert deleted_task is not None
    assert deleted_task["status"] == "exhausted"
    assert "跳过" in deleted_task["last_error"]
    mixed_run = database.connection.execute(
        "SELECT status, attempts, last_error FROM profile_extraction_runs "
        "WHERE extraction_id = 'run-mixed'"
    ).fetchone()
    assert mixed_run is not None
    assert (mixed_run["status"], mixed_run["attempts"], mixed_run["last_error"]) == (
        "succeeded",
        1,
        None,
    )
    mixed_task = database.connection.execute(
        "SELECT status, attempts FROM profile_extraction_tasks WHERE task_id = 'task-mixed'"
    ).fetchone()
    assert mixed_task is not None
    assert (mixed_task["status"], mixed_task["attempts"]) == ("exhausted", 3)
    database.close()
