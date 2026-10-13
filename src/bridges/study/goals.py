"""账户级学习目标与路线仓库：账户作用域、版本守卫与历史版本读取。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from pydantic import ValidationError

from bridges.contracts.study_goals import (
    StudyGoalProjection,
    StudyRouteProjection,
)
from bridges.storage.database import BridgesDatabase, ScopedConnection

_GOAL_COLUMNS = (
    "goal_id, statement, observable_result, scope, constraints, status, version,"
    " source_message_id, created_at, updated_at"
)
_ROUTE_COLUMNS = (
    "version, nodes_json, current_node_id, revision_reason, origin, created_at"
)


class StudyGoalRepository:
    """目标、路线与会话引用的读写；写入由调用方的终态事务保证原子。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    def get_linked_goal(
        self, account_id: str, conversation_id: str
    ) -> StudyGoalProjection | None:
        row = (
            self._database.scoped(account_id)
            .execute(
                "SELECT g.goal_id, g.statement, g.observable_result, g.scope,"
                " g.constraints, g.status, g.version, g.source_message_id,"
                " g.created_at, g.updated_at"
                " FROM study_conversation_goals AS c"
                " JOIN study_goals AS g"
                " ON g.account_id = c.account_id AND g.goal_id = c.goal_id"
                " WHERE c.account_id = ? AND c.conversation_id = ?",
                (account_id, conversation_id),
            )
            .fetchone()
        )
        if row is None:
            return None
        goal = _goal_from_row(row)
        if goal is None:
            return None
        route = self.current_route(account_id, goal.goal_id)
        return goal.model_copy(update={"route": route})

    def get_goal(self, account_id: str, goal_id: str) -> StudyGoalProjection | None:
        row = (
            self._database.scoped(account_id)
            .execute(
                f"SELECT {_GOAL_COLUMNS} FROM study_goals"
                " WHERE account_id = ? AND goal_id = ?",
                (account_id, goal_id),
            )
            .fetchone()
        )
        if row is None:
            return None
        return _goal_from_row(row)

    def current_route(
        self, account_id: str, goal_id: str
    ) -> StudyRouteProjection | None:
        row = (
            self._database.scoped(account_id)
            .execute(
                f"SELECT {_ROUTE_COLUMNS} FROM study_goal_routes"
                " WHERE account_id = ? AND goal_id = ?"
                " ORDER BY version DESC LIMIT 1",
                (account_id, goal_id),
            )
            .fetchone()
        )
        if row is None:
            return None
        return _route_from_row(row)

    def list_route_revisions(
        self, account_id: str, goal_id: str
    ) -> list[StudyRouteProjection]:
        """按版本升序返回全部历史路线；修订不覆盖历史版本。"""

        rows = (
            self._database.scoped(account_id)
            .execute(
                f"SELECT {_ROUTE_COLUMNS} FROM study_goal_routes"
                " WHERE account_id = ? AND goal_id = ? ORDER BY version ASC",
                (account_id, goal_id),
            )
            .fetchall()
        )
        revisions = [_route_from_row(row) for row in rows]
        return [revision for revision in revisions if revision is not None]

    def create_goal_with_route_in_transaction(
        self,
        *,
        account_id: str,
        conversation_id: str,
        goal: StudyGoalProjection,
        route: StudyRouteProjection | None,
        now: datetime,
    ) -> bool:
        """终态事务内创建目标、首版路线与会话引用。

        会话已关联目标时返回 False（重复首轮不多建目标），不写入任何行；
        调用方在既有事务内执行，目标、路线、引用与消息终态一起提交或回滚。
        """

        scoped = self._database.scoped(account_id)
        existing = scoped.execute(
            "SELECT goal_id FROM study_conversation_goals"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        ).fetchone()
        if existing is not None:
            return False
        scoped.execute(
            "INSERT INTO study_goals (goal_id, account_id, statement, observable_result,"
            " scope, constraints, status, source_message_id, version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                goal.goal_id,
                account_id,
                goal.statement,
                goal.observable_result,
                goal.scope,
                goal.constraints,
                goal.status,
                goal.source_message_id,
                goal.version,
                goal.created_at.isoformat(),
                goal.updated_at.isoformat(),
            ),
        )
        if route is not None:
            _insert_route_row(scoped, account_id, goal.goal_id, route)
        scoped.execute(
            "INSERT INTO study_conversation_goals"
            " (account_id, conversation_id, goal_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (account_id, conversation_id, goal.goal_id, now.isoformat(), now.isoformat()),
        )
        return True

    def save_route_revision_in_transaction(
        self,
        *,
        account_id: str,
        goal_id: str,
        route: StudyRouteProjection,
        expected_version: int,
    ) -> bool:
        """在终态事务内提交新路线版本，只接受规划时读取到的预期版本。

        并发方已提交更新的版本时守卫拒绝写入并返回 False——不覆盖新状态，
        也不向调用方虚报成功。
        """

        scoped = self._database.scoped(account_id)
        cursor = scoped.execute(
            "INSERT INTO study_goal_routes"
            " (account_id, goal_id, version, nodes_json, current_node_id,"
            " revision_reason, origin, created_at)"
            " SELECT ?, ?, ?, ?, ?, ?, ?, ?"
            " WHERE (SELECT COALESCE(MAX(version), 0) FROM study_goal_routes"
            " WHERE account_id = ? AND goal_id = ?) = ?",
            (
                account_id,
                goal_id,
                route.version,
                _nodes_json(route),
                route.current_node_id,
                route.revision_reason,
                route.origin,
                route.created_at.isoformat(),
                account_id,
                goal_id,
                expected_version,
            ),
        )
        return cursor.rowcount == 1


def _insert_route_row(
    scoped: ScopedConnection, account_id: str, goal_id: str, route: StudyRouteProjection
) -> None:
    scoped.execute(
        "INSERT INTO study_goal_routes"
        " (account_id, goal_id, version, nodes_json, current_node_id,"
        " revision_reason, origin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            account_id,
            goal_id,
            route.version,
            _nodes_json(route),
            route.current_node_id,
            route.revision_reason,
            route.origin,
            route.created_at.isoformat(),
        ),
    )


def _nodes_json(route: StudyRouteProjection) -> str:
    return json.dumps(
        [node.model_dump(mode="json") for node in route.nodes], ensure_ascii=False
    )


def _goal_from_row(row: sqlite3.Row) -> StudyGoalProjection | None:
    try:
        return StudyGoalProjection.model_validate(
            {
                "goal_id": row["goal_id"],
                "statement": row["statement"],
                "observable_result": row["observable_result"],
                "scope": row["scope"],
                "constraints": row["constraints"],
                "status": row["status"],
                "version": row["version"],
                "source_message_id": row["source_message_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    except (TypeError, ValueError, ValidationError, KeyError):
        return None


def _route_from_row(row: sqlite3.Row) -> StudyRouteProjection | None:
    try:
        nodes = json.loads(row["nodes_json"])
    except (TypeError, ValueError):
        return None
    try:
        return StudyRouteProjection.model_validate(
            {
                "version": row["version"],
                "nodes": nodes,
                "current_node_id": row["current_node_id"],
                "revision_reason": row["revision_reason"],
                "origin": row["origin"],
                "created_at": row["created_at"],
            }
        )
    except (TypeError, ValueError, ValidationError, KeyError):
        return None
