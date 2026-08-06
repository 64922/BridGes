"""账户删除（Issue 37，AC3-4）。

删除编排分三个阶段，保证「部分失败不宣称成功」：

1. 状态记录：在 ``account_deletions`` 写入 deleting 行，并保存事务删除
   前收集的对象内容哈希清单（pending_hashes）——这是文件系统清理在
   SQL 行消失后仍可重试的凭据；
2. 数据库删除：单事务按依赖顺序删除该账户全部数据行（含 accounts）；
   事务失败整体回滚、零副作用；
3. 文件系统清理：对象物理文件（引用计数为零才删，跨账户共享内容保留）、
   账户级凭据（百炼 Key 与 SMTP 授权码、密钥元数据与探针状态）、身份
   账户记录。任一步失败记录 failed + 中文原因并可重试（重试端点与后台
   执行器轮），绝不冒充成功。

会话撤销与流式停止由 API 层在服务调用后编排（需要响应上下文）；SQL
行删除完成后「已撤权数据不可继续被聊天、搜索或插件读取」立即成立。
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from bridges.contracts.lifecycle import (
    AccountDeletionProjection,
    AccountDeletionStatus,
    DataLifecycleError,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.credentials.service import KeyCredentialService
from bridges.credentials.store import CredentialStorePort
from bridges.identity.service import IdentityService
from bridges.lifecycle.catalog import delete_account_rows
from bridges.observability.service import ObservabilityService
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError
from bridges.storage.repository import BridgesObjectRepository

#: deleting 状态超过该时长视为进程中断（崩溃/断电），可安全重试。
_STALE_DELETION_TTL = timedelta(minutes=10)

ObjectFileCleaner = Callable[[str], None]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _new_id() -> str:
    return secrets.token_urlsafe(16)


class DeletionService:
    """账户删除与可重试清理编排。"""

    def __init__(
        self,
        database: BridgesDatabase,
        object_repository: BridgesObjectRepository,
        identity_service: IdentityService | None,
        credential_store: CredentialStorePort,
        smtp_credential_store: CredentialStorePort,
        observability_service: ObservabilityService,
        key_credential_service: KeyCredentialService | None = None,
        object_file_cleaner: ObjectFileCleaner | None = None,
    ) -> None:
        self._database = database
        self._objects = object_repository
        self._identity = identity_service
        self._credentials = credential_store
        self._smtp_credentials = smtp_credential_store
        self._observability = observability_service
        self._key_credentials = key_credential_service
        self._file_cleaner = object_file_cleaner

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_status(self, account_id: str) -> AccountDeletionProjection | None:
        """返回该账户最近一次删除状态（含失败可重试信息）。"""
        row = self._database.scoped(account_id).execute(
            "SELECT * FROM account_deletions WHERE account_id = ?"
            " ORDER BY started_at DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_projection(row)

    def _row_to_projection(self, row: Any) -> AccountDeletionProjection:
        return AccountDeletionProjection(
            deletion_id=str(row["deletion_id"]),
            account_id=str(row["account_id"]),
            status=AccountDeletionStatus(str(row["status"])),
            retry_count=int(row["retry_count"]),
            last_error=(
                str(row["last_error"]) if row["last_error"] is not None else None
            ),
            started_at=datetime.fromisoformat(str(row["started_at"])),
            completed_at=(
                datetime.fromisoformat(str(row["completed_at"]))
                if row["completed_at"] is not None
                else None
            ),
        )

    # ------------------------------------------------------------------
    # 删除编排
    # ------------------------------------------------------------------

    def delete_account(self, account_id: str) -> AccountDeletionProjection:
        """执行账户删除（事务 + 文件系统清理），失败可重试不宣称成功。"""
        pending_hashes = self._collect_object_hashes(account_id)
        deletion_id = _new_id()
        try:
            with self._database.transaction():
                self._database.connection.execute(
                    "INSERT INTO account_deletions(deletion_id, account_id,"
                    " status, retry_count, last_error, pending_hashes,"
                    " started_at) VALUES (?, ?, ?, 0, NULL, ?, ?)",
                    (
                        deletion_id,
                        account_id,
                        AccountDeletionStatus.DELETING.value,
                        json.dumps(pending_hashes),
                        _now(),
                    ),
                )
        except StorageError as exc:
            raise DataLifecycleError(
                "deletion_unavailable", str(exc), 503
            ) from exc
        try:
            # 事务删除全部数据行：失败整体回滚，状态行仍可观察并重试。
            delete_account_rows(self._database, account_id)
        except StorageError as exc:
            return self._mark_failed(
                deletion_id, account_id, "数据库删除失败", str(exc)
            )
        return self._run_cleanup_phase(deletion_id, account_id, pending_hashes)

    def retry_deletion(
        self, deletion_id: str
    ) -> AccountDeletionProjection:
        """重试一次失败的删除（幂等：已完成/进行中拒绝或直接返回）。"""
        row = self._database.connection.execute(
            "SELECT * FROM account_deletions WHERE deletion_id = ?",
            (deletion_id,),
        ).fetchone()
        if row is None:
            raise DataLifecycleError(
                "deletion_not_found", "删除状态记录不存在。", 404
            )
        account_id = str(row["account_id"])
        status = str(row["status"])
        if status == AccountDeletionStatus.COMPLETED.value:
            return self._row_to_projection(row)
        if status == AccountDeletionStatus.DELETING.value:
            started = datetime.fromisoformat(str(row["started_at"]))
            if datetime.now(UTC) - started < _STALE_DELETION_TTL:
                raise DataLifecycleError(
                    "deletion_in_progress",
                    "该账户删除仍在进行中，请稍后重试。",
                    409,
                )
            # 超过陈旧阈值：删除进程可能已中断（崩溃/断电），视为失败
            # 状态重试，避免状态机永远停留在进行中。
            self._database.connection.execute(
                "UPDATE account_deletions SET status = ?, retry_count ="
                " retry_count + 1 WHERE deletion_id = ?",
                (AccountDeletionStatus.FAILED.value, deletion_id),
            )
        pending_hashes = json.loads(row["pending_hashes"] or "[]")
        # 数据库行若仍在（上次在事务前失败），重跑事务删除；已删除则
        # 只补文件系统清理阶段。
        existing = self._database.connection.execute(
            "SELECT COUNT(*) AS count FROM accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if int(existing["count"]) > 0:
            try:
                delete_account_rows(self._database, account_id)
            except StorageError as exc:
                return self._mark_failed(
                    deletion_id, account_id, "数据库删除失败", str(exc)
                )
        return self._run_cleanup_phase(deletion_id, account_id, pending_hashes)

    def process_pending_retries(self) -> str:
        """重试全部失败状态的删除（后台执行器轮），返回中文摘要。"""
        rows = self._database.connection.execute(
            "SELECT deletion_id FROM account_deletions"
            " WHERE status = ? ORDER BY started_at",
            (AccountDeletionStatus.FAILED.value,),
        ).fetchall()
        summary: list[str] = []
        for row in rows:
            try:
                projection = self.retry_deletion(str(row["deletion_id"]))
                summary.append(
                    f"账户删除重试 {projection.account_id}:"
                    f"{projection.status.value}"
                )
            except DataLifecycleError as exc:
                summary.append(f"账户删除重试失败：{exc.message}")
        return "worker: " + ("；".join(summary) if summary else "无待重试的账户删除。")

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _collect_object_hashes(self, account_id: str) -> list[str]:
        """收集账户对象的全部内容哈希（事务删除前，供物理清理重试）。"""
        rows = self._database.scoped(account_id).execute(
            "SELECT content_hash FROM objects WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        return [str(row["content_hash"]) for row in rows]

    def _run_cleanup_phase(
        self,
        deletion_id: str,
        account_id: str,
        pending_hashes: list[str],
    ) -> AccountDeletionProjection:
        """执行事务外清理：对象文件、凭据、身份。任一失败可重试。"""
        try:
            self._cleanup_object_files(pending_hashes)
            self._cleanup_credentials(account_id)
            if self._identity is not None:
                self._identity.delete_account(account_id)
        except Exception as exc:  # noqa: BLE001 - 清理失败可重试
            return self._mark_failed(
                deletion_id, account_id, "文件与凭据清理失败", str(exc)
            )
        try:
            with self._database.transaction():
                self._database.connection.execute(
                    "UPDATE account_deletions SET status = ?, last_error = NULL,"
                    " completed_at = ? WHERE deletion_id = ?",
                    (AccountDeletionStatus.COMPLETED.value, _now(), deletion_id),
                )
        except StorageError as exc:
            return self._mark_failed(
                deletion_id, account_id, "删除状态记录更新失败", str(exc)
            )
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.ACCOUNT_DELETE,
            result=AuditResult.SUCCESS,
            details={"deletion_id": deletion_id, "account_id": account_id},
        )
        return self._row_to_projection(
            self._database.connection.execute(
                "SELECT * FROM account_deletions WHERE deletion_id = ?",
                (deletion_id,),
            ).fetchone()
        )

    def _cleanup_object_files(self, pending_hashes: list[str]) -> None:
        """删除账户对象物理文件；跨账户共享内容（引用计数>0）保留。

        SQL 行已删，引用计数查询须用裸连接（含全部账户记录）。
        """
        for content_hash in pending_hashes:
            row = self._database.connection.execute(
                "SELECT COUNT(*) AS count FROM objects WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            if int(row["count"]) > 0:
                continue
            cleaner = self._file_cleaner or self._objects.remove_file
            cleaner(content_hash)

    def _cleanup_credentials(self, account_id: str) -> None:
        """清除账户全部外部凭据：百炼 Key、SMTP 授权码、密钥元数据与探针。"""
        if self._key_credentials is not None:
            self._key_credentials.delete(account_id)
        else:
            self._credentials.delete(account_id)
        self._smtp_credentials.delete(account_id)

    def _mark_failed(
        self,
        deletion_id: str,
        account_id: str,
        phase: str,
        error: str,
    ) -> AccountDeletionProjection:
        """记录失败状态（可重试），审计最小非敏感失败事件。"""
        try:
            with self._database.transaction():
                self._database.connection.execute(
                    "UPDATE account_deletions SET status = ?, last_error = ?,"
                    " retry_count = retry_count + 1, completed_at = ?"
                    " WHERE deletion_id = ?",
                    (AccountDeletionStatus.FAILED.value, error, _now(), deletion_id),
                )
        except StorageError:
            pass  # 状态行写入失败不覆盖原始错误语义
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.ACCOUNT_DELETE_FAILED,
            result=AuditResult.RETRYABLE_FAIL,
            details={
                "deletion_id": deletion_id,
                "account_id": account_id,
                "phase": phase,
            },
        )
        raise DataLifecycleError(
            "deletion_failed",
            f"{phase}：{error}。删除未完成，可稍后重试。",
            502,
            retryable=True,
        )
