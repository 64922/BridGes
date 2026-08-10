"""账户隔离的对话、消息与模型运行锁仓库（bridges.db）。

与 ``BridgesObjectRepository`` 同一模式：所有查询强制按 ``account_id``
过滤，跨账户访问返回不存在；所有写入走显式事务边界。
"""

from __future__ import annotations

import json
import math
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.ai import ModelRunLock
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.contracts.feedback import AnswerFeedback, FeedbackKind, FeedbackStatus
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError


@dataclass
class ConversationRecord:
    conversation_id: str
    account_id: str
    title: str
    mode: str
    mode_locked: bool
    pinned: bool
    project_id: str | None
    created_at: datetime
    updated_at: datetime
    legacy_project_name: str | None = None
    plugin_selection: list[dict[str, Any]] | None = None


@dataclass
class ModeEventRecord:
    event_id: str
    conversation_id: str
    account_id: str
    from_mode: str
    to_mode: str
    created_at: datetime


@dataclass
class GenerationRunRecord:
    """一次持久化生成运行（Issue 02：queued → running → done | failed | stopped）。

    ``config`` 保存本轮发送/重试参数（知识库开关、画像开关和表达策略
    快照），执行器重跑时按同一份任务契约执行；``attempt_count`` 是领取
    执行次数（租约超时恢复会递增），终态原因与脱敏耗时随运行落库。
    """

    run_id: str
    account_id: str
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    attempt_number: int
    status: str
    config: dict[str, Any] | None
    stage: str | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    attempt_count: int
    stop_requested: bool
    error_code: str | None
    error_message: str | None
    duration_ms: int | None
    created_at: datetime
    updated_at: datetime


@dataclass
class GenerationEventRecord:
    """运行上的一条单调游标事件（SSE 订阅回放与恢复的真相源）。"""

    run_id: str
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass
class MessageRecord:
    message_id: str
    conversation_id: str
    account_id: str
    role: ChatMessageRole
    attempt_number: int
    status: ChatMessageStatus
    content: str
    thinking: dict[str, list[str]] | None
    error_code: str | None
    error_message: str | None
    duration_ms: int | None
    model_id: str | None
    run_lock_id: str | None
    created_at: datetime
    updated_at: datetime
    web_search: dict[str, Any] | None = None
    arxiv_search: dict[str, Any] | None = None
    teaching: dict[str, Any] | None = None
    context_note: dict[str, Any] | None = None
    skill: dict[str, Any] | None = None
    career_planning: dict[str, Any] | None = None
    read_aloud: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    mcp_call: dict[str, Any] | None = None
    route: dict[str, Any] | None = None


class ConversationModeLockConflict(StorageError):
    """首条消息已由另一事务提交，不能再次创建首轮。"""


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _iso(now: datetime) -> str:
    return now.isoformat()


def _percentile(values: list[int], percentile: float) -> int | None:
    """按最近秩计算分位数（空序列返回 None；性能摘要专用）。"""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(math.ceil(percentile / 100 * len(ordered))) - 1))
    return ordered[index]


