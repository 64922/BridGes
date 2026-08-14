"""Issue 06：worker 启动契约——构造仓库前必须完成数据库初始化与完整性校验。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bridges.config import get_settings
from bridges.runtime.executor import BackgroundExecutor
from bridges.storage import SCHEMA_VERSION
from bridges.storage.database import MIGRATIONS


def _fresh_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BackgroundExecutor:
    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "executor-database-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return BackgroundExecutor(get_settings())


def test_ensure_database_initializes_schema_before_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = _fresh_executor(tmp_path, monkeypatch)
    assert executor.database_ready is False

    database = executor.ensure_database()
    assert database is not None
    assert database.schema_version == SCHEMA_VERSION
    assert database.schema_ready is True
    assert executor.database_ready is True

    # 仓库只有在数据库就绪后才可构造；重复 ensure 幂等返回同一连接。
    repository = executor._ensure_repository()
    assert repository is not None
    assert executor.ensure_database() is database

    # 空数据库迁移后核心契约表真实存在（根因表不再缺失）。
    with sqlite3.connect(tmp_path / "bridges.db") as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "learning_project_migration_conversations" in tables
    database.close()


def test_ensure_database_fails_closed_on_corrupted_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """metadata 声称当前版本但核心表缺失：worker 失败关闭，不构造仓库。"""
    path = tmp_path / "bridges.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        for version in range(1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute("DROP TABLE learning_project_migration_conversations")
        connection.execute(
            f"INSERT INTO schema_meta(key, value) VALUES ('version', '{SCHEMA_VERSION}')"
        )
        connection.commit()

    executor = _fresh_executor(tmp_path, monkeypatch)
    assert executor.ensure_database() is None
    assert executor.database_ready is False
    assert executor._ensure_repository() is None
    assert executor._idle_reason is not None
    assert "database_schema_integrity" in executor._idle_reason


def test_ensure_database_idles_without_database_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BRIDGES_DATABASE_URL", raising=False)
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()

    executor = BackgroundExecutor(get_settings())
    assert executor.ensure_database() is None
    assert executor.database_ready is False
    assert executor._idle_reason is not None
    assert "未配置 BRIDGES_DATABASE_URL" in executor._idle_reason
