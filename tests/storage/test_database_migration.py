"""Issue 05：bridges.db 版本化事务迁移、幂等启动与旧数据库只读保留。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
    StorageError,
)


def _schema_version(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_first_startup_transactionally_creates_versioned_sqlite_database(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    version = database.initialize()

    assert version == 3
    assert (tmp_path / "bridges.db").exists()
    assert _schema_version(tmp_path / "bridges.db") == 3
    with sqlite3.connect(tmp_path / "bridges.db") as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"accounts", "objects", "schema_meta"} <= tables
    # Issue 14 迁移产物：mode_events 表与 messages.thinking 列真实存在
    assert "mode_events" in tables
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(messages)")
    }
    assert "thinking" in columns
    # foreign_keys 是连接级设置，必须通过数据库自己的连接确认已启用。
    assert int(database.connection.execute("PRAGMA foreign_keys").fetchone()[0]) == 1


def test_repeated_startup_does_not_remigrate_or_corrupt_sqlite_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bridges.db"
    first = BridgesDatabase(path)
    first.initialize()
    repository = BridgesObjectRepository(
        first, EncryptedFileObjectStore(tmp_path, encryption_key="test-key")
    )
    account_id = repository.register_account("repeat@example.com")

    # 重复启动（重新打开数据库）只补齐缺失迁移，不重复执行、不损坏数据。
    second = BridgesDatabase(path)
    assert second.initialize() == 3
    assert second.initialize() == 3  # 第三次启动同样幂等
    row = second.connection.execute(
        "SELECT account_id FROM accounts WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert row is not None and str(row["account_id"]) == account_id


def test_sqlite_database_rejects_newer_migration_version_in_chinese(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bridges.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '999')"
        )

    database = BridgesDatabase(path)
    with pytest.raises(StorageError) as exc_info:
        database.initialize()
    message = str(exc_info.value)
    assert "迁移版本" in message
    assert str(path) not in message  # 不输出宿主敏感绝对路径


def test_sqlite_database_corruption_reports_chinese_without_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bridges.db"
    path.write_bytes(b"this is not a sqlite database file")

    # 连接配置阶段即检测到损坏文件并抛中文错误，不输出宿主敏感绝对路径。
    with pytest.raises(StorageError) as exc_info:
        BridgesDatabase(path)
    message = str(exc_info.value)
    assert "数据库" in message
    assert str(tmp_path) not in message


def test_sqlite_foreign_keys_enforced_for_object_accounts(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    with pytest.raises(sqlite3.IntegrityError), database.transaction():
        database.connection.execute(
            "INSERT INTO objects(object_id, account_id, content_hash,"
            " original_filename, content_length, created_at, updated_at)"
            " VALUES ('orphan-id', 'no-such-account', 'hash', 'f.txt', 1,"
            " 't', 't')"
        )


def test_legacy_database_preserved_readonly_and_never_scanned_on_migration(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "science_companion.db"
    with sqlite3.connect(legacy) as connection:
        connection.execute(
            "CREATE TABLE legacy_accounts (id TEXT PRIMARY KEY, email TEXT)"
        )
        connection.execute(
            "INSERT INTO legacy_accounts VALUES ('old-1', 'old@example.com')"
        )
    legacy_bytes = legacy.read_bytes()

    BridgesDatabase(tmp_path / "bridges.db").initialize()

    # 旧数据库只读保留：字节不变，无自动导入路径。
    assert legacy.read_bytes() == legacy_bytes
    with sqlite3.connect(tmp_path / "bridges.db") as connection:
        account_count = int(
            connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        )
    assert account_count == 0  # 首次启动不扫描或吸收旧账户


def test_app_first_startup_creates_versioned_sqlite_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create_app 配置数据库时，首次启动即事务化创建带版本记录的 bridges.db。"""
    from fastapi.testclient import TestClient

    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "app-startup-master-key")
    get_settings.cache_clear()

    app = create_app()
    assert TestClient(app).get("/health").status_code == 200
    assert app.state.bridges_database is not None
    assert app.state.object_repository is not None
    assert (tmp_path / "bridges.db").exists()
    assert _schema_version(tmp_path / "bridges.db") == 3

    # 重启（再次启动）不重复迁移、不报错。
    get_settings.cache_clear()
    second = create_app()
    assert second.state.persistence_error is None
    assert second.state.bridges_database is not None
