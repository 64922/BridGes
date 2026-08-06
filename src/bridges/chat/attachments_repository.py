"""聊天附件绑定表与取消标记的账户作用域仓库（Issue 45 收编）。

与 ``ConversationRepository`` 同一模式：所有查询强制按 ``account_id``
过滤，跨账户访问返回不存在/空集；写方法不开事务，由调用方在
``database.transaction()`` 内调用（保持既有事务语义）。表属主清单见
``docs/table-owners.md``：``chat_attachments`` 与
``chat_attachment_cancellations`` 属 chat 域；``objects`` 与
``document_records`` 仅在本仓库的附件投影查询中只读 JOIN（属主分别是
storage 与 ingestion，本仓库不拥有其写路径）。
"""

from __future__ import annotations

import sqlite3
from typing import cast

from bridges.storage.database import BridgesDatabase

#: 附件投影查询共用的对象元数据片段（含摄取状态 LEFT JOIN）。
#: objects（storage 域）与 document_records（ingestion 域）为跨域只读
#: 投影，写路径仍归属主仓库（BridgesObjectRepository / ingestion）。
_ATTACHMENT_SELECT = (
    "SELECT a.object_id, a.account_id, a.conversation_id, a.message_id,"
    " a.upload_id, a.media_type, a.status, a.created_at, a.updated_at,"
    " o.original_filename, o.content_length, o.content_hash,"
    " r.status AS ingestion_raw_status, r.lease_expires_at, r.failure_reason"
    " FROM chat_attachments a"
    " JOIN objects o ON o.object_id = a.object_id"
    " LEFT JOIN document_records r ON r.object_id = a.object_id AND r.account_id = a.account_id"
)


