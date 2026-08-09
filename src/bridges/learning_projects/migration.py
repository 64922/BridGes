"""学习项目文件迁移到全局知识库的账户级编排（Issue 02）。

迁移以旧项目文件的 ``source_document_id`` 为稳定幂等键。原始项目对象和
``project_file`` 记录始终保留；目标只通过 ``knowledge_base`` 来源创建，
同账户同内容哈希复用一份目标文档。任务队列负责断点续跑和租约恢复，本模块
负责账户授权、审计台账、目标校验、墓碑和项目对话解除归属。
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import UTC, datetime

from bridges.contracts.learning_project_migration import (
    LearningProjectMigrationConversation,
    LearningProjectMigrationItem,
    LearningProjectMigrationSummary,
    ProjectMigrationRunStatus,
    ProjectMigrationStatus,
)
from bridges.ingestion.service import IngestionError, IngestionService
from bridges.runtime.queue import Claim, TaskQueue, TaskWorker
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError
from bridges.storage.repository import BridgesObjectRepository


MIGRATION_COMPATIBILITY_WINDOW = "issue-02-v1"
MIGRATION_QUEUE_PREFIX = "learning-project-migration"
MIGRATION_BATCH_SIZE = 20
MIGRATION_LEASE_SECONDS = 1800


class ProjectMigrationError(Exception):
    """迁移 API 的中文领域错误。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(12)}"


def project_writes_frozen(database: BridgesDatabase, account_id: str) -> bool:
    """判断账户是否已经进入项目迁移兼容窗口。"""
    row = database.scoped(account_id).execute(
        "SELECT 1 FROM learning_project_migration_runs"
        " WHERE account_id = ? LIMIT 1",
        (account_id,),
    ).fetchone()
    return row is not None