class ConversationRepository:
    """对话/消息/运行锁的 SQLite 仓库，全部操作限定在账户内。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database

    @property
    def database(self) -> BridgesDatabase:
        """返回共享数据库句柄，供同一事务的领域仓库使用。"""
        return self._db

    @contextmanager
    def connection_lock(self) -> Iterator[None]:
        """串行化跨线程的复合读取，避免共享 SQLite 连接交错使用。"""
        with self._db.snapshot_lock():
            yield

    # -- conversations -----------------------------------------------------

    def create_conversation(
        self,
        *,
        account_id: str,
        conversation_id: str,
        title: str,
        mode: str,
        created_at: datetime,
        project_id: str | None = None,
        plugin_selection: list[dict[str, Any]] | None = None,
    ) -> None:
        updated_at = created_at
        try:
            with self._db.transaction():
                self._db.scoped(account_id).execute(
                    "INSERT INTO conversations"
                    "(conversation_id, account_id, title, mode, mode_locked, pinned, project_id,"
                    " plugin_selection, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?)",
                    (
                        conversation_id,
                        account_id,
                        title,
                        mode,
                        project_id,
                        _json_dumps(plugin_selection) if plugin_selection else None,
                        _iso(created_at),
                        _iso(updated_at),
                    ),
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转换为领域错误
            raise StorageError("保存对话失败，请稍后重试。") from exc

    def get_conversation(
        self, account_id: str, conversation_id: str
    ) -> ConversationRecord | None:
        row = self._db.scoped(account_id).execute(
            "SELECT conversation_id, account_id, title, mode, mode_locked, pinned, project_id,"
            " (SELECT a.project_name FROM learning_project_migration_conversations a"
            "  WHERE a.account_id = conversations.account_id"
            "  AND a.conversation_id = conversations.conversation_id"
            "  ORDER BY a.detached_at DESC LIMIT 1) AS legacy_project_name,"
            " plugin_selection, created_at, updated_at"
            " FROM conversations WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        ).fetchone()
        if row is None:
            return None
        return self._conversation_from_row(row)

    def list_conversations(self, account_id: str) -> list[ConversationRecord]:
        rows = self._db.scoped(account_id).execute(
            "SELECT conversation_id, account_id, title, mode, mode_locked, pinned, project_id,"
            " (SELECT a.project_name FROM learning_project_migration_conversations a"
            "  WHERE a.account_id = conversations.account_id"
            "  AND a.conversation_id = conversations.conversation_id"
            "  ORDER BY a.detached_at DESC LIMIT 1) AS legacy_project_name,"
            " plugin_selection, created_at, updated_at FROM conversations"
            " WHERE account_id = ?"
            " ORDER BY pinned DESC, updated_at DESC, created_at DESC, conversation_id",
            (account_id,),
        ).fetchall()
        return [self._conversation_from_row(row) for row in rows]

    def legacy_project_name(self, account_id: str, conversation_id: str) -> str | None:
        row = self._db.scoped(account_id).execute(
            "SELECT project_name FROM learning_project_migration_conversations"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY detached_at DESC LIMIT 1",
            (account_id, conversation_id),
        ).fetchone()
        return str(row["project_name"]) if row is not None else None

    @staticmethod
    def _conversation_from_row(row: Any) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            account_id=str(row["account_id"]),
            title=str(row["title"]),
            mode=str(row["mode"]),
            mode_locked=bool(row["mode_locked"]),
            pinned=bool(row["pinned"]),
            project_id=(str(row["project_id"]) if row["project_id"] is not None else None),
            created_at=_parse_iso(str(row["created_at"])),
            updated_at=_parse_iso(str(row["updated_at"])),
            legacy_project_name=(
                str(row["legacy_project_name"])
                if row["legacy_project_name"] is not None
                else None
            ),
            plugin_selection=_json_loads_list(row["plugin_selection"]),
        )

    def set_conversation_title(
        self, account_id: str, conversation_id: str, title: str, updated_at: datetime
    ) -> None:
        with self._db.transaction():
            self._db.scoped(account_id).execute(
                "UPDATE conversations SET title = ?, updated_at = ?"
                " WHERE conversation_id = ? AND account_id = ?",
                (title, _iso(updated_at), conversation_id, account_id),
            )

    def update_conversation(
        self,
        account_id: str,
        conversation_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        plugin_selection: list[dict[str, Any]] | None = None,
        updated_at: datetime,
    ) -> int:
        """更新会话元数据；每个字段更新都带账户条件，返回影响行数。

        ``plugin_selection`` 为 None 时保持不变；空列表表示清空选择。
        """
        assignments: list[str] = []
        values: list[Any] = []
        if title is not None:
            assignments.append("title = ?")
            values.append(title)
        if pinned is not None:
            assignments.append("pinned = ?")
            values.append(1 if pinned else 0)
        if plugin_selection is not None:
            assignments.append("plugin_selection = ?")
            values.append(_json_dumps(plugin_selection) if plugin_selection else None)
        if not assignments:
            return 0
        assignments.append("updated_at = ?")
        values.append(_iso(updated_at))
        values.extend([conversation_id, account_id])
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE conversations SET "
                + ", ".join(assignments)
                + " WHERE conversation_id = ? AND account_id = ?",
                values,
            )
            return cursor.rowcount

    def delete_conversation(self, account_id: str, conversation_id: str) -> int:
        """删除会话及其消息/模式事件；跨账户目标返回 0。"""
        with self._db.transaction():
            self._db.scoped(account_id).execute(
                "DELETE FROM mode_events WHERE conversation_id = ? AND account_id = ?",
                (conversation_id, account_id),
            )
            self._db.scoped(account_id).execute(
                "DELETE FROM messages WHERE conversation_id = ? AND account_id = ?",
                (conversation_id, account_id),
            )
            cursor = self._db.scoped(account_id).execute(
                "DELETE FROM conversations WHERE conversation_id = ? AND account_id = ?",
                (conversation_id, account_id),
            )
            return cursor.rowcount

    def touch_conversation(
        self, account_id: str, conversation_id: str, updated_at: datetime
    ) -> None:
        with self._db.transaction():
            self._db.scoped(account_id).execute(
                "UPDATE conversations SET updated_at = ?"
                " WHERE conversation_id = ? AND account_id = ?",
                (_iso(updated_at), conversation_id, account_id),
            )

    # -- mode events --------------------------------------------------------

    def list_mode_events(
        self, account_id: str, conversation_id: str
    ) -> list[ModeEventRecord]:
        rows = self._db.scoped(account_id).execute(
            "SELECT event_id, conversation_id, account_id, from_mode, to_mode,"
            " created_at FROM mode_events WHERE conversation_id = ? AND account_id = ?"
            " ORDER BY created_at, event_id",
            (conversation_id, account_id),
        ).fetchall()
        return [
            ModeEventRecord(
                event_id=str(row["event_id"]),
                conversation_id=str(row["conversation_id"]),
                account_id=str(row["account_id"]),
                from_mode=str(row["from_mode"]),
                to_mode=str(row["to_mode"]),
                created_at=_parse_iso(str(row["created_at"])),
            )
            for row in rows
        ]

    # -- messages ----------------------------------------------------------

    def _persist_message_route(self, record: MessageRecord) -> None:
        """在消息所在事务内保存路由快照，外部能力启动前即完成。"""
        if record.route is None:
            return
        self._db.scoped(record.account_id).execute(
            "UPDATE messages SET route = ? WHERE message_id = ? AND account_id = ?",
            (_json_dumps(record.route), record.message_id, record.account_id),
        )

    def insert_message(self, record: MessageRecord) -> None:
        try:
            with self._db.transaction():
                self._db.scoped(record.account_id).execute(
                    "INSERT INTO messages"
                    "(message_id, conversation_id, account_id, role, attempt_number,"
                    " status, content, thinking, error_code, error_message,"
                    " duration_ms, model_id, run_lock_id, created_at, updated_at,"
                    " web_search, arxiv_search, teaching, context_note, skill,"
                    " career_planning, image, video, mcp_call, route)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.message_id,
                        record.conversation_id,
                        record.account_id,
                        record.role.value,
                        record.attempt_number,
                        record.status.value,
                        record.content,
                        _json_dumps(record.thinking) if record.thinking else None,
                        record.error_code,
                        record.error_message,
                        record.duration_ms,
                        record.model_id,
                        record.run_lock_id,
                        _iso(record.created_at),
                        _iso(record.updated_at),
                        _json_dumps(record.web_search) if record.web_search else None,
                        _json_dumps(record.arxiv_search) if record.arxiv_search else None,
                        _json_dumps(record.teaching) if record.teaching else None,
                        _json_dumps(record.context_note) if record.context_note else None,
                        _json_dumps(record.skill) if record.skill else None,
                        _json_dumps(record.career_planning)
                        if record.career_planning
                        else None,
                        _json_dumps(record.image) if record.image else None,
                        _json_dumps(record.video) if record.video else None,
                        _json_dumps(record.mcp_call) if record.mcp_call else None,
                        _json_dumps(record.route) if record.route else None,
                    ),
                )
                self._persist_message_route(record)
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StorageError("保存消息失败，请稍后重试。") from exc

    def insert_messages_with_attachments(
        self,
        user_record: MessageRecord,
        assistant_record: MessageRecord,
        attachment_ids: list[str],
    ) -> None:
        """在同一事务中保存一轮消息并绑定待发送附件。"""
        try:
            with self._db.transaction():
                for record in (user_record, assistant_record):
                    self._db.scoped(user_record.account_id).execute(
                        "INSERT INTO messages"
                        "(message_id, conversation_id, account_id, role, attempt_number,"
                        " status, content, thinking, error_code, error_message,"
                        " duration_ms, model_id, run_lock_id, created_at, updated_at,"
                        " web_search, arxiv_search, teaching, context_note, skill,"
                        " career_planning, image, video, mcp_call, route)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                        " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            record.message_id,
                            record.conversation_id,
                            record.account_id,
                            record.role.value,
                            record.attempt_number,
                            record.status.value,
                            record.content,
                            _json_dumps(record.thinking) if record.thinking else None,
                            record.error_code,
                            record.error_message,
                            record.duration_ms,
                            record.model_id,
                            record.run_lock_id,
                            _iso(record.created_at),
                            _iso(record.updated_at),
                            _json_dumps(record.web_search) if record.web_search else None,
                            _json_dumps(record.arxiv_search) if record.arxiv_search else None,
                            _json_dumps(record.teaching) if record.teaching else None,
                            _json_dumps(record.context_note) if record.context_note else None,
                            _json_dumps(record.skill) if record.skill else None,
                            _json_dumps(record.career_planning)
                            if record.career_planning
                            else None,
                            _json_dumps(record.image) if record.image else None,
                            _json_dumps(record.video) if record.video else None,
                            _json_dumps(record.mcp_call) if record.mcp_call else None,
                            _json_dumps(record.route) if record.route else None,
                        ),
                    )
                    self._persist_message_route(record)
                placeholders = ",".join("?" for _ in attachment_ids)
                rows = self._db.scoped(user_record.account_id).execute(
                    "SELECT object_id FROM chat_attachments"
                    " WHERE account_id = ? AND conversation_id = ?"
                    " AND message_id IS NULL AND status = 'uploaded'"
                    f" AND object_id IN ({placeholders})",
                    (
                        user_record.account_id,
                        user_record.conversation_id,
                        *attachment_ids,
                    ),
                ).fetchall()
                if {str(row["object_id"]) for row in rows} != set(attachment_ids):
                    raise StorageError("附件不存在或没有访问权限。")
                now = _iso(user_record.updated_at)
                for object_id in attachment_ids:
                    self._db.scoped(user_record.account_id).execute(
                        "UPDATE chat_attachments SET message_id = ?, status = 'bound',"
                        " updated_at = ? WHERE object_id = ? AND account_id = ?"
                        " AND conversation_id = ? AND message_id IS NULL",
                        (
                            user_record.message_id,
                            now,
                            object_id,
                            user_record.account_id,
                            user_record.conversation_id,
                        ),
                    )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StorageError("保存消息与附件失败，请稍后重试。") from exc

    def list_messages(self, account_id: str, conversation_id: str) -> list[MessageRecord]:
        rows = self._db.scoped(account_id).execute(
            "SELECT message_id, conversation_id, account_id, role, attempt_number,"
            " status, content, thinking, error_code, error_message, duration_ms,"
            " model_id, run_lock_id, created_at, updated_at, web_search, arxiv_search,"
            " teaching, context_note, skill, career_planning, read_aloud, image, video,"
            " mcp_call, route"
            " FROM messages WHERE conversation_id = ? AND account_id = ?"
            " ORDER BY created_at, CASE role WHEN 'user' THEN 0 ELSE 1 END,"
            " attempt_number, message_id",
            (conversation_id, account_id),
        ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def get_message(self, account_id: str, message_id: str) -> MessageRecord | None:
        row = self._db.scoped(account_id).execute(
            "SELECT message_id, conversation_id, account_id, role, attempt_number,"
            " status, content, thinking, error_code, error_message, duration_ms,"
            " model_id, run_lock_id, created_at, updated_at, web_search, arxiv_search,"
            " teaching, context_note, skill, career_planning, read_aloud, image, video,"
            " mcp_call, route"
            " FROM messages WHERE message_id = ? AND account_id = ?",
            (message_id, account_id),
        ).fetchone()
        if row is None:
            return None
        return self._message_from_row(row)

    def update_message_content(
        self, account_id: str, message_id: str, content: str, updated_at: datetime
    ) -> int:
        """流式增量落库；仅当消息仍处于 streaming 状态时生效，返回影响行数。"""
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE messages SET content = ?, updated_at = ?"
                " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                (content, _iso(updated_at), message_id, account_id),
            )
            return cursor.rowcount

    def update_message_thinking(
        self,
        account_id: str,
        message_id: str,
        thinking: dict[str, list[str]],
        updated_at: datetime,
    ) -> int:
        """流式更新可公开思考摘要；仅当消息仍处于 streaming 状态时生效。"""
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE messages SET thinking = ?, updated_at = ?"
                " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                (_json_dumps(thinking), _iso(updated_at), message_id, account_id),
            )
            return cursor.rowcount

    def update_message_web_search(
        self,
        account_id: str,
        message_id: str,
        web_search: dict[str, Any],
        updated_at: datetime,
    ) -> int:
        """流式更新公网搜索状态；仅当消息仍在生成时生效。"""
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE messages SET web_search = ?, updated_at = ?"
                " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                (_json_dumps(web_search), _iso(updated_at), message_id, account_id),
            )
            return cursor.rowcount

    def update_message_arxiv_search(
        self,
        account_id: str,
        message_id: str,
        arxiv_search: dict[str, Any],
        updated_at: datetime,
    ) -> int:
        """流式更新 arXiv 搜索状态；仅当消息仍在生成时生效。"""
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE messages SET arxiv_search = ?, updated_at = ?"
                " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                (_json_dumps(arxiv_search), _iso(updated_at), message_id, account_id),
            )
            return cursor.rowcount

    def update_message_teaching(
        self,
        account_id: str,
        message_id: str,
        teaching: dict[str, Any],
        updated_at: datetime,
    ) -> int:
        """流式更新学习模式投影；只允许写入仍在生成的助手消息。"""
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE messages SET teaching = ?, updated_at = ?"
                " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                (_json_dumps(teaching), _iso(updated_at), message_id, account_id),
            )
            return cursor.rowcount

    def update_message_context_note(
        self,
        account_id: str,
        message_id: str,
        context_note: dict[str, Any],
        updated_at: datetime,
    ) -> int:
        """落库「本次上下文说明」披露快照（Issue 27）。

        披露快照在生成中写入；终态后不变，历史回答保留当时切片版本。
        只允许写入仍在生成的助手消息，避免覆盖终态披露。
        """
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE messages SET context_note = ?, updated_at = ?"
                " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                (_json_dumps(context_note), _iso(updated_at), message_id, account_id),
            )
            return cursor.rowcount

    def update_message_humanizer(
        self,
        account_id: str,
        message_id: str,
        humanizer: dict[str, Any],
        updated_at: datetime,
    ) -> int:
        """落库人味化结果投影（Issue 28）；只允许写入仍在生成的助手消息。

        结果投影在终态前写入（skill 列），终态收敛不会覆盖；重试新尝试
        携带各自的结果投影。
        """
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE messages SET skill = ?, updated_at = ?"
                " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                (_json_dumps(humanizer), _iso(updated_at), message_id, account_id),
            )
            return cursor.rowcount

    def update_message_career_planning(
        self,
        account_id: str,
        message_id: str,
        career_planning: dict[str, Any],
        updated_at: datetime,
    ) -> int:
        """落库生涯规划结果投影（Issue 29）；只允许写入仍在生成的助手消息。

        结果投影在终态前写入（career_planning 列），终态收敛不会覆盖；
        重试新尝试携带各自的结果投影。
        """
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE messages SET career_planning = ?, updated_at = ?"
                " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                (_json_dumps(career_planning), _iso(updated_at), message_id, account_id),
            )
            return cursor.rowcount

    def update_message_read_aloud(
        self,
        account_id: str,
        message_id: str,
        read_aloud: dict[str, Any] | None,
    ) -> int:
        """落库朗读状态快照（Issue 30），不限定消息生成状态。

        朗读在消息终态之后发生：生成/失败/删除都写入同一列，幂等覆盖；
        None 表示清除（删除朗读后复位）。只按账户+消息定位，跨账户写
        入被作用域强制拒绝。
        """
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE messages SET read_aloud = ?, updated_at = ?"
                " WHERE message_id = ? AND account_id = ?",
                (
                    _json_dumps(read_aloud) if read_aloud is not None else None,
                    _iso(datetime.now(UTC)),
                    message_id,
                    account_id,
                ),
            )
            return cursor.rowcount

    def update_message_mcp_call(
        self,
        account_id: str,
        message_id: str,
        mcp_call: dict[str, Any],
        updated_at: datetime,
    ) -> int:
        """落库 MCP 调用结果投影（Issue 36），不限定消息生成状态。

        调用成功/失败在生成中写入、敏感挂起在终态后经确认写回最终结果，
        因此与朗读列一样按账户+消息定位幂等覆盖；跨账户写被作用域拒绝。
        """
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE messages SET mcp_call = ?, updated_at = ?"
                " WHERE message_id = ? AND account_id = ?",
                (_json_dumps(mcp_call), _iso(updated_at), message_id, account_id),
            )
            return cursor.rowcount

    def finalize_message(
        self,
        account_id: str,
        message_id: str,
        *,
        status: ChatMessageStatus,
        error_code: str | None,
        error_message: str | None,
        duration_ms: int,
        model_id: str | None,
        run_lock_id: str | None,
        updated_at: datetime,
        thinking: dict[str, list[str]] | None = None,
        web_search: dict[str, Any] | None = None,
        arxiv_search: dict[str, Any] | None = None,
        teaching: dict[str, Any] | None = None,
        persist_learning: Callable[[], None] | None = None,
    ) -> int:
        """把生成中的消息原子收敛到终态；仅 streaming → 目标状态，返回影响行数。

        ``thinking`` 为 None 时保留消息已有的思考摘要（陈旧收敛等不覆盖场景）。
        """
        with self._db.transaction():
            if arxiv_search is not None:
                assignments = [
                    "status = ?",
                    "error_code = ?",
                    "error_message = ?",
                    "duration_ms = ?",
                    "model_id = ?",
                    "run_lock_id = ?",
                    "updated_at = ?",
                ]
                values: list[Any] = [
                    status.value,
                    error_code,
                    error_message,
                    duration_ms,
                    model_id,
                    run_lock_id,
                    _iso(updated_at),
                ]
                if thinking is not None:
                    assignments.append("thinking = ?")
                    values.append(_json_dumps(thinking))
                if web_search is not None:
                    assignments.append("web_search = ?")
                    values.append(_json_dumps(web_search))
                assignments.append("arxiv_search = ?")
                values.append(_json_dumps(arxiv_search))
                values.extend([message_id, account_id])
                cursor = self._db.scoped(account_id).execute(
                    "UPDATE messages SET "
                    + ", ".join(assignments)
                    + " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                    values,
                )
            elif thinking is None and web_search is None:
                cursor = self._db.scoped(account_id).execute(
                    "UPDATE messages SET status = ?, error_code = ?,"
                    " error_message = ?, duration_ms = ?, model_id = ?,"
                    " run_lock_id = ?, updated_at = ?"
                    " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                    (
                        status.value,
                        error_code,
                        error_message,
                        duration_ms,
                        model_id,
                        run_lock_id,
                        _iso(updated_at),
                        message_id,
                        account_id,
                    ),
                )
            elif thinking is None:
                assert web_search is not None
                cursor = self._db.scoped(account_id).execute(
                    "UPDATE messages SET status = ?, error_code = ?,"
                    " error_message = ?, duration_ms = ?, model_id = ?,"
                    " run_lock_id = ?, updated_at = ?, web_search = ?"
                    " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                    (
                        status.value,
                        error_code,
                        error_message,
                        duration_ms,
                        model_id,
                        run_lock_id,
                        _iso(updated_at),
                        _json_dumps(web_search),
                        message_id,
                        account_id,
                    ),
                )
            elif web_search is None:
                cursor = self._db.scoped(account_id).execute(
                    "UPDATE messages SET status = ?, error_code = ?,"
                    " error_message = ?, duration_ms = ?, model_id = ?,"
                    " run_lock_id = ?, updated_at = ?, thinking = ?"
                    " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                    (
                        status.value,
                        error_code,
                        error_message,
                        duration_ms,
                        model_id,
                        run_lock_id,
                        _iso(updated_at),
                        _json_dumps(thinking),
                        message_id,
                        account_id,
                    ),
                )
            else:
                cursor = self._db.scoped(account_id).execute(
                    "UPDATE messages SET status = ?, error_code = ?,"
                    " error_message = ?, duration_ms = ?, model_id = ?,"
                    " run_lock_id = ?, updated_at = ?, thinking = ?, web_search = ?"
                    " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                    (
                        status.value,
                        error_code,
                        error_message,
                        duration_ms,
                        model_id,
                        run_lock_id,
                        _iso(updated_at),
                        _json_dumps(thinking),
                        _json_dumps(web_search),
                        message_id,
                        account_id,
                    ),
                )
            if cursor.rowcount and teaching is not None:
                self._db.scoped(account_id).execute(
                    "UPDATE messages SET teaching = ?"
                    " WHERE message_id = ? AND account_id = ? AND status = ?",
                    (_json_dumps(teaching), message_id, account_id, status.value),
                )
            if cursor.rowcount and persist_learning is not None:
                persist_learning()
            return cursor.rowcount

    def message_count(self, account_id: str, conversation_id: str) -> int:
        row = self._db.scoped(account_id).execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    # -- answer feedback (Issue 27) ----------------------------------------

    def save_feedback(self, feedback: AnswerFeedback) -> AnswerFeedback:
        """持久化一条回答反馈；按账户隔离。"""
        try:
            with self._db.transaction():
                self._db.scoped(feedback.account_id).execute(
                    "INSERT INTO answer_feedback"
                    "(feedback_id, account_id, conversation_id, message_id, kind,"
                    " feedback_text, preference, assertion_id, career_item_ref,"
                    " status, resolution_note, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        feedback.feedback_id,
                        feedback.account_id,
                        feedback.conversation_id,
                        feedback.message_id,
                        feedback.kind.value,
                        feedback.feedback_text,
                        feedback.preference,
                        feedback.assertion_id,
                        feedback.career_item_ref,
                        feedback.status.value,
                        feedback.resolution_note,
                        _iso(feedback.created_at),
                        _iso(feedback.updated_at),
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            raise StorageError("保存反馈失败，请稍后重试。") from exc
        return feedback

    def find_duplicate_feedback(
        self,
        account_id: str,
        message_id: str,
        kind: FeedbackKind,
        assertion_id: str | None,
        career_item_ref: str | None,
        feedback_text: str,
    ) -> AnswerFeedback | None:
        """幂等去重：同一账户对同一消息的相同反馈只保留一条。"""
        row = self._db.scoped(account_id).execute(
            "SELECT feedback_id, account_id, conversation_id, message_id, kind,"
            " feedback_text, preference, assertion_id, career_item_ref, status,"
            " resolution_note, created_at, updated_at"
            " FROM answer_feedback WHERE account_id = ? AND message_id = ?"
            " AND kind = ? AND assertion_id IS ? AND career_item_ref IS ?"
            " AND feedback_text = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (
                account_id,
                message_id,
                kind.value,
                assertion_id,
                career_item_ref,
                feedback_text,
            ),
        ).fetchone()
        if row is None:
            return None
        return self._feedback_from_row(row)

    def list_feedback(
        self, account_id: str, conversation_id: str
    ) -> list[AnswerFeedback]:
        """按对话列出该账户的反馈，最新在前（供前端恢复与闭环查看）。"""
        rows = self._db.scoped(account_id).execute(
            "SELECT feedback_id, account_id, conversation_id, message_id, kind,"
            " feedback_text, preference, assertion_id, career_item_ref, status,"
            " resolution_note, created_at, updated_at"
            " FROM answer_feedback WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY created_at DESC",
            (account_id, conversation_id),
        ).fetchall()
        return [self._feedback_from_row(row) for row in rows]

    def get_feedback(self, account_id: str, feedback_id: str) -> AnswerFeedback | None:
        row = self._db.scoped(account_id).execute(
            "SELECT feedback_id, account_id, conversation_id, message_id, kind,"
            " feedback_text, preference, assertion_id, career_item_ref, status,"
            " resolution_note, created_at, updated_at"
            " FROM answer_feedback WHERE feedback_id = ? AND account_id = ?",
            (feedback_id, account_id),
        ).fetchone()
        if row is None:
            return None
        return self._feedback_from_row(row)

    def mark_feedback_status(
        self,
        account_id: str,
        feedback_id: str,
        status: FeedbackStatus,
        resolution_note: str | None,
        updated_at: datetime,
    ) -> AnswerFeedback | None:
        """更新反馈生命周期状态（resolved），返回更新后的记录。"""
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE answer_feedback SET status = ?, resolution_note = ?,"
                " updated_at = ? WHERE feedback_id = ? AND account_id = ?",
                (status.value, resolution_note, _iso(updated_at), feedback_id, account_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_feedback(account_id, feedback_id)

    @staticmethod
    def _feedback_from_row(row: Any) -> AnswerFeedback:
        return AnswerFeedback(
            feedback_id=str(row["feedback_id"]),
            account_id=str(row["account_id"]),
            conversation_id=str(row["conversation_id"]),
            message_id=str(row["message_id"]),
            kind=FeedbackKind(str(row["kind"])),
            feedback_text=str(row["feedback_text"]),
            preference=(
                str(row["preference"]) if row["preference"] is not None else None
            ),
            assertion_id=(
                str(row["assertion_id"]) if row["assertion_id"] is not None else None
            ),
            career_item_ref=(
                str(row["career_item_ref"])
                if row["career_item_ref"] is not None
                else None
            ),
            status=FeedbackStatus(str(row["status"])),
            resolution_note=(
                str(row["resolution_note"]) if row["resolution_note"] is not None else None
            ),
            created_at=_parse_iso(str(row["created_at"])),
            updated_at=_parse_iso(str(row["updated_at"])),
        )

    # -- model run locks ---------------------------------------------------

    def insert_run_lock(self, account_id: str, lock: ModelRunLock) -> None:
        """持久化不可变模型运行锁，绑定稳定账户 ID。"""
        with self._db.transaction():
            self._db.scoped(account_id).execute(
                "INSERT INTO model_run_locks"
                "(lock_id, account_id, capability_name, capability_version,"
                " actual_model_id, region, status, error_code, error_message,"
                " usage, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    lock.lock_id,
                    account_id,
                    lock.capability_name,
                    lock.capability_version,
                    lock.actual_model_id,
                    lock.region,
                    lock.status.value,
                    lock.error_code,
                    lock.error_message,
                    _json_dumps(lock.usage) if lock.usage is not None else None,
                    _iso(lock.created_at),
                ),
            )

    # -- generation runs (Issue 02) ---------------------------------------

    def _insert_turn_locked(
        self,
        user_record: MessageRecord,
        assistant_record: MessageRecord,
        run_record: GenerationRunRecord,
        events: list[tuple[str, dict[str, Any]]],
        attachment_ids: list[str] | None = None,
    ) -> None:
        """事务内的轮次落库：消息、运行、初始事件与附件绑定（须已持锁）。

        Issue 03 把本段从 ``insert_generation_turn`` 抽为私有辅助，供
        续轮（02）与原子首轮（03）在同一事务边界内复用；调用方负责
        开启/提交事务与幂等语义。
        """
        for record in (user_record, assistant_record):
            self._db.scoped(record.account_id).execute(
                "INSERT INTO messages"
                "(message_id, conversation_id, account_id, role, attempt_number,"
                " status, content, thinking, error_code, error_message,"
                " duration_ms, model_id, run_lock_id, created_at, updated_at,"
                " web_search, arxiv_search, teaching, context_note, skill,"
                " career_planning, image, video, mcp_call, route)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.message_id,
                    record.conversation_id,
                    record.account_id,
                    record.role.value,
                    record.attempt_number,
                    record.status.value,
                    record.content,
                    _json_dumps(record.thinking) if record.thinking else None,
                    record.error_code,
                    record.error_message,
                    record.duration_ms,
                    record.model_id,
                    record.run_lock_id,
                    _iso(record.created_at),
                    _iso(record.updated_at),
                    _json_dumps(record.web_search) if record.web_search else None,
                    _json_dumps(record.arxiv_search)
                    if record.arxiv_search
                    else None,
                    _json_dumps(record.teaching) if record.teaching else None,
                    _json_dumps(record.context_note)
                    if record.context_note
                    else None,
                    _json_dumps(record.skill) if record.skill else None,
                    _json_dumps(record.career_planning)
                    if record.career_planning
                    else None,
                    _json_dumps(record.image) if record.image else None,
                    _json_dumps(record.video) if record.video else None,
                    _json_dumps(record.mcp_call) if record.mcp_call else None,
                    _json_dumps(record.route) if record.route else None,
                ),
            )
            self._persist_message_route(record)
        self._insert_generation_run_and_events(
            user_record.account_id, run_record, events
        )
        if attachment_ids:
            placeholders = ",".join("?" for _ in attachment_ids)
            rows = self._db.scoped(user_record.account_id).execute(
                "SELECT object_id FROM chat_attachments"
                " WHERE account_id = ? AND conversation_id = ?"
                " AND message_id IS NULL AND status = 'uploaded'"
                f" AND object_id IN ({placeholders})",
                (
                    user_record.account_id,
                    user_record.conversation_id,
                    *attachment_ids,
                ),
            ).fetchall()
            if {str(row["object_id"]) for row in rows} != set(attachment_ids):
                raise StorageError("附件不存在或没有访问权限。")
            now = _iso(user_record.updated_at)
            for object_id in attachment_ids:
                self._db.scoped(user_record.account_id).execute(
                    "UPDATE chat_attachments SET message_id = ?, status = 'bound',"
                    " updated_at = ? WHERE object_id = ? AND account_id = ?"
                    " AND conversation_id = ? AND message_id IS NULL",
                    (
                        user_record.message_id,
                        now,
                        object_id,
                        user_record.account_id,
                        user_record.conversation_id,
                    ),
                )

    def insert_generation_turn(
        self,
        user_record: MessageRecord,
        assistant_record: MessageRecord,
        run_record: GenerationRunRecord,
        events: list[tuple[str, dict[str, Any]]],
        attachment_ids: list[str] | None = None,
        *,
        lock_mode: bool = False,
    ) -> None:
        """同一事务原子创建一轮消息与 queued 运行（Issue 02 纵向切片）。

        用户消息、助手占位、运行记录、运行初始事件（started/profile）与
        generation 队列行要么全部落库、要么全部回滚——发送请求返回前
        运行已可被后台执行器领取，绝不出现「消息已保存但运行缺失」或
        反之的半状态。``attachment_ids`` 非空时在同一事务内绑定附件。
        """
        try:
            with self._db.transaction():
                if lock_mode:
                    already_started = self._db.scoped(user_record.account_id).execute(
                        "SELECT 1 FROM messages"
                        " WHERE conversation_id = ? AND account_id = ? LIMIT 1",
                        (user_record.conversation_id, user_record.account_id),
                    ).fetchone()
                    if already_started is not None:
                        raise ConversationModeLockConflict(
                            "首条消息已提交，不能重复创建会话首轮。"
                        )
                    locked = self._db.scoped(user_record.account_id).execute(
                        "UPDATE conversations SET mode_locked = 1, updated_at = ?"
                        " WHERE conversation_id = ? AND account_id = ? AND mode_locked = 0",
                        (
                            _iso(user_record.updated_at),
                            user_record.conversation_id,
                            user_record.account_id,
                        ),
                    )
                    if locked.rowcount != 1:
                        raise StorageError("对话不存在或模式已锁定。")
                self._insert_turn_locked(
                    user_record, assistant_record, run_record, events, attachment_ids
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StorageError("保存消息与生成运行失败，请稍后重试。") from exc

    def insert_first_turn(
        self,
        *,
        account_id: str,
        conversation_id: str,
        idempotency_key: str,
        title: str,
        mode: str,
        created_at: datetime,
        project_id: str | None,
        plugin_selection: list[dict[str, Any]] | None,
        user_record: MessageRecord,
        assistant_record: MessageRecord,
        run_record: GenerationRunRecord,
        events: list[tuple[str, dict[str, Any]]],
        attachment_ids: list[str] | None = None,
    ) -> tuple[str, bool, bool]:
        """同一事务原子创建会话与首轮（Issue 03，幂等）。

        会话、用户消息、助手占位、queued 运行、初始事件与附件绑定要么
        全部落库、要么全部回滚——首轮请求返回前运行已可被后台执行器
        领取。``idempotency_key`` 在事务内先查后插（BEGIN IMMEDIATE
        串行化并发），同账户同键重放返回已存在会话且不产生任何新数据；
        唯一索引 ``(account_id, idempotency_key)`` 为兜底约束。

        返回 ``(conversation_id, created, conflict_not_empty)``：
        ``created`` 为 False 表示幂等命中（调用方应按重放组装投影，
        不执行首轮副作用）；``conflict_not_empty`` 表示指定会话已有
        消息（并发不同键首轮同一预建会话时由事务内检查拦截，调用方
        按 409 拒绝——检查与写入同持写锁，杜绝双发）。
        """
        try:
            with self._db.transaction():
                existing = self._db.scoped(account_id).execute(
                    "SELECT conversation_id FROM conversations"
                    " WHERE account_id = ? AND idempotency_key = ?",
                    (account_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return str(existing["conversation_id"]), False, False
                # 指定了已预建的空会话（附件上传路径先建会话再发送）时
                # 复用并写入首轮参数；缺省在事务内新建会话。非空会话
                # 作为首轮目标按冲突返回（幂等重放已在上面短路，因此
                # 这里只可能是并发不同键双发）。
                prebuilt = self._db.scoped(account_id).execute(
                    "SELECT mode, mode_locked FROM conversations"
                    " WHERE conversation_id = ? AND account_id = ?",
                    (conversation_id, account_id),
                ).fetchone()
                if prebuilt is not None:
                    if bool(prebuilt["mode_locked"]) and str(prebuilt["mode"]) != mode:
                        return conversation_id, False, True
                    already_started = self._db.scoped(account_id).execute(
                        "SELECT 1 FROM messages"
                        " WHERE conversation_id = ? AND account_id = ? LIMIT 1",
                        (conversation_id, account_id),
                    ).fetchone()
                    if already_started is not None:
                        return conversation_id, False, True
                    self._db.scoped(account_id).execute(
                        "UPDATE conversations SET title = ?, mode = ?, mode_locked = 1,"
                        " project_id = ?, plugin_selection = ?,"
                        " idempotency_key = ?, updated_at = ?"
                        " WHERE conversation_id = ? AND account_id = ?",
                        (
                            title,
                            mode,
                            project_id,
                            _json_dumps(plugin_selection)
                            if plugin_selection
                            else None,
                            idempotency_key,
                            _iso(created_at),
                            conversation_id,
                            account_id,
                        ),
                    )
                else:
                    self._db.scoped(account_id).execute(
                        "INSERT INTO conversations"
                        "(conversation_id, account_id, title, mode, mode_locked, pinned, project_id,"
                        " plugin_selection, idempotency_key, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?)",
                        (
                            conversation_id,
                            account_id,
                            title,
                            mode,
                            project_id,
                            _json_dumps(plugin_selection)
                            if plugin_selection
                            else None,
                            idempotency_key,
                            _iso(created_at),
                            _iso(created_at),
                        ),
                    )
                self._insert_turn_locked(
                    user_record,
                    assistant_record,
                    run_record,
                    events,
                    attachment_ids,
                )
            return conversation_id, True, False
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StorageError("保存首轮会话失败，请稍后重试。") from exc

    def insert_generation_attempt(
        self,
        assistant_record: MessageRecord,
        run_record: GenerationRunRecord,
        events: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """同一事务原子创建新助手尝试与 queued 运行（重试路径）。

        用户消息已存在于上一条尝试，本方法只插入新尝试、运行、初始事件
        与队列行——绝不重复插入用户消息或改写历史。
        """
        try:
            with self._db.transaction():
                self._db.scoped(assistant_record.account_id).execute(
                    "INSERT INTO messages"
                    "(message_id, conversation_id, account_id, role, attempt_number,"
                    " status, content, thinking, error_code, error_message,"
                    " duration_ms, model_id, run_lock_id, created_at, updated_at,"
                    " web_search, arxiv_search, teaching, context_note, skill,"
                    " career_planning, image, video, mcp_call, route)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        assistant_record.message_id,
                        assistant_record.conversation_id,
                        assistant_record.account_id,
                        assistant_record.role.value,
                        assistant_record.attempt_number,
                        assistant_record.status.value,
                        assistant_record.content,
                        (
                            _json_dumps(assistant_record.thinking)
                            if assistant_record.thinking
                            else None
                        ),
                        assistant_record.error_code,
                        assistant_record.error_message,
                        assistant_record.duration_ms,
                        assistant_record.model_id,
                        assistant_record.run_lock_id,
                        _iso(assistant_record.created_at),
                        _iso(assistant_record.updated_at),
                        (
                            _json_dumps(assistant_record.web_search)
                            if assistant_record.web_search
                            else None
                        ),
                        (
                            _json_dumps(assistant_record.arxiv_search)
                            if assistant_record.arxiv_search
                            else None
                        ),
                        (
                            _json_dumps(assistant_record.teaching)
                            if assistant_record.teaching
                            else None
                        ),
                        (
                            _json_dumps(assistant_record.context_note)
                            if assistant_record.context_note
                            else None
                        ),
                        _json_dumps(assistant_record.skill)
                        if assistant_record.skill
                        else None,
                        (
                            _json_dumps(assistant_record.career_planning)
                            if assistant_record.career_planning
                            else None
                        ),
                        _json_dumps(assistant_record.image)
                        if assistant_record.image
                        else None,
                        _json_dumps(assistant_record.video)
                        if assistant_record.video
                        else None,
                        _json_dumps(assistant_record.mcp_call)
                        if assistant_record.mcp_call
                        else None,
                        _json_dumps(assistant_record.route)
                        if assistant_record.route
                        else None,
                    ),
                )
                self._persist_message_route(assistant_record)
                self._insert_generation_run_and_events(
                    assistant_record.account_id, run_record, events
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StorageError("保存生成运行失败，请稍后重试。") from exc

    def _insert_generation_run_and_events(
        self,
        account_id: str,
        run_record: GenerationRunRecord,
        events: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """事务内写入运行行、初始事件并登记领取队列（调用方持有事务）。"""
        self._insert_generation_run_now(run_record)
        self._enqueue_generation_now(run_record.run_id, account_id)
        for kind, payload in events:
            seq = self._next_event_seq(account_id, run_record.run_id)
            self._db.scoped(account_id).execute(
                "INSERT INTO generation_events"
                "(run_id, seq, account_id, kind, payload, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_record.run_id,
                    seq,
                    account_id,
                    kind,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    _iso(run_record.updated_at),
                ),
            )

    def _insert_generation_run_now(self, record: GenerationRunRecord) -> None:
        """事务内写入运行行（调用方已持有事务）。"""
        self._db.scoped(record.account_id).execute(
            "INSERT INTO generation_runs"
            "(run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, attempt_number, status, stage,"
            " config_json, lease_owner, lease_expires_at, attempt_count,"
            " stop_requested, error_code, error_message, duration_ms,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.run_id,
                record.account_id,
                record.conversation_id,
                record.user_message_id,
                record.assistant_message_id,
                record.attempt_number,
                record.status,
                record.stage,
                _json_dumps(record.config) if record.config else None,
                record.lease_owner,
                _iso(record.lease_expires_at) if record.lease_expires_at else None,
                record.attempt_count,
                1 if record.stop_requested else 0,
                record.error_code,
                record.error_message,
                record.duration_ms,
                _iso(record.created_at),
                _iso(record.updated_at),
            ),
        )

    def _enqueue_generation_now(self, run_id: str, account_id: str) -> None:
        """事务内把运行登记进统一领取队列（系统级 task_claims 表）。"""
        self._db.connection.execute(
            "INSERT INTO task_claims(claim_id, queue_name, task_key, attempt,"
            " status, payload_json, created_at, updated_at)"
            " VALUES (?, 'generation', ?, 0, 'queued', ?, ?, ?)"
            " ON CONFLICT(queue_name, task_key) DO UPDATE SET"
            " payload_json = excluded.payload_json, updated_at = excluded.updated_at,"
            " status = 'queued', attempt = 0, next_retry_at = NULL"
            " WHERE task_claims.status != 'claimed'",
            (
                f"cl-gen-{secrets.token_urlsafe(16)}",
                f"generation:{run_id}",
                json.dumps({"run_id": run_id, "account_id": account_id}),
                _iso(datetime.now(UTC)),
                _iso(datetime.now(UTC)),
            ),
        )

    def _next_event_seq(self, account_id: str, run_id: str) -> int:
        row = self._db.scoped(account_id).execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq"
            " FROM generation_events WHERE run_id = ? AND account_id = ?",
            (run_id, account_id),
        ).fetchone()
        return int(row["next_seq"]) if row is not None else 1

    def append_generation_event(
        self,
        account_id: str,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        created_at: datetime,
    ) -> int:
        """向运行追加一条游标事件，返回新 seq；须在调用方事务内执行。

        运行必须属于当前账户（先按账户定位运行），跨账户追加返回 0。
        """
        with self._db.transaction():
            run = self._db.scoped(account_id).execute(
                "SELECT 1 FROM generation_runs WHERE run_id = ? AND account_id = ?",
                (run_id, account_id),
            ).fetchone()
            if run is None:
                return 0
            row = self._db.scoped(account_id).execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq"
                " FROM generation_events WHERE run_id = ? AND account_id = ?",
                (run_id, account_id),
            ).fetchone()
            seq = int(row["next_seq"]) if row is not None else 1
            self._db.scoped(account_id).execute(
                "INSERT INTO generation_events"
                "(run_id, seq, account_id, kind, payload, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    seq,
                    account_id,
                    kind,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    _iso(created_at),
                ),
            )
            return seq

    def get_generation_run(
        self, account_id: str, run_id: str
    ) -> GenerationRunRecord | None:
        row = self._db.scoped(account_id).execute(
            "SELECT run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, attempt_number, status, stage,"
            " config_json, lease_owner, lease_expires_at, attempt_count,"
            " stop_requested, error_code, error_message, duration_ms,"
            " created_at, updated_at"
            " FROM generation_runs WHERE run_id = ? AND account_id = ?",
            (run_id, account_id),
        ).fetchone()
        if row is None:
            return None
        return self._run_from_row(row)

    def get_run_by_message(
        self, account_id: str, message_id: str
    ) -> GenerationRunRecord | None:
        """返回该助手消息最近一次运行（任意状态，页面恢复用）。"""
        row = self._db.scoped(account_id).execute(
            "SELECT run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, attempt_number, status, stage,"
            " config_json, lease_owner, lease_expires_at, attempt_count,"
            " stop_requested, error_code, error_message, duration_ms,"
            " created_at, updated_at"
            " FROM generation_runs WHERE assistant_message_id = ?"
            " AND account_id = ? ORDER BY created_at DESC, run_id LIMIT 1",
            (message_id, account_id),
        ).fetchone()
        if row is None:
            return None
        return self._run_from_row(row)

    def list_active_runs(
        self, account_id: str, conversation_id: str
    ) -> list[GenerationRunRecord]:
        """会话内尚未终态的运行（queued/running）；读取收敛的活跃判定源。"""
        rows = self._db.scoped(account_id).execute(
            "SELECT run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, attempt_number, status, stage,"
            " config_json, lease_owner, lease_expires_at, attempt_count,"
            " stop_requested, error_code, error_message, duration_ms,"
            " created_at, updated_at"
            " FROM generation_runs WHERE conversation_id = ? AND account_id = ?"
            " AND status IN ('queued', 'running')"
            " ORDER BY created_at, run_id",
            (conversation_id, account_id),
        ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def delete_run_by_message(self, account_id: str, message_id: str) -> int:
        """删除消息关联的运行与事件（测试构造遗留数据/运维清理路径）。"""
        with self._db.transaction():
            rows = self._db.scoped(account_id).execute(
                "SELECT run_id FROM generation_runs"
                " WHERE assistant_message_id = ? AND account_id = ?",
                (message_id, account_id),
            ).fetchall()
            for row in rows:
                self._db.scoped(account_id).execute(
                    "DELETE FROM generation_events WHERE run_id = ? AND account_id = ?",
                    (str(row["run_id"]), account_id),
                )
            cursor = self._db.scoped(account_id).execute(
                "DELETE FROM generation_runs WHERE assistant_message_id = ?"
                " AND account_id = ?",
                (message_id, account_id),
            )
            return cursor.rowcount

    def last_generation_event_seq(self, account_id: str, run_id: str) -> int:
        row = self._db.scoped(account_id).execute(
            "SELECT COALESCE(MAX(seq), 0) AS seq FROM generation_events"
            " WHERE run_id = ? AND account_id = ?",
            (run_id, account_id),
        ).fetchone()
        return int(row["seq"]) if row is not None else 0

    def list_generation_events(
        self, account_id: str, run_id: str, after_seq: int
    ) -> list[GenerationEventRecord]:
        rows = self._db.scoped(account_id).execute(
            "SELECT run_id, seq, kind, payload, created_at FROM generation_events"
            " WHERE run_id = ? AND account_id = ? AND seq > ?"
            " ORDER BY seq",
            (run_id, account_id, after_seq),
        ).fetchall()
        return [
            GenerationEventRecord(
                run_id=str(row["run_id"]),
                seq=int(row["seq"]),
                kind=str(row["kind"]),
                payload=_json_loads_any(row["payload"]) or {},
                created_at=_parse_iso(str(row["created_at"])),
            )
            for row in rows
        ]

    def claim_generation_run(
        self,
        account_id: str,
        run_id: str,
        *,
        owner: str,
        lease_expires_at: datetime,
        max_attempts: int = 2,
    ) -> int:
        """原子领取运行（queued 领取或租约过期恢复），返回行数。

        Issue 02 租约语义：queued 直接领取；running 且租约过期时按崩溃
        恢复领取（attempt_count 递增）。``max_attempts`` 是执行尝试上限
        （默认 2 = 1 次原始 + 1 次恢复），达到上限的运行不再被领取，由
        收尸收敛为可重试失败——绝不永久 running。领取在 BEGIN IMMEDIATE
        事务内完成，多个执行器竞争时只有一个成功（唯一执行模型调用）。
        """
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE generation_runs SET status = 'running', lease_owner = ?,"
                " lease_expires_at = ?, attempt_count = attempt_count + 1,"
                " updated_at = ? WHERE run_id = ? AND account_id = ?"
                " AND attempt_count < ? AND (status = 'queued'"
                " OR (status = 'running' AND lease_expires_at IS NOT NULL"
                "     AND lease_expires_at < ?))",
                (
                    owner,
                    _iso(lease_expires_at),
                    _iso(datetime.now(UTC)),
                    run_id,
                    account_id,
                    max_attempts,
                    _iso(datetime.now(UTC)),
                ),
            )
            return cursor.rowcount

    def update_generation_stage(
        self, account_id: str, run_id: str, stage: str
    ) -> int:
        """更新运行当前阶段（Issue 02 规格：每次运行保存当前阶段）。"""
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE generation_runs SET stage = ?, updated_at = ?"
                " WHERE run_id = ? AND account_id = ? AND status = 'running'",
                (stage, _iso(datetime.now(UTC)), run_id, account_id),
            )
            return cursor.rowcount

    def update_generation_config(
        self, account_id: str, run_id: str, config: dict[str, Any]
    ) -> int:
        """保存运行的确定性输入快照，供恢复与用户重试复用。"""
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE generation_runs SET config_json = ?, updated_at = ?"
                " WHERE run_id = ? AND account_id = ?"
                " AND status IN ('queued', 'running')",
                (
                    _json_dumps(config),
                    _iso(datetime.now(UTC)),
                    run_id,
                    account_id,
                ),
            )
            return cursor.rowcount

    def renew_generation_lease(
        self, account_id: str, run_id: str, lease_expires_at: datetime
    ) -> int:
        """续期运行租约（长生成心跳），仅限 running 状态生效。"""
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE generation_runs SET lease_expires_at = ?, updated_at = ?"
                " WHERE run_id = ? AND account_id = ? AND status = 'running'",
                (
                    _iso(lease_expires_at),
                    _iso(datetime.now(UTC)),
                    run_id,
                    account_id,
                ),
            )
            return cursor.rowcount

    def request_generation_stop(self, account_id: str, run_id: str) -> int:
        """写入停止请求（仅非终态运行生效）；执行器在安全检查点收敛。"""
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE generation_runs SET stop_requested = 1, updated_at = ?"
                " WHERE run_id = ? AND account_id = ? AND status IN ('queued', 'running')",
                (_iso(datetime.now(UTC)), run_id, account_id),
            )
            return cursor.rowcount

    def finalize_generation_run(
        self,
        account_id: str,
        run_id: str,
        *,
        status: str,
        error_code: str | None,
        error_message: str | None,
        duration_ms: int | None,
        now: datetime,
    ) -> int:
        """把运行原子收敛到终态；仅 queued/running → 目标状态，返回行数。

        与消息终态收敛同一守卫语义：最多一个尝试可提交终态。
        """
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE generation_runs SET status = ?, error_code = ?,"
                " error_message = ?, duration_ms = ?, lease_owner = NULL,"
                " lease_expires_at = NULL, updated_at = ?"
                " WHERE run_id = ? AND account_id = ? AND status IN ('queued', 'running')",
                (
                    status,
                    error_code,
                    error_message,
                    duration_ms,
                    _iso(now),
                    run_id,
                    account_id,
                ),
            )
            return cursor.rowcount

    def performance_summary(
        self, account_id: str, since: datetime | None = None
    ) -> dict[str, Any]:
        """本地性能摘要（Issue 06 T6）：p50/p95、超时率、阶段占比、重试。

        全部脱敏：只聚合 ID/毫秒/状态/类别，绝不返回消息、文档、搜索
        结果或密钥正文；不建立任何遥测外传，摘要仅供本地性能观测与
        防代码回归断言（本地确定性适配器 p95 首 token ≤ 2s、终态 ≤ 5s）。
        """
        since_sql = " AND created_at >= ?" if since is not None else ""
        params: list[Any] = [account_id]
        if since is not None:
            params.append(_iso(since))
        rows = self._db.scoped(account_id).execute(
            "SELECT status, error_code, duration_ms, attempt_count"
            " FROM generation_runs WHERE account_id = ?"
            " AND status IN ('done', 'failed', 'stopped')" + since_sql,
            params,
        ).fetchall()
        durations = [
            int(row["duration_ms"]) for row in rows if row["duration_ms"] is not None
        ]
        timeout_count = sum(
            1
            for row in rows
            if row["error_code"] is not None and "timeout" in str(row["error_code"])
        )
        retry_count = sum(
            max(0, int(row["attempt_count"] or 1) - 1) for row in rows
        )
        stage_params: list[Any] = [account_id]
        if since is not None:
            stage_params.append(_iso(since))
        stage_rows = self._db.scoped(account_id).execute(
            "SELECT payload FROM generation_events"
            " WHERE account_id = ? AND kind = 'stage'" + since_sql,
            stage_params,
        ).fetchall()
        stage_totals: dict[str, dict[str, Any]] = {}
        first_tokens: list[int] = []
        for row in stage_rows:
            payload = _json_loads_any(row["payload"]) or {}
            stage = str(payload.get("stage", ""))
            if not stage:
                continue
            entry = stage_totals.setdefault(stage, {"duration_ms": 0, "count": 0})
            # 只统计结束事件（active 不计入次数；阶段占比按耗时聚合）
            if payload.get("status") == "done":
                entry["count"] += 1
            duration = payload.get("duration_ms")
            if isinstance(duration, int):
                entry["duration_ms"] += duration
            first_token = payload.get("first_token_ms")
            if isinstance(first_token, int):
                first_tokens.append(first_token)
        total_stage_ms = sum(
            entry["duration_ms"] for entry in stage_totals.values()
        )
        return {
            "run_count": len(rows),
            "duration_ms": {
                "p50": _percentile(durations, 50),
                "p95": _percentile(durations, 95),
            },
            "first_token_ms": {
                "p50": _percentile(first_tokens, 50),
                "p95": _percentile(first_tokens, 95),
            },
            "timeout_rate": (
                round(timeout_count / len(rows), 4) if rows else 0.0
            ),
            "retry_count": retry_count,
            "stage_share": {
                stage: {
                    "duration_ms": entry["duration_ms"],
                    "count": entry["count"],
                    "share": (
                        round(entry["duration_ms"] / total_stage_ms, 4)
                        if total_stage_ms
                        else 0.0
                    ),
                }
                for stage, entry in sorted(stage_totals.items())
            },
        }

    def expire_overdue_runs(
        self, max_attempts: int, now: datetime
    ) -> list[GenerationRunRecord]:
        """收尸：租约过期且尝试已到上限的运行收敛为可重试失败。

        系统级收敛（跨账户调度，不经账户作用域）：持有运行的执行器
        被强制退出后，租约到期时若已无恢复预算，必须给出明确可重试
        终态而不是永久 running。返回被收尸的运行记录（执行器据此
        收敛消息并补发终态事件，订阅端绝不悬挂）。
        """
        with self._db.transaction():
            rows = self._db.connection.execute(
                "SELECT run_id, account_id, conversation_id, user_message_id,"
                " assistant_message_id, attempt_number, status, stage,"
                " config_json, lease_owner, lease_expires_at, attempt_count,"
                " stop_requested, error_code, error_message, duration_ms,"
                " created_at, updated_at FROM generation_runs"
                " WHERE status = 'running' AND lease_expires_at IS NOT NULL"
                " AND lease_expires_at < ? AND attempt_count >= ?",
                (_iso(now), max_attempts),
            ).fetchall()
            if not rows:
                return []
            self._db.connection.execute(
                "UPDATE generation_runs SET status = 'failed', error_code = ?,"
                " error_message = ?, lease_owner = NULL, lease_expires_at = NULL,"
                " updated_at = ? WHERE status = 'running' AND lease_expires_at IS NOT NULL"
                " AND lease_expires_at < ? AND attempt_count >= ?",
                (
                    "generation_worker_lost",
                    "生成进程意外退出，已保留已接收内容，可点击重试。",
                    _iso(now),
                    _iso(now),
                    max_attempts,
                ),
            )
        return [self._run_from_row(row) for row in rows]

    def request_stop_account_runs(self, account_id: str, now: datetime) -> int:
        """为该账户全部非终态运行写入停止请求（账户删除编排调用）。"""
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE generation_runs SET stop_requested = 1, updated_at = ?"
                " WHERE account_id = ? AND status IN ('queued', 'running')",
                (_iso(now), account_id),
            )
            return cursor.rowcount

    @staticmethod
    def _run_from_row(row: Any) -> GenerationRunRecord:
        return GenerationRunRecord(
            run_id=str(row["run_id"]),
            account_id=str(row["account_id"]),
            conversation_id=str(row["conversation_id"]),
            user_message_id=str(row["user_message_id"]),
            assistant_message_id=str(row["assistant_message_id"]),
            attempt_number=int(row["attempt_number"]),
            status=str(row["status"]),
            config=_json_loads_any(row["config_json"]),
            stage=(str(row["stage"]) if row["stage"] is not None else None),
            lease_owner=(
                str(row["lease_owner"]) if row["lease_owner"] is not None else None
            ),
            lease_expires_at=(
                _parse_iso(str(row["lease_expires_at"]))
                if row["lease_expires_at"] is not None
                else None
            ),
            attempt_count=int(row["attempt_count"]),
            stop_requested=bool(row["stop_requested"]),
            error_code=(
                str(row["error_code"]) if row["error_code"] is not None else None
            ),
            error_message=(
                str(row["error_message"]) if row["error_message"] is not None else None
            ),
            duration_ms=(
                int(row["duration_ms"]) if row["duration_ms"] is not None else None
            ),
            created_at=_parse_iso(str(row["created_at"])),
            updated_at=_parse_iso(str(row["updated_at"])),
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _message_from_row(row: Any) -> MessageRecord:
        return MessageRecord(
            message_id=str(row["message_id"]),
            conversation_id=str(row["conversation_id"]),
            account_id=str(row["account_id"]),
            role=ChatMessageRole(str(row["role"])),
            attempt_number=int(row["attempt_number"]),
            status=ChatMessageStatus(str(row["status"])),
            content=str(row["content"]),
            thinking=_json_loads(row["thinking"]),
            error_code=(
                str(row["error_code"]) if row["error_code"] is not None else None
            ),
            error_message=(
                str(row["error_message"]) if row["error_message"] is not None else None
            ),
            duration_ms=int(row["duration_ms"]) if row["duration_ms"] is not None else None,
            model_id=str(row["model_id"]) if row["model_id"] is not None else None,
            run_lock_id=(
                str(row["run_lock_id"]) if row["run_lock_id"] is not None else None
            ),
            created_at=_parse_iso(str(row["created_at"])),
            updated_at=_parse_iso(str(row["updated_at"])),
            web_search=_json_loads_any(row["web_search"]),
            arxiv_search=_json_loads_any(row["arxiv_search"]),
            teaching=_json_loads_any(row["teaching"]),
            context_note=_json_loads_any(row["context_note"]),
            skill=_json_loads_any(row["skill"]),
            career_planning=_json_loads_any(row["career_planning"]),
            read_aloud=_json_loads_any(row["read_aloud"]),
            image=_json_loads_any(row["image"]),
            video=_json_loads_any(row["video"]),
            mcp_call=_json_loads_any(row["mcp_call"]),
            route=_json_loads_any(row["route"]),
        )


def _json_loads(value: Any) -> dict[str, list[str]] | None:
    if value is None:
        return None
    try:
        import json

        parsed = json.loads(str(value))
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    cleaned: dict[str, list[str]] = {}
    for key, items in parsed.items():
        if isinstance(items, list) and all(isinstance(item, str) for item in items):
            cleaned[str(key)] = items
    return cleaned if cleaned else None


def _json_loads_list(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    try:
        import json

        parsed = json.loads(str(value))
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    cleaned = [item for item in parsed if isinstance(item, dict)]
    return cleaned or None


def _json_loads_any(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        import json

        parsed = json.loads(str(value))
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_dumps(value: dict[str, Any] | list[dict[str, Any]]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)