class AttachmentRepository:
    """``chat_attachments`` / ``chat_attachment_cancellations`` 的账户作用域读写。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    # -- 读取 --------------------------------------------------------------

    def get_row(
        self, account_id: str, conversation_id: str, object_id: str
    ) -> sqlite3.Row | None:
        """返回单条附件投影行（含对象元数据与摄取状态）；跨账户返回 None。"""
        row = self._database.scoped(account_id).execute(
            _ATTACHMENT_SELECT
            + " WHERE a.object_id = ? AND a.account_id = ?"
            " AND a.conversation_id = ? AND o.status = 'active'",
            (object_id, account_id, conversation_id),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def rows_for_message(
        self, account_id: str, conversation_id: str, message_id: str
    ) -> list[sqlite3.Row]:
        """返回消息绑定的附件投影行（按创建时间与对象标识稳定排序）。"""
        rows = self._database.scoped(account_id).execute(
            _ATTACHMENT_SELECT
            + " WHERE a.account_id = ? AND a.conversation_id = ? AND a.message_id = ?"
            " AND o.status = 'active' ORDER BY a.created_at, a.object_id",
            (account_id, conversation_id, message_id),
        ).fetchall()
        return list(rows)

    def row_by_upload_id(
        self, account_id: str, upload_id: str
    ) -> sqlite3.Row | None:
        """按客户端幂等标识返回附件投影行；越权或不存在返回 None。"""
        row = self._database.scoped(account_id).execute(
            _ATTACHMENT_SELECT
            + " WHERE a.upload_id = ? AND a.account_id = ? AND o.status = 'active'",
            (upload_id, account_id),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def unbound_duplicate_row(
        self,
        account_id: str,
        conversation_id: str,
        filename: str,
        content_hash: str,
    ) -> sqlite3.Row | None:
        """返回本会话中同名同内容的未绑定附件（内容去重，取最早一条）。"""
        row = self._database.scoped(account_id).execute(
            _ATTACHMENT_SELECT
            + " WHERE a.account_id = ? AND a.conversation_id = ?"
            " AND a.message_id IS NULL AND o.original_filename = ?"
            " AND o.content_hash = ? AND o.status = 'active'"
            " ORDER BY a.created_at LIMIT 1",
            (account_id, conversation_id, filename, content_hash),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def unbound_object_ids(
        self, account_id: str, conversation_id: str, object_ids: list[str]
    ) -> set[str]:
        """返回指定标识中仍处于「已上传未绑定」状态的对象集合。

        发送消息前校验全部附件仍可绑定：任何对象不在返回集即视为
        不存在或已绑定，杜绝越权绑定。
        """
        if not object_ids:
            return set()
        placeholders = ",".join("?" for _ in object_ids)
        rows = self._database.scoped(account_id).execute(
            "SELECT object_id FROM chat_attachments"
            " WHERE account_id = ? AND conversation_id = ?"
            " AND message_id IS NULL AND status = 'uploaded'"
            f" AND object_id IN ({placeholders})",
            (account_id, conversation_id, *object_ids),
        ).fetchall()
        return {str(row["object_id"]) for row in rows}

    def object_ids_for_message(
        self, account_id: str, conversation_id: str, message_id: str
    ) -> list[str]:
        """返回消息绑定的对象标识（检索层确定本轮附件作用域用）。"""
        rows = self._database.scoped(account_id).execute(
            "SELECT object_id FROM chat_attachments"
            " WHERE account_id = ? AND conversation_id = ? AND message_id = ?",
            (account_id, conversation_id, message_id),
        ).fetchall()
        return [str(row["object_id"]) for row in rows]

    def bound_exists(
        self,
        account_id: str,
        object_id: str,
        conversation_id: str,
        message_id: str,
    ) -> bool:
        """附件是否仍绑定在指定用户消息上（引用打开时实时校验用）。"""
        row = self._database.scoped(account_id).execute(
            "SELECT 1 FROM chat_attachments WHERE account_id = ?"
            " AND object_id = ? AND conversation_id = ? AND message_id = ?"
            " LIMIT 1",
            (account_id, object_id, conversation_id, message_id),
        ).fetchone()
        return row is not None

    def cancellation_exists(
        self, account_id: str, conversation_id: str, upload_id: str
    ) -> bool:
        """该上传是否已被取消（取消后同一幂等标识拒绝再次上传）。"""
        row = self._database.scoped(account_id).execute(
            "SELECT 1 FROM chat_attachment_cancellations"
            " WHERE account_id = ? AND conversation_id = ? AND upload_id = ?",
            (account_id, conversation_id, upload_id),
        ).fetchone()
        return row is not None

    def attachment_exists(
        self,
        account_id: str,
        conversation_id: str,
        object_id: str,
        *,
        message_id: str | None,
    ) -> bool:
        """附件绑定行是否存在（删除前的授权检查；跨账户返回 False）。"""
        condition = "message_id IS NULL" if message_id is None else "message_id = ?"
        params: tuple[object, ...] = (
            account_id,
            conversation_id,
            object_id,
            *((message_id,) if message_id is not None else ()),
        )
        row = self._database.scoped(account_id).execute(
            "SELECT object_id FROM chat_attachments"
            " WHERE account_id = ? AND conversation_id = ? AND object_id = ?"
            f" AND {condition}",
            params,
        ).fetchone()
        return row is not None

    def object_ids_for_conversation(
        self, account_id: str, conversation_id: str
    ) -> list[str]:
        """返回会话的全部附件对象标识（会话删除时标记对象用）。"""
        rows = self._database.scoped(account_id).execute(
            "SELECT object_id FROM chat_attachments"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        ).fetchall()
        return [str(row["object_id"]) for row in rows]

    # -- 写入（调用方须在 database.transaction() 内） ----------------------

    def insert_uploaded(
        self,
        *,
        account_id: str,
        conversation_id: str,
        object_id: str,
        upload_id: str,
        media_type: str,
        created_at: str,
    ) -> None:
        """写入一条已上传未绑定附件行（对象已入对象库，写失败由调用方回收）。"""
        self._database.scoped(account_id).execute(
            "INSERT INTO chat_attachments"
            "(object_id, account_id, conversation_id, message_id, upload_id,"
            " media_type, status, created_at, updated_at)"
            " VALUES (?, ?, ?, NULL, ?, ?, 'uploaded', ?, ?)",
            (object_id, account_id, conversation_id, upload_id, media_type, created_at, created_at),
        )

    def insert_cancellation(
        self,
        *,
        account_id: str,
        conversation_id: str,
        upload_id: str,
        created_at: str,
    ) -> None:
        """记录一次上传取消（幂等；取消后同标识不允许再次上传）。"""
        self._database.scoped(account_id).execute(
            "INSERT OR IGNORE INTO chat_attachment_cancellations"
            " (account_id, conversation_id, upload_id, created_at) VALUES (?, ?, ?, ?)",
            (account_id, conversation_id, upload_id, created_at),
        )

    def delete_one(
        self,
        account_id: str,
        conversation_id: str,
        object_id: str,
        *,
        message_id: str | None,
    ) -> None:
        """删除一条附件绑定行（message_id 为 None 时删除未绑定行）。"""
        condition = "message_id IS NULL" if message_id is None else "message_id = ?"
        params: tuple[object, ...] = (
            account_id,
            conversation_id,
            object_id,
            *((message_id,) if message_id is not None else ()),
        )
        self._database.scoped(account_id).execute(
            "DELETE FROM chat_attachments"
            " WHERE account_id = ? AND conversation_id = ? AND object_id = ?"
            f" AND {condition}",
            params,
        )

    def delete_by_object_id(self, account_id: str, object_id: str) -> None:
        """按对象标识删除全部绑定行（按上传任务取消时的清理路径）。"""
        self._database.scoped(account_id).execute(
            "DELETE FROM chat_attachments WHERE object_id = ? AND account_id = ?",
            (object_id, account_id),
        )

    def delete_rows_for_conversation(
        self, account_id: str, conversation_id: str
    ) -> None:
        """删除会话的全部附件绑定行。"""
        self._database.scoped(account_id).execute(
            "DELETE FROM chat_attachments WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        )

    def delete_cancellations_for_conversation(
        self, account_id: str, conversation_id: str
    ) -> None:
        """删除会话的全部取消标记（避免会话删除后残留无主记录）。"""
        self._database.scoped(account_id).execute(
            "DELETE FROM chat_attachment_cancellations"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        )
