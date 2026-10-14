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

    @property
    def database(self) -> BridgesDatabase:
        return self._database

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

    def list_goals(
        self,
        account_id: str,
        *,
        statuses: tuple[str, ...] = ("active", "paused"),
        limit: int = 20,
    ) -> list[StudyGoalProjection]:
        """按账户返回本人目标（含当前路线），最近更新的排前面。

        只读取当前账户的行；状态过滤只接受已知状态，未知值不会带出
        无权目标。删除、完成或版本变化的目标由调用方按最新读取处理。
        """

        placeholders = ", ".join("?" for _ in statuses)
        rows = (
            self._database.scoped(account_id)
            .execute(
                f"SELECT {_GOAL_COLUMNS} FROM study_goals"
                f" WHERE account_id = ? AND status IN ({placeholders})"
                " ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (account_id, *statuses, max(1, int(limit))),
            )
            .fetchall()
        )
        goals: list[StudyGoalProjection] = []
        for row in rows:
            goal = _goal_from_row(row)
            if goal is None:
                continue
            route = self.current_route(account_id, goal.goal_id)
            goals.append(goal.model_copy(update={"route": route}))
        return goals

    def list_resume_candidates(
        self, account_id: str, *, limit: int = 5
    ) -> list[StudyGoalProjection]:
        """续学/切换规划的目标候选：本人活动或暂停的目标，有界且最近优先。"""

        return self.list_goals(account_id, limit=limit)

    def link_existing_goal_in_transaction(
        self,
        *,
        account_id: str,
        conversation_id: str,
        goal_id: str,
        expected_version: int,
        now: datetime,
    ) -> bool:
        """终态事务内把会话关联到既有目标（续学或切换回来）。

        目标不存在、被完成、版本已变化时拒绝（不静默覆盖其他会话的
        暂停/切换）。暂停中的目标被用户明确续学时在同一事务内恢复为
        active 并递增版本；会话已有其他关联时视为冲突，不悄悄改写。
        """

        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            "SELECT status, version FROM study_goals"
            " WHERE account_id = ? AND goal_id = ?",
            (account_id, goal_id),
        ).fetchone()
        if row is None:
            return False
        status = str(row["status"])
        version = int(row["version"])
        if status == "completed" or version != expected_version:
            return False
        existing = scoped.execute(
            "SELECT goal_id FROM study_conversation_goals"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        ).fetchone()
        if existing is not None:
            # 重试重放：会话已关联同一目标时幂等成功；关联其他目标时拒绝，
            # 不悄悄改写，也不留下半次恢复。
            return str(existing["goal_id"]) == goal_id
        if status == "paused":
            cursor = scoped.execute(
                "UPDATE study_goals SET status = 'active', version = version + 1,"
                " updated_at = ? WHERE account_id = ? AND goal_id = ?"
                " AND version = ? AND status = 'paused'",
                (now.isoformat(), account_id, goal_id, expected_version),
            )
            if cursor.rowcount != 1:
                return False
        scoped.execute(
            "INSERT INTO study_conversation_goals"
            " (account_id, conversation_id, goal_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (account_id, conversation_id, goal_id, now.isoformat(), now.isoformat()),
        )
        return True

    def pause_goal_in_transaction(
        self,
        *,
        account_id: str,
        goal_id: str,
        expected_version: int,
        now: datetime,
    ) -> bool:
        """把活动目标暂停并递增版本；版本不符时拒绝，不覆盖新状态。"""

        cursor = self._database.scoped(account_id).execute(
            "UPDATE study_goals SET status = 'paused', version = version + 1,"
            " updated_at = ? WHERE account_id = ? AND goal_id = ?"
            " AND version = ? AND status = 'active'",
            (now.isoformat(), account_id, goal_id, expected_version),
        )
        return cursor.rowcount == 1

    def reactivate_goal_in_transaction(
        self,
        *,
        account_id: str,
        goal_id: str,
        expected_version: int,
        now: datetime,
    ) -> bool:
        """把暂停目标恢复为活动并递增版本（本会话明确继续）；版本不符拒绝。"""

        cursor = self._database.scoped(account_id).execute(
            "UPDATE study_goals SET status = 'active', version = version + 1,"
            " updated_at = ? WHERE account_id = ? AND goal_id = ?"
            " AND version = ? AND status = 'paused'",
            (now.isoformat(), account_id, goal_id, expected_version),
        )
        return cursor.rowcount == 1

    def replace_link_in_transaction(
        self,
        *,
        account_id: str,
        conversation_id: str,
        previous_goal_id: str,
        goal_id: str,
        now: datetime,
    ) -> bool:
        """切换：把会话引用从原目标改到新目标；原关联已变化时拒绝。"""

        cursor = self._database.scoped(account_id).execute(
            "UPDATE study_conversation_goals SET goal_id = ?, updated_at = ?"
            " WHERE account_id = ? AND conversation_id = ? AND goal_id = ?",
            (goal_id, now.isoformat(), account_id, conversation_id, previous_goal_id),
        )
        return cursor.rowcount == 1

    def delete_goal_row_in_transaction(
        self, *, account_id: str, goal_id: str
    ) -> None:
        """补偿已写入但整笔切换未成立的新目标行及其路线（同一事务内回滚语义）。"""

        scoped = self._database.scoped(account_id)
        scoped.execute(
            "DELETE FROM study_goal_routes WHERE account_id = ? AND goal_id = ?",
            (account_id, goal_id),
        )
        scoped.execute(
            "DELETE FROM study_goals WHERE account_id = ? AND goal_id = ?",
            (account_id, goal_id),
        )

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
        self.insert_goal_with_route_in_transaction(
            account_id=account_id, goal=goal, route=route, now=now
        )
        scoped.execute(
            "INSERT INTO study_conversation_goals"
            " (account_id, conversation_id, goal_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (account_id, conversation_id, goal.goal_id, now.isoformat(), now.isoformat()),
        )
        return True

    def insert_goal_with_route_in_transaction(
        self,
        *,
        account_id: str,
        goal: StudyGoalProjection,
        route: StudyRouteProjection | None,
        now: datetime,
    ) -> None:
        """写入目标行与可选首版路线（不建立会话引用；供创建与切换复用）。"""

        scoped = self._database.scoped(account_id)
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

    def switch_to_existing_goal_in_transaction(
        self,
        *,
        account_id: str,
        conversation_id: str,
        previous_goal_id: str,
        goal_id: str,
        expected_version: int,
        now: datetime,
    ) -> bool:
        """切换会话引用到既有目标；目标版本/关联不符时拒绝，不半提交。

        暂停中的既有目标在此一并恢复为 active 并递增版本；调用方在
        同一终态事务内执行，全部核对在写入前完成。
        """

        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            "SELECT status, version FROM study_goals"
            " WHERE account_id = ? AND goal_id = ?",
            (account_id, goal_id),
        ).fetchone()
        if row is None:
            return False
        status = str(row["status"])
        version = int(row["version"])
        if status == "completed" or version != expected_version:
            return False
        existing = scoped.execute(
            "SELECT goal_id FROM study_conversation_goals"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        ).fetchone()
        if existing is None or str(existing["goal_id"]) != previous_goal_id:
            return False
        if status == "paused":
            cursor = scoped.execute(
                "UPDATE study_goals SET status = 'active', version = version + 1,"
                " updated_at = ? WHERE account_id = ? AND goal_id = ?"
                " AND version = ? AND status = 'paused'",
                (now.isoformat(), account_id, goal_id, expected_version),
            )
            if cursor.rowcount != 1:
                return False
        cursor = scoped.execute(
            "UPDATE study_conversation_goals SET goal_id = ?, updated_at = ?"
            " WHERE account_id = ? AND conversation_id = ? AND goal_id = ?",
            (goal_id, now.isoformat(), account_id, conversation_id, previous_goal_id),
        )
        return cursor.rowcount == 1

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
