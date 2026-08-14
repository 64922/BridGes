"""Issue 06：API 启动契约——数据库 schema ready 后才报告可服务。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.storage import SCHEMA_VERSION, BridgesDatabase, StorageError
from bridges.storage.database import MIGRATIONS


def _fresh_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "database-ready-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    monkeypatch.setenv("BRIDGES_RUN_ID", "test-run-42")
    get_settings.cache_clear()
    return create_app()


def test_create_app_with_fresh_database_reports_schema_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _fresh_app(tmp_path, monkeypatch)
    assert app.state.bridges_database is not None
    assert app.state.bridges_database.schema_version == SCHEMA_VERSION
    assert app.state.bridges_database.schema_ready is True
    assert app.state.database_path_fingerprint is not None

    client = TestClient(app)
    response = client.get("/health/ready")
    assert response.status_code == 200
    health = response.json()
    assert health["ready"] == "pass"
    assert any(
        dep["name"] == "database_schema_ready" and dep["status"] == "pass"
        for dep in health["dependencies"]
    )
    assert health["extensions"]["run_id"] == "test-run-42"
    assert health["extensions"]["database_path_fingerprint"] is not None
    # Issue 06：readiness 暴露 schema 版本（实际/期望）与脱敏对象名清单。
    assert health["extensions"]["database_schema_version"] == SCHEMA_VERSION
    assert health["extensions"]["expected_database_schema_version"] == SCHEMA_VERSION
    assert "conversations" in health["extensions"]["database_schema_tables"]
    assert (
        "learning_project_migration_conversations"
        in health["extensions"]["database_schema_tables"]
    )

    # 非健康检查端点可正常访问（schema 已就绪）。
    assert client.get("/health/live").status_code == 200


def test_create_app_with_corrupted_database_reports_schema_integrity_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    app = _fresh_app(tmp_path, monkeypatch)
    assert app.state.bridges_database is None
    assert app.state.persistence_error is not None
    assert "database_schema_integrity" in app.state.persistence_error

    client = TestClient(app)
    # Issue 06：readiness 未就绪时返回 503（而非 200），Playwright 的 URL
    # 轮询只认状态码，因此绝不会在 schema 未 ready 时开始用户测试。
    response = client.get("/health/ready")
    assert response.status_code == 503
    health = response.json()
    assert health["ready"] == "fail"
    db_dep = next(
        (dep for dep in health["dependencies"] if dep["name"] == "persistence"),
        None,
    )
    assert db_dep is not None
    assert db_dep["status"] == "fail"
    assert "database_schema_integrity" in db_dep["message"]

    # 非健康检查端点被 503 拒绝。
    me_response = client.get("/me")
    assert me_response.status_code == 503


def test_create_app_without_database_url_still_reports_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未配置数据库时保持原有内存模式，ready 不因此失败。"""
    monkeypatch.delenv("BRIDGES_DATABASE_URL", raising=False)
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "memory-mode-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()

    app = create_app()
    assert app.state.bridges_database is None
    client = TestClient(app)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["ready"] == "pass"
