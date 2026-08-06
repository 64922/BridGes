"""Issue 05：bridges.db 版本化事务迁移、幂等启动与旧数据库只读保留。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bridges.storage import (
    SCHEMA_VERSION,
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

    assert version == SCHEMA_VERSION
    assert (tmp_path / "bridges.db").exists()
    assert _schema_version(tmp_path / "bridges.db") == SCHEMA_VERSION
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
    assert second.initialize() == SCHEMA_VERSION
    assert second.initialize() == SCHEMA_VERSION  # 第三次启动同样幂等
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
    assert _schema_version(tmp_path / "bridges.db") == SCHEMA_VERSION

    # 重启（再次启动）不重复迁移、不报错。
    get_settings.cache_clear()
    second = create_app()
    assert second.state.persistence_error is None
    assert second.state.bridges_database is not None


# ---------------------------------------------------------------------------
# 候选 4：账户作用域查询面（ScopedConnection）——隔离从约定升级为执行层强制
# ---------------------------------------------------------------------------


def test_scoped_connection_rejects_select_without_account_filter(
    tmp_path: Path,
) -> None:
    """SELECT 不带 WHERE account_id 过滤时，作用域查询直接拒绝执行。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()

    with pytest.raises(StorageError):
        database.scoped("acc-a").execute(
            "SELECT object_id FROM objects WHERE status = 'active'"
        )
    with pytest.raises(StorageError):
        database.scoped("acc-a").execute("SELECT * FROM conversations")


def test_scoped_connection_rejects_update_and_delete_without_account_where(
    tmp_path: Path,
) -> None:
    """UPDATE/DELETE 的 WHERE 不含 account_id 时拒绝执行（含兜底清理类语句）。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()

    with pytest.raises(StorageError):
        database.scoped("acc-a").execute(
            "DELETE FROM conversations WHERE conversation_id = ?", ("c-1",)
        )
    with pytest.raises(StorageError):
        database.scoped("acc-a").execute(
            "UPDATE objects SET status = 'active' WHERE object_id = ?", ("o-1",)
        )


def test_scoped_connection_rejects_insert_without_account_column(
    tmp_path: Path,
) -> None:
    """INSERT 的列清单缺少 account_id 时拒绝执行，禁止写入无归属记录。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()

    with pytest.raises(StorageError):
        database.scoped("acc-a").execute(
            "INSERT INTO conversations(conversation_id, title)"
            " VALUES (?, ?)",
            ("c-1", "无归属对话"),
        )


def test_scoped_connection_allows_account_bound_sql(
    tmp_path: Path,
) -> None:
    """合规的作用域 SQL 正常执行，并只操作作用域账户的数据。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    database.scoped("acc-a").execute(
        "INSERT INTO conversations(conversation_id, account_id, title,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("c-1", "acc-a", "A 的对话", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    database.scoped("acc-b").execute(
        "INSERT INTO conversations(conversation_id, account_id, title,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("c-2", "acc-b", "B 的对话", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )

    # 作用域 A 查不到 B 的记录
    rows = database.scoped("acc-a").execute(
        "SELECT conversation_id FROM conversations WHERE account_id = ?",
        ("acc-a",),
    ).fetchall()
    assert [str(row["conversation_id"]) for row in rows] == ["c-1"]
    rows = database.scoped("acc-a").execute(
        "SELECT conversation_id FROM conversations"
        " WHERE conversation_id = ? AND account_id = ?",
        ("c-2", "acc-a"),
    ).fetchall()
    assert rows == []


def test_upgrade_from_older_schema_overwrites_version_row(tmp_path: Path) -> None:
    """既有库升级：版本行已存在时迁移成功并覆盖版本号（修复 UNIQUE 冲突）。

    该路径在 Issue 17 前从未被真实触发（既有库升级时最终版本 INSERT 与
    旧版本行冲突导致整体回滚）；回归测试保证后续每次 Schema 递增都安全。
    """
    from bridges.storage.database import MIGRATIONS

    path = tmp_path / "bridges.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '1')"
        )
        # 复刻真实 v1 模式：迁移 2..7 的语句都依赖 objects 表存在。
        for statement in MIGRATIONS[1]:
            connection.execute(statement)
        connection.commit()

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert _schema_version(path) == SCHEMA_VERSION
    with sqlite3.connect(path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    # v7 迁移产物真实存在
    assert "document_records" in tables
    assert "index_versions" in tables
    assert "document_chunks" in tables
    database.close()


def test_upgrade_backfills_legacy_claimable_rows_into_task_claims(
    tmp_path: Path,
) -> None:
    """Issue 43：升级到 v27+ 时，存量可处理业务行回填进 task_claims。

    迁移前已处于 queued/error(<上限)/failed 的业务行（document_records、
    account_deletions 等）在旧实现下由每轮全表扫描重试；新实现只处理
    队列行——升级必须回填，否则存量任务孤儿化永不处理。已耗尽/永久
    失败的行不回填（等子系统手动重试时重新入队）。
    """
    from bridges.storage.database import MIGRATIONS

    path = tmp_path / "bridges.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '26')"
        )
        for version in range(1, 27):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        # 存量业务行：queued（应回填）、error 已耗尽（不回填）、
        # 删除 failed（应回填）。
        connection.execute(
            "INSERT INTO document_records(document_id, account_id, object_id,"
            " conversation_id, content_hash, parser_version, status, retry_count,"
            " claimed_at, lease_expires_at, created_at, updated_at)"
            " VALUES ('doc-queued', 'acc-1', 'obj-1', NULL, 'hash-1', 'text-v1',"
            " 'queued', 0, NULL, NULL, '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
        connection.execute(
            "INSERT INTO document_records(document_id, account_id, object_id,"
            " conversation_id, content_hash, parser_version, status, retry_count,"
            " claimed_at, lease_expires_at, created_at, updated_at)"
            " VALUES ('doc-exhausted', 'acc-1', 'obj-2', NULL, 'hash-2', 'text-v1',"
            " 'error', 3, NULL, NULL, '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
        connection.execute(
            "INSERT INTO account_deletions(deletion_id, account_id, status,"
            " retry_count, last_error, pending_hashes, started_at)"
            " VALUES ('del-1', 'acc-2', 'failed', 1, '模拟失败', '[]',"
            " '2026-01-01T00:00:00')"
        )
        connection.commit()

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT queue_name, task_key FROM task_claims ORDER BY task_key"
        ).fetchall()
    keys = [(str(row[0]), str(row[1])) for row in rows]
    assert ("ingestion", "ingestion:acc-1:obj-1") in keys
    assert ("deletion", "deletion:del-1") in keys
    # 已耗尽的 error 文档不回填（等用户手动重试时重新入队）。
    assert not any(key == "ingestion:acc-1:obj-2" for _, key in keys)
    database.close()
