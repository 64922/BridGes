"""账户隔离的对话、消息与模型运行锁仓库（bridges.db）。

与 ``BridgesObjectRepository`` 同一模式：所有查询强制按 ``account_id``
过滤，跨账户访问返回不存在；所有写入走显式事务边界。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bridges.contracts.ai import ModelRunLock
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
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
                    " web_search, arxiv_search)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        " web_search, arxiv_search)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            " model_id, run_lock_id, created_at, updated_at, web_search, arxiv_search"
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
            " model_id, run_lock_id, created_at, updated_at, web_search, arxiv_search"
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
            return cursor.rowcount

    def message_count(self, account_id: str, conversation_id: str) -> int:
        row = self._db.scoped(account_id).execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

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
