"""账户隔离的对象元数据仓库（bridges.db 权威侧）。

对象行以外键关联不可变内部账户 ID；所有读取按 ``account_id`` 过滤，未知或
跨账户访问返回同一中文错误，杜绝 ID 枚举。删除先标记 ``pending_cleanup``
（可观察、可重试），物理文件删除成功后整行移除，实现完整删除。
"""

from __future__ import annotations

import contextlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError
from bridges.storage.object_store import EncryptedFileObjectStore

OBJECT_STATUS_ACTIVE = "active"
OBJECT_STATUS_PENDING_CLEANUP = "pending_cleanup"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _new_id() -> str:
    return secrets.token_urlsafe(16)


@dataclass(frozen=True)
class StoredObject:
    """对象元数据投影；内容需通过 ``get_content`` 读取。"""

    object_id: str
    account_id: str
    content_hash: str
    original_filename: str
    media_type: str
    content_length: int
    status: str
    cleanup_retry_count: int
    last_cleanup_error: str | None
    created_at: str
    updated_at: str


class BridgesObjectRepository:
    """bridges.db 元数据与加密对象库之上的账户隔离对象仓库。"""

    def __init__(
        self,
        database: BridgesDatabase,
        object_store: EncryptedFileObjectStore,
    ) -> None:
        self._database = database
        self._object_store = object_store
        # 构造即保证数据模式可用；initialize 幂等，重复调用安全。
        self._database.initialize()

    def close(self) -> None:
        """关闭底层数据库连接。"""
        self._database.close()

    # ------------------------------------------------------------------
    # 账户
    # ------------------------------------------------------------------

    def register_account(self, email: str) -> str:
        """注册一个账户并返回不可变内部账户 ID；邮箱重复报中文错误。"""
        account_id = _new_id()
        try:
            self.ensure_account(account_id, email)
        except StorageError as exc:
            raise StorageError("该邮箱已注册。") from exc
        return account_id

    def ensure_account(self, account_id: str, email: str) -> None:
        """让对象库认识身份域的稳定账户 ID，并拒绝冲突的邮箱归属。"""
        normalized_email = email.strip().lower()
        existing = self._database.connection.execute(
            "SELECT account_id, email FROM accounts WHERE account_id = ? OR email = ?",
            (account_id, normalized_email),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["account_id"]) == account_id
                and str(existing["email"]) == normalized_email
            ):
                return
            raise StorageError("账户身份与对象库归属不一致。")
        try:
            with self._database.transaction():
                self._database.connection.execute(
                    "INSERT INTO accounts(account_id, email, created_at)"
                    " VALUES (?, ?, ?)",
                    (account_id, normalized_email, _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError("账户身份与对象库归属不一致。") from exc

    def _require_account(self, account_id: str) -> None:
        row = self._database.connection.execute(
            "SELECT account_id FROM accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            raise StorageError("账户不存在或没有访问权限。")

    # ------------------------------------------------------------------
    # 对象
    # ------------------------------------------------------------------

    def create_object(
        self,
        account_id: str,
        original_filename: str,
        content: bytes,
        media_type: str = "application/octet-stream",
    ) -> StoredObject:
        """保存对象：密文先落盘，再事务化写入元数据行。

        元数据写入失败时回收刚落盘的文件——但仅当没有其他记录引用该内容
        哈希（内容可能已被其他账户共享），否则保留文件，避免误删他人对象；
        回收失败的孤立文件由 ``find_orphans``/``cleanup_orphans`` 兜底。
        """
        self._require_account(account_id)
        content_hash = self._object_store.put(content)
        object_id = _new_id()
        now = _now()
        try:
            with self._database.transaction():
                self._database.connection.execute(
                    "INSERT INTO objects(object_id, account_id, content_hash,"
                    " original_filename, media_type, content_length, status,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        object_id,
                        account_id,
                        content_hash,
                        original_filename,
                        media_type,
                        len(content),
                        OBJECT_STATUS_ACTIVE,
                        now,
                        now,
                    ),
                )
        except BaseException as exc:
            try:
                other_references = self._file_references(content_hash) > 0
            except (StorageError, sqlite3.Error):
                # 引用计数查询失败（如表异常）：INSERT 已失败，按无其他引用
                # 处理，仅回收本次写入的文件，避免遗留孤立对象。
                other_references = False
            with contextlib.suppress(StorageError):
                if not other_references:
                    self._object_store.remove(content_hash)
            if isinstance(exc, sqlite3.Error):
                raise StorageError(
                    "对象元数据写入失败，请检查数据目录。"
                ) from exc
            raise
        return self.get_object(account_id, object_id)

    def get_object(self, account_id: str, object_id: str) -> StoredObject:
        """按账户读取对象元数据；跨账户或不存在返回同一中文错误。"""
        return self._row_to_object(self._select_row(account_id, object_id))

    def get_content(self, account_id: str, object_id: str) -> bytes:
        """读取并解密对象内容；损坏时抛中文错误。"""
        row = self._select_row(account_id, object_id)
        return self._object_store.get(str(row["content_hash"]))

    def list_objects(self, account_id: str) -> list[StoredObject]:
        """列出账户下的全部活跃对象。"""
        rows = self._database.connection.execute(
            "SELECT * FROM objects WHERE account_id = ? AND status = ?"
            " ORDER BY created_at",
            (account_id, OBJECT_STATUS_ACTIVE),
        ).fetchall()
        return [self._row_to_object(row) for row in rows]

    def delete_object(self, account_id: str, object_id: str) -> StoredObject:
        """删除对象：先标记待清理（可观察），再立即尝试物理清理（可重试）。

        返回标记后的记录（``status=pending_cleanup``）；清理结果通过
        ``list_pending_cleanups`` 观察，失败可重试。
        """
        self._select_row(account_id, object_id)  # 授权检查：跨账户一律拒绝
        try:
            with self._database.transaction():
                self._database.connection.execute(
                    "UPDATE objects SET status = ?, updated_at = ?"
                    " WHERE object_id = ? AND account_id = ?",
                    (OBJECT_STATUS_PENDING_CLEANUP, _now(), object_id, account_id),
                )
        except sqlite3.Error as exc:
            raise StorageError(
                "对象删除标记写入失败，请检查数据目录。"
            ) from exc
        pending = self._database.connection.execute(
            "SELECT * FROM objects WHERE object_id = ? AND account_id = ?",
            (object_id, account_id),
        ).fetchone()
        self.run_pending_cleanups()
        return self._row_to_object(cast(sqlite3.Row, pending))

    # ------------------------------------------------------------------
    # 清理：可观察、可重试
    # ------------------------------------------------------------------

    def list_pending_cleanups(self) -> list[StoredObject]:
        """返回全部待清理记录（内部运维视角，非账户用户 API）。"""
        rows = self._database.connection.execute(
            "SELECT * FROM objects WHERE status = ? ORDER BY updated_at",
            (OBJECT_STATUS_PENDING_CLEANUP,),
        ).fetchall()
        return [self._row_to_object(row) for row in rows]

    def run_pending_cleanups(self) -> int:
        """重试全部待清理对象，返回本次成功清理数。

        内容哈希路径可能被多个账户/多条记录共享：仅当没有其他记录引用该
        文件时才物理删除，否则只移除本行，避免误删他人对象（跨账户串号）。
        物理文件删除成功则整行移除（完整删除）；失败保留待清理状态并累加
        重试次数与中文原因，下次调用继续重试。
        """
        cleaned = 0
        rows = self._database.connection.execute(
            "SELECT * FROM objects WHERE status = ?",
            (OBJECT_STATUS_PENDING_CLEANUP,),
        ).fetchall()
        for row in rows:
            object_id = str(row["object_id"])
            content_hash = str(row["content_hash"])
            if self._file_references(content_hash) > 1:
                # 文件仍被其他记录引用，物理删除会破坏他人的对象；只移除本行。
                # 行删除失败不得静默计成功：进入可观察、可重试的失败记录。
                try:
                    with self._database.transaction():
                        self._database.connection.execute(
                            "DELETE FROM objects WHERE object_id = ?",
                            (object_id,),
                        )
                except (StorageError, sqlite3.Error) as exc:
                    reason = (
                        str(exc)
                        if isinstance(exc, StorageError)
                        else "对象清理失败，请检查数据目录。"
                    )
                    self._record_cleanup_failure(object_id, reason)
                    continue
                cleaned += 1
                continue
            try:
                self._object_store.remove(content_hash)
                with self._database.transaction():
                    self._database.connection.execute(
                        "DELETE FROM objects WHERE object_id = ?",
                        (object_id,),
                    )
            except (StorageError, sqlite3.Error) as exc:
                reason = (
                    str(exc)
                    if isinstance(exc, StorageError)
                    else "对象清理失败，请检查数据目录。"
                )
                self._record_cleanup_failure(object_id, reason)
                continue
            cleaned += 1
        return cleaned

    def _record_cleanup_failure(self, object_id: str, reason: str) -> None:
        """记录一次清理失败：累加重试次数并保留中文原因（幂等可重试）。"""
        with contextlib.suppress(
            StorageError, sqlite3.Error
        ), self._database.transaction():
            self._database.connection.execute(
                "UPDATE objects SET cleanup_retry_count ="
                " cleanup_retry_count + 1, last_cleanup_error = ?,"
                " updated_at = ? WHERE object_id = ?",
                (reason, _now(), object_id),
            )

    def find_orphans(self) -> list[str]:
        """返回没有对应元数据行的对象文件哈希（可观察的孤立对象）。"""
        rows = self._database.connection.execute(
            "SELECT content_hash FROM objects"
        ).fetchall()
        known = {str(row["content_hash"]) for row in rows}
        return sorted(self._object_store.list_files() - known)

    def cleanup_orphans(self) -> int:
        """删除全部孤立对象文件并返回删除数量；幂等可重试。"""
        removed = 0
        for content_hash in self.find_orphans():
            self._object_store.remove(content_hash)
            removed += 1
        return removed

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _file_references(self, content_hash: str) -> int:
        """返回引用该内容哈希的记录数（跨账户统计，含待清理记录）。"""
        row = self._database.connection.execute(
            "SELECT COUNT(*) AS count FROM objects WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        assert row is not None
        return int(row["count"])

    def _select_row(self, account_id: str, object_id: str) -> sqlite3.Row:
        row = self._database.connection.execute(
            "SELECT * FROM objects WHERE object_id = ? AND account_id = ?",
            (object_id, account_id),
        ).fetchone()
        if row is None or str(row["status"]) != OBJECT_STATUS_ACTIVE:
            raise StorageError("对象不存在或没有访问权限。")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _row_to_object(row: sqlite3.Row) -> StoredObject:
        return StoredObject(
            object_id=str(row["object_id"]),
            account_id=str(row["account_id"]),
            content_hash=str(row["content_hash"]),
            original_filename=str(row["original_filename"]),
            media_type=str(row["media_type"]),
            content_length=int(row["content_length"]),
            status=str(row["status"]),
            cleanup_retry_count=int(row["cleanup_retry_count"]),
            last_cleanup_error=(
                str(row["last_cleanup_error"])
                if row["last_cleanup_error"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
