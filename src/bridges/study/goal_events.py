"""Issue 08：目标关联、切换、冲突与澄清事件的账户级记录。

职责边界：

- 事件在消息成功终态事务内写入，与目标/引用/待答处置状态同一事务，
  失败不留半次切换；冲突事件同样落库，让「消息已交付但目标进展未提交」
  如实可见，而不是静默吞掉。
- 用户可见通知由事件权威派生（恢复/切换/冲突/待确认），不做无依据的
  客户端状态切换；澄清事件只标记「正等待用户确认目标」。
- 事件保留原目标暂停时的待答处置与进展版本，供恢复和审计读取。
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from bridges.contracts.study_goals import (
    StudyGoalEventKind,
    StudyGoalNoticeKind,
    StudyGoalNoticeProjection,
)
from bridges.storage.database import BridgesDatabase

#: 冲突事件有界展示的固定中文文案；错误类别按真实原因区分。
_CONFLICT_MESSAGES = {
    "goal_missing": (
        "目标操作未提交：该学习目标已不存在或无法访问；"
        "本次没有改动任何学习记录。"
    ),
    "goal_changed": (
        "目标状态已被其他会话更新，本次恢复或切换未提交；"
        "请重试或重新选择目标。"
    ),
    "switch_conflict": (
        "本次目标切换未提交：关联状态已变化（可能由其他会话更新）；"
        "消息已回复，学习进展与目标关联保持原样。请重试切换。"
    ),
    "route_conflict": (
        "学习路线已被其他会话更新，本次修订未提交；"
        "请重新确认当前路线后再调整。"
    ),
}
_CONFLICT_DEFAULT = "目标操作未提交：状态已变化；本次没有改动学习进展。"
_CLARIFICATION_MESSAGE = "请按上方回复确认要续学的目标；确认前不会改动任何学习进展。"
_RESTORED_TEMPLATE = "已恢复学习目标「{statement}」，按已保存的进展与位置继续。"
_SWITCHED_TEMPLATE = "已切换到学习目标「{statement}」，原目标已保存并暂停。"


class StudyGoalEventRepository:
    """事件写入与读取；写入由调用方的终态事务保证原子。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    def record_in_transaction(
        self,
        *,
        account_id: str,
        conversation_id: str,
        kind: StudyGoalEventKind,
        source_message_id: str,
        goal_id: str = "",
        goal_statement: str = "",
        previous_goal_id: str = "",
        previous_goal_statement: str = "",
        detail: str = "",
        goal_version: int = 0,
        progress_version: int = 0,
        pending_check_id: str = "",
        pending_question: str = "",
        candidate_goal_ids: list[str] | None = None,
        now: datetime | None = None,
    ) -> None:
        """在既有事务内写入一条事件；重试重放按来源消息+类型去重。"""

        if kind != "conflict":
            existing = self._database.scoped(account_id).execute(
                "SELECT 1 AS present FROM study_goal_events"
                " WHERE account_id = ? AND conversation_id = ?"
                " AND source_message_id = ? AND kind = ? LIMIT 1",
                (account_id, conversation_id, source_message_id, kind),
            ).fetchone()
            if existing is not None:
                return
        timestamp = (now or datetime.now(UTC)).isoformat()
        self._database.scoped(account_id).execute(
            "INSERT INTO study_goal_events"
            " (event_id, account_id, conversation_id, goal_id, goal_statement,"
            " previous_goal_id, previous_goal_statement, kind, detail, goal_version,"
            " progress_version, pending_check_id, pending_question,"
            " candidate_goal_ids_json, source_message_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                secrets.token_urlsafe(16),
                account_id,
                conversation_id,
                goal_id,
                _short(goal_statement, 2000),
                previous_goal_id,
                _short(previous_goal_statement, 2000),
                kind,
                _short(detail, 200),
                max(0, int(goal_version)),
                max(0, int(progress_version)),
                pending_check_id,
                _short(pending_question, 300),
                json.dumps(list(candidate_goal_ids or []), ensure_ascii=False),
                source_message_id,
                timestamp,
            ),
        )

    def latest_event(
        self, account_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        row = self._database.scoped(account_id).execute(
            "SELECT * FROM study_goal_events"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY rowid DESC LIMIT 1",
            (account_id, conversation_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def latest_notice(
        self, account_id: str, conversation_id: str
    ) -> StudyGoalNoticeProjection | None:
        """把最新事件映射为固定中文通知；创建型关联与失效澄清不打扰用户。"""

        event = self.latest_event(account_id, conversation_id)
        if event is None:
            return None
        kind = str(event.get("kind") or "")
        detail = str(event.get("detail") or "")
        statement = str(event.get("goal_statement") or "")
        if kind == "link":
            if detail == "resume":
                notice_kind: StudyGoalNoticeKind = "restored"
                message = _RESTORED_TEMPLATE.format(statement=statement or "（已保存目标）")
            elif detail == "switch":
                notice_kind = "switched"
                message = _SWITCHED_TEMPLATE.format(statement=statement or "（新目标）")
            else:
                # 首次创建目标由目标区块直接呈现，不额外打扰。
                return None
        elif kind == "conflict":
            notice_kind = "conflict"
            message = _CONFLICT_MESSAGES.get(detail, _CONFLICT_DEFAULT)
        elif kind == "clarification":
            notice_kind = "clarification"
            message = _CLARIFICATION_MESSAGE
        else:
            return None
        try:
            notice = StudyGoalNoticeProjection(
                kind=notice_kind,
                message=message,
                goal_statement=statement,
                created_at=_parse_time(str(event.get("created_at") or "")),
            )
        except (TypeError, ValueError, ValidationError):
            return None
        return notice

    def pending_clarification(self, account_id: str, conversation_id: str) -> bool:
        """最近事件是否为尚待用户确认的澄清；任何更新的成功事件自动取代它。"""

        event = self.latest_event(account_id, conversation_id)
        return event is not None and str(event.get("kind") or "") == "clarification"

    def candidate_ids_for_pending_clarification(
        self, account_id: str, conversation_id: str
    ) -> list[str]:
        event = self.latest_event(account_id, conversation_id)
        if event is None or str(event.get("kind") or "") != "clarification":
            return []
        try:
            values = json.loads(str(event.get("candidate_goal_ids_json") or "[]"))
        except (TypeError, ValueError):
            return []
        return [str(value) for value in values] if isinstance(values, list) else []

    def has_transition_for_message(
        self, account_id: str, conversation_id: str, message_id: str
    ) -> bool:
        """本轮来源消息是否成功提交过目标关联/切换（供跨目标写入守卫）。"""

        row = self._database.scoped(account_id).execute(
            "SELECT 1 AS present FROM study_goal_events"
            " WHERE account_id = ? AND conversation_id = ? AND source_message_id = ?"
            " AND kind IN ('link', 'switch_out') LIMIT 1",
            (account_id, conversation_id, message_id),
        ).fetchone()
        return row is not None


def _short(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
