"""Issue 03 学习验证与证据的迁移与生命周期合同。"""

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
    REQUIRED_TABLES,
    SCHEMA_VERSION,
    BridgesDatabase,
)
from bridges.study.verification import StudyVerificationRepository

_PENDING_COLUMNS = (
    "account_id, conversation_id, check_id, kind, question, knowledge_context,"
    " basis_json, hint_history_json, source_message_id, version, created_at,"
    " updated_at"
)
_EVIDENCE_COLUMNS = (
    "evidence_id, account_id, conversation_id, run_id, check_id, check_kind,"
    " check_version, question, knowledge_context, basis_protocol_version,"
    " answered, outcome, semantics, assistance, reasoning_note, supporting_json,"
    " missing_json, hint_message_ids_json, source_message_ids_json,"
    " source_refs_json, evidence_contract_version, answer_message_id,"
    " feedback_message_id, created_at"
)


def test_study_verification_migration_export_delete_and_recovery(
    tmp_path: Path, monkeypatch: Any
) -> None:
    assert SCHEMA_VERSION == 71
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue03-verification-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()

    try:
        with TestClient(app) as owner_client:
            owner = _register(owner_client, "verify-owner")
            first = owner_client.post(
                "/chat/first-turn",
                json={
                    "mode": "study",
                    "content": "互斥是什么？",
                    "idempotency_key": "issue03-storage-owner",
                },
            )
            assert first.status_code == 201, first.text
            conversation_id = first.json()["conversation"]["conversation_id"]
            run_id = first.json()["run_id"]
            database = app.state.bridges_database
            created_at = "2026-10-13T00:00:00+00:00"
            with database.transaction():
                database.scoped(owner["id"]).execute(
                    f"INSERT INTO study_pending_checks ({_PENDING_COLUMNS})"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        owner["id"],
                        conversation_id,
                        "check-1",
                        "formal_check",
                        "如果 A、B 互斥，P(A∩B) 是多少？",
                        "互斥与独立",
                        json.dumps(
                            {
                                "evaluation_points": ["交集为空"],
                                "contract_version": "study-verification-v1",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            [
                                {
                                    "message_id": "hint-1",
                                    "run_id": run_id,
                                    "at": created_at,
                                }
                            ],
                            ensure_ascii=False,
                        ),
                        "assistant-1",
                        1,
                        created_at,
                        created_at,
                    ),
                )
                database.scoped(owner["id"]).execute(
                    f"INSERT INTO study_evidence ({_EVIDENCE_COLUMNS})"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "evidence-1",
                        owner["id"],
                        conversation_id,
                        run_id,
                        "check-1",
                        "formal_check",
                        1,
                        "如果 A、B 互斥，P(A∩B) 是多少？",
                        "互斥与独立",
                        "study-verification-v1",
                        1,
                        "correct",
                        "hinted_completion",
                        "after_hint",
                        "学生指出交集为空",
                        json.dumps(["互斥 → 交集为空"]),
                        json.dumps([]),
                        json.dumps(["hint-1"]),
                        json.dumps(["user-1", "assistant-1"]),
                        json.dumps(
                            [
                                {
                                    "object_id": "obj-1",
                                    "filename": "概率笔记.pdf",
                                    "page_number": 3,
                                    "section_title": "事件",
                                    "index_version_id": "idx-v1",
                                }
                            ],
                            ensure_ascii=False,
                        ),
                        "study-verification-v1",
                        "user-1",
                        "assistant-1",
                        created_at,
                    ),
                )

            repository = StudyVerificationRepository(database)
            pending = repository.get_pending(owner["id"], conversation_id)
            assert pending is not None
            assert pending.check_id == "check-1"
            assert pending.basis.contract_version == "study-verification-v1"
            assert pending.hint_history[0].message_id == "hint-1"
            evidence = repository.list_evidence(owner["id"], conversation_id)
            assert len(evidence) == 1
            assert evidence[0].semantics.value == "hinted_completion"
            assert evidence[0].source_refs[0]["index_version_id"] == "idx-v1"

            assert "study_pending_checks" in REQUIRED_TABLES
            assert "study_evidence" in REQUIRED_TABLES
            assert "study_pending_checks" in ACCOUNT_TABLES
            assert "study_evidence" in ACCOUNT_TABLES
            category = next(
                item for item in EXPORT_CATEGORIES if item.key == "study_verification"
            )
            assert category.tables == ("study_pending_checks", "study_evidence")
            assert len(export_rows(database, owner["id"], "study_pending_checks")) == 1
            assert len(export_rows(database, owner["id"], "study_evidence")) == 1
            summary = logical_summary(database, owner["id"])
            assert summary["study_pending_checks"] == 1
            assert summary["study_evidence"] == 1
            preview = {
                item.key: count
                for item, count, _ in category_preview(database, owner["id"])
            }
            assert preview["study_verification"] == 2

            with TestClient(app) as other_client:
                other = _register(other_client, "verify-other")
                assert repository.get_pending(other["id"], conversation_id) is None
                assert repository.list_evidence(other["id"], conversation_id) == []
                assert export_rows(
                    database, other["id"], "study_pending_checks"
                ) == []
                assert export_rows(database, other["id"], "study_evidence") == []

            backup_path = tmp_path / "bridges-backup.db"
            with database.snapshot_lock():
                database.snapshot_to(backup_path)
            restored = BridgesDatabase(backup_path)
            assert restored.initialize() == SCHEMA_VERSION
            restored_summary = logical_summary(restored, owner["id"])
            assert restored_summary["study_pending_checks"] == 1
            assert restored_summary["study_evidence"] == 1
            restored.close()

            delete_account_rows(database, owner["id"])
            assert export_rows(database, owner["id"], "study_pending_checks") == []
            assert export_rows(database, owner["id"], "study_evidence") == []
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_pending_checks"
            ).fetchone()[0] == 0
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_evidence"
            ).fetchone()[0] == 0
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
