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
    created_at: datetime
    updated_at: datetime


@dataclass
class MessageRecord:
    message_id: str
    conversation_id: str
    account_id: str
    role: ChatMessageRole
    attempt_number: int
    status: ChatMessageStatus
    content: str
    error_code: str | None
    error_message: str | None
    duration_ms: int | None
    model_id: str | None
    run_lock_id: str | None
    created_at: datetime
    updated_at: datetime


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
    ) -> None:
        updated_at = created_at
        try:
            with self._db.transaction():
                self._db.connection.execute(
                    "INSERT INTO conversations"
                    "(conversation_id, account_id, title, mode, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        conversation_id,
                        account_id,
                        title,
                        mode,
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
        row = self._db.connection.execute(
            "SELECT conversation_id, account_id, title, mode, created_at, updated_at"
            " FROM conversations WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        ).fetchone()
        if row is None:
            return None
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            account_id=str(row["account_id"]),
            title=str(row["title"]),
            mode=str(row["mode"]),
            created_at=_parse_iso(str(row["created_at"])),
            updated_at=_parse_iso(str(row["updated_at"])),
        )

    def list_conversations(self, account_id: str) -> list[ConversationRecord]:
        rows = self._db.connection.execute(
            "SELECT conversation_id, account_id, title, mode, created_at, updated_at"
            " FROM conversations WHERE account_id = ? ORDER BY updated_at DESC, created_at DESC",
            (account_id,),
        ).fetchall()
        return [
            ConversationRecord(
                conversation_id=str(row["conversation_id"]),
                account_id=str(row["account_id"]),
                title=str(row["title"]),
                mode=str(row["mode"]),
                created_at=_parse_iso(str(row["created_at"])),
                updated_at=_parse_iso(str(row["updated_at"])),
            )
            for row in rows
        ]

    def set_conversation_title(
        self, account_id: str, conversation_id: str, title: str, updated_at: datetime
    ) -> None:
        with self._db.transaction():
            self._db.connection.execute(
                "UPDATE conversations SET title = ?, updated_at = ?"
                " WHERE conversation_id = ? AND account_id = ?",
                (title, _iso(updated_at), conversation_id, account_id),
            )

    def touch_conversation(
        self, account_id: str, conversation_id: str, updated_at: datetime
    ) -> None:
        with self._db.transaction():
            self._db.connection.execute(
                "UPDATE conversations SET updated_at = ?"
                " WHERE conversation_id = ? AND account_id = ?",
                (_iso(updated_at), conversation_id, account_id),
            )

    # -- messages ----------------------------------------------------------

    def insert_message(self, record: MessageRecord) -> None:
        try:
            with self._db.transaction():
                self._db.connection.execute(
                    "INSERT INTO messages"
                    "(message_id, conversation_id, account_id, role, attempt_number,"
                    " status, content, error_code, error_message, duration_ms,"
                    " model_id, run_lock_id, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.message_id,
                        record.conversation_id,
                        record.account_id,
                        record.role.value,
                        record.attempt_number,
                        record.status.value,
                        record.content,
                        record.error_code,
                        record.error_message,
                        record.duration_ms,
                        record.model_id,
                        record.run_lock_id,
                        _iso(record.created_at),
                        _iso(record.updated_at),
                    ),
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StorageError("保存消息失败，请稍后重试。") from exc

    def list_messages(self, account_id: str, conversation_id: str) -> list[MessageRecord]:
        rows = self._db.connection.execute(
            "SELECT message_id, conversation_id, account_id, role, attempt_number,"
            " status, content, error_code, error_message, duration_ms, model_id,"
            " run_lock_id, created_at, updated_at"
            " FROM messages WHERE conversation_id = ? AND account_id = ?"
            " ORDER BY created_at, CASE role WHEN 'user' THEN 0 ELSE 1 END, message_id",
            (conversation_id, account_id),
        ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def get_message(self, account_id: str, message_id: str) -> MessageRecord | None:
        row = self._db.connection.execute(
            "SELECT message_id, conversation_id, account_id, role, attempt_number,"
            " status, content, error_code, error_message, duration_ms, model_id,"
            " run_lock_id, created_at, updated_at"
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
            cursor = self._db.connection.execute(
                "UPDATE messages SET content = ?, updated_at = ?"
                " WHERE message_id = ? AND account_id = ? AND status = 'streaming'",
                (content, _iso(updated_at), message_id, account_id),
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
    ) -> int:
        """把生成中的消息原子收敛到终态；仅 streaming → 目标状态，返回影响行数。"""
        with self._db.transaction():
            cursor = self._db.connection.execute(
                "UPDATE messages SET status = ?, error_code = ?, error_message = ?,"
                " duration_ms = ?, model_id = ?, run_lock_id = ?, updated_at = ?"
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
            return cursor.rowcount

    def message_count(self, account_id: str, conversation_id: str) -> int:
        row = self._db.connection.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    # -- model run locks ---------------------------------------------------

    def insert_run_lock(self, account_id: str, lock: ModelRunLock) -> None:
        """持久化不可变模型运行锁，绑定稳定账户 ID。"""
        with self._db.transaction():
            self._db.connection.execute(
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
        )


def _json_dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)
