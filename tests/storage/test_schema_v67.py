"""画像有效期与目标生命周期迁移到 67 后，已有画像条目可无损升级。"""

import sqlite3

from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase


def test_upgrade_from_v66_adds_validity_and_lifecycle_columns(tmp_path) -> None:
    path = tmp_path / "upgrade-66.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 67):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '66')")
        connection.execute(
            "INSERT INTO profile_items ("
            "profile_item_id, account_id, text, identity_key, fact_subject,"
            " fact_relation, fact_object, fact_scope, fact_key,"
            " source_message_ids_json, status, write_origin, confidence,"
            " version, created_at, updated_at)"
            " VALUES ('i1', 'a', '考研', 'k', 'user', 'goal', '考研',"
            " 'long_term', 'fk', '[]', 'active', 'user', 'high', 1,"
            " '2026-01-05T09:00:00+00:00', '2026-01-05T09:00:00+00:00')"
        )
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 67

    columns = {
        row["name"]
        for row in database.connection.execute("PRAGMA table_info(profile_items)")
    }
    assert {
        "valid_from",
        "valid_until",
        "validity_anchor_at",
        "validity_phrase",
        "goal_state",
    } <= columns
    row = database.connection.execute(
        "SELECT text, goal_state, valid_until FROM profile_items"
        " WHERE profile_item_id = 'i1'"
    ).fetchone()
    assert row["text"] == "考研"
    assert row["goal_state"] == "active"
    assert row["valid_until"] is None
    assert database.initialize() == SCHEMA_VERSION
