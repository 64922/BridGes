"""有界摘要缓存迁移到 65 后，已有会话库可无损升级。"""

import sqlite3

from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase


def test_upgrade_from_v64_preserves_conversations_and_adds_summaries(tmp_path) -> None:
    path = tmp_path / "upgrade.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 65):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_meta VALUES ('version', '64')")
        connection.execute(
            "INSERT INTO accounts(account_id, email, created_at)"
            " VALUES ('a', 'a@example.com', '2026-10-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO conversations("
            "conversation_id, account_id, title, mode, created_at, updated_at)"
            " VALUES ('c', 'a', '旧会话', 'companion',"
            " '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')"
        )
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION

    assert SCHEMA_VERSION >= 65
    row = database.scoped("a").execute(
        "SELECT title FROM conversations WHERE conversation_id = ? AND account_id = ?",
        ("c", "a"),
    ).fetchone()
    assert row["title"] == "旧会话"
    columns = {
        row["name"]
        for row in database.connection.execute("PRAGMA table_info(conversation_summaries)")
    }
    assert {
        "conversation_id",
        "covered_first_message_id",
        "covered_last_message_id",
        "source_fingerprint",
        "summary_text",
        "status",
    } <= columns
    indexes = {
        row["name"]
        for row in database.connection.execute("PRAGMA index_list(conversation_summaries)")
    }
    assert "idx_conversation_summaries_conversation" in indexes
    assert database.initialize() == SCHEMA_VERSION


def test_upgrade_from_v65_adds_durable_invalidation_generation(tmp_path) -> None:
    path = tmp_path / "upgrade-65.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 66):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_meta VALUES ('version', '65')")
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert database.connection.execute(
        "SELECT COUNT(*) FROM conversation_summary_generations"
    ).fetchone()[0] == 0
    assert database.initialize() == SCHEMA_VERSION
