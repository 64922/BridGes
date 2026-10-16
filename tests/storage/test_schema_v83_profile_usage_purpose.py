"""画像工单 09：v83 迁移为原子条目补齐敏感自述的用途列。

从 v82 起步应用 v83 后，旧行的 ``usage_purpose`` 为空（未说明用途的敏感
自述不进入回答）；新写入的用途值在同一库中落盘；重复初始化保持幂等。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase


def _build_v82_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        for version in range(1, 83):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '82')")


def test_upgrade_from_v82_adds_usage_purpose_column(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-82.db"
    _build_v82_database(path)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION

    columns = {
        str(item["name"])
        for item in database.connection.execute(
            "PRAGMA table_info(profile_items)"
        ).fetchall()
    }
    assert "usage_purpose" in columns

    # 升级幂等：重复初始化不重复执行迁移。
    assert database.initialize() == SCHEMA_VERSION
    database.close()


def test_v83_migration_keeps_legacy_rows_without_purpose(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-82-rows.db"
    _build_v82_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO profile_items ("
            "profile_item_id, account_id, text, identity_key, fact_subject, "
            "fact_relation, fact_object, fact_scope, fact_key, evidence_quote, "
            "valid_from, valid_until, validity_anchor_at, validity_phrase, "
            "goal_state, supersedes_id, superseded_by_id, source_record_id, "
            "source_message_ids_json, source_quotes_json, topic_hint, "
            "topics_json, status, write_origin, confidence, version, created_at, "
            "updated_at, user_edited_at, migration_run_id, retired_reason, "
            "conflict_state) VALUES ("
            "'legacy-1', 'account-alice', '我是大二学生', 'key-1', 'user', "
            "'grade', '大二', 'long_term', 'fact-key-1', NULL, NULL, NULL, "
            "NULL, NULL, 'active', NULL, NULL, NULL, '[]', '{}', NULL, '[]', "
            "'active', 'automatic', 'high', 1, "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', "
            "NULL, NULL, NULL, 'none')"
        )

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    row = database.connection.execute(
        "SELECT usage_purpose FROM profile_items WHERE profile_item_id = 'legacy-1'"
    ).fetchone()
    assert row is not None
    assert row["usage_purpose"] is None

    # 迁移后的库可以写入并读回用途值。
    database.connection.execute(
        "UPDATE profile_items SET usage_purpose = '户外建议'"
        " WHERE profile_item_id = 'legacy-1'"
    )
    updated = database.connection.execute(
        "SELECT usage_purpose FROM profile_items WHERE profile_item_id = 'legacy-1'"
    ).fetchone()
    assert updated is not None and str(updated["usage_purpose"]) == "户外建议"
    database.close()
