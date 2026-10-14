"""Issue 09 学情采用记录的迁移与生命周期合同。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.study_adaptation import (
    StudyAdaptationAdjustment,
    StudyAdaptationSnapshot,
    StudyAdoptedRecord,
)
from bridges.contracts.study_diagnosis import StudyDiagnosisVerdict
from bridges.lifecycle.catalog import (
    ACCOUNT_TABLES,
    EXPORT_CATEGORIES,
    category_preview,
    delete_account_rows,
    export_rows,
    logical_summary,
)
from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase
from bridges.study.adaptation import StudyAdaptationService

_NOW = datetime(2026, 10, 14, tzinfo=UTC)


def _snapshot(conversation_id: str) -> StudyAdaptationSnapshot:
    return StudyAdaptationSnapshot(
        adoption_id="sa-issue09-lifecycle",
        conversation_id=conversation_id,
        goal_id="",
        goal_version=0,
        route_version=0,
        entries=[
            StudyAdoptedRecord(
                record_id="lr-issue09",
                record_version=2,
                subject="概率论",
                title="互斥与独立",
                usage_context="理解事件关系",
                verdict=StudyDiagnosisVerdict.MISCONCEPTION,
                performance="把互斥理解为互不影响。",
                adjustments=[
                    StudyAdaptationAdjustment.PREREQUISITE_REVIEW,
                    StudyAdaptationAdjustment.HINT_GRANULARITY,
                ],
                reason="已核验存在具体误解：先检查或补讲这个知识点。",
            )
        ],
        uncertainty_note="",
        created_at=_NOW,
    )


def test_record_adoptions_migration_export_delete_and_recovery(
    tmp_path: Path, monkeypatch: Any
) -> None:
    assert SCHEMA_VERSION >= 77
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue09-adoptions-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()

    try:
        with TestClient(app) as owner_client:
            owner = _register(owner_client, "adoptions-owner")
            first = owner_client.post(
                "/chat/first-turn",
                json={
                    "mode": "study",
                    "content": "互斥是不是独立？",
                    "idempotency_key": "issue09-adoptions-owner",
                },
            )
            assert first.status_code == 201, first.text
            conversation_id = first.json()["conversation"]["conversation_id"]
            database = app.state.bridges_database
            service = StudyAdaptationService(database)
            snapshot = _snapshot(conversation_id)
            with database.transaction():
                service.commit_in_transaction(
                    owner["id"], "run-issue09", "assistant-issue09", snapshot
                )
                # 幂等：同一采用标识重复提交不产生第二行（重试沿用同一快照）。
                service.commit_in_transaction(
                    owner["id"], "run-issue09", "assistant-issue09", snapshot
                )
            assert service.projections(owner["id"], conversation_id)[0].adoption_id == (
                "sa-issue09-lifecycle"
            )
            assert (
                database.connection.execute(
                    "SELECT COUNT(*) AS count FROM study_record_adoptions"
                ).fetchone()["count"]
                == 1
            )

            # 生命周期登记：删除顺序、导出类别与逻辑摘要都覆盖新表。
            assert "study_record_adoptions" in ACCOUNT_TABLES
            assert ACCOUNT_TABLES.index("study_record_adoptions") < ACCOUNT_TABLES.index(
                "study_learning_records"
            )
            category = next(
                item
                for item in EXPORT_CATEGORIES
                if item.key == "study_record_adoptions"
            )
            assert category.tables == ("study_record_adoptions",)
            events = export_rows(database, owner["id"], "study_record_adoptions")
            assert len(events) == 1
            assert events[0]["status"] == "applied"
            assert events[0]["entries_json"]
            summary = logical_summary(database, owner["id"])
            assert summary["study_record_adoptions"] == 1
            preview = {
                item.key: count
                for item, count, _ in category_preview(database, owner["id"])
            }
            assert preview["study_record_adoptions"] == 1

            # 账户隔离：他人看不到采用记录。
            with TestClient(app) as other_client:
                other = _register(other_client, "adoptions-other")
                assert service.projections(other["id"], conversation_id) == []
                assert export_rows(database, other["id"], "study_record_adoptions") == []

            # 快照恢复保留采用记录。
            backup_path = tmp_path / "bridges-backup.db"
            with database.snapshot_lock():
                database.snapshot_to(backup_path)
            restored = BridgesDatabase(backup_path)
            assert restored.initialize() == SCHEMA_VERSION
            assert logical_summary(restored, owner["id"])["study_record_adoptions"] == 1
            restored.close()

            delete_account_rows(database, owner["id"])
            assert export_rows(database, owner["id"], "study_record_adoptions") == []
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_upgrade_from_v76_adds_record_adoptions_table(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-76.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 77):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '76')")
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION

    row = database.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name='study_record_adoptions'"
    ).fetchone()
    assert row is not None
    columns = {
        str(item["name"])
        for item in database.connection.execute(
            "PRAGMA table_info(study_record_adoptions)"
        ).fetchall()
    }
    assert {
        "adoption_id",
        "account_id",
        "conversation_id",
        "run_id",
        "assistant_message_id",
        "goal_id",
        "goal_version",
        "route_version",
        "status",
        "invalid_reason",
        "entries_json",
        "uncertainty_note",
        "contract_version",
        "created_at",
        "updated_at",
    } <= columns
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
