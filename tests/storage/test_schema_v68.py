"""画像依据反馈迁移到 68 后，账户反馈状态可用且旧库可无损升级。"""

import sqlite3

from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase


def test_fresh_database_has_profile_item_feedback() -> None:
    database = BridgesDatabase(":memory:")
    assert database.initialize() == SCHEMA_VERSION

    columns = {
        row["name"]
        for row in database.connection.execute(
            "PRAGMA table_info(profile_item_feedback)"
        )
    }
    assert {
        "feedback_id",
        "account_id",
        "profile_item_id",
        "kind",
        "note",
        "item_version",
        "created_at",
    } <= columns
    indexes = {
        row["name"]
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "idx_profile_item_feedback_item" in indexes


def test_upgrade_from_v67_adds_feedback_table_without_touching_items(tmp_path) -> None:
    path = tmp_path / "upgrade-67.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 68):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '67')")
        connection.execute(
            "INSERT INTO profile_items ("
            "profile_item_id, account_id, text, identity_key, fact_subject,"
            " fact_relation, fact_object, fact_scope, fact_key,"
            " source_message_ids_json, status, write_origin, confidence,"
            " version, created_at, updated_at)"
            " VALUES ('i1', 'a', '英语六级', 'k', 'user', 'goal', '英语六级',"
            " 'long_term', 'fk', '[]', 'active', 'user', 'high', 1,"
            " '2026-01-05T09:00:00+00:00', '2026-01-05T09:00:00+00:00')"
        )
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 68

    row = database.connection.execute(
        "SELECT text FROM profile_items WHERE profile_item_id = 'i1'"
    ).fetchone()
    assert row["text"] == "英语六级"
    database.connection.execute(
        "INSERT INTO profile_item_feedback VALUES ("
        "'f1', 'a', 'i1', 'expired', NULL, 1, '2026-10-02T09:00:00+00:00')"
    )
    assert database.initialize() == SCHEMA_VERSION
