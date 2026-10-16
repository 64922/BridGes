"""画像工单 17：v89 迁移新增历史重整台账表。

台账是账户数据（审计类别）：随账户删除清除、进入导出与逻辑摘要、随备份
恢复返回；不保存消息或条目正文。从 v88 升级后新表可用、重复初始化幂等。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.lifecycle.catalog import (
    ACCOUNT_TABLES,
    EXPORT_CATEGORIES,
    delete_account_rows,
    logical_summary,
)
from bridges.storage.database import (
    MIGRATIONS,
    REQUIRED_TABLES,
    SCHEMA_VERSION,
    BridgesDatabase,
)

ALICE = "account-alice"
RUNS_TABLE = "profile_rebuild_runs"
ITEMS_TABLE = "profile_rebuild_items"


def _build_v88_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        for version in range(1, 89):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '88')")


def test_upgrade_from_v88_adds_rebuild_ledger_tables(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-88.db"
    _build_v88_database(path)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 89

    tables = {
        str(row["name"])
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {RUNS_TABLE, ITEMS_TABLE} <= tables
    run_columns = {
        str(row["name"]) for row in database.connection.execute(f"PRAGMA table_info({RUNS_TABLE})")
    }
    assert {
        "rebuild_id",
        "account_id",
        "status",
        "policy_version",
        "extractor_version",
        "source_digest",
        "state_digest",
        "schema_version",
        "window_start_at",
        "window_end_at",
        "baseline_json",
        "window_total",
        "eligible_count",
        "committed_count",
        "created_at",
        "completed_at",
        "reconciled_at",
    } <= run_columns
    item_columns = {
        str(row["name"]) for row in database.connection.execute(f"PRAGMA table_info({ITEMS_TABLE})")
    }
    assert {
        "rebuild_id",
        "message_id",
        "account_id",
        "conversation_id",
        "source_created_at",
        "status",
        "skip_reason",
        "source_hash",
        "extraction_id",
        "committed_count",
        "last_error",
        "updated_at",
    } <= item_columns

    assert database.initialize() == SCHEMA_VERSION
    database.close()


def test_rebuild_ledger_is_account_scoped_and_exported(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()

    assert RUNS_TABLE in REQUIRED_TABLES
    assert ITEMS_TABLE in REQUIRED_TABLES

    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_rebuild_runs ("
            " rebuild_id, account_id, status, policy_version, extractor_version,"
            " source_digest, state_digest, schema_version, baseline_json,"
            " created_at, updated_at"
            ") VALUES (?, ?, 'created', 'p', 'e', 's', 'd', 89, '{}', 't', 't')",
            ("profile-rebuild-test", ALICE),
        )
        database.connection.execute(
            "INSERT INTO profile_rebuild_items ("
            " rebuild_id, message_id, account_id, conversation_id,"
            " source_created_at, status, updated_at"
            ") VALUES (?, ?, ?, 'conversation-1', 't', 'pending', 't')",
            ("profile-rebuild-test", "msg-1", ALICE),
        )

    exported = {
        table for category in EXPORT_CATEGORIES for table in category.tables
    }
    assert {RUNS_TABLE, ITEMS_TABLE} <= exported
    category = next(
        entry for entry in EXPORT_CATEGORIES if entry.key == "profile_history_rebuild"
    )
    assert set(category.tables) == {RUNS_TABLE, ITEMS_TABLE}

    summary = logical_summary(database, ALICE)
    assert summary.get(RUNS_TABLE) == 1
    assert summary.get(ITEMS_TABLE) == 1

    assert ACCOUNT_TABLES.index(ITEMS_TABLE) < ACCOUNT_TABLES.index("profile_items")
    assert ACCOUNT_TABLES.index(RUNS_TABLE) < ACCOUNT_TABLES.index("profile_items")

    delete_account_rows(database, ALICE)
    for table in (RUNS_TABLE, ITEMS_TABLE):
        row = database.connection.execute(
            f"SELECT COUNT(*) AS total FROM {table} WHERE account_id = ?", (ALICE,)
        ).fetchone()
        assert int(row["total"]) == 0
    database.close()
