"""Issue 02 会话局部学习进展的迁移与生命周期合同。"""

from __future__ import annotations

import json
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
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase
from bridges.study.local_progress import StudyLocalProgressRepository


def test_study_local_progress_migration_export_delete_and_recovery(
    tmp_path: Path, monkeypatch: Any
) -> None:
    assert SCHEMA_VERSION == 70
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue02-local-progress-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()

    try:
        with TestClient(app) as owner_client:
            owner = _register(owner_client, "progress-owner")
            first = owner_client.post(
                "/chat/first-turn",
                json={
                    "mode": "study",
                    "content": "函数是什么？",
                    "idempotency_key": "issue02-progress-owner",
                },
            )
            assert first.status_code == 201, first.text
            conversation_id = first.json()["conversation"]["conversation_id"]
            database = app.state.bridges_database
            with database.transaction():
                database.scoped(owner["id"]).execute(
                    "INSERT INTO study_local_progress"
                    " (account_id, conversation_id, topic, knowledge_context, current_focus,"
                    " source_message_ids_json, version, runtime_run_id, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        owner["id"],
                        conversation_id,
                        "函数",
                        "函数把输入映射到输出。",
                        "函数是什么？",
                        json.dumps(["user-1", "assistant-1"]),
                        1,
                        first.json()["run_id"],
                        "2026-10-13T00:00:00+00:00",
                    ),
                )

            assert StudyLocalProgressRepository(database).get(
                owner["id"], conversation_id
            ) is not None
            assert "study_local_progress" in ACCOUNT_TABLES
            category = next(
                item for item in EXPORT_CATEGORIES if item.key == "study_local_progress"
            )
            assert category.tables == ("study_local_progress",)
            assert len(export_rows(database, owner["id"], "study_local_progress")) == 1
            assert logical_summary(database, owner["id"])["study_local_progress"] == 1
            assert {
                item.key: count
                for item, count, _ in category_preview(database, owner["id"])
            }["study_local_progress"] == 1

            with TestClient(app) as other_client:
                other = _register(other_client, "progress-other")
                assert StudyLocalProgressRepository(database).get(
                    other["id"], conversation_id
                ) is None
                assert export_rows(database, other["id"], "study_local_progress") == []

            backup_path = tmp_path / "bridges-backup.db"
            with database.snapshot_lock():
                database.snapshot_to(backup_path)
            restored = BridgesDatabase(backup_path)
            assert restored.initialize() == SCHEMA_VERSION
            assert logical_summary(restored, owner["id"])["study_local_progress"] == 1
            restored.close()

            delete_account_rows(database, owner["id"])
            assert export_rows(database, owner["id"], "study_local_progress") == []
            assert export_rows(database, other["id"], "study_local_progress") == []
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"{tag}_user",
            "qq_email": f"87654321{sum(bytearray(tag.encode('utf-8'))) % 10**8:08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]
