"""Issue 04 学习控制、暂停与阶段小结的迁移与生命周期合同。"""

from __future__ import annotations

import json
import sqlite3
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
    REQUIRED_TABLES,
    SCHEMA_VERSION,
    BridgesDatabase,
)
from bridges.study.controls import StudyControlRepository

_CONTROL_COLUMNS = (
    "control_id, account_id, conversation_id, run_id, source_message_id, kind,"
    " bound_check_id, bound_check_version, bound_question, released,"
    " progress_version, created_at"
)
_PAUSE_COLUMNS = (
    "pause_id, account_id, conversation_id, run_id, source_message_id, topic,"
    " position, pending_check_id, pending_question, progress_version, paused_at,"
    " resumed_at"
)
_SUMMARY_COLUMNS = (
    "summary_id, account_id, conversation_id, run_id, source_message_id,"
    " assistant_message_id, trigger, status, content, basis_json,"
    " progress_version, created_at, updated_at"
)


def test_study_controls_migration_export_delete_and_recovery(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Issue 04 引入 v73 结构；后续版本在其上追加，本测试只要求当前模式
    # 不早于 v73。
    assert SCHEMA_VERSION >= 73
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue04-controls-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()

    try:
        with TestClient(app) as owner_client:
            owner = _register(owner_client, "controls-owner")
            first = owner_client.post(
                "/chat/first-turn",
                json={
                    "mode": "study",
                    "content": "互斥是什么？",
                    "idempotency_key": "issue04-storage-owner",
                },
            )
            assert first.status_code == 201, first.text
            conversation_id = first.json()["conversation"]["conversation_id"]
            run_id = first.json()["run_id"]
            database = app.state.bridges_database
            created_at = "2026-10-13T00:00:00+00:00"
            with database.transaction():
                database.scoped(owner["id"]).execute(
                    f"INSERT INTO study_session_controls ({_CONTROL_COLUMNS})"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "control-1",
                        owner["id"],
                        conversation_id,
                        run_id,
                        "user-1",
                        "pause",
                        "check-1",
                        1,
                        "如果 A、B 互斥，P(A∩B) 是多少？",
                        0,
                        2,
                        created_at,
                    ),
                )
                database.scoped(owner["id"]).execute(
                    f"INSERT INTO study_session_pauses ({_PAUSE_COLUMNS})"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        "pause-1",
                        owner["id"],
                        conversation_id,
                        run_id,
                        "user-1",
                        "互斥与独立",
                        "今天先到这里",
                        "check-1",
                        "如果 A、B 互斥，P(A∩B) 是多少？",
                        2,
                        created_at,
                    ),
                )
                # 已恢复的历史暂停：active_pause 必须只返回未恢复的最新一条。
                database.scoped(owner["id"]).execute(
                    f"INSERT INTO study_session_pauses ({_PAUSE_COLUMNS})"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "pause-0",
                        owner["id"],
                        conversation_id,
                        run_id,
                        "user-0",
                        "互斥与独立",
                        "先前位置",
                        "",
                        "",
                        1,
                        "2026-10-12T00:00:00+00:00",
                        "2026-10-12T01:00:00+00:00",
                    ),
                )
                database.scoped(owner["id"]).execute(
                    f"INSERT INTO study_session_summaries ({_SUMMARY_COLUMNS})"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "summary-1",
                        owner["id"],
                        conversation_id,
                        run_id,
                        "user-1",
                        "assistant-1",
                        "pause",
                        "complete",
                        "【本阶段小结】\n本次验证（即时反馈）：无。",
                        json.dumps(
                            {
                                "contract_version": "study-summary-v1",
                                "items": [],
                            },
                            ensure_ascii=False,
                        ),
                        3,
                        created_at,
                        created_at,
                    ),
                )

            repository = StudyControlRepository(database)
            control = repository.latest_control(owner["id"], conversation_id)
            assert control is not None
            assert control.kind.value == "pause"
            assert control.question == "如果 A、B 互斥，P(A∩B) 是多少？"
            assert control.released is False
            pause = repository.active_pause(owner["id"], conversation_id)
            assert pause is not None
            assert pause.pause_id == "pause-1"
            assert pause.position == "今天先到这里"
            assert pause.progress_version == 2
            summary = repository.latest_summary(owner["id"], conversation_id)
            assert summary is not None
            assert summary.trigger.value == "pause"
            assert summary.status.value == "complete"
            assert "【本阶段小结】" in summary.content

            assert "study_session_controls" in REQUIRED_TABLES
            assert "study_session_pauses" in REQUIRED_TABLES
            assert "study_session_summaries" in REQUIRED_TABLES
            assert "study_session_controls" in ACCOUNT_TABLES
            assert "study_session_pauses" in ACCOUNT_TABLES
            assert "study_session_summaries" in ACCOUNT_TABLES
            category = next(
                item for item in EXPORT_CATEGORIES if item.key == "study_controls"
            )
            assert category.tables == (
                "study_session_controls",
                "study_session_pauses",
                "study_session_summaries",
            )
            assert len(export_rows(database, owner["id"], "study_session_controls")) == 1
            assert len(export_rows(database, owner["id"], "study_session_pauses")) == 2
            assert (
                len(export_rows(database, owner["id"], "study_session_summaries")) == 1
            )
            summary_counts = logical_summary(database, owner["id"])
            assert summary_counts["study_session_controls"] == 1
            assert summary_counts["study_session_pauses"] == 2
            assert summary_counts["study_session_summaries"] == 1
            assert {
                item.key: count
                for item, count, _ in category_preview(database, owner["id"])
            }["study_controls"] == 4

            with TestClient(app) as other_client:
                other = _register(other_client, "controls-other")
                assert repository.latest_control(other["id"], conversation_id) is None
                assert repository.active_pause(other["id"], conversation_id) is None
                assert repository.latest_summary(other["id"], conversation_id) is None
                assert export_rows(
                    database, other["id"], "study_session_controls"
                ) == []
                assert export_rows(database, other["id"], "study_session_pauses") == []
                assert export_rows(
                    database, other["id"], "study_session_summaries"
                ) == []

            backup_path = tmp_path / "bridges-backup.db"
            with database.snapshot_lock():
                database.snapshot_to(backup_path)
            restored = BridgesDatabase(backup_path)
            assert restored.initialize() == SCHEMA_VERSION
            restored_counts = logical_summary(restored, owner["id"])
            assert restored_counts["study_session_controls"] == 1
            assert restored_counts["study_session_pauses"] == 2
            assert restored_counts["study_session_summaries"] == 1
            restored.close()

            delete_account_rows(database, owner["id"])
            assert export_rows(database, owner["id"], "study_session_controls") == []
            assert export_rows(database, owner["id"], "study_session_pauses") == []
            assert export_rows(database, owner["id"], "study_session_summaries") == []
            for table in (
                "study_session_controls",
                "study_session_pauses",
                "study_session_summaries",
            ):
                assert database.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0] == 0
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_upgrade_from_v72_adds_study_control_tables(tmp_path: Path) -> None:
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
        "study_session_controls",
        "study_session_pauses",
        "study_session_summaries",
    ):
        row = database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        assert row is not None
    index = database.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        ("idx_study_session_summaries_conversation",),
    ).fetchone()
    assert index is not None
    assert database.initialize() == SCHEMA_VERSION


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"{tag}_user",
            "qq_email": f"65432100{sum(bytearray(tag.encode('utf-8'))) % 10**8:08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]
