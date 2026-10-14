"""Issue 07 学习目标与路线的迁移与生命周期合同。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.study_goals import StudyGoalProjection, StudyRouteProjection
from bridges.lifecycle.catalog import (
    ACCOUNT_TABLES,
    EXPORT_CATEGORIES,
    category_preview,
    delete_account_rows,
    export_rows,
    logical_summary,
)
from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION, BridgesDatabase
from bridges.study.goals import StudyGoalRepository


def _goal(source_message_id: str = "user-1") -> StudyGoalProjection:
    return StudyGoalProjection(
        goal_id="goal-1",
        statement="我想学线性代数",
        observable_result="能读懂机器学习里的矩阵运算",
        scope="线性代数基础",
        constraints="不要布置作业",
        source_message_id=source_message_id,
        version=1,
        created_at=datetime(2026, 10, 13, tzinfo=UTC),
        updated_at=datetime(2026, 10, 13, tzinfo=UTC),
    )


def _route(version: int = 1) -> StudyRouteProjection:
    return StudyRouteProjection.model_validate(
        {
            "version": version,
            "nodes": [
                {
                    "node_id": "n1",
                    "title": "向量与线性组合",
                    "prerequisites": [],
                    "depth": "基础",
                },
                {
                    "node_id": "n2",
                    "title": "矩阵与矩阵乘法",
                    "prerequisites": ["n1"],
                    "depth": "基础",
                },
            ],
            "current_node_id": "n1",
            "revision_reason": "首次形成路线",
            "origin": "assistant_proposal",
            "created_at": datetime(2026, 10, 13, tzinfo=UTC),
        }
    )


def test_study_goals_migration_export_delete_and_recovery(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Issue 07 引入 v72 结构；后续版本（Issue 04 起）在其上追加，本测试
    # 只要求当前模式不早于 v72。
    assert SCHEMA_VERSION >= 72
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue07-goals-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()

    try:
        with TestClient(app) as owner_client:
            owner = _register(owner_client, "goals-owner")
            first = owner_client.post(
                "/chat/first-turn",
                json={
                    "mode": "study",
                    "content": "我想学线性代数",
                    "idempotency_key": "issue07-goals-owner",
                },
            )
            assert first.status_code == 201, first.text
            conversation_id = first.json()["conversation"]["conversation_id"]
            database = app.state.bridges_database
            repository = StudyGoalRepository(database)
            with database.transaction():
                created = repository.create_goal_with_route_in_transaction(
                    account_id=owner["id"],
                    conversation_id=conversation_id,
                    goal=_goal(),
                    route=_route(),
                    now=datetime(2026, 10, 13, tzinfo=UTC),
                )
            assert created is True

            with database.transaction():
                # 会话已有目标：重复创建必须被拒绝且不新建任何行。
                duplicate = repository.create_goal_with_route_in_transaction(
                    account_id=owner["id"],
                    conversation_id=conversation_id,
                    goal=_goal().model_copy(update={"goal_id": "goal-2"}),
                    route=_route(),
                    now=datetime(2026, 10, 13, tzinfo=UTC),
                )
            assert duplicate is False

            linked = repository.get_linked_goal(owner["id"], conversation_id)
            assert linked is not None
            assert linked.statement == "我想学线性代数"
            assert linked.route is not None
            assert linked.route.version == 1
            assert [node.title for node in linked.route.nodes] == [
                "向量与线性组合",
                "矩阵与矩阵乘法",
            ]
            assert [
                item.version
                for item in repository.list_route_revisions(owner["id"], "goal-1")
            ] == [1]

            with database.transaction():
                revised = repository.save_route_revision_in_transaction(
                    account_id=owner["id"],
                    goal_id="goal-1",
                    route=_route(version=2).model_copy(
                        update={"origin": "user_request", "revision_reason": "先学矩阵"}
                    ),
                    expected_version=1,
                )
            assert revised is True
            assert [
                item.version
                for item in repository.list_route_revisions(owner["id"], "goal-1")
            ] == [1, 2]

            assert "study_goals" in ACCOUNT_TABLES
            assert "study_goal_routes" in ACCOUNT_TABLES
            assert "study_conversation_goals" in ACCOUNT_TABLES
            category = next(
                item for item in EXPORT_CATEGORIES if item.key == "study_goals"
            )
            # v75（Issue 08）为该类别补加目标事件日志表。
            assert category.tables == (
                "study_goals",
                "study_goal_routes",
                "study_conversation_goals",
                "study_goal_events",
            )
            # 1 目标 + 2 路线版本 + 1 会话引用。
            assert len(export_rows(database, owner["id"], "study_goals")) == 1
            assert len(export_rows(database, owner["id"], "study_goal_routes")) == 2
            assert (
                len(export_rows(database, owner["id"], "study_conversation_goals"))
                == 1
            )
            summary = logical_summary(database, owner["id"])
            assert summary["study_goals"] == 1
            assert summary["study_goal_routes"] == 2
            assert summary["study_conversation_goals"] == 1
            assert {
                item.key: count
                for item, count, _ in category_preview(database, owner["id"])
            }["study_goals"] == 4

            with TestClient(app) as other_client:
                other = _register(other_client, "goals-other")
                assert (
                    repository.get_linked_goal(other["id"], conversation_id) is None
                )
                assert repository.get_goal(other["id"], "goal-1") is None
                assert repository.list_route_revisions(other["id"], "goal-1") == []
                assert export_rows(database, other["id"], "study_goals") == []

            backup_path = tmp_path / "bridges-backup.db"
            with database.snapshot_lock():
                database.snapshot_to(backup_path)
            restored = BridgesDatabase(backup_path)
            assert restored.initialize() == SCHEMA_VERSION
            restored_summary = logical_summary(restored, owner["id"])
            assert restored_summary["study_goals"] == 1
            assert restored_summary["study_goal_routes"] == 2
            assert restored_summary["study_conversation_goals"] == 1
            restored.close()

            delete_account_rows(database, owner["id"])
            assert export_rows(database, owner["id"], "study_goals") == []
            assert export_rows(database, other["id"], "study_goals") == []
    finally:
        app.state.bridges_database.close()
        app.state.state_store.close()


def test_upgrade_from_v70_adds_goal_tables(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-70.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 71):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '70')")
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION

    for table in ("study_goals", "study_goal_routes", "study_conversation_goals"):
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
            "qq_email": f"87654321{sum(bytearray(tag.encode('utf-8'))) % 10**8:08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]
