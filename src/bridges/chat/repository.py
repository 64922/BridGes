"""账户隔离的对话、消息与模型运行锁仓库（bridges.db）。

与 ``BridgesObjectRepository`` 同一模式：所有查询强制按 ``account_id``
过滤，跨账户访问返回不存在；所有写入走显式事务边界。
"""

from __future__ import annotations

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
    pinned: bool
    project_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class ModeEventRecord:
    event_id: str
    conversation_id: str
    account_id: str
    from_mode: str
    to_mode: str
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


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _iso(now: datetime) -> str:
    return now.isoformat()


class ConversationRepository:
    """对话/消息/运行锁的 SQLite 仓库，全部操作限定在账户内。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database

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
    ) -> None:
        updated_at = created_at
        try:
            with self._db.transaction():
                self._db.scoped(account_id).execute(
                    "INSERT INTO conversations"
                    "(conversation_id, account_id, title, mode, pinned, project_id,"
                    " created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
                    (
                        conversation_id,
                        account_id,
                        title,
                        mode,
                        project_id,
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
            "SELECT conversation_id, account_id, title, mode, pinned, project_id,"
            " created_at, updated_at"
            " FROM conversations WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        ).fetchone()
        if row is None:
            return None
        return self._conversation_from_row(row)

    def list_conversations(self, account_id: str) -> list[ConversationRecord]:
        rows = self._db.scoped(account_id).execute(
            "SELECT conversation_id, account_id, title, mode, pinned, project_id,"
            " created_at, updated_at FROM conversations WHERE account_id = ?"
            " ORDER BY pinned DESC, updated_at DESC, created_at DESC, conversation_id",
            (account_id,),
        ).fetchall()
        return [self._conversation_from_row(row) for row in rows]

    @staticmethod
    def _conversation_from_row(row: Any) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            account_id=str(row["account_id"]),
            title=str(row["title"]),
            mode=str(row["mode"]),
            pinned=bool(row["pinned"]),
            project_id=(str(row["project_id"]) if row["project_id"] is not None else None),
            created_at=_parse_iso(str(row["created_at"])),
            updated_at=_parse_iso(str(row["updated_at"])),
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
        updated_at: datetime,
    ) -> int:
        """更新会话元数据；每个字段更新都带账户条件，返回影响行数。"""
        assignments: list[str] = []
        values: list[Any] = []
        if title is not None:
            assignments.append("title = ?")
            values.append(title)
        if pinned is not None:
            assignments.append("pinned = ?")
            values.append(1 if pinned else 0)
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

    def set_conversation_mode(
        self, account_id: str, conversation_id: str, mode: str, updated_at: datetime
    ) -> None:
        """更新对话当前模式并刷新活动时间；切换只影响后续消息。"""
        with self._db.transaction():
            self._db.scoped(account_id).execute(
                "UPDATE conversations SET mode = ?, updated_at = ?"
                " WHERE conversation_id = ? AND account_id = ?",
                (mode, _iso(updated_at), conversation_id, account_id),
            )

    # -- mode events --------------------------------------------------------

    def insert_mode_event(
        self,
        *,
        event_id: str,
        conversation_id: str,
        account_id: str,
        from_mode: str,
        to_mode: str,
        created_at: datetime,
    ) -> None:
        """写入一条可见模式切换事件（按 created_at 与消息同序渲染）。"""
        try:
            with self._db.transaction():
                self._db.scoped(account_id).execute(
                    "INSERT INTO mode_events"
                    "(event_id, conversation_id, account_id, from_mode, to_mode,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event_id,
                        conversation_id,
                        account_id,
                        from_mode,
                        to_mode,
                        _iso(created_at),
                    ),
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StorageError("保存模式切换事件失败，请稍后重试。") from exc

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

    def insert_message(self, record: MessageRecord) -> None:
        try:
            with self._db.transaction():
                self._db.scoped(record.account_id).execute(
                    "INSERT INTO messages"
                    "(message_id, conversation_id, account_id, role, attempt_number,"
                    " status, content, thinking, error_code, error_message,"
                    " duration_ms, model_id, run_lock_id, created_at, updated_at,"
                    " web_search, arxiv_search, teaching, context_note, skill,"
                    " career_planning)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    ),
                )
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
                        " career_planning)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        ),
                    )
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
            " teaching, context_note, skill, career_planning, read_aloud"
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
            " teaching, context_note, skill, career_planning, read_aloud"
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


def _json_loads_any(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        import json

        parsed = json.loads(str(value))
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)
