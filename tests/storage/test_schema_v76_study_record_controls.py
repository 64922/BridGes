"""Issue 06 档案纠正/删除的迁移与生命周期合同。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.lifecycle.catalog import (
    ACCOUNT_TABLES,
    EXPORT_CATEGORIES,
    category_preview,
    delete_account_rows,
    export_rows,
    logical_summary,
)
from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase
from bridges.study.diagnosis_repository import StudyDiagnosisRepository
from bridges.study.record_controls import StudyRecordControlService
from tests.storage.test_schema_v74_study_diagnosis import _candidate, _result

_NOW = datetime(2026, 10, 14, tzinfo=UTC)


def test_record_controls_migration_export_delete_and_recovery(
    tmp_path: Path, monkeypatch: Any
) -> None:
    assert SCHEMA_VERSION >= 76
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue06-record-controls-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()

    try:
        with TestClient(app) as owner_client:
            owner = _register(owner_client, "record-controls-owner")
            first = owner_client.post(
                "/chat/first-turn",
                json={
                    "mode": "study",
                    "content": "互斥是不是独立？",
                    "idempotency_key": "issue06-record-controls-owner",
                },
            )
            assert first.status_code == 201, first.text
            conversation_id = first.json()["conversation"]["conversation_id"]
            database = app.state.bridges_database
            repository = StudyDiagnosisRepository(database)
            with database.transaction():
                task_id = repository.create_task_in_transaction(
                    _candidate(owner["id"], conversation_id), now=_NOW
                )
            generation = repository.mark_running(owner["id"], task_id)
            assert generation == 1
            task = repository.get_task(owner["id"], task_id)
            assert task is not None
            assert repository.commit_result(
                task, generation, _result(), evidence_at=_NOW
            ) is True
            record_id = str(
                database.connection.execute(
                    "SELECT record_id FROM study_learning_records"
                ).fetchone()["record_id"]
            )

            # 用户纠正与删除是账户级真实持久数据，事件保留主动来源与版本。
            controls = StudyRecordControlService(database)
            corrected = controls.correct_record(
                owner["id"], conversation_id, record_id, "我当时理解的是别的。", 1
            )
            assert corrected.state.value == "corrected"
            assert corrected.record_version == 2
            controls.delete_record(owner["id"], conversation_id, record_id, 2)
            row = database.connection.execute(
                "SELECT state, record_version FROM study_learning_records"
            ).fetchone()
            assert row["state"] == "deleted"
            assert row["record_version"] == 3

            # 生命周期登记：删除顺序、导出类别与逻辑摘要都覆盖新表。
            assert "study_record_controls" in ACCOUNT_TABLES
            assert ACCOUNT_TABLES.index("study_record_controls") < ACCOUNT_TABLES.index(
                "study_learning_records"
            )
            category = next(
                item
                for item in EXPORT_CATEGORIES
                if item.key == "study_record_controls"
            )
            assert category.tables == ("study_record_controls",)
            events = export_rows(database, owner["id"], "study_record_controls")
            assert len(events) == 2
            assert {event["kind"] for event in events} == {"correction", "deletion"}
            correction = next(
                event for event in events if event["kind"] == "correction"
            )
            assert correction["user_note"] == "我当时理解的是别的。"
            assert correction["superseded_record_version"] == 1
            assert correction["record_version"] == 2
            assert (
                len(export_rows(database, owner["id"], "study_learning_records")) == 1
            )
            summary = logical_summary(database, owner["id"])
            assert summary["study_record_controls"] == 2
            assert summary["study_learning_records"] == 1
            preview = {
                item.key: count
                for item, count, _ in category_preview(database, owner["id"])
            }
            assert preview["study_record_controls"] == 2
            assert preview["study_diagnosis"] == 3

            # 删除的记录不再进入用户可见投影与有效读取边界。
            assert controls.record_projections(owner["id"], conversation_id) == []
            boundary = controls.record_boundary(owner["id"], record_id)
            assert boundary is not None
            assert boundary.effective is False
            assert boundary.reason == "user_deleted"
            assert boundary.record_version == 3

            # 账户隔离：他人看不到任何管理事件或档案。
            with TestClient(app) as other_client:
                other = _register(other_client, "record-controls-other")
                assert export_rows(database, other["id"], "study_record_controls") == []
                assert export_rows(database, other["id"], "study_learning_records") == []
                assert controls.record_boundary(other["id"], record_id) is None

            # 快照恢复保留纠正/删除事件与记录状态。
            backup_path = tmp_path / "bridges-backup.db"
            with database.snapshot_lock():
                database.snapshot_to(backup_path)
            restored = BridgesDatabase(backup_path)
            assert restored.initialize() == SCHEMA_VERSION
            restored_summary = logical_summary(restored, owner["id"])
            assert restored_summary["study_record_controls"] == 2
            assert restored_summary["study_learning_records"] == 1
            restored_row = restored.connection.execute(
                "SELECT state FROM study_learning_records WHERE account_id = ?",
                (owner["id"],),
            ).fetchone()
            assert restored_row["state"] == "deleted"
            restored.close()

            delete_account_rows(database, owner["id"])
            assert export_rows(database, owner["id"], "study_record_controls") == []
            assert export_rows(database, owner["id"], "study_learning_records") == []
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_upgrade_from_v75_adds_record_control_columns_and_table(
    tmp_path: Path,
) -> None:
    path = tmp_path / "upgrade-75.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 76):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '75')")
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION

    row = database.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='study_record_controls'"
    ).fetchone()
    assert row is not None
    columns = {
        str(item["name"])
        for item in database.connection.execute(
            "PRAGMA table_info(study_learning_records)"
        ).fetchall()
    }
    assert {
        "state",
        "user_note",
        "corrected_at",
        "deleted_at",
        "retracted_evidence_ids_json",
    } <= columns
    control_columns = {
        str(item["name"])
        for item in database.connection.execute(
            "PRAGMA table_info(study_record_controls)"
        ).fetchall()
    }
    assert {
        "control_id",
        "account_id",
        "record_id",
        "kind",
        "user_note",
        "superseded_verdict",
        "superseded_performance",
        "superseded_record_version",
        "record_version",
        "created_at",
    } <= control_columns
    # 升级幂等：重复初始化不重复执行迁移。
    assert database.initialize() == SCHEMA_VERSION
    database.close()


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"{tag}_user",
            "qq_email": f"7654321{sum(bytearray(tag.encode('utf-8'))) % 10**8:08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]
