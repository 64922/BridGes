"""聊天附件的安全校验、绑定与账户授权访问。"""

from __future__ import annotations

import hashlib
import io
import re
import secrets
import sqlite3
import unicodedata
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import unquote

from bridges.contracts.chat import ChatAttachmentProjection
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError
from bridges.storage.repository import OBJECT_STATUS_PENDING_CLEANUP, BridgesObjectRepository

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_COUNT = 10

_EXTENSION_TYPES = {
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".json": "application/json",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".webp": "image/webp",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_RESERVED_NAMES = {
    "AUX", "CLOCK$", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7",
    "COM8", "COM9", "CON", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6",
    "LPT7", "LPT8", "LPT9", "NUL", "PRN",
}


class ChatAttachmentError(Exception):
    """面向 API 的安全附件错误。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ChatAttachmentRecord:
    object_id: str
    account_id: str
    conversation_id: str
    message_id: str | None
    upload_id: str
    original_filename: str
    media_type: str
    content_length: int
    content_hash: str
    status: str
    created_at: datetime
    updated_at: datetime

    def projection(self) -> ChatAttachmentProjection:
        return ChatAttachmentProjection(
            object_id=self.object_id,
            original_filename=self.original_filename,
            media_type=self.media_type,
            content_length=self.content_length,
            content_hash=self.content_hash,
            conversation_id=self.conversation_id,
            message_id=self.message_id,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class ChatAttachmentService:
    """把原始文件交给对象库，并拥有聊天消息关联表的写模型。"""

    def __init__(
        self, database: BridgesDatabase, object_repository: BridgesObjectRepository
    ) -> None:
        self._database = database
        self._objects = object_repository

    def upload(
        self,
        account_id: str,
        conversation_id: str,
        original_filename: str,
        content: bytes,
        *,
        upload_id: str | None = None,
    ) -> tuple[ChatAttachmentProjection, bool]:
        """上传一个待绑定附件；返回投影与是否为新建记录。"""
        self._require_conversation(account_id, conversation_id)
        filename = _validate_filename(original_filename)
        if not content:
            raise ChatAttachmentError("empty_file", "文件为空，无法上传。")
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise ChatAttachmentError(
                "file_too_large", "文件超过 10 MB 大小限制，请压缩后重试。", 413
            )
        media_type = _sniff_media_type(filename, content)
        upload_key = upload_id or secrets.token_urlsafe(18)
        if len(upload_key) > 120 or not re.fullmatch(r"[A-Za-z0-9._~-]+", upload_key):
            raise ChatAttachmentError("invalid_upload_id", "上传标识无效，请重新选择文件。")

        existing = self._find_by_upload_id(account_id, upload_key)
        if existing is not None:
            if (
                existing.account_id != account_id
                or existing.conversation_id != conversation_id
            ):
                raise ChatAttachmentError(
                    "attachment_not_found", "附件不存在或没有访问权限。", 404
                )
            if existing.content_hash != hashlib.sha256(content).hexdigest():
                raise ChatAttachmentError(
                    "upload_id_conflict", "上传标识已用于其他文件，请重新选择。", 409
                )
            return existing.projection(), False

        cancelled = self._database.scoped(account_id).execute(
            "SELECT 1 FROM chat_attachment_cancellations"
            " WHERE account_id = ? AND conversation_id = ? AND upload_id = ?",
            (account_id, conversation_id, upload_key),
        ).fetchone()
        if cancelled is not None:
            raise ChatAttachmentError(
                "upload_cancelled", "该上传已取消，请重新选择文件。", 409
            )

        content_hash = hashlib.sha256(content).hexdigest()
        duplicate = self._find_unbound_duplicate(
            account_id, conversation_id, filename, content_hash
        )
        if duplicate is not None:
            return duplicate.projection(), False

        try:
            stored = self._objects.create_object(
                account_id, filename, content, media_type=media_type
            )
        except StorageError as exc:
            raise ChatAttachmentError(
                "attachment_save_failed", "附件保存失败，请稍后重试。", 503
            ) from exc
        now = datetime.now(UTC).isoformat()
        try:
            with self._database.transaction():
                cancelled = self._database.scoped(account_id).execute(
                    "SELECT 1 FROM chat_attachment_cancellations"
                    " WHERE account_id = ? AND conversation_id = ? AND upload_id = ?",
                    (account_id, conversation_id, upload_key),
                ).fetchone()
                if cancelled is not None:
                    raise ChatAttachmentError(
                        "upload_cancelled", "该上传已取消，请重新选择文件。", 409
                    )
                self._database.scoped(account_id).execute(
                    "INSERT INTO chat_attachments"
                    "(object_id, account_id, conversation_id, message_id, upload_id,"
                    " media_type, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, NULL, ?, ?, 'uploaded', ?, ?)",
                    (
                        stored.object_id,
                        account_id,
                        conversation_id,
                        upload_key,
                        media_type,
                        now,
                        now,
                    ),
                )
        except ChatAttachmentError:
            with suppress(StorageError):
                self._objects.delete_object(account_id, stored.object_id)
            raise
        except (StorageError, sqlite3.Error) as exc:
            with suppress(StorageError):
                self._objects.delete_object(account_id, stored.object_id)
            raise ChatAttachmentError(
                "attachment_save_failed", "附件保存失败，请稍后重试。", 503
            ) from exc
        record = self.get(account_id, conversation_id, stored.object_id)
        assert record is not None
        return record.projection(), True

    def get(
        self, account_id: str, conversation_id: str, object_id: str
    ) -> ChatAttachmentRecord | None:
        row = self._database.scoped(account_id).execute(
            "SELECT a.object_id, a.account_id, a.conversation_id, a.message_id,"
            " a.upload_id, a.media_type, a.status, a.created_at, a.updated_at,"
            " o.original_filename, o.content_length, o.content_hash"
            " FROM chat_attachments a JOIN objects o ON o.object_id = a.object_id"
            " WHERE a.object_id = ? AND a.account_id = ?"
            " AND a.conversation_id = ? AND o.status = 'active'",
            (object_id, account_id, conversation_id),
        ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list_for_message(
        self, account_id: str, conversation_id: str, message_id: str
    ) -> list[ChatAttachmentRecord]:
        rows = self._database.scoped(account_id).execute(
            "SELECT a.object_id, a.account_id, a.conversation_id, a.message_id,"
            " a.upload_id, a.media_type, a.status, a.created_at, a.updated_at,"
            " o.original_filename, o.content_length, o.content_hash"
            " FROM chat_attachments a JOIN objects o ON o.object_id = a.object_id"
            " WHERE a.account_id = ? AND a.conversation_id = ? AND a.message_id = ?"
            " AND o.status = 'active' ORDER BY a.created_at, a.object_id",
            (account_id, conversation_id, message_id),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def validate_unbound(
        self, account_id: str, conversation_id: str, object_ids: list[str]
    ) -> None:
        if len(object_ids) > MAX_ATTACHMENT_COUNT:
            raise ChatAttachmentError("too_many_attachments", "一条消息最多添加 10 个附件。")
        unique_ids = list(dict.fromkeys(object_ids))
        if len(unique_ids) != len(object_ids):
            raise ChatAttachmentError("duplicate_attachment", "同一附件不能重复添加。")
        if not unique_ids:
            return
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self._database.scoped(account_id).execute(
            "SELECT object_id FROM chat_attachments"
            " WHERE account_id = ? AND conversation_id = ?"
            " AND message_id IS NULL AND status = 'uploaded'"
            f" AND object_id IN ({placeholders})",
            (account_id, conversation_id, *unique_ids),
        ).fetchall()
        if {str(row["object_id"]) for row in rows} != set(unique_ids):
            raise ChatAttachmentError(
                "attachment_not_found", "附件不存在或没有访问权限。", 404
            )

    def delete(
        self,
        account_id: str,
        conversation_id: str,
        object_id: str,
        *,
        message_id: str | None = None,
    ) -> None:
        condition = "message_id IS NULL" if message_id is None else "message_id = ?"
        params: tuple[object, ...] = (
            account_id,
            conversation_id,
            object_id,
            *((message_id,) if message_id is not None else ()),
        )
        with self._database.transaction():
            row = self._database.scoped(account_id).execute(
                "SELECT object_id FROM chat_attachments"
                " WHERE account_id = ? AND conversation_id = ? AND object_id = ?"
                f" AND {condition}",
                params,
            ).fetchone()
            if row is None:
                raise ChatAttachmentError("attachment_not_found", "附件不存在或没有访问权限。", 404)
            self._database.scoped(account_id).execute(
                "DELETE FROM chat_attachments"
                " WHERE account_id = ? AND conversation_id = ? AND object_id = ?"
                f" AND {condition}",
                params,
            )
            self._database.scoped(account_id).execute(
                "UPDATE objects SET status = 'pending_cleanup', updated_at = ?"
                " WHERE object_id = ? AND account_id = ? AND status = 'active'",
                (datetime.now(UTC).isoformat(), object_id, account_id),
            )
        self._objects.run_pending_cleanups()

    def delete_by_upload_id(
        self, account_id: str, conversation_id: str, upload_id: str
    ) -> None:
        """按客户端幂等标识取消未绑定上传；越权或未知标识均幂等成功。"""
        now = datetime.now(UTC).isoformat()
        object_id: str | None = None
        with self._database.transaction():
            record = self._find_by_upload_id(account_id, upload_id)
            if record is not None:
                if (
                    record.account_id != account_id
                    or record.conversation_id != conversation_id
                ):
                    raise ChatAttachmentError(
                        "attachment_not_found", "附件不存在或没有访问权限。", 404
                    )
                if record.message_id is not None:
                    raise ChatAttachmentError(
                        "attachment_already_bound", "附件已关联消息，不能按上传任务取消。", 409
                    )
                object_id = record.object_id
            self._database.scoped(account_id).execute(
                "INSERT OR IGNORE INTO chat_attachment_cancellations"
                " (account_id, conversation_id, upload_id, created_at) VALUES (?, ?, ?, ?)",
                (account_id, conversation_id, upload_id, now),
            )
            if object_id is not None:
                self._database.scoped(account_id).execute(
                    "DELETE FROM chat_attachments WHERE object_id = ? AND account_id = ?",
                    (object_id, account_id),
                )
                self._database.scoped(account_id).execute(
                    "UPDATE objects SET status = ?, updated_at = ?"
                    " WHERE object_id = ? AND account_id = ? AND status = 'active'",
                    (OBJECT_STATUS_PENDING_CLEANUP, now, object_id, account_id),
                )
        self._objects.run_pending_cleanups()

    def delete_for_conversation(self, account_id: str, conversation_id: str) -> None:
        with self._database.transaction():
            rows = self._database.scoped(account_id).execute(
                "SELECT object_id FROM chat_attachments"
                " WHERE account_id = ? AND conversation_id = ?",
                (account_id, conversation_id),
            ).fetchall()
            # 一并清理本会话的取消标记：避免会话删除后残留无主记录
            # （否则随取消次数无限积累）。
            self._database.scoped(account_id).execute(
                "DELETE FROM chat_attachment_cancellations"
                " WHERE account_id = ? AND conversation_id = ?",
                (account_id, conversation_id),
            )
            if rows:
                now = datetime.now(UTC).isoformat()
                self._database.scoped(account_id).execute(
                    "DELETE FROM chat_attachments WHERE account_id = ? AND conversation_id = ?",
                    (account_id, conversation_id),
                )
                for row in rows:
                    self._database.scoped(account_id).execute(
                        "UPDATE objects SET status = ?, updated_at = ?"
                        " WHERE object_id = ? AND account_id = ?",
                        (OBJECT_STATUS_PENDING_CLEANUP, now, str(row["object_id"]), account_id),
                    )
        self._objects.run_pending_cleanups()

    def download(
        self, account_id: str, conversation_id: str, object_id: str
    ) -> tuple[ChatAttachmentRecord, bytes]:
        record = self.get(account_id, conversation_id, object_id)
        if record is None:
            raise ChatAttachmentError("attachment_not_found", "附件不存在或没有访问权限。", 404)
        try:
            content = self._objects.get_content(account_id, object_id)
        except StorageError as exc:
            raise ChatAttachmentError(
                "attachment_unavailable", "附件内容当前不可读取，请稍后重试。", 503
            ) from exc
        return record, content

    def _require_conversation(self, account_id: str, conversation_id: str) -> None:
        row = self._database.scoped(account_id).execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        ).fetchone()
        if row is None:
            raise ChatAttachmentError("conversation_not_found", "对话不存在或没有访问权限。", 404)

    def _find_by_upload_id(
        self, account_id: str, upload_id: str
    ) -> ChatAttachmentRecord | None:
        row = self._database.scoped(account_id).execute(
            "SELECT a.object_id, a.account_id, a.conversation_id, a.message_id,"
            " a.upload_id, a.media_type, a.status, a.created_at, a.updated_at,"
            " o.original_filename, o.content_length, o.content_hash"
            " FROM chat_attachments a JOIN objects o ON o.object_id = a.object_id"
            " WHERE a.upload_id = ? AND a.account_id = ? AND o.status = 'active'",
            (upload_id, account_id),
        ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def _find_unbound_duplicate(
        self, account_id: str, conversation_id: str, filename: str, content_hash: str
    ) -> ChatAttachmentRecord | None:
        row = self._database.scoped(account_id).execute(
            "SELECT a.object_id, a.account_id, a.conversation_id, a.message_id,"
            " a.upload_id, a.media_type, a.status, a.created_at, a.updated_at,"
            " o.original_filename, o.content_length, o.content_hash"
            " FROM chat_attachments a JOIN objects o ON o.object_id = a.object_id"
            " WHERE a.account_id = ? AND a.conversation_id = ?"
            " AND a.message_id IS NULL AND o.original_filename = ?"
            " AND o.content_hash = ? AND o.status = 'active'"
            " ORDER BY a.created_at LIMIT 1",
            (account_id, conversation_id, filename, content_hash),
        ).fetchone()
        return self._row_to_record(row) if row is not None else None

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ChatAttachmentRecord:
        return ChatAttachmentRecord(
            object_id=str(row["object_id"]),
            account_id=str(row["account_id"]),
            conversation_id=str(row["conversation_id"]),
            message_id=str(row["message_id"]) if row["message_id"] is not None else None,
            upload_id=str(row["upload_id"]),
            original_filename=str(row["original_filename"]),
            media_type=str(row["media_type"]),
            content_length=int(row["content_length"]),
            content_hash=str(row["content_hash"]),
            status=str(row["status"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )


def _validate_filename(value: str) -> str:
    filename = unicodedata.normalize("NFC", unquote(value)).strip()
    if (
        not filename
        or len(filename) > 255
        or "\x00" in filename
        or "/" in filename
        or "\\" in filename
        or filename in {".", ".."}
        or any(unicodedata.category(char).startswith("C") for char in filename)
        or filename.endswith((".", " "))
    ):
        raise ChatAttachmentError(
            "invalid_filename", "文件名含有危险字符或路径片段，请重命名后重试。"
        )
    if filename.rsplit(".", 1)[0].upper() in _RESERVED_NAMES:
        raise ChatAttachmentError("invalid_filename", "文件名属于系统保留名称，请重命名后重试。")
    return filename


def _sniff_media_type(filename: str, content: bytes) -> str:
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    expected = _EXTENSION_TYPES.get(extension)
    detected: str | None = None
    if content.startswith(b"%PDF-"):
        detected = "application/pdf"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif content.startswith((b"GIF87a", b"GIF89a")):
        detected = "image/gif"
    elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        detected = "image/webp"
    elif content.startswith(b"PK\x03\x04"):
        detected = _sniff_office_type(extension, content)
    else:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            detected = None
        else:
            if b"\x00" not in content and expected in {
                "text/plain", "text/markdown", "text/csv", "application/json"
            }:
                detected = expected
    if expected is None or detected is None or expected != detected:
        raise ChatAttachmentError("invalid_file_type", "文件类型与扩展名不匹配，或该类型不受支持。")
    return detected


def _sniff_office_type(extension: str, content: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return None
    if any(
        name.lower().endswith("vbaproject.bin")
        or "/embeddings/" in name.lower()
        for name in names
    ):
        return None
    if "word/document.xml" in names and extension == ".docx":
        return _EXTENSION_TYPES[extension]
    if "xl/workbook.xml" in names and extension == ".xlsx":
        return _EXTENSION_TYPES[extension]
    if "ppt/presentation.xml" in names and extension == ".pptx":
        return _EXTENSION_TYPES[extension]
    return None