class ProjectMigrationService:
    """账户级项目文件迁移服务。"""

    def __init__(
        self,
        *,
        database: BridgesDatabase,
        object_repository: BridgesObjectRepository,
        ingestion_service: IngestionService,
        task_queue: TaskQueue | None = None,
    ) -> None:
        self._database = database
        self._objects = object_repository
        self._ingestion = ingestion_service
        self._queue = task_queue or TaskQueue(database)
        self._queue.set_lease_seconds(
            MIGRATION_QUEUE_PREFIX, MIGRATION_LEASE_SECONDS
        )

    # ------------------------------------------------------------------
    # API：启动、读取、重试
    # ------------------------------------------------------------------

    def start(self, account_id: str) -> LearningProjectMigrationSummary:
        """创建账户迁移快照；重复调用返回同一运行，不创建第二份目标。"""
        existing = self._database.scoped(account_id).execute(
            "SELECT run_id FROM learning_project_migration_runs"
            " WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if existing is not None:
            self._enqueue_open_items(account_id)
            return self.get_status(account_id)  # type: ignore[return-value]

        run_id = _new_id("migration")
        now = _now()
        try:
            with self._database.transaction():
                account = self._database.scoped(account_id).execute(
                    "SELECT account_id FROM accounts WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
                if account is None:
                    raise ProjectMigrationError(
                        "account_not_found", "账户不存在或没有访问权限。", 404
                    )
                self._database.scoped(account_id).execute(
                    "INSERT INTO learning_project_migration_runs"
                    " (run_id, account_id, status, compatibility_window,"
                    "  started_at, updated_at) VALUES (?, ?, 'running', ?, ?, ?)",
                    (run_id, account_id, MIGRATION_COMPATIBILITY_WINDOW, now, now),
                )
                source_rows = self._source_rows(account_id)
                total_bytes = 0
                for row in source_rows:
                    source_length = int(row["source_content_length"] or 0)
                    total_bytes += source_length
                    migration_id = _new_id("file-migration")
                    self._database.scoped(account_id).execute(
                        "INSERT OR IGNORE INTO learning_project_migrations"
                        " (migration_id, run_id, account_id, project_id, project_name,"
                        "  source_document_id, source_object_id, source_filename,"
                        "  source_media_type, source_content_hash, source_content_length,"
                        "  source_created_at, ingestion_version, status, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                        (
                            migration_id,
                            run_id,
                            account_id,
                            str(row["project_id"]),
                            str(row["project_name"]),
                            str(row["source_document_id"]),
                            str(row["source_object_id"]),
                            str(row["source_filename"]),
                            str(row["source_media_type"]),
                            str(row["source_content_hash"]),
                            source_length,
                            str(row["source_created_at"]),
                            str(row["parser_version"]),
                            now,
                            now,
                        ),
                    )
                self._database.scoped(account_id).execute(
                    "UPDATE learning_project_migration_runs SET total_bytes = ?,"
                    " updated_at = ? WHERE run_id = ? AND account_id = ?",
                    (total_bytes, now, run_id, account_id),
                )
                self._snapshot_conversations(account_id, run_id, now)
                for row in source_rows:
                    self._queue.enqueue(
                        self._queue_name(account_id),
                        self._task_key(account_id, str(row["source_document_id"])),
                    )
        except ProjectMigrationError:
            raise
        except (StorageError, sqlite3.Error) as exc:
            raise ProjectMigrationError(
                "migration_start_failed", "迁移快照创建失败，请稍后重试。", 503
            ) from exc
        self._finalize_run(account_id)
        return self.get_status(account_id)  # type: ignore[return-value]

    def get_status(self, account_id: str) -> LearningProjectMigrationSummary | None:
        """返回当前账户迁移摘要；未知账户或未启动返回 None。"""
        row = self._database.scoped(account_id).execute(
            "SELECT * FROM learning_project_migration_runs"
            " WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return self._summary_from_row(row)

    def retry(
        self, account_id: str, source_document_id: str | None = None
    ) -> LearningProjectMigrationSummary:
        """安全重试失败项；不允许通过重试复活源删除或墓碑目标。"""
        run = self._database.scoped(account_id).execute(
            "SELECT run_id FROM learning_project_migration_runs"
            " WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if run is None:
            raise ProjectMigrationError(
                "migration_not_started", "该账户尚未开始学习项目迁移。", 404
            )
        where = (
            " AND source_document_id = ?" if source_document_id is not None else ""
        )
        params: tuple[object, ...] = (account_id,) + (
            (source_document_id,) if source_document_id is not None else ()
        )
        now = _now()
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE learning_project_migrations SET status = 'pending',"
                " failure_stage = NULL, failure_reason = NULL,"
                " updated_at = ? WHERE account_id = ? AND status = 'failed'"
                + where,
                (now, *params),
            )
        self._enqueue_open_items(account_id)
        return self.get_status(account_id)  # type: ignore[return-value]

    def process_pending(
        self, *, account_id: str | None = None, max_items: int = MIGRATION_BATCH_SIZE
    ) -> LearningProjectMigrationSummary | str:
        """处理一轮迁移任务；批量大小限制单轮耗时并支持断点续跑。"""
        if max_items < 1 or max_items > 100:
            raise ProjectMigrationError(
                "invalid_batch_size", "迁移批量大小必须在 1 到 100 之间。", 422
            )
        if account_id is not None:
            if self.get_status(account_id) is None:
                raise ProjectMigrationError(
                    "migration_not_started", "该账户尚未开始学习项目迁移。", 404
                )
            accounts = [account_id]
        else:
            rows = self._database.connection.execute(
                "SELECT account_id FROM learning_project_migration_runs"
                " WHERE status != 'completed' ORDER BY started_at"
            ).fetchall()
            accounts = [str(row["account_id"]) for row in rows]
        if not accounts:
            return "迁移处理完成：当前没有待处理账户。"
        # 先收敛已有摄取任务，再创建目标并在同一轮尝试处理目标摄取。
        self._ingestion.process_pending()
        for current_account in accounts:
            self._mark_missing_targets(current_account)
            self._enqueue_open_items(current_account)
            worker = TaskWorker(
                self._queue,
                self._queue_name(current_account),
                "learning-project-migration-worker",
                self._handle_claim,
                default_max_attempts=3,
            )
            worker.drain(max_steps=max_items)
        self._ingestion.process_pending()
        for current_account in accounts:
            self._mark_missing_targets(current_account)
            self._enqueue_open_items(current_account)
            worker = TaskWorker(
                self._queue,
                self._queue_name(current_account),
                "learning-project-migration-worker",
                self._handle_claim,
                default_max_attempts=3,
            )
            worker.drain(max_steps=max_items)
        for current_account in accounts:
            self._finalize_run(current_account)
        if account_id is not None:
            return self.get_status(account_id)  # type: ignore[return-value]
        return f"迁移处理完成：本轮处理 {len(accounts)} 个账户。"

    # ------------------------------------------------------------------
    # 生命周期与旧写入口
    # ------------------------------------------------------------------

    def mark_target_deleted(
        self,
        account_id: str,
        target_object_id: str,
        *,
        target_document_id: str | None = None,
        reason: str = "用户删除知识库迁移目标。",
    ) -> int:
        """写入迁移墓碑并标记所有指向目标的来源，返回影响数。"""
        rows = self._database.scoped(account_id).execute(
            "SELECT migration_id, source_document_id, source_content_hash,"
            " target_document_id, target_object_id"
            " FROM learning_project_migrations"
            " WHERE account_id = ? AND target_object_id = ?",
            (account_id, target_object_id),
        ).fetchall()
        if not rows:
            return 0
        now = _now()
        with self._database.transaction():
            for row in rows:
                self._database.scoped(account_id).execute(
                    "INSERT OR IGNORE INTO learning_project_migration_tombstones"
                    " (tombstone_id, migration_id, account_id, source_document_id,"
                    "  source_content_hash, target_document_id, target_object_id,"
                    "  deleted_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _new_id("tombstone"),
                        str(row["migration_id"]),
                        account_id,
                        str(row["source_document_id"]),
                        str(row["source_content_hash"]),
                        target_document_id or row["target_document_id"],
                        target_object_id,
                        now,
                        reason[:1000],
                    ),
                )
                self._database.scoped(account_id).execute(
                    "UPDATE learning_project_migrations SET status = 'target_deleted',"
                    " failure_stage = 'target', failure_reason = ?, updated_at = ?"
                    " WHERE account_id = ? AND migration_id = ?",
                    (reason[:1000], now, account_id, str(row["migration_id"])),
                )
        return len(rows)

    # ------------------------------------------------------------------
    # 任务处理
    # ------------------------------------------------------------------

    def _handle_claim(self, claim: Claim) -> None:
        _, account_id, source_document_id = claim.task_key.split(":", 2)
        try:
            self._process_item(account_id, source_document_id)
        except Exception as exc:  # noqa: BLE001 - 单项失败隔离，其他项继续
            self._mark_failed(
                account_id,
                source_document_id,
                stage="migration",
                reason=f"迁移任务异常：{exc}",
            )

    def _process_item(self, account_id: str, source_document_id: str) -> None:
        row = self._database.scoped(account_id).execute(
            "SELECT * FROM learning_project_migrations"
            " WHERE account_id = ? AND source_document_id = ?",
            (account_id, source_document_id),
        ).fetchone()
        if row is None or str(row["status"]) in {
            ProjectMigrationStatus.COMPLETED.value,
            ProjectMigrationStatus.SOURCE_DELETED.value,
            ProjectMigrationStatus.TARGET_DELETED.value,
        }:
            return
        now = _now()
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE learning_project_migrations SET status = 'processing',"
                " retry_count = retry_count + 1, last_attempt_at = ?, updated_at = ?"
                " WHERE account_id = ? AND source_document_id = ?",
                (now, now, account_id, source_document_id),
            )

        source = self._database.scoped(account_id).execute(
            "SELECT d.document_id, d.status AS source_status,"
            " r.source_content_hash, r.source_content_length,"
            " r.source_filename, r.source_media_type,"
            " o.object_id, o.content_hash, o.status"
            " FROM learning_project_migrations r"
            " LEFT JOIN document_records d ON d.document_id = r.source_document_id"
            "  AND d.account_id = r.account_id"
            " LEFT JOIN objects o ON o.object_id = r.source_object_id"
            " WHERE r.account_id = ? AND r.source_document_id = ?",
            (account_id, source_document_id),
        ).fetchone()
        if (
            source is None
            or source["document_id"] is None
            or source["object_id"] is None
            or str(source["status"]) != "active"
        ):
            self._mark_source_deleted(account_id, source_document_id)
            return
        if str(source["source_status"]) in {"error", "empty"}:
            self._mark_failed(
                account_id,
                source_document_id,
                "source",
                "原项目文件的摄取校验未通过，迁移未创建新的知识库记录。",
            )
            return
        source_object_id = str(source["object_id"])
        try:
            content = self._objects.get_content(account_id, source_object_id)
        except StorageError as exc:
            self._mark_failed(account_id, source_document_id, "source", str(exc))
            return
        expected_hash = str(source["source_content_hash"])
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != expected_hash or len(content) != int(source["source_content_length"]):
            self._mark_failed(
                account_id,
                source_document_id,
                "source",
                "原始对象校验值或字节数不一致，未创建目标记录。",
            )
            return
        if str(source["source_media_type"]) in {"text/plain", "text/markdown"}:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError:
                self._mark_failed(
                    account_id,
                    source_document_id,
                    "source",
                    "原项目文本文件不是有效的 UTF-8，迁移未创建新的知识库记录。",
                )
                return
        tombstone = self._database.scoped(account_id).execute(
            "SELECT 1 FROM learning_project_migration_tombstones"
            " WHERE account_id = ? AND source_document_id = ?",
            (account_id, source_document_id),
        ).fetchone()
        if tombstone is not None:
            self._set_status(
                account_id,
                source_document_id,
                ProjectMigrationStatus.TARGET_DELETED,
                stage="target",
                reason="目标知识库文件已有删除墓碑，禁止复活。",
            )
            return

        mapping = self._database.scoped(account_id).execute(
            "SELECT target_document_id, target_object_id FROM learning_project_migrations"
            " WHERE account_id = ? AND source_document_id = ?",
            (account_id, source_document_id),
        ).fetchone()
        if mapping is not None and mapping["target_document_id"] is not None:
            target_exists = self._database.scoped(account_id).execute(
                "SELECT 1 FROM document_records r JOIN objects o ON o.object_id = r.object_id"
                " WHERE r.account_id = ? AND r.document_id = ?"
                " AND r.source = 'knowledge_base' AND o.status = 'active'",
                (account_id, str(mapping["target_document_id"])),
            ).fetchone()
            if target_exists is None:
                self.mark_target_deleted(
                    account_id,
                    str(mapping["target_object_id"]),
                    target_document_id=str(mapping["target_document_id"]),
                    reason="迁移目标已删除，禁止重新创建目标记录。",
                )
                return
        target = self._find_target(account_id, source_document_id, expected_hash)
        if target is None:
            target = self._create_target(account_id, source_document_id, content)
            if target is None:
                return
        target_document_id, target_object_id = target
        self._set_target(account_id, source_document_id, target_document_id, target_object_id)
        target_row = self._database.scoped(account_id).execute(
            "SELECT r.status, r.failure_stage, r.failure_reason, r.content_hash,"
            " o.status AS object_status, o.content_hash AS object_hash"
            " FROM document_records r JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND r.document_id = ?"
            " AND r.source = 'knowledge_base'",
            (account_id, target_document_id),
        ).fetchone()
        if target_row is None or str(target_row["object_status"]) != "active":
            self._mark_failed(account_id, source_document_id, "target", "目标知识库记录不可读取。")
            return
        if str(target_row["content_hash"]) != expected_hash or str(target_row["object_hash"]) != expected_hash:
            self._mark_failed(account_id, source_document_id, "target", "目标对象校验值不一致。")
            return
        target_status = str(target_row["status"])
        if target_status == "ready":
            self._set_status(
                account_id,
                source_document_id,
                ProjectMigrationStatus.COMPLETED,
            )
        elif target_status in {"queued", "parsing", "processing"}:
            self._set_status(
                account_id,
                source_document_id,
                ProjectMigrationStatus.WAITING_INGESTION,
            )
        else:
            self._mark_failed(
                account_id,
                source_document_id,
                "index",
                str(target_row["failure_reason"] or "目标知识库摄取未完成。"),
            )

    def _find_target(
        self, account_id: str, source_document_id: str, content_hash: str
    ) -> tuple[str, str] | None:
        mapping = self._database.scoped(account_id).execute(
            "SELECT target_document_id, target_object_id FROM learning_project_migrations"
            " WHERE account_id = ? AND source_document_id = ?",
            (account_id, source_document_id),
        ).fetchone()
        if mapping is not None and mapping["target_document_id"] is not None:
            target = self._database.scoped(account_id).execute(
                "SELECT r.document_id, r.object_id FROM document_records r"
                " JOIN objects o ON o.object_id = r.object_id"
                " WHERE r.account_id = ? AND r.document_id = ?"
                " AND r.source = 'knowledge_base' AND o.status = 'active'",
                (account_id, str(mapping["target_document_id"])),
            ).fetchone()
            if target is not None:
                return str(target["document_id"]), str(target["object_id"])
        target = self._database.scoped(account_id).execute(
            "SELECT r.document_id, r.object_id FROM document_records r"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND r.source = 'knowledge_base'"
            " AND r.content_hash = ? AND o.status = 'active'"
            " AND NOT EXISTS (SELECT 1 FROM learning_project_migration_tombstones t"
            "  WHERE t.account_id = r.account_id AND t.target_object_id = r.object_id)"
            " ORDER BY r.created_at, r.document_id LIMIT 1",
            (account_id, content_hash),
        ).fetchone()
        if target is None:
            return None
        return str(target["document_id"]), str(target["object_id"])

    def _create_target(
        self, account_id: str, source_document_id: str, content: bytes
    ) -> tuple[str, str] | None:
        source = self._database.scoped(account_id).execute(
            "SELECT project_name, source_filename, source_media_type, source_content_hash"
            " FROM learning_project_migrations"
            " WHERE account_id = ? AND source_document_id = ?",
            (account_id, source_document_id),
        ).fetchone()
        if source is None:
            return None
        content_hash = str(source["source_content_hash"])
        filename = self._target_filename(
            str(source["project_name"]), str(source["source_filename"]), content_hash
        )
        try:
            stored = self._objects.create_object(
                account_id,
                filename,
                content,
                media_type=str(source["source_media_type"]),
            )
            self._ingestion.enqueue(account_id, stored.object_id, None)
        except (StorageError, IngestionError) as exc:
            self._mark_failed(account_id, source_document_id, "target", str(exc))
            return None
        target = self._database.scoped(account_id).execute(
            "SELECT document_id FROM document_records"
            " WHERE account_id = ? AND object_id = ? AND source = 'knowledge_base'",
            (account_id, stored.object_id),
        ).fetchone()
        if target is None:
            self._mark_failed(
                account_id,
                source_document_id,
                "target",
                "目标知识库摄取记录未创建。",
            )
            return None
        return str(target["document_id"]), stored.object_id

    @staticmethod
    def _target_filename(project_name: str, filename: str, content_hash: str) -> str:
        suffix = f" [{content_hash[:8]}]"
        available = max(1, 255 - len(suffix))
        return f"{project_name} - {filename}"[:available] + suffix

    # ------------------------------------------------------------------
    # 台账投影与事务辅助
    # ------------------------------------------------------------------

    def _source_rows(self, account_id: str) -> list[sqlite3.Row]:
        return self._database.scoped(account_id).execute(
            "SELECT r.document_id AS source_document_id, r.object_id AS source_object_id,"
            " r.project_id, COALESCE(p.name, '已归档项目') AS project_name,"
            " COALESCE(o.original_filename, '已删除文件') AS source_filename,"
            " COALESCE(o.media_type, 'application/octet-stream') AS source_media_type,"
            " r.content_hash AS source_content_hash,"
            " COALESCE(o.content_length, 0) AS source_content_length,"
            " r.created_at AS source_created_at, r.parser_version"
            " FROM document_records r"
            " LEFT JOIN learning_projects p ON p.project_id = r.project_id"
            "  AND p.account_id = r.account_id"
            " LEFT JOIN objects o ON o.object_id = r.object_id"
            "  AND o.account_id = r.account_id"
            " WHERE r.account_id = ? AND r.source = 'project_file'"
            " ORDER BY r.created_at, r.document_id",
            (account_id,),
        ).fetchall()

    def _snapshot_conversations(self, account_id: str, run_id: str, now: str) -> None:
        rows = self._database.scoped(account_id).execute(
            "SELECT c.conversation_id, c.project_id,"
            " COALESCE(p.name, '已归档项目') AS project_name"
            " FROM conversations c LEFT JOIN learning_projects p"
            " ON p.project_id = c.project_id AND p.account_id = c.account_id"
            " WHERE c.account_id = ? AND c.project_id IS NOT NULL",
            (account_id,),
        ).fetchall()
        for row in rows:
            self._database.scoped(account_id).execute(
                "INSERT OR IGNORE INTO learning_project_migration_conversations"
                " (audit_id, run_id, account_id, conversation_id, project_id,"
                "  project_name, detached_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _new_id("conversation-migration"),
                    run_id,
                    account_id,
                    str(row["conversation_id"]),
                    str(row["project_id"]),
                    str(row["project_name"]),
                    now,
                ),
            )

    def _enqueue_open_items(self, account_id: str) -> None:
        self._queue.set_lease_seconds(
            self._queue_name(account_id), MIGRATION_LEASE_SECONDS
        )
        rows = self._database.scoped(account_id).execute(
            "SELECT source_document_id FROM learning_project_migrations"
            " WHERE account_id = ? AND status IN ('pending', 'waiting_ingestion')",
            (account_id,),
        ).fetchall()
        for row in rows:
            self._queue.enqueue(
                self._queue_name(account_id),
                self._task_key(account_id, str(row["source_document_id"])),
            )

    def _mark_missing_targets(self, account_id: str) -> None:
        rows = self._database.scoped(account_id).execute(
            "SELECT source_document_id, target_document_id, target_object_id"
            " FROM learning_project_migrations"
            " WHERE account_id = ? AND target_document_id IS NOT NULL"
            " AND status NOT IN ('target_deleted', 'source_deleted')",
            (account_id,),
        ).fetchall()
        for row in rows:
            target = self._database.scoped(account_id).execute(
                "SELECT 1 FROM document_records r JOIN objects o ON o.object_id = r.object_id"
                " WHERE r.account_id = ? AND r.document_id = ?"
                " AND r.source = 'knowledge_base' AND o.status = 'active'",
                (account_id, str(row["target_document_id"])),
            ).fetchone()
            if target is None:
                self.mark_target_deleted(
                    account_id,
                    str(row["target_object_id"]),
                    target_document_id=str(row["target_document_id"]),
                    reason="迁移目标已删除，禁止重新创建目标记录。",
                )

    def _finalize_run(self, account_id: str) -> None:
        run = self._database.scoped(account_id).execute(
            "SELECT run_id, status FROM learning_project_migration_runs"
            " WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if run is None:
            return
        counts = self._database.scoped(account_id).execute(
            "SELECT status, COUNT(*) AS count FROM learning_project_migrations"
            " WHERE account_id = ? GROUP BY status",
            (account_id,),
        ).fetchall()
        by_status = {str(row["status"]): int(row["count"]) for row in counts}
        total = sum(by_status.values())
        if total == by_status.get(ProjectMigrationStatus.COMPLETED.value, 0):
            now = _now()
            with self._database.transaction():
                self._database.scoped(account_id).execute(
                    "UPDATE conversations SET project_id = NULL"
                    " WHERE account_id = ? AND project_id IS NOT NULL"
                    " AND EXISTS (SELECT 1 FROM learning_project_migration_conversations a"
                    "  WHERE a.account_id = conversations.account_id"
                    "  AND a.conversation_id = conversations.conversation_id)",
                    (account_id,),
                )
                self._database.scoped(account_id).execute(
                    "UPDATE learning_project_migration_runs SET status = 'completed',"
                    " completed_at = COALESCE(completed_at, ?), updated_at = ?"
                    " WHERE account_id = ?",
                    (now, now, account_id),
                )
        elif by_status.get("pending", 0) or by_status.get("processing", 0) or by_status.get("waiting_ingestion", 0):
            with self._database.transaction():
                self._database.scoped(account_id).execute(
                    "UPDATE learning_project_migration_runs SET status = 'running',"
                    " updated_at = ? WHERE account_id = ? AND status != 'completed'",
                    (_now(), account_id),
                )
        elif str(run["status"]) != ProjectMigrationRunStatus.COMPLETED.value:
            with self._database.transaction():
                self._database.scoped(account_id).execute(
                    "UPDATE learning_project_migration_runs SET status = 'failed',"
                    " updated_at = ? WHERE account_id = ?",
                    (_now(), account_id),
                )

    def _summary_from_row(self, run: sqlite3.Row) -> LearningProjectMigrationSummary:
        account_id = str(run["account_id"])
        rows = self._database.scoped(account_id).execute(
            "SELECT * FROM learning_project_migrations"
            " WHERE account_id = ? ORDER BY created_at, migration_id",
            (account_id,),
        ).fetchall()
        items = [self._item_from_row(row) for row in rows]
        status_counts = {status.value: 0 for status in ProjectMigrationStatus}
        for item in items:
            status_counts[item.status.value] += 1
        conversations = [
            LearningProjectMigrationConversation(
                conversation_id=str(row["conversation_id"]),
                project_id=str(row["project_id"]),
                project_name=str(row["project_name"]),
                detached_at=datetime.fromisoformat(str(row["detached_at"])),
            )
            for row in self._database.scoped(account_id).execute(
                "SELECT conversation_id, project_id, project_name, detached_at"
                " FROM learning_project_migration_conversations"
                " WHERE account_id = ? ORDER BY detached_at, conversation_id",
                (account_id,),
            ).fetchall()
        ]
        return LearningProjectMigrationSummary(
            run_id=str(run["run_id"]),
            status=ProjectMigrationRunStatus(str(run["status"])),
            total_files=len(items),
            pending_files=status_counts[ProjectMigrationStatus.PENDING.value],
            processing_files=(
                status_counts[ProjectMigrationStatus.PROCESSING.value]
                + status_counts[ProjectMigrationStatus.WAITING_INGESTION.value]
            ),
            completed_files=status_counts[ProjectMigrationStatus.COMPLETED.value],
            failed_files=status_counts[ProjectMigrationStatus.FAILED.value],
            source_deleted_files=status_counts[ProjectMigrationStatus.SOURCE_DELETED.value],
            target_deleted_files=status_counts[ProjectMigrationStatus.TARGET_DELETED.value],
            total_bytes=int(run["total_bytes"]),
            completed_bytes=sum(
                item.content_length
                for item in items
                if item.status is ProjectMigrationStatus.COMPLETED
            ),
            failure_count=(
                status_counts[ProjectMigrationStatus.FAILED.value]
                + status_counts[ProjectMigrationStatus.SOURCE_DELETED.value]
                + status_counts[ProjectMigrationStatus.TARGET_DELETED.value]
            ),
            detached_conversation_count=len(conversations),
            compatibility_window=str(run["compatibility_window"]),
            started_at=datetime.fromisoformat(str(run["started_at"])),
            completed_at=(
                datetime.fromisoformat(str(run["completed_at"]))
                if run["completed_at"]
                else None
            ),
            items=items,
            conversations=conversations,
        )

    @staticmethod
    def _item_from_row(row: sqlite3.Row) -> LearningProjectMigrationItem:
        return LearningProjectMigrationItem(
            migration_id=str(row["migration_id"]),
            source_document_id=str(row["source_document_id"]),
            source_object_id=str(row["source_object_id"]),
            project_id=str(row["project_id"]),
            project_name=str(row["project_name"]),
            filename=str(row["source_filename"]),
            content_hash=str(row["source_content_hash"]),
            content_length=int(row["source_content_length"]),
            target_document_id=(
                str(row["target_document_id"]) if row["target_document_id"] else None
            ),
            target_object_id=(
                str(row["target_object_id"]) if row["target_object_id"] else None
            ),
            status=ProjectMigrationStatus(str(row["status"])),
            failure_stage=(str(row["failure_stage"]) if row["failure_stage"] else None),
            failure_reason=(
                str(row["failure_reason"]) if row["failure_reason"] else None
            ),
            retry_count=int(row["retry_count"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def _mark_failed(
        self, account_id: str, source_document_id: str, stage: str, reason: str
    ) -> None:
        self._set_status(
            account_id,
            source_document_id,
            ProjectMigrationStatus.FAILED,
            stage=stage,
            reason=reason,
        )

    def _mark_source_deleted(self, account_id: str, source_document_id: str) -> None:
        self._set_status(
            account_id,
            source_document_id,
            ProjectMigrationStatus.SOURCE_DELETED,
            stage="source",
            reason="原始项目文件已删除、撤回或对象缺失。",
        )

    def _set_target(
        self, account_id: str, source_document_id: str, document_id: str, object_id: str
    ) -> None:
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE learning_project_migrations SET target_document_id = ?,"
                " target_object_id = ?, updated_at = ?"
                " WHERE account_id = ? AND source_document_id = ?",
                (document_id, object_id, _now(), account_id, source_document_id),
            )

    def _set_status(
        self,
        account_id: str,
        source_document_id: str,
        status: ProjectMigrationStatus,
        *,
        stage: str | None = None,
        reason: str | None = None,
    ) -> None:
        completed_at = _now() if status is ProjectMigrationStatus.COMPLETED else None
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE learning_project_migrations SET status = ?, failure_stage = ?,"
                " failure_reason = ?, completed_at = COALESCE(?, completed_at),"
                " updated_at = ? WHERE account_id = ? AND source_document_id = ?",
                (
                    status.value,
                    stage,
                    reason[:1000] if reason else None,
                    completed_at,
                    _now(),
                    account_id,
                    source_document_id,
                ),
            )

    @staticmethod
    def _queue_name(account_id: str) -> str:
        # 账户 ID 只用于本地队列键，不作为用户可见路径或跨账户查询条件。
        return f"{MIGRATION_QUEUE_PREFIX}:{account_id}"

    @staticmethod
    def _task_key(account_id: str, source_document_id: str) -> str:
        return f"migration:{account_id}:{source_document_id}"


__all__ = [
    "MIGRATION_BATCH_SIZE",
    "MIGRATION_COMPATIBILITY_WINDOW",
    "ProjectMigrationError",
    "ProjectMigrationService",
    "project_writes_frozen",
]
