"""Issue 05 后台诊断与学习档案的迁移与生命周期合同。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
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
from bridges.study.diagnosis_repository import (
    StudyDiagnosisCandidate,
    StudyDiagnosisRepository,
    StudyDiagnosisResult,
)

_NOW = datetime(2026, 10, 14, tzinfo=UTC)


def _candidate(
    account_id: str, conversation_id: str, *, evidence_id: str = "ev-1"
) -> StudyDiagnosisCandidate:
    return StudyDiagnosisCandidate(
        account_id=account_id,
        conversation_id=conversation_id,
        evidence_id=evidence_id,
        question="如果 A、B 互斥，P(A∩B) 是多少？",
        knowledge_context="互斥与独立",
        input_version="1|study-verification-v1|study-verification-v1",
        basis={
            "evaluation_points": ["PRIVATE-BASIS-42"],
            "accepted_equivalents": [],
            "key_misconceptions": [],
            "assumptions": [],
            "expected_answer": "0",
            "contract_version": "study-verification-v1",
        },
        assistance="independent",
        answer_message_id="user-1",
        check_kind="formal_check",
        source_refs=[],
    )


def _result(
    verdict: StudyDiagnosisVerdict = StudyDiagnosisVerdict.INDEPENDENT_COMPLETION,
) -> StudyDiagnosisResult:
    return StudyDiagnosisResult(
        subject="概率论",
        title="互斥与独立",
        usage_context="理解事件关系",
        verdict=verdict,
        performance="能说明互斥不等于独立。",
        supporting=["给出了正确的反例"],
        refuting=[],
        uncertainty="只在单题上验证过。",
        next_validation="换一个条件概率题再验证。",
    )


def test_diagnosis_migration_export_delete_and_recovery(
    tmp_path: Path, monkeypatch: Any
) -> None:
    assert SCHEMA_VERSION == 73
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue05-diagnosis-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()

    try:
        with TestClient(app) as owner_client:
            owner = _register(owner_client, "diagnosis-owner")
            first = owner_client.post(
                "/chat/first-turn",
                json={
                    "mode": "study",
                    "content": "互斥是不是独立？",
                    "idempotency_key": "issue05-diagnosis-owner",
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
                # 幂等：重复登记同一证据与输入版本不产生第二行。
                assert repository.create_task_in_transaction(
                    _candidate(owner["id"], conversation_id), now=_NOW
                ) == task_id
            generation = repository.mark_running(owner["id"], task_id)
            assert generation == 1
            task = repository.get_task(owner["id"], task_id)
            assert task is not None
            assert task.status.value == "running"
            assert repository.commit_result(
                task, generation, _result(), evidence_at=_NOW
            ) is True
            # 迟到/重复提交（同代次）不重复记账。
            assert repository.commit_result(
                task, generation, _result(), evidence_at=_NOW
            ) is False

            tasks = repository.list_tasks(owner["id"], conversation_id)
            assert len(tasks) == 1
            assert tasks[0].status.value == "succeeded"
            assert tasks[0].task_id == task_id
            assert tasks[0].question == "如果 A、B 互斥，P(A∩B) 是多少？"

            assert "study_diagnosis_tasks" in ACCOUNT_TABLES
            assert "study_diagnosis_results" in ACCOUNT_TABLES
            assert "study_learning_records" in ACCOUNT_TABLES
            category = next(
                item for item in EXPORT_CATEGORIES if item.key == "study_diagnosis"
            )
            assert category.tables == (
                "study_diagnosis_results",
                "study_diagnosis_tasks",
                "study_learning_records",
            )
            assert len(export_rows(database, owner["id"], "study_diagnosis_tasks")) == 1
            assert (
                len(export_rows(database, owner["id"], "study_diagnosis_results")) == 1
            )
            assert (
                len(export_rows(database, owner["id"], "study_learning_records")) == 1
            )
            summary = logical_summary(database, owner["id"])
            assert summary["study_diagnosis_tasks"] == 1
            assert summary["study_diagnosis_results"] == 1
            assert summary["study_learning_records"] == 1
            assert {
                item.key: count
                for item, count, _ in category_preview(database, owner["id"])
            }["study_diagnosis"] == 3

            with TestClient(app) as other_client:
                other = _register(other_client, "diagnosis-other")
                assert repository.get_task(other["id"], task_id) is None
                assert repository.list_tasks(other["id"], conversation_id) == []
                assert export_rows(database, other["id"], "study_diagnosis_tasks") == []

            backup_path = tmp_path / "bridges-backup.db"
            with database.snapshot_lock():
                database.snapshot_to(backup_path)
            restored = BridgesDatabase(backup_path)
            assert restored.initialize() == SCHEMA_VERSION
            restored_summary = logical_summary(restored, owner["id"])
            assert restored_summary["study_diagnosis_tasks"] == 1
            assert restored_summary["study_diagnosis_results"] == 1
            assert restored_summary["study_learning_records"] == 1
            restored.close()

            delete_account_rows(database, owner["id"])
            assert export_rows(database, owner["id"], "study_diagnosis_tasks") == []
            assert export_rows(database, owner["id"], "study_diagnosis_results") == []
            assert export_rows(database, owner["id"], "study_learning_records") == []
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_upgrade_from_v72_adds_diagnosis_tables(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-72.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 73):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '72')")
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION

    for table in (
        "study_diagnosis_tasks",
        "study_diagnosis_results",
        "study_learning_records",
    ):
        row = database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        assert row is not None
    assert database.initialize() == SCHEMA_VERSION


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
