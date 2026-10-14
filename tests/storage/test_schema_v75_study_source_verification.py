"""Issue 11 学习公开查证记录的迁移与生命周期合同。"""

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
from bridges.storage.database import (
    MIGRATIONS,
    REQUIRED_INDEXES,
    REQUIRED_TABLES,
    SCHEMA_VERSION,
    BridgesDatabase,
)
from bridges.study.demand_verification import StudySourceVerificationRepository

_COLUMNS = (
    "verification_id, account_id, conversation_id, run_id, assistant_message_id,"
    " purpose, status, query_summary, sources_json, adopted_source_ids_json,"
    " has_conflicts, gap_note, can_retry, contract_version, created_at"
)


def test_study_source_verification_migration_export_delete_and_recovery(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Issue 11 引入 v75 结构（与 04/05 并行交付后重编号对齐）；后续版
    # 本在其上追加时本测试放宽为 >= 75（v76 为档案控制迁移、v77 为目标事件迁移）。
    assert SCHEMA_VERSION >= 75
    assert 75 in MIGRATIONS
    assert "study_source_verifications" in REQUIRED_TABLES
    assert "idx_study_source_verifications_conversation" in REQUIRED_INDEXES
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue11-verification-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()

    try:
        with TestClient(app) as owner_client:
            owner = _register(owner_client, "source-verify-owner")
            first = owner_client.post(
                "/chat/first-turn",
                json={
                    "mode": "study",
                    "content": "互斥是什么？",
                    "idempotency_key": "issue11-storage-owner",
                },
            )
            assert first.status_code == 201, first.text
            conversation_id = first.json()["conversation"]["conversation_id"]
            run_id = first.json()["run_id"]
            database = app.state.bridges_database
            created_at = "2026-10-14T00:00:00+00:00"
            with database.transaction():
                database.scoped(owner["id"]).execute(
                    f"INSERT INTO study_source_verifications ({_COLUMNS})"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "verification-1",
                        owner["id"],
                        conversation_id,
                        run_id,
                        "assistant-1",
                        "paper",
                        "success",
                        "注意力机制 综述",
                        json.dumps(
                            [
                                {
                                    "source_id": "web-1",
                                    "title": "注意力机制综述",
                                    "site": "example.org",
                                    "url": "https://example.org/attention",
                                    "read_scope": "full_text",
                                    "adoption": "adopted",
                                    "provider": "tavily",
                                    "provider_version": "tavily-search-api-v1",
                                    "published_at": None,
                                    "accessed_at": created_at,
                                    "fetched_at": created_at,
                                }
                            ],
                            ensure_ascii=False,
                        ),
                        json.dumps(["web-1"]),
                        0,
                        "",
                        1,
                        "study-source-verification-v1",
                        created_at,
                    ),
                )

            repository = StudySourceVerificationRepository(database)
            record = repository.get_for_message(owner["id"], "assistant-1")
            assert record is not None
            assert record.purpose.value == "paper"
            assert record.status.value == "success"
            assert record.sources[0].read_scope.value == "full_text"
            assert record.adopted_source_ids == ["web-1"]
            assert record.status_note
            listed = repository.list_for_conversation(owner["id"], conversation_id)
            assert len(listed) == 1
            assert listed[0].verification_id == "verification-1"

            assert "study_source_verifications" in ACCOUNT_TABLES
            category = next(
                item for item in EXPORT_CATEGORIES if item.key == "study_source_verification"
            )
            assert category.tables == ("study_source_verifications",)
            exported = export_rows(database, owner["id"], "study_source_verifications")
            assert len(exported) == 1
            assert exported[0]["query_summary"] == "注意力机制 综述"
            summary = logical_summary(database, owner["id"])
            assert summary["study_source_verifications"] == 1
            preview = {
                item.key: count for item, count, _ in category_preview(database, owner["id"])
            }
            assert preview["study_source_verification"] == 1

            with TestClient(app) as other_client:
                other = _register(other_client, "source-verify-other")
                assert repository.get_for_message(other["id"], "assistant-1") is None
                assert repository.list_for_conversation(other["id"], conversation_id) == []
                assert export_rows(database, other["id"], "study_source_verifications") == []

            backup_path = tmp_path / "bridges-backup.db"
            with database.snapshot_lock():
                database.snapshot_to(backup_path)
            restored = BridgesDatabase(backup_path)
            assert restored.initialize() == SCHEMA_VERSION
            restored_summary = logical_summary(restored, owner["id"])
            assert restored_summary["study_source_verifications"] == 1
            restored.close()

            delete_account_rows(database, owner["id"])
            assert export_rows(database, owner["id"], "study_source_verifications") == []
            assert (
                database.connection.execute(
                    "SELECT COUNT(*) FROM study_source_verifications"
                ).fetchone()[0]
                == 0
            )
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"{tag}_user",
            "qq_email": f"76543210{sum(bytearray(tag.encode('utf-8'))) % 10**8:08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]
