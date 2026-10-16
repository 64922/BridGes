"""学习目标与路线行的解析/写入辅助（Issue 08 的仓库行层）。

从 :mod:`bridges.study.goals` 拆出，保持该模块在 500 行上限内（画像工单
14 接入时按其需要收拢职责）；只服务目标仓库，不构成公开接口。
"""

from __future__ import annotations

import json
import sqlite3

from pydantic import ValidationError

from bridges.contracts.study_goals import (
    StudyGoalProjection,
    StudyRouteProjection,
)
from bridges.storage.database import ScopedConnection


def insert_route_row(
    scoped: ScopedConnection,
    account_id: str,
    goal_id: str,
    route: StudyRouteProjection,
) -> None:
    """写入一版路线行（调用方在同一事务内保证目标存在）。"""

    scoped.execute(
        "INSERT INTO study_goal_routes"
        " (account_id, goal_id, version, nodes_json, current_node_id,"
        " revision_reason, origin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            account_id,
            goal_id,
            route.version,
            nodes_json(route),
            route.current_node_id,
            route.revision_reason,
            route.origin,
            route.created_at.isoformat(),
        ),
    )


def nodes_json(route: StudyRouteProjection) -> str:
    return json.dumps(
        [node.model_dump(mode="json") for node in route.nodes], ensure_ascii=False
    )


def goal_from_row(row: sqlite3.Row) -> StudyGoalProjection | None:
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


def route_from_row(row: sqlite3.Row) -> StudyRouteProjection | None:
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


__all__ = [
    "goal_from_row",
    "insert_route_row",
    "nodes_json",
    "route_from_row",
]
