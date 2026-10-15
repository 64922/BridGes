"""画像工单 06：v82 迁移为原子条目补齐修订原因与冲突状态列。

从 v81 起步应用 v82 后，旧行两列取默认值（无修订原因、无冲突），
新写入的纠错/变化/冲突数据在同一库中落盘；重复初始化保持幂等。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase


def _build_v81_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        for version in range(1, 82):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '81')")


def test_upgrade_from_v81_adds_revision_and_conflict_columns(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-81.db"
    _build_v81_database(path)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION

    columns = {
        str(item["name"])
        for item in database.connection.execute(
            "PRAGMA table_info(profile_items)"
        ).fetchall()
    }
    assert {"retired_reason", "conflict_state"} <= columns

    # 升级幂等：重复初始化不重复执行迁移。
    assert database.initialize() == SCHEMA_VERSION
    database.close()


def test_v82_migration_keeps_legacy_rows_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-81-rows.db"
    _build_v81_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO profile_items ("
            "profile_item_id, account_id, text, identity_key, fact_subject, "
            "fact_relation, fact_object, fact_scope, fact_key, evidence_quote, "
            "valid_from, valid_until, validity_anchor_at, validity_phrase, "
            "goal_state, supersedes_id, superseded_by_id, source_record_id, "
            "source_message_ids_json, source_quotes_json, topic_hint, "
            "topics_json, status, write_origin, confidence, version, created_at, "
            "updated_at, user_edited_at, migration_run_id) VALUES ("
            "'legacy-1', 'account-alice', '我是大二学生', 'key-1', 'user', "
            "'grade', '大二', 'long_term', 'fact-key-1', NULL, NULL, NULL, "
            "NULL, NULL, 'active', NULL, NULL, NULL, '[]', '{}', NULL, '[]', "
            "'active', 'automatic', 'high', 1, "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', "
            "NULL, NULL)"
        )

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    row = database.connection.execute(
        "SELECT retired_reason, conflict_state FROM profile_items"
        " WHERE profile_item_id = 'legacy-1'"
    ).fetchone()
    assert row is not None
    assert row["retired_reason"] is None
    assert str(row["conflict_state"]) == "none"
    database.close()
