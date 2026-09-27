"""工单 01：旧库画像结构恢复的数据库级回归。

运行库实测（2026-09-27 只读核查）：``schema_meta.version=58`` 与程序上一版
目标版本一致，但 ``profile_items`` / ``profile_item_migrations`` 及其索引都不
存在——历史编号冲突让 51 号迁移不可达，而启动完整性清单当时不覆盖画像结构，
于是进程自认健康、画像接口持续 500。

本文件用「最新版本标记但缺画像对象」的夹具复现该状态，并验证：
迁移能补齐缺失对象、既有合法对象不被清空、同名不兼容对象明确失败、
修复前先落可恢复备份、迁移失败整体回滚、正常旧库/最新库/空库各走对应路径。
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from bridges.profiles.atomic import SqliteAtomicProfileRepository
from bridges.storage import SCHEMA_VERSION, BridgesDatabase, StorageError
from bridges.storage.database import (
    MIGRATIONS,
    REQUIRED_INDEXES,
    REQUIRED_TABLES,
    SCHEMA_INTEGRITY_ERROR_CODE,
    SCHEMA_OBJECT_INCOMPATIBLE_ERROR_CODE,
)

#: 漂移发生的版本：运行库标记到 58 时画像对象已缺失。
DRIFT_VERSION = 58

#: 修复迁移新增的逐条对账台账。
RECONCILIATION_TABLE = "profile_item_migration_records"

PROFILE_TABLES = ("profile_items", "profile_item_migrations", RECONCILIATION_TABLE)
PROFILE_INDEXES = ("idx_profile_items_account_status", "idx_profile_item_migrations_account")

#: 夹具里的业务行（迁移前后逐表对账基准）。
BUSINESS_TABLES = ("accounts", "conversations", "messages", "objects")

_ITEM_COLUMNS = (
    "profile_item_id, account_id, text, identity_key, status, write_origin, "
    "confidence, version, created_at, updated_at"
)


def _replay(path: Path, *, version: int, business_rows: bool = True) -> None:
    """按迁移脚本重放出一个真实旧库（可选带业务行）。"""

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
        if business_rows:
            connection.execute(
                "INSERT INTO accounts(account_id, email, created_at) VALUES (?, ?, ?)",
                ("acc-1", "acc1@qq.com", "2026-01-01T00:00:00+00:00"),
            )
            connection.execute(
                "INSERT INTO conversations(conversation_id, account_id, title, mode, pinned,"
                " created_at, updated_at) VALUES ('conv-1', 'acc-1', '旧对话', 'companion', 0,"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO messages(message_id, conversation_id, account_id, role,"
                " attempt_number, status, content, created_at, updated_at)"
                " VALUES ('msg-1', 'conv-1', 'acc-1', 'user', 1, 'done', '旧消息',"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO objects(object_id, account_id, content_hash, original_filename,"
                " content_length, status, media_type, created_at, updated_at)"
                " VALUES ('obj-1', 'acc-1', 'hash-1', '旧文件.txt', 12, 'ready', 'text/plain',"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO profile_four_dimension_records(record_id, account_id, dimension,"
                " label, content, first_stable_recorded_at, updated_at, version, status,"
                " source_record_id, source_version, content_hash, write_origin,"
                " migration_version) VALUES ('fdr-1', 'acc-1', 'knowledge_interest',"
                " '兴趣', '喜欢看科普', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00',"
                " 1, 'active', 'src-1', 1, 'hash-fdr-1', 'automatic', 'v1')"
            )
        connection.commit()
    finally:
        connection.close()


def _build_drifted_database(path: Path) -> None:
    """「标记为最新版本但缺画像对象」的夹具：复刻运行库的结构漂移。"""

    _replay(path, version=DRIFT_VERSION)
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE IF EXISTS profile_items")
        connection.execute("DROP TABLE IF EXISTS profile_item_migrations")
        connection.commit()
    finally:
        connection.close()


def _object_names(path: Path, kind: str) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?", (kind,)
            )
        }
    finally:
        connection.close()


def _columns(path: Path, table: str) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
    finally:
        connection.close()


def _index_sql(path: Path, index: str) -> str | None:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (index,)
        ).fetchone()
    finally:
        connection.close()
    return None if row is None else str(row[0])


def _counts(path: Path, tables: tuple[str, ...]) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
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


def test_drifted_database_fails_real_read_path_then_repairs_on_startup(
    tmp_path: Path,
) -> None:
    """验收 1：缺表夹具修复前真实读取路径失败，修复后同一路径可读。"""

    path = tmp_path / "bridges.db"
    _build_drifted_database(path)
    before = _counts(path, BUSINESS_TABLES)
    assert _stored_version(path) == DRIFT_VERSION
    assert set(PROFILE_TABLES) - _object_names(path, "table") == set(PROFILE_TABLES)

    # 修复前：真实仓库读取路径（页面 /profiles/items 走的就是它）直接炸在缺表。
    uninitialized = BridgesDatabase(path)
    repository = SqliteAtomicProfileRepository(uninitialized, initialize=False)
    with pytest.raises(sqlite3.OperationalError) as excinfo:
        repository.list_items("acc-1")
    assert "no such table: profile_items" in str(excinfo.value)

    # 启动修复：迁移补齐缺失对象，业务数据与旧四维记录一字不动。
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert set(PROFILE_TABLES) <= _object_names(path, "table")
    assert set(PROFILE_INDEXES) <= _object_names(path, "index")
    assert _counts(path, BUSINESS_TABLES) == before
    assert _counts(path, ("profile_four_dimension_records",)) == {
        "profile_four_dimension_records": 1
    }

    # 修复后：同一真实读取路径返回空列表（空画像），不再是 500。
    repaired = SqliteAtomicProfileRepository(BridgesDatabase(path))
    assert repaired.list_items("acc-1") == []
    assert repaired.get_latest_migration_report("acc-1") is None


def test_repair_keeps_existing_valid_profile_rows(tmp_path: Path) -> None:
    """验收 3：补齐只针对缺失对象，已有合法对象与行不得被清空。"""

    path = tmp_path / "bridges.db"
    _replay(path, version=DRIFT_VERSION)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            f"INSERT INTO profile_items({_ITEM_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "item-existing",
                "acc-1",
                "已有条目",
                "identity-existing",
                "active",
                "user",
                "high",
                3,
                "2026-01-01T00:00:00+00:00",
                "2026-01-02T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO profile_item_migrations(run_id, account_id, migration_version,"
            " status, migrated, reconciliation_digest, retryable, created_at)"
            " VALUES ('atomic-existing', 'acc-1', 'profile-atomic-v1', 'completed', 1,"
            " 'digest-existing', 0, '2026-01-01T00:00:00+00:00')"
        )
        connection.commit()
    finally:
        connection.close()
    # 只丢掉台账表，模拟「部分漂移」：已有画像条目与报告必须留下。
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE IF EXISTS profile_item_migration_records")
        connection.commit()
    finally:
        connection.close()

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION

    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT text, version, write_origin FROM profile_items "
            "WHERE profile_item_id = 'item-existing'"
        ).fetchone()
        assert row == ("已有条目", 3, "user")
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM profile_item_migrations WHERE run_id = 'atomic-existing'"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_repaired_structure_matches_normal_upgrade_path(tmp_path: Path) -> None:
    """修复路径与正常 51 号迁移路径必须收敛到同一结构。"""

    normal = tmp_path / "normal.db"
    _replay(normal, version=DRIFT_VERSION)
    assert BridgesDatabase(normal).initialize() == SCHEMA_VERSION

    drifted = tmp_path / "drifted.db"
    _build_drifted_database(drifted)
    assert BridgesDatabase(drifted).initialize() == SCHEMA_VERSION

    for table in PROFILE_TABLES:
        assert _columns(normal, table) == _columns(drifted, table)
    for index in PROFILE_INDEXES:
        assert _index_sql(normal, index) == _index_sql(drifted, index)


def test_empty_and_latest_databases_create_profile_structures(tmp_path: Path) -> None:
    """验收 2：空库与已是最新的库都不缺画像对象，重复启动不重复创建。"""

    empty = tmp_path / "empty.db"
    first = BridgesDatabase(empty)
    assert first.initialize() == SCHEMA_VERSION
    assert first.migration_backup_path is None
    assert _object_names(empty, "table") >= REQUIRED_TABLES
    assert _object_names(empty, "index") >= REQUIRED_INDEXES

    again = BridgesDatabase(empty)
    assert again.initialize() == SCHEMA_VERSION
    assert _counts(empty, PROFILE_TABLES) == dict.fromkeys(PROFILE_TABLES, 0)


def test_older_database_upgrades_through_the_normal_path(tmp_path: Path) -> None:
    """正常旧库（低于 51）走完整迁移链，画像对象由 51 号迁移建立。"""

    path = tmp_path / "bridges.db"
    _replay(path, version=50)
    before = _counts(path, BUSINESS_TABLES)
    assert "profile_items" not in _object_names(path, "table")

    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert set(PROFILE_TABLES) <= _object_names(path, "table")
    assert set(PROFILE_INDEXES) <= _object_names(path, "index")
    assert _counts(path, BUSINESS_TABLES) == before
    assert database.migration_backup_path is not None
    assert _stored_version(database.migration_backup_path) == 50


def test_incompatible_profile_object_blocks_startup_with_stable_code(
    tmp_path: Path,
) -> None:
    """验收 3：同名对象结构不兼容时必须明确失败，不能靠存在性误报正常。"""

    path = tmp_path / "bridges.db"
    _replay(path, version=DRIFT_VERSION)
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE profile_items")
        connection.execute("CREATE TABLE profile_items (junk TEXT)")
        connection.commit()
    finally:
        connection.close()

    database = BridgesDatabase(path)
    with pytest.raises(StorageError) as excinfo:
        database.initialize()
    message = str(excinfo.value)
    assert SCHEMA_OBJECT_INCOMPATIBLE_ERROR_CODE in message
    assert "profile_items" in message
    assert "identity_key" in message
    # 脱敏：诊断只含对象名与列名，不带路径、行数据或画像正文。
    assert str(path) not in message
    assert database.schema_ready is False

    # 版本已到当前值时没有迁移路径，同样必须失败关闭而不是「先报健康」。
    healthy = tmp_path / "healthy.db"
    BridgesDatabase(healthy).initialize()
    connection = sqlite3.connect(healthy)
    try:
        connection.execute("DROP TABLE profile_item_migrations")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(StorageError) as missing:
        BridgesDatabase(healthy).initialize()
    assert SCHEMA_INTEGRITY_ERROR_CODE in str(missing.value)
    assert "profile_item_migrations" in str(missing.value)


def test_repair_failure_rolls_back_and_backup_stays_restorable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收 6：注入迁移失败时事务回滚，迁移前备份可恢复且业务数据不丢。"""

    path = tmp_path / "bridges.db"
    _build_drifted_database(path)
    before = _counts(path, BUSINESS_TABLES)
    monkeypatch.setitem(
        MIGRATIONS, SCHEMA_VERSION, [*MIGRATIONS[SCHEMA_VERSION], "THIS IS NOT VALID SQL"]
    )

    database = BridgesDatabase(path)
    with pytest.raises(StorageError):
        database.initialize()

    # 迁移脚本与版本号同属一个事务：失败即整体回滚，不留半迁移状态。
    assert _stored_version(path) == DRIFT_VERSION
    assert "profile_items" not in _object_names(path, "table")
    assert RECONCILIATION_TABLE not in _object_names(path, "table")
    assert _counts(path, BUSINESS_TABLES) == before

    backup = database.migration_backup_path
    assert backup is not None and backup.exists()
    assert _stored_version(backup) == DRIFT_VERSION
    assert _counts(backup, BUSINESS_TABLES) == before
    assert "profile_items" not in _object_names(backup, "table")

    # 备份可恢复：放回正式路径后仍能继续升级并读到原业务数据。
    restored = tmp_path / "restored.db"
    shutil.copy2(backup, restored)
    assert _stored_version(restored) == DRIFT_VERSION
    restored_connection = sqlite3.connect(restored)
    try:
        assert (
            restored_connection.execute(
                "SELECT content FROM profile_four_dimension_records WHERE record_id = 'fdr-1'"
            ).fetchone()[0]
            == "喜欢看科普"
        )
    finally:
        restored_connection.close()
    monkeypatch.undo()
    assert BridgesDatabase(restored).initialize() == SCHEMA_VERSION
    assert _counts(restored, BUSINESS_TABLES) == before
    assert "profile_items" in _object_names(restored, "table")
