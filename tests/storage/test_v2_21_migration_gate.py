"""Issue 21 迁移门：版本化数据库迁移的备份、对账、失败恢复与回滚验证。

设计文档把「每个数据库变更有备份、前后数量对账、旧数据只读渲染和失败
恢复」列为硬门（``docs/v2/delivery-plan.md`` 第 2 节）。本文件在真实文件
数据库上验证这四条：升级前自动落备份、备份与升级后逐表条数对账、迁移
中途失败整体回滚且版本与数据不变、以及用迁移前备份回到可读旧数据。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bridges.storage import SCHEMA_VERSION, BridgesDatabase, StorageError
from bridges.storage.database import MIGRATIONS

#: 迁移前的旧版本（承载 Issue 15 之前的全部业务表）。
LEGACY_VERSION = SCHEMA_VERSION - 1

#: 旧库里预置的业务数据（对账基准）。
LEGACY_TABLES = ("accounts", "conversations", "messages", "objects")


def _build_legacy_database(path: Path, *, version: int = LEGACY_VERSION) -> None:
    """在 ``path`` 建一个旧版本、含真实业务行的数据库。"""
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        for target in range(1, version + 1):
            for statement in MIGRATIONS[target]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', ?)", (str(version),)
        )
        for index in (1, 2):
            connection.execute(
                "INSERT INTO accounts(account_id, email, created_at) VALUES (?, ?, ?)",
                (f"acc-{index}", f"acc{index}@qq.com", "2026-01-01T00:00:00+00:00"),
            )
        connection.execute(
            "INSERT INTO conversations(conversation_id, account_id, title, mode, pinned,"
            " created_at, updated_at) VALUES ('conv-1', 'acc-1', '旧对话', 'companion', 0,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO messages(message_id, conversation_id, account_id, role,"
            " attempt_number, status, content, created_at, updated_at)"
            " VALUES ('msg-1', 'conv-1', 'acc-1', 'assistant', 1, 'done', '旧回答',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO objects(object_id, account_id, content_hash, original_filename,"
            " content_length, status, media_type, created_at, updated_at)"
            " VALUES ('obj-1', 'acc-1', 'hash-1', '旧文件.txt', 12, 'ready', 'text/plain',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        connection.commit()
    finally:
        connection.close()


def _counts(path: Path, tables: tuple[str, ...]) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }
    finally:
        connection.close()


def _table_names(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()


def _stored_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return int(row[0])


def test_upgrade_writes_pre_migration_backup_and_reconciles_counts(tmp_path: Path) -> None:
    path = tmp_path / "bridges.db"
    _build_legacy_database(path)
    before = _counts(path, LEGACY_TABLES)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    backup = database.migration_backup_path
    assert backup is not None and backup.exists()
    assert backup.name.startswith("bridges.db.backup-before-v")
    # 备份是升级前的快照：版本与业务条数都停在旧版本。
    assert _stored_version(backup) == LEGACY_VERSION
    assert _counts(backup, LEGACY_TABLES) == before
    # 前后数量对账：旧表条数不变，且没有表在升级中消失。
    assert _counts(path, LEGACY_TABLES) == before
    assert _table_names(backup) <= _table_names(path)


def test_first_and_repeated_startup_write_no_migration_backup(tmp_path: Path) -> None:
    """首次建库与已是最新版本的重启都不产生迁移前备份文件。"""
    path = tmp_path / "bridges.db"
    first = BridgesDatabase(path)
    assert first.initialize() == SCHEMA_VERSION
    assert first.migration_backup_path is None
    second = BridgesDatabase(path)
    assert second.initialize() == SCHEMA_VERSION
    assert second.migration_backup_path is None
    assert sorted(item.name for item in tmp_path.glob("*.backup-before-v*")) == []


def test_upgrade_refuses_to_migrate_when_backup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bridges.db"
    _build_legacy_database(path)
    before = _counts(path, LEGACY_TABLES)

    def _failing_snapshot(self: BridgesDatabase, target: object) -> None:
        raise StorageError("模拟备份写入失败")

    monkeypatch.setattr(BridgesDatabase, "snapshot_to", _failing_snapshot)
    database = BridgesDatabase(path)
    with pytest.raises(StorageError) as excinfo:
        database.initialize()
    assert "备份" in str(excinfo.value)
    # 无备份即不迁移：版本与数据保持旧值，数据库仍可读。
    assert _stored_version(path) == LEGACY_VERSION
    assert _counts(path, LEGACY_TABLES) == before


def test_failed_migration_rolls_back_to_previous_version_and_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bridges.db"
    _build_legacy_database(path)
    before = _counts(path, LEGACY_TABLES)
    monkeypatch.setitem(
        MIGRATIONS,
        SCHEMA_VERSION,
        ["CREATE TABLE migration_probe (id TEXT)", "THIS IS NOT VALID SQL"],
    )

    database = BridgesDatabase(path)
    with pytest.raises(StorageError):
        database.initialize()
    # 迁移脚本与版本号同属一个事务：中途失败整体回滚，不留半迁移状态。
    assert _stored_version(path) == LEGACY_VERSION
    assert "migration_probe" not in _table_names(path)
    assert _counts(path, LEGACY_TABLES) == before
    # 失败前已落下还原点，可用于恢复。
    assert database.migration_backup_path is not None
    assert database.migration_backup_path.exists()


def test_rollback_to_pre_migration_backup_restores_readable_old_data(
    tmp_path: Path,
) -> None:
    """回滚验证：把迁移前备份放回正式路径，旧数据与新库都能继续读。"""
    path = tmp_path / "bridges.db"
    _build_legacy_database(path)
    before = _counts(path, LEGACY_TABLES)

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    backup = database.migration_backup_path
    assert backup is not None
    database.close()

    # 回滚：恢复到可读旧数据（不承诺回滚 schema，但旧数据必须可读）。
    backup.replace(path)
    rolled_back = BridgesDatabase(path)
    assert _stored_version(path) == LEGACY_VERSION
    assert _counts(path, LEGACY_TABLES) == before
    # 旧数据只读可渲染：消息内容原样保留。
    row = rolled_back.connection.execute(
        "SELECT content FROM messages WHERE message_id = 'msg-1'"
    ).fetchone()
    assert row is not None and str(row["content"]) == "旧回答"
    # 回滚后的旧库再启动仍会升级，并重新落下备份。
    assert rolled_back.initialize() == SCHEMA_VERSION
    assert rolled_back.migration_backup_path is not None
    assert _counts(path, LEGACY_TABLES) == before
