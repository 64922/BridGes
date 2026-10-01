"""Issue 16 迁移结构：画像条目事实身份列与 v63 → v64 增量升级。

断言新列与新索引真实存在、进入启动完整性清单，旧版本库升级后既有行保留
并得到安全的默认身份（不伪造关系）；旧行由领域层在账户迁移时补齐身份。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage.database import (
    MIGRATIONS,
    REQUIRED_INDEXES,
    REQUIRED_TABLE_COLUMNS,
    SCHEMA_VERSION,
    BridgesDatabase,
)

LEGACY_VERSION = 63
FACT_KEY_INDEX = "idx_profile_items_account_fact_key"
NEW_ITEM_COLUMNS = {
    "fact_subject",
    "fact_relation",
    "fact_object",
    "fact_scope",
    "fact_key",
    "evidence_quote",
    "supersedes_id",
    "superseded_by_id",
}
NEW_MIGRATION_COLUMNS = {"identity_backfilled"}


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _index_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }


def test_fresh_database_has_fact_identity_columns(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert SCHEMA_VERSION >= LEGACY_VERSION + 1
    assert _columns(path, "profile_items") >= NEW_ITEM_COLUMNS
    assert _columns(path, "profile_item_migrations") >= NEW_MIGRATION_COLUMNS
    assert FACT_KEY_INDEX in _index_names(path)


def test_fact_identity_columns_are_required_at_startup() -> None:
    assert REQUIRED_TABLE_COLUMNS["profile_items"] >= NEW_ITEM_COLUMNS
    assert REQUIRED_TABLE_COLUMNS["profile_item_migrations"] >= NEW_MIGRATION_COLUMNS
    assert FACT_KEY_INDEX in REQUIRED_INDEXES


def test_upgrade_from_v63_preserves_rows_and_adds_identity_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "upgrade.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, LEGACY_VERSION + 1):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '63')")
        connection.execute(
            "INSERT INTO profile_items("
            "profile_item_id, account_id, text, identity_key, source_record_id,"
            " source_message_ids_json, topic_hint, status, write_origin, confidence,"
            " version, created_at, updated_at) "
            "VALUES ('item-old', 'a', '旧条目', 'key-old', NULL, '[]',"
            " 'academic_status', 'active', 'automatic', 'high', 1,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    row = database.connection.execute(
        "SELECT * FROM profile_items WHERE profile_item_id = 'item-old'"
    ).fetchone()
    assert row is not None
    assert row["text"] == "旧条目"
    # 旧行获得安全的默认身份：关系未保存时不伪造，保持整句陈述与空身份键，
    # 等待账户迁移按正文补齐；其他事实列默认空/长期。
    assert row["fact_subject"] == "user"
    assert row["fact_relation"] == "statement"
    assert row["fact_object"] == ""
    assert row["fact_scope"] == "long_term"
    assert row["fact_key"] == ""
    assert row["evidence_quote"] is None
    assert row["supersedes_id"] is None
    assert row["superseded_by_id"] is None
    # 迁移报告旧行在升级后可按默认读取（0 表示本批次没有补齐身份）。
    report_columns = {
        str(entry[1])
        for entry in database.connection.execute(
            "PRAGMA table_info(profile_item_migrations)"
        )
    }
    assert report_columns >= NEW_MIGRATION_COLUMNS
    assert database.initialize() == SCHEMA_VERSION
