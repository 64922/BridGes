"""SCHEMA_VERSION 32 → 33 迁移测试：学习项目迁移审计表。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bridges.storage import SCHEMA_VERSION, BridgesDatabase
from bridges.storage.database import MIGRATIONS


def _build_v32_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '32')"
        )
        for version in range(1, 33):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.commit()


def test_upgrade_from_v32_creates_migration_audit_tables_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _build_v32_database(path)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert database.initialize() == SCHEMA_VERSION

    tables = {
        str(row["name"])
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "learning_project_migration_runs",
        "learning_project_migrations",
        "learning_project_migration_conversations",
        "learning_project_migration_tombstones",
    } <= tables
    database.close()
