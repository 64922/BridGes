"""Issue 08 跨会话续学与目标切换的迁移与生命周期合同。"""

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
from bridges.study.goal_events import StudyGoalEventRepository

_NOW = datetime(2026, 10, 14, tzinfo=UTC)


def test_goal_events_export_delete_and_recovery(
    tmp_path: Path, monkeypatch: Any
) -> None:
    assert SCHEMA_VERSION >= 75
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue08-goal-events-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()

    try:
        with TestClient(app) as owner_client:
            owner = _register(owner_client, "goal-events-owner")
            first = owner_client.post(
                "/chat/first-turn",
                json={
                    "mode": "study",
                    "content": "互斥是不是独立？",
                    "idempotency_key": "issue08-goal-events-owner",
                },
            )
            assert first.status_code == 201, first.text
            conversation_id = first.json()["conversation"]["conversation_id"]
            database = app.state.bridges_database
            repository = StudyGoalEventRepository(database)

            with database.transaction():
                repository.record_in_transaction(
                    account_id=owner["id"],
                    conversation_id=conversation_id,
                    kind="switch_out",
                    source_message_id="user-switch",
                    goal_id="goal-old",
                    goal_statement="系统学概率论",
                    detail="明确改学",
                    goal_version=2,
                    progress_version=5,
                    pending_check_id="check-1",
                    pending_question="独立事件的定义是什么？",
                    now=_NOW,
                )
                repository.record_in_transaction(
                    account_id=owner["id"],
                    conversation_id=conversation_id,
                    kind="link",
                    source_message_id="user-switch",
                    goal_id="goal-new",
                    goal_statement="系统学线性代数",
                    previous_goal_id="goal-old",
                    previous_goal_statement="系统学概率论",
                    detail="switch",
                    goal_version=1,
                    progress_version=5,
                    now=_NOW,
                )
                # 幂等：同一来源消息 + 类型的重放不重复记账。
                repository.record_in_transaction(
                    account_id=owner["id"],
                    conversation_id=conversation_id,
                    kind="link",
                    source_message_id="user-switch",
                    goal_id="goal-new",
                    goal_statement="系统学线性代数",
                    detail="switch",
                    goal_version=1,
                    now=_NOW,
                )
                repository.record_in_transaction(
                    account_id=owner["id"],
                    conversation_id=conversation_id,
                    kind="conflict",
                    source_message_id="user-conflict",
                    goal_id="goal-old",
                    goal_statement="系统学概率论",
                    detail="switch_conflict",
                    now=_NOW,
                )
                repository.record_in_transaction(
                    account_id=owner["id"],
                    conversation_id=conversation_id,
                    kind="clarification",
                    source_message_id="user-clarify",
                    detail="续学目标有多个候选",
                    candidate_goal_ids=["goal-old", "goal-new"],
                    now=_NOW,
                )

            notice = repository.latest_notice(owner["id"], conversation_id)
            assert notice is not None
            assert notice.kind == "clarification"
            assert repository.pending_clarification(owner["id"], conversation_id) is True
            assert repository.candidate_ids_for_pending_clarification(
                owner["id"], conversation_id
            ) == ["goal-old", "goal-new"]
            assert (
                repository.has_transition_for_message(
                    owner["id"], conversation_id, "user-switch"
                )
                is True
            )
            assert (
                repository.has_transition_for_message(
                    owner["id"], conversation_id, "user-conflict"
                )
                is False
            )

            # 冲突可重复记录（不同时刻的同类冲突是不同事实）。
            with database.transaction():
                repository.record_in_transaction(
                    account_id=owner["id"],
                    conversation_id=conversation_id,
                    kind="conflict",
                    source_message_id="user-conflict",
                    goal_id="goal-old",
                    goal_statement="系统学概率论",
                    detail="switch_conflict",
                    now=_NOW,
                )

            with database.transaction():
                repository.record_in_transaction(
                    account_id=owner["id"],
                    conversation_id=conversation_id,
                    kind="clarification_lapsed",
                    source_message_id="user-plain",
                    detail="未选择候选目标",
                    now=_NOW,
                )
            # 澄清失效后不再打扰：无通知、无待确认。
            assert repository.latest_notice(owner["id"], conversation_id) is None
            assert (
                repository.pending_clarification(owner["id"], conversation_id) is False
            )

            assert "study_goal_events" in ACCOUNT_TABLES
            category = next(
                item for item in EXPORT_CATEGORIES if item.key == "study_goals"
            )
            assert "study_goal_events" in category.tables
            rows = export_rows(database, owner["id"], "study_goal_events")
            assert len(rows) == 6
            summary = logical_summary(database, owner["id"])
            assert summary["study_goal_events"] == 6
            assert {
                item.key: count
                for item, count, _ in category_preview(database, owner["id"])
            }["study_goals"] == 6

            with TestClient(app) as other_client:
                other = _register(other_client, "goal-events-other")
                assert repository.latest_event(other["id"], conversation_id) is None
                assert export_rows(database, other["id"], "study_goal_events") == []

            backup_path = tmp_path / "bridges-backup.db"
            with database.snapshot_lock():
                database.snapshot_to(backup_path)
            restored = BridgesDatabase(backup_path)
            assert restored.initialize() == SCHEMA_VERSION
            assert logical_summary(restored, owner["id"])["study_goal_events"] == 6
            restored.close()

            delete_account_rows(database, owner["id"])
            assert export_rows(database, owner["id"], "study_goal_events") == []
            assert export_rows(database, other["id"], "study_goal_events") == []
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_upgrade_from_v74_adds_goal_id_columns(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-74.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 75):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '74')")
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION

    row = database.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        ("study_goal_events",),
    ).fetchone()
    assert row is not None
    index = database.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        ("idx_study_goal_events_conversation",),
    ).fetchone()
    assert index is not None
    for table in ("study_pending_checks", "study_evidence", "study_diagnosis_tasks"):
        columns = {
            str(item["name"])
            for item in database.connection.execute(
                f'PRAGMA table_info("{table}")'
            ).fetchall()
        }
        assert "goal_id" in columns, table
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
