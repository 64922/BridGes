"""持久化文档摄取状态机（Issue 17，GQ-05 迁移）。

把账户内的安全对象转换为可追溯、可恢复的本地检索材料：入队 → 解析
（账户内同内容复用解析缓存；图片可经 OCR 端口提取文字）→ 哈希分块
→ 向量化（凭据来源为唯一的全局百炼运行凭据，GQ-01/GQ-05）→ 写入版本化全文/向量索引。失败保留
原文件与中文原因，可从失败阶段安全重试且不产生重复分块；后台执行器
重启后按租约自动恢复未完成任务，重复领取保持幂等。Embedding 可用性
由运行时是否成功构造全局端口决定，不再依赖账户探测快照；调用失败时
全文索引仍独立完成，绝不写空向量或伪装向量就绪。
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple, cast

from bridges.contracts.ingestion import (
    DocumentIngestionProjection,
    DocumentIngestionStatus,
    IndexStatusProjection,
)
from bridges.contracts.knowledge_base import KnowledgeBaseMaterialProjection
from bridges.ingestion.chunker import TextChunk, chunk_document
from bridges.ingestion.embedding import EmbeddingError, EmbeddingPort
from bridges.ingestion.index import IndexWriteError, VersionedIndex, build_index_status
from bridges.ingestion.ocr import (
    OcrError,
    OcrPageRequest,
    OcrPageSummary,
    OcrPort,
)
from bridges.ingestion.parsers import (
    DOCX_PARSER_VERSION,
    IMAGE_PARSER_VERSION,
    MARKDOWN_PARSER_VERSION,
    PDF_PARSER_VERSION,
    TEXT_PARSER_VERSION,
    ParsedDocument,
    ParseError,
    parse_document,
)
from bridges.runtime.queue import (
    Claim,
    RetryKind,
    TaskPermanentError,
    TaskQueue,
    TaskRetryError,
    TaskWorker,
)
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError
from bridges.storage.repository import BridgesObjectRepository

#: 摄取任务队列名（task_claims 调度表）。
_INGESTION_QUEUE = "ingestion"

#: 每账户每轮最多领取的文档数（防止单轮阻塞过久）。
CLAIM_BATCH_SIZE = 5
#: 处理租约时长：领取后在该时间内必须完成，否则视为中断可重新领取。
LEASE_SECONDS = 1800
#: error 文档的自动重试上限：超过后不再自动领取（永久失败或凭据缺失），
#: 只等用户手动重试（mark_retry 重置计数）。
MAX_AUTO_RETRIES = 3
#: 摄取支持的文件媒体类型（与解析器覆盖范围一致）。
SUPPORTED_MEDIA_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}
#: document_records.source 的中文呈现（知识库材料投影使用）。
_SOURCE_DISPLAY = {
    "chat_attachment": "聊天附件",
    "knowledge_base": "本地上传",
    "project_file": "学习项目",
}


class _MaterialIndexContext(NamedTuple):
    """知识库材料投影共享的账户级索引上下文。"""

    embedding_available: bool
    vector_unavailable_reason: str | None
    index_version_id: str | None
    index_rebuilding: bool


class _ParseProvenance(NamedTuple):
    """一次解析的来源证据与脱敏 OCR 页数汇总（Issue 14）。

    - ``cache_hit``：本次直接复用了账户内解析缓存（未调用 Qwen、
      未新建模型锁）；
    - ``ocr_run_id``：产生当前解析文本的摄取 run（缓存命中时引用原始
      OCR/解析运行证据；非 OCR 解析为 None）；
    - ``pages_*``：本次 OCR 的脱敏页数汇总（非图片为 0），不含任何
      文本或内容。
    """

    cache_hit: bool
    ocr_run_id: str | None
    pages_total: int
    pages_succeeded: int
    pages_failed: int


class IngestionError(Exception):
    """摄取领域错误；message 为面向用户的中文原因。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ocr_run_id(account_id: str, document_id: str, claim_id: str) -> str:
    """一次摄取处理（一次领取执行轮）的 OCR 审计 run ID。

    run 绑定账户、文档、领取与当轮随机 token：同一轮处理内多页共享同一
    run，页级锁按 (run, page, call) 确定性派生、幂等合并。队列在租约
    恢复/退避重试时复用同一 ``claim_id``，因此必须叠加每轮随机 token——
    真正重新发起供应商请求（崩溃恢复重跑、worker 重试、缓存失效后重建）
    都产生新 run 与新调用序号，旧失败锁原样保留，绝不复用
    ``knowledge-base-ocr-{account_id}`` 或与旧锁发生内容冲突。
    """
    round_token = secrets.token_urlsafe(8)
    return f"ingestion-ocr:{account_id}:{document_id}:{claim_id}:{round_token}"


def parser_version_for(media_type: str) -> str:
    """返回媒体类型对应的当前解析器版本（缓存判等依据）。"""
    if media_type == "application/pdf":
        return PDF_PARSER_VERSION
    if media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return DOCX_PARSER_VERSION
    if media_type == "text/plain":
        return TEXT_PARSER_VERSION
    if media_type == "text/markdown":
        return MARKDOWN_PARSER_VERSION
    if media_type.startswith("image/"):
        return IMAGE_PARSER_VERSION
    return "unsupported-v1"


def display_ingestion_status(
    raw_status: str | None, lease_expires_at: str | None = None
) -> DocumentIngestionStatus:
    """把数据库原始状态映射为对外呈现状态（附件投影与详情共用）。

    ``parsing/processing`` 的领取租约过期且任务未完成时呈现
    ``recovery``（上次处理中断，后台恢复中）；无记录返回 ``none``。
    """
    if raw_status is None:
        return DocumentIngestionStatus.NONE
    if raw_status in ("parsing", "processing"):
        if lease_expires_at is not None:
            try:
                expired = datetime.fromisoformat(lease_expires_at) < datetime.now(UTC)
            except ValueError:
                expired = False
            if expired:
                return DocumentIngestionStatus.RECOVERY
        return DocumentIngestionStatus.PROCESSING
    if raw_status == "queued":
        return DocumentIngestionStatus.QUEUED
    if raw_status == "ready":
        return DocumentIngestionStatus.READY
    if raw_status == "empty":
        return DocumentIngestionStatus.EMPTY
    if raw_status == "error":
        return DocumentIngestionStatus.ERROR
    return DocumentIngestionStatus.QUEUED


class IngestionService:
    """摄取状态机的写模型与投影面。

    API 进程只使用入队/重试/投影路径（只读数据库 + 全局 Embedding
    端口可用性）；后台执行器进程使用领取/处理/索引维护路径（解析、
    图片 OCR、向量化与版本化索引）。
    """

    def __init__(
        self,
        *,
        database: BridgesDatabase,
        object_repository: BridgesObjectRepository,
        embedding: EmbeddingPort | None = None,
        ocr: OcrPort | None = None,
        index: VersionedIndex | None = None,
        task_queue: TaskQueue | None = None,
    ) -> None:
        self._database = database
        self._objects = object_repository
        self._embedding = embedding
        self._ocr = ocr
        self._index = index
        # Issue 43：领取/租约/退避/崩溃恢复由统一任务队列承担；本服务
        # 只提供「处理这一份文档」的 handler。
        self._task_queue = task_queue or TaskQueue(database)
        self._task_queue.set_lease_seconds(_INGESTION_QUEUE, LEASE_SECONDS)
        self._worker = TaskWorker(
            self._task_queue,
            _INGESTION_QUEUE,
            "ingestion-worker",
            self._handle_ingestion_claim,
            default_max_attempts=MAX_AUTO_RETRIES,
        )

    # ------------------------------------------------------------------
    # API 进程：入队 / 重试 / 投影
    # ------------------------------------------------------------------

    def enqueue(
        self,
        account_id: str,
        object_id: str,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        """为全局知识库对象创建摄取记录（幂等；不支持的媒体类型不入队）。

        两个历史参数保留用于读模型迁移，但不能再写入聊天附件或项目
        文件来源；旧 API 应在进入本服务前返回 410。
        """
        if conversation_id is not None or project_id is not None:
            raise IngestionError(
                "legacy_file_source_retired",
                "聊天附件和项目文件已退役，请改用全局知识库。",
                410,
            )
        object_row = self._database.scoped(account_id).execute(
            "SELECT content_hash, media_type, original_filename FROM objects"
            " WHERE object_id = ? AND account_id = ? AND status = 'active'",
            (object_id, account_id),
        ).fetchone()
        if object_row is None:
            raise IngestionError(
                "ingestion_not_found", "附件不存在或没有访问权限。", 404
            )
        if str(object_row["media_type"]) not in SUPPORTED_MEDIA_TYPES:
            return
        now = _now()
        source = "knowledge_base"
        try:
            with self._database.transaction():
                self._database.scoped(account_id).execute(
                    "INSERT OR IGNORE INTO document_records"
                    " (document_id, account_id, object_id, conversation_id, content_hash,"
                    "  parser_version, status, source, project_id, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                    (
                        f"doc-{object_id}",
                        account_id,
                        object_id,
                        conversation_id,
                        str(object_row["content_hash"]),
                        parser_version_for(str(object_row["media_type"])),
                        source,
                        project_id,
                        now,
                        now,
                    ),
                )
                # 领取型任务入队（同一事务）：后台执行器按统一队列契约
                # 领取处理，无需每轮全表扫描 queued 行。
                self._task_queue.enqueue(
                    _INGESTION_QUEUE, f"ingestion:{account_id}:{object_id}"
                )
        except sqlite3.Error as exc:
            raise IngestionError(
                "ingestion_save_failed", "摄取记录保存失败，请稍后重试。", 503
            ) from exc

    def mark_retry(self, account_id: str, object_id: str) -> DocumentIngestionProjection | None:
        """把失败文档重新入队（用户手动重试，重置自动重试计数）。

        非失败状态幂等返回当前投影。
        """
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE document_records SET status = 'queued', retry_count = 0,"
                " claimed_at = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE account_id = ? AND object_id = ? AND status = 'error'",
                (_now(), account_id, object_id),
            )
            # 手动重试重新入队（同一事务）：重置退避计数，恢复自动领取。
            self._task_queue.enqueue(
                _INGESTION_QUEUE, f"ingestion:{account_id}:{object_id}"
            )
        return self.projection(account_id, object_id)

    def projection(self, account_id: str, object_id: str) -> DocumentIngestionProjection | None:
        """返回文档摄取投影；无记录返回 None（前端显示"未索引"）。"""
        row = self._database.scoped(account_id).execute(
            "SELECT * FROM document_records WHERE account_id = ? AND object_id = ?",
            (account_id, object_id),
        ).fetchone()
        if row is None:
            return None
        return self._projection_from_row(account_id, row)

    def index_status(self, account_id: str) -> IndexStatusProjection:
        """返回当前账户索引状态（含向量可用性与版本链）。

        只读数据库即可构造：API 进程与 worker 进程都展示真实版本链，
        不依赖本实例是否挂载了索引写组件。向量可用性由运行时是否成功
        构造全局 Embedding 端口决定（GQ-05），不再依赖账户探测快照。
        """
        available, reason = self._embedding_availability()
        return build_index_status(
            self._database,
            account_id,
            embedding_probed=available,
            embedding_available=available,
            vector_unavailable_reason=reason,
        )

    def _embedding_availability(self) -> tuple[bool, str | None]:
        """Embedding 可用性：是否已构造全局 Embedding 端口。

        构造成功即视为可用；调用结果失败（认证/限流等）由调用方如实
        折叠为失败原因与关键词检索降级，绝不伪装向量就绪。
        """
        if self._embedding is None:
            return False, "向量索引不可用：全局 Embedding 能力未启用。"
        return True, None

    def _projection_from_row(
        self, account_id: str, row: sqlite3.Row
    ) -> DocumentIngestionProjection:
        raw_status = str(row["status"])
        lease = str(row["lease_expires_at"]) if row["lease_expires_at"] else None
        status = display_ingestion_status(raw_status, lease)
        _, vector_reason = self._embedding_availability()
        rebuilding = self._index_rebuilding(account_id)
        return DocumentIngestionProjection(
            document_id=str(row["document_id"]),
            object_id=str(row["object_id"]),
            conversation_id=(
                str(row["conversation_id"]) if row["conversation_id"] is not None else None
            ),
            status=status,
            parser_version=str(row["parser_version"]),
            content_hash=str(row["content_hash"]),
            title=str(row["title"]) if row["title"] is not None else None,
            page_count=int(row["page_count"]),
            section_count=int(row["section_count"]),
            chunk_count=int(row["chunk_count"]),
            vector_enabled=bool(row["vector_enabled"]),
            vector_indexed=bool(row["vector_indexed"]),
            failure_stage=(
                str(row["failure_stage"]) if row["failure_stage"] is not None else None
            ),
            failure_reason=(
                str(row["failure_reason"]) if row["failure_reason"] is not None else None
            ),
            retry_count=int(row["retry_count"]),
            index_rebuilding=rebuilding,
            vector_unavailable_reason=vector_reason,
            parse_cache_hit=bool(int(row["parse_cache_hit"])),
            ocr_evidence_run_id=(
                str(row["ocr_evidence_run_id"])
                if row["ocr_evidence_run_id"] is not None
                else None
            ),
            ocr_pages_total=int(row["ocr_pages_total"]),
            ocr_pages_succeeded=int(row["ocr_pages_succeeded"]),
            ocr_pages_failed=int(row["ocr_pages_failed"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def _index_rebuilding(self, account_id: str) -> bool:
        if self._index is None:
            return False
        row = self._database.connection.execute(
            "SELECT 1 FROM index_versions WHERE account_id = ? AND status = 'building' LIMIT 1",
            (account_id,),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # 全局知识库：材料投影 / 显式重建 / 级联删除（Issue 18）
    # ------------------------------------------------------------------

    def list_materials(self, account_id: str) -> list[KnowledgeBaseMaterialProjection]:
        """列出账户全部知识库材料投影（最新上传在前）。"""
        rows = self._database.scoped(account_id).execute(
            "SELECT r.*, o.original_filename, o.media_type, o.content_length"
            " FROM document_records r"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND r.source = 'knowledge_base'"
            " AND o.status = 'active'"
            " ORDER BY r.created_at DESC, r.rowid DESC",
            (account_id,),
        ).fetchall()
        context = self._material_index_context(account_id)
        return [self._material_from_row(row, context) for row in rows]

    def material_projection(
        self, account_id: str, object_id: str
    ) -> KnowledgeBaseMaterialProjection | None:
        """返回单份知识库材料投影；跨账户/不存在/非知识库来源返回 None。"""
        row = self._database.scoped(account_id).execute(
            "SELECT r.*, o.original_filename, o.media_type, o.content_length"
            " FROM document_records r"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND r.object_id = ?"
            " AND r.source = 'knowledge_base' AND o.status = 'active'",
            (account_id, object_id),
        ).fetchone()
        if row is None:
            return None
        return self._material_from_row(row, self._material_index_context(account_id))

    def rebuild_material(self, account_id: str, object_id: str) -> None:
        """显式触发版本化重建：清除派生分块与索引行并重新入队（幂等）。

        清理、状态重置在同一事务内完成；后台执行器接管后按当前索引合同
        重新解析/分块/索引。租约检查与清理在同一事务内重新读取后执行，
        防止检查到清理之间被后台执行器领取（TOCTOU）；文档正被处理时抛
        409 冲突；已处于 queued 时幂等返回，不重复清理。
        """
        if self._material_record(account_id, object_id) is None:
            raise IngestionError("material_not_found", "材料不存在或没有访问权限。", 404)
        with self._database.transaction():
            row = self._material_record(account_id, object_id)
            if row is None:
                raise IngestionError(
                    "material_not_found", "材料不存在或没有访问权限。", 404
                )
            if self._lease_active(row):
                raise IngestionError(
                    "material_processing",
                    "材料正在处理中，请等待当前处理完成后再重建。",
                    409,
                )
            if str(row["status"]) == "queued":
                return
            document_id = str(row["document_id"])
            self._purge_document_chunks(account_id, document_id)
            self._database.scoped(account_id).execute(
                "UPDATE document_records SET status = 'queued', retry_count = 0,"
                " title = NULL, page_count = 0, section_count = 0, chunk_count = 0,"
                " vector_enabled = 0, vector_indexed = 0,"
                " parse_cache_hit = 0, ocr_evidence_run_id = NULL,"
                " ocr_pages_total = 0, ocr_pages_succeeded = 0, ocr_pages_failed = 0,"
                " failure_stage = NULL, failure_reason = NULL,"
                " claimed_at = NULL, lease_expires_at = NULL,"
                " rebuild_requested = 1, updated_at = ?"
                " WHERE account_id = ? AND document_id = ?",
                (_now(), account_id, document_id),
            )
            # 重建重新入队（同一事务）：此前完成的队列行需复活为 queued，
            # 由后台执行器重新处理（队列行已完成时不会自动重领）。
            self._task_queue.enqueue(
                _INGESTION_QUEUE, f"ingestion:{account_id}:{object_id}"
            )

    def delete_material(self, account_id: str, object_id: str) -> None:
        """级联删除材料：分块、fts/向量派生行、解析缓存、摄取记录与对象。

        数据库级联在单个事务内完成，随后走既有 ``pending_cleanup`` 路径
        删除对象（共享内容哈希只移除行，不误删他人文件）。租约检查与级联
        在同一事务内重新读取后执行（防 TOCTOU）；文档正被后台任务处理时
        抛 409 冲突，可稍后安全重试。对象删除失败时数据库级联已提交，
        报 503 可重试错误，绝不伪装成材料不存在。
        """
        self._delete_scoped_material(
            account_id,
            object_id,
            lookup=lambda: self._material_record(account_id, object_id),
            not_found=IngestionError(
                "material_not_found", "材料不存在或没有访问权限。", 404
            ),
        )

    # ------------------------------------------------------------------
    # 学习项目文件：项目作用域的投影与级联删除（Issue 19）
    # ------------------------------------------------------------------

    def list_project_materials(
        self, account_id: str, project_id: str
    ) -> list[KnowledgeBaseMaterialProjection]:
        """列出项目全部文件投影（最新上传在前）；知识库列表不受影响。"""
        rows = self._database.scoped(account_id).execute(
            "SELECT r.*, o.original_filename, o.media_type, o.content_length"
            " FROM document_records r"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND r.source = 'project_file'"
            " AND r.project_id = ? AND o.status = 'active'"
            " ORDER BY r.created_at DESC, r.rowid DESC",
            (account_id, project_id),
        ).fetchall()
        context = self._material_index_context(account_id)
        return [self._material_from_row(row, context) for row in rows]

    def project_material_projection(
        self, account_id: str, project_id: str, object_id: str
    ) -> KnowledgeBaseMaterialProjection | None:
        """返回单份项目文件投影；跨账户/跨项目/不存在/非项目来源返回 None。"""
        row = self._database.scoped(account_id).execute(
            "SELECT r.*, o.original_filename, o.media_type, o.content_length"
            " FROM document_records r"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND r.object_id = ?"
            " AND r.source = 'project_file' AND r.project_id = ?"
            " AND o.status = 'active'",
            (account_id, object_id, project_id),
        ).fetchone()
        if row is None:
            return None
        return self._material_from_row(row, self._material_index_context(account_id))

    def delete_project_material(
        self, account_id: str, project_id: str, object_id: str
    ) -> None:
        """级联删除项目文件：与知识库材料同一级联语义与处理中 409 冲突。"""
        self._delete_scoped_material(
            account_id,
            object_id,
            lookup=lambda: self._project_material_record(
                account_id, project_id, object_id
            ),
            not_found=IngestionError(
                "file_not_found", "项目文件不存在或没有访问权限。", 404
            ),
        )

    def purge_document_rows(self, account_id: str, document_id: str) -> None:
        """在调用方事务内删除一份文档的全部分块行及 fts/向量派生行。

        ``transaction()`` 不支持嵌套：学习项目删除在单个事务内级联多份
        文件时直接复用本原语，而不是调用自行开启事务的服务级方法。
        """
        self._purge_document_chunks(account_id, document_id)

    def _project_material_record(
        self, account_id: str, project_id: str, object_id: str
    ) -> sqlite3.Row | None:
        """读取项目文件的摄取记录；非本项目/非项目来源一律不可见。"""
        row = self._database.scoped(account_id).execute(
            "SELECT document_id, status, lease_expires_at, content_hash"
            " FROM document_records WHERE account_id = ? AND object_id = ?"
            " AND source = 'project_file' AND project_id = ?",
            (account_id, object_id, project_id),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def _delete_scoped_material(
        self,
        account_id: str,
        object_id: str,
        *,
        lookup: Callable[[], sqlite3.Row | None],
        not_found: IngestionError,
    ) -> None:
        """按来源作用域级联删除一份材料（知识库材料与项目文件共用）。"""
        if lookup() is None:
            raise not_found
        with self._database.transaction():
            row = lookup()
            if row is None:
                raise not_found
            if self._lease_active(row):
                raise IngestionError(
                    "material_processing",
                    "材料正在被后台任务处理，暂时无法删除，请稍后重试。",
                    409,
                )
            document_id = str(row["document_id"])
            content_hash = str(row["content_hash"])
            self._purge_document_chunks(account_id, document_id)
            shared = self._database.scoped(account_id).execute(
                "SELECT 1 FROM document_records"
                " WHERE account_id = ? AND content_hash = ? AND document_id != ?"
                " LIMIT 1",
                (account_id, content_hash, document_id),
            ).fetchone()
            if shared is None:
                self._database.scoped(account_id).execute(
                    "DELETE FROM document_parse_cache"
                    " WHERE account_id = ? AND content_hash = ?",
                    (account_id, content_hash),
                )
            self._database.scoped(account_id).execute(
                "DELETE FROM document_records"
                " WHERE account_id = ? AND document_id = ?",
                (account_id, document_id),
            )
        try:
            self._objects.delete_object(account_id, object_id)
        except StorageError as exc:
            raise IngestionError(
                "material_delete_failed",
                "材料的索引数据已删除，但原文件删除未完全成功，请重试删除。",
                503,
            ) from exc

    def _material_record(self, account_id: str, object_id: str) -> sqlite3.Row | None:
        """读取知识库材料的摄取记录；非知识库来源（聊天附件）一律不可见。"""
        row = self._database.scoped(account_id).execute(
            "SELECT document_id, status, lease_expires_at, content_hash"
            " FROM document_records WHERE account_id = ? AND object_id = ?"
            " AND source = 'knowledge_base'",
            (account_id, object_id),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    @staticmethod
    def _lease_active(row: sqlite3.Row) -> bool:
        """文档正被后台任务处理（parsing/processing 且领取租约未过期）。"""
        lease = str(row["lease_expires_at"]) if row["lease_expires_at"] else None
        return (
            display_ingestion_status(str(row["status"]), lease)
            == DocumentIngestionStatus.PROCESSING
        )

    def _material_index_context(self, account_id: str) -> _MaterialIndexContext:
        """材料投影共享的索引上下文：向量可用性、活跃版本与重建标记。"""
        embedding_available, vector_reason = self._embedding_availability()
        active = self._database.connection.execute(
            "SELECT version_id FROM index_active WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        return _MaterialIndexContext(
            embedding_available=embedding_available,
            vector_unavailable_reason=vector_reason,
            index_version_id=(
                str(active["version_id"]) if active is not None else None
            ),
            index_rebuilding=self._index_rebuilding(account_id),
        )

    def _material_from_row(
        self,
        row: sqlite3.Row,
        context: _MaterialIndexContext,
    ) -> KnowledgeBaseMaterialProjection:
        lease = str(row["lease_expires_at"]) if row["lease_expires_at"] else None
        material_status = display_ingestion_status(str(row["status"]), lease)
        content_hash = str(row["content_hash"])
        return KnowledgeBaseMaterialProjection(
            document_id=str(row["document_id"]),
            object_id=str(row["object_id"]),
            filename=str(row["original_filename"]),
            media_type=str(row["media_type"]),
            content_length=int(row["content_length"]),
            content_hash=content_hash,
            content_hash_summary=content_hash[:12],
            source=_SOURCE_DISPLAY.get(str(row["source"]), str(row["source"])),
            status=material_status,
            title=str(row["title"]) if row["title"] is not None else None,
            chunk_count=int(row["chunk_count"]),
            failure_stage=(
                str(row["failure_stage"]) if row["failure_stage"] is not None else None
            ),
            failure_reason=(
                str(row["failure_reason"]) if row["failure_reason"] is not None else None
            ),
            retry_count=int(row["retry_count"]),
            vector_enabled=bool(row["vector_enabled"]),
            vector_indexed=bool(row["vector_indexed"]),
            parse_cache_hit=bool(int(row["parse_cache_hit"])),
            ocr_evidence_run_id=(
                str(row["ocr_evidence_run_id"])
                if row["ocr_evidence_run_id"] is not None
                else None
            ),
            ocr_pages_total=int(row["ocr_pages_total"]),
            ocr_pages_succeeded=int(row["ocr_pages_succeeded"]),
            ocr_pages_failed=int(row["ocr_pages_failed"]),
            embedding_available=context.embedding_available,
            vector_unavailable_reason=context.vector_unavailable_reason,
            index_version_id=context.index_version_id,
            index_rebuilding=context.index_rebuilding,
            usable_for_chat=material_status == DocumentIngestionStatus.READY,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    # ------------------------------------------------------------------
    # 后台执行器：领取 / 处理 / 索引维护 / 清理
    # ------------------------------------------------------------------

    def process_pending(self) -> str:
        """执行一轮后台摄取：清理孤立记录 → 队列领取处理 → 索引维护。

        Issue 43：领取/租约/退避/崩溃恢复由统一领取型任务队列承担；
        每文档创建/重试时入队，worker 按租约领取并逐件处理。
        """
        if self._index is None or self._embedding is None:
            return "worker: 摄取处理未启用（缺少索引或向量化组件）。"
        purged = self._purge_orphaned()
        handled, _failed = self._worker.drain(max_steps=CLAIM_BATCH_SIZE)
        # 索引维护与用户请求的重建不依赖文档领取状态（只依赖文档是否
        # 已完成），在本轮任务收敛后逐账户执行。
        accounts = self._accounts_with_records()
        for account_id in accounts:
            self._apply_rebuild_requests(account_id)
            self._maintain_account_index(account_id)
        return (
            f"worker: 摄取完成：本轮处理 {handled} 份文档，"
            f"清理 {purged} 条孤立摄取记录。"
        )

    def _accounts_with_records(self) -> list[str]:
        rows = self._database.connection.execute(
            "SELECT DISTINCT account_id FROM document_records"
        ).fetchall()
        return [str(row["account_id"]) for row in rows]

    def _handle_ingestion_claim(self, claim: Claim) -> None:
        """队列 handler：处理一份文档（只管干这一件活儿）。

        记录已清理或已收敛（ready/empty，如手动重试已成功）时跳过；
        失败按业务行状态区分永久失败（损坏/凭据缺失，_fail 已把
        retry_count 顶到上限）与可重试失败。
        """
        _, account_id, object_id = claim.task_key.split(":", 2)
        row = self._database.connection.execute(
            "SELECT document_id, status FROM document_records"
            " WHERE account_id = ? AND object_id = ?",
            (account_id, object_id),
        ).fetchone()
        if row is None:
            return  # 记录已清理（purge/删除）：跳过
        if str(row["status"]) in ("ready", "empty"):
            return  # 已收敛：跳过
        document_id = str(row["document_id"])
        # 业务行呈现「处理中」（parsing + 租约）：租约过期时前端呈现
        # recovery（上次处理中断，后台恢复中）；领取调度本身由队列承担。
        now = datetime.now(UTC)
        now_text = _now()
        with self._database.transaction():
            self._database.connection.execute(
                "UPDATE document_records SET status = 'parsing', claimed_at = ?,"
                " lease_expires_at = ?, updated_at = ?"
                " WHERE document_id = ? AND account_id = ?",
                (
                    now_text,
                    (now + timedelta(seconds=LEASE_SECONDS)).isoformat(
                        timespec="seconds"
                    ),
                    now_text,
                    document_id,
                    account_id,
                ),
            )
        self._process_document(
            account_id,
            document_id,
            ocr_run_id=_ocr_run_id(account_id, document_id, claim.claim_id),
        )
        row = self._database.connection.execute(
            "SELECT status, failure_reason, retry_count FROM document_records"
            " WHERE account_id = ? AND document_id = ?",
            (account_id, document_id),
        ).fetchone()
        if row is None or str(row["status"]) in ("ready", "empty"):
            return  # 成功收敛
        reason = str(row["failure_reason"]) or "处理失败"
        # 永久失败：_fail(permanent=True) 把 retry_count 顶到自动重试
        # 上限，不消耗队列重试预算（等用户手动 mark_retry）。
        if int(row["retry_count"]) >= MAX_AUTO_RETRIES:
            raise TaskPermanentError(reason)
        raise TaskRetryError(
            reason,
            retry_kind=RetryKind.EXPONENTIAL,
            max_attempts=MAX_AUTO_RETRIES,
        )

    def _apply_rebuild_requests(self, account_id: str) -> None:
        """执行用户显式请求的版本化重建（Issue 18）：全量重建产生新版本。

        只在文档完成本轮重新处理（不再 queued）后触发；重建失败保留
        标记下一轮重试，上一可用版本继续服务。
        """
        assert self._index is not None
        rows = self._database.connection.execute(
            "SELECT document_id FROM document_records"
            " WHERE account_id = ? AND rebuild_requested = 1 AND status != 'queued'",
            (account_id,),
        ).fetchall()
        if not rows:
            return
        available, _ = self._embedding_availability()
        try:
            self._index.rebuild(account_id, embedding_available=available)
        except IndexWriteError:
            return
        with self._database.transaction():
            self._database.connection.execute(
                "UPDATE document_records SET rebuild_requested = 0, updated_at = ?"
                " WHERE account_id = ? AND rebuild_requested = 1 AND status != 'queued'",
                (_now(), account_id),
            )

    def _process_document(
        self, account_id: str, document_id: str, *, ocr_run_id: str
    ) -> bool:
        """处理一份文档：读对象 → 解析（缓存复用）→ 分块 → 向量化 → 索引。

        ``ocr_run_id`` 是本次处理的摄取 run：图片 OCR 的页级模型锁按该
        run 关联（Issue 14），缓存命中则完全不调用 Qwen、不新建锁。
        """
        row = self._database.connection.execute(
            "SELECT * FROM document_records WHERE document_id = ? AND account_id = ?",
            (document_id, account_id),
        ).fetchone()
        if row is None:
            return False
        object_id = str(row["object_id"])
        try:
            content = self._objects.get_content(account_id, object_id)
        except StorageError as exc:
            self._fail(
                account_id, document_id, "read", f"读取原文件失败：{exc}。",
                permanent=True,
            )
            return False

        filename, media_type = self._object_meta(account_id, object_id)
        content_hash = str(row["content_hash"])
        try:
            parsed, provenance = self._parse_with_cache(
                account_id,
                content_hash,
                content,
                filename,
                media_type,
                document_id=document_id,
                object_id=object_id,
                ocr_run_id=ocr_run_id,
            )
        except ParseError as exc:
            # 损坏文件不会因重试变好：标记为永久失败，等用户手动重试。
            self._fail(account_id, document_id, "parse", exc.message, permanent=True)
            return False

        if not parsed.text.strip():
            self._mark_empty(account_id, document_id, parsed, provenance)
            return False

        chunks = chunk_document(parsed)
        if not chunks:
            self._mark_empty(account_id, document_id, parsed, provenance)
            return False

        if not self._write_chunk_rows(account_id, document_id, chunks):
            self._fail(account_id, document_id, "chunk", "分块写入失败，请检查数据目录。")
            return False

        available = self._embedding is not None
        vectors: list[list[float] | None] = [None] * len(chunks)
        if available:
            try:
                embedded = self._embedding.embed(  # type: ignore[union-attr]
                    account_id, [chunk.content for chunk in chunks]
                )
                vectors = list(embedded)
            except EmbeddingError:
                # GQ-05：向量化调用失败 → 全文索引仍独立完成（关键词检索
                # 诚实降级），不写空向量、不伪装向量就绪；能力恢复后由
                # ensure_contract 检测向量覆盖不全触发全量重建补向量。
                pass
        # 索引合同按实际向量化结果启用：向量全缺时按纯全文合同写入，
        # 恢复后重建补向量（保证 build_initial 的向量覆盖校验通过）。
        vectorized = available and all(vector is not None for vector in vectors)

        if not self._index_document(account_id, document_id, chunks, vectors, vectorized):
            return False

        self._mark_ready(
            account_id, document_id, parsed, chunks,
            vector_enabled=vectorized,
            vector_indexed=vectorized,
            provenance=provenance,
        )
        return True

    def _object_meta(self, account_id: str, object_id: str) -> tuple[str, str]:
        row = self._database.connection.execute(
            "SELECT original_filename, media_type FROM objects"
            " WHERE object_id = ? AND account_id = ?",
            (object_id, account_id),
        ).fetchone()
        if row is None:
            return object_id, "application/octet-stream"
        return str(row["original_filename"]), str(row["media_type"])

    def _parse_with_cache(
        self,
        account_id: str,
        content_hash: str,
        content: bytes,
        filename: str,
        media_type: str,
        *,
        document_id: str,
        object_id: str,
        ocr_run_id: str,
    ) -> tuple[ParsedDocument, _ParseProvenance]:
        """账户内同内容复用解析结果；缓存版本过期时重新解析并更新缓存。

        Issue 14 合同：缓存命中 → 不调用 Qwen、不新建模型锁，来源证据标记
        ``cache_hit`` 并引用产生缓存文本的原始 run；缓存失效后的重新识别
        走真实 OCR（新 run → 新页级锁），原锁不被覆盖。
        """
        version = parser_version_for(media_type)
        cache = self._database.connection.execute(
            "SELECT parsed_json, parser_version, ocr_run_id FROM document_parse_cache"
            " WHERE account_id = ? AND content_hash = ?",
            (account_id, content_hash),
        ).fetchone()
        if cache is not None and str(cache["parser_version"]) == version:
            try:
                parsed = ParsedDocument.from_json(str(cache["parsed_json"]))
            except (ValueError, TypeError, KeyError):
                pass  # 缓存损坏则重新解析
            else:
                cached_run = (
                    str(cache["ocr_run_id"]) if cache["ocr_run_id"] is not None else None
                )
                return parsed, _ParseProvenance(
                    cache_hit=True,
                    ocr_run_id=cached_run,
                    pages_total=0,
                    pages_succeeded=0,
                    pages_failed=0,
                )
        summary = OcrPageSummary()
        ocr_text: str | None = None
        if media_type.startswith("image/") and self._ocr is not None:
            summary = OcrPageSummary(pages_total=1)
            try:
                ocr_text = self._ocr.extract(
                    OcrPageRequest(
                        account_id=account_id,
                        object_id=object_id,
                        document_id=document_id,
                        run_id=ocr_run_id,
                        page_ordinal=1,
                        call_ordinal=1,
                        media_type=media_type,
                        content_hash=content_hash,
                        content=content,
                    )
                )
                summary = OcrPageSummary(pages_total=1, pages_succeeded=1)
            except OcrError:
                # 图片 OCR 失败时保留元数据并由解析器追加诚实标记；OCR
                # 是可选增强，不得把材料推进 error，也不得标成「已识别」。
                summary = OcrPageSummary(pages_total=1, pages_failed=1)
                ocr_text = None
        parsed = parse_document(
            content,
            filename,
            media_type,
            ocr_text=ocr_text,
        )
        # 缓存行记录「由哪个摄取 run 产生」，供命中方引用原始 OCR 运行
        # 证据或解析版本（Issue 14 AC）；非 OCR 解析同样记录产生 run。
        with self._database.transaction():
            self._database.connection.execute(
                "INSERT INTO document_parse_cache"
                " (account_id, content_hash, parser_version, parsed_json,"
                "  ocr_run_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(account_id, content_hash) DO UPDATE SET"
                "  parser_version = excluded.parser_version,"
                "  parsed_json = excluded.parsed_json,"
                "  ocr_run_id = excluded.ocr_run_id",
                (
                    account_id,
                    content_hash,
                    version,
                    parsed.to_json(),
                    ocr_run_id,
                    _now(),
                ),
            )
        evidence_run = ocr_run_id if summary.pages_total > 0 else None
        return parsed, _ParseProvenance(
            cache_hit=False,
            ocr_run_id=evidence_run,
            pages_total=summary.pages_total,
            pages_succeeded=summary.pages_succeeded,
            pages_failed=summary.pages_failed,
        )

    def _write_chunk_rows(
        self, account_id: str, document_id: str, chunks: list[TextChunk]
    ) -> bool:
        now = _now()
        try:
            with self._database.transaction():
                # 先移除本文档的旧分块及其在全部索引版本中的派生行：
                # 解析器版本升级后重试时，旧分块与新分块不得混写。
                self._purge_document_chunks(account_id, document_id)
                for chunk in chunks:
                    self._database.connection.execute(
                        "INSERT INTO document_chunks"
                        " (chunk_id, document_id, account_id, chunk_index, content,"
                        "  section_title, page_number, start_offset, end_offset,"
                        "  content_hash, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            f"{document_id}:{chunk.chunk_index}",
                            document_id,
                            account_id,
                            chunk.chunk_index,
                            chunk.content,
                            chunk.section_title,
                            chunk.page_number,
                            chunk.start_offset,
                            chunk.end_offset,
                            chunk.content_hash,
                            now,
                            now,
                        ),
                    )
            return True
        except sqlite3.Error:
            return False

    def _purge_document_chunks(
        self, account_id: str, document_id: str
    ) -> None:
        """删除一份文档的全部分块行及 fts/向量派生行（调用方在事务内）。"""
        chunk_ids = [
            str(chunk["chunk_id"])
            for chunk in self._database.connection.execute(
                "SELECT chunk_id FROM document_chunks"
                " WHERE document_id = ? AND account_id = ?",
                (document_id, account_id),
            ).fetchall()
        ]
        if not chunk_ids:
            return
        placeholders = ",".join("?" for _ in chunk_ids)
        # 各版本计数随派生行同步递减，保持投影与向量完备性校验准确。
        for row in self._database.connection.execute(
            "SELECT version_id, COUNT(*) AS count FROM fts_chunks"
            f" WHERE chunk_id IN ({placeholders}) GROUP BY version_id",
            chunk_ids,
        ).fetchall():
            self._database.connection.execute(
                "UPDATE index_versions SET chunk_count = MAX(0, chunk_count - ?)"
                " WHERE version_id = ?",
                (int(row["count"]), str(row["version_id"])),
            )
        for row in self._database.connection.execute(
            "SELECT version_id, COUNT(*) AS count FROM index_vectors"
            f" WHERE chunk_id IN ({placeholders}) GROUP BY version_id",
            chunk_ids,
        ).fetchall():
            self._database.connection.execute(
                "UPDATE index_versions SET vector_count = MAX(0, vector_count - ?)"
                " WHERE version_id = ?",
                (int(row["count"]), str(row["version_id"])),
            )
        self._database.connection.execute(
            f"DELETE FROM fts_chunks WHERE chunk_id IN ({placeholders})", chunk_ids
        )
        self._database.connection.execute(
            f"DELETE FROM index_vectors WHERE chunk_id IN ({placeholders})", chunk_ids
        )
        self._database.connection.execute(
            "DELETE FROM document_chunks WHERE document_id = ? AND account_id = ?",
            (document_id, account_id),
        )

    def _index_document(
        self,
        account_id: str,
        document_id: str,
        chunks: list[TextChunk],
        vectors: list[list[float] | None],
        embedding_available: bool,
    ) -> bool:
        assert self._index is not None
        try:
            chunk_rows = [
                dict(row)
                for row in self._database.connection.execute(
                    "SELECT chunk_id, content FROM document_chunks"
                    " WHERE document_id = ? AND account_id = ? ORDER BY chunk_index",
                    (document_id, account_id),
                ).fetchall()
            ]
            target = self._index.ensure_contract(
                account_id, embedding_available=embedding_available
            )
            if target.version_row is None:
                # 账户首份文档：以本文档为种子建立初始版本并原子切换。
                target = self._index.build_initial(
                    account_id,
                    chunk_rows=chunk_rows,
                    vectors=vectors,
                    embedding_available=embedding_available,
                )
            else:
                self._index.write_document_chunks(
                    account_id, target, chunk_rows=chunk_rows, vectors=vectors
                )
        except IndexWriteError as exc:
            self._fail(account_id, document_id, "index", str(exc))
            return False
        return True

    def _maintain_account_index(self, account_id: str) -> None:
        """确保账户索引满足当前合同与向量能力（合同变化触发全量重建）。"""
        assert self._index is not None
        available, _ = self._embedding_availability()
        try:
            self._index.ensure_contract(account_id, embedding_available=available)
        except IndexWriteError:
            # 重建失败：新版本标记 failed，上一可用版本继续服务，下轮重试。
            return
        active = self._index.active_version(account_id)
        vectorized = bool(active is not None and int(active["vector_count"]) > 0)
        with self._database.transaction():
            self._database.connection.execute(
                "UPDATE document_records SET vector_indexed = ?, updated_at = ?"
                " WHERE account_id = ? AND status = 'ready'",
                (1 if vectorized else 0, _now(), account_id),
            )

    def _mark_ready(
        self,
        account_id: str,
        document_id: str,
        parsed: ParsedDocument,
        chunks: list[TextChunk],
        *,
        vector_enabled: bool,
        vector_indexed: bool,
        provenance: _ParseProvenance,
    ) -> None:
        with self._database.transaction():
            self._database.connection.execute(
                "UPDATE document_chunks SET vector_status = ?, updated_at = ?"
                " WHERE document_id = ? AND account_id = ?",
                (
                    "indexed" if vector_indexed else "unavailable",
                    _now(),
                    document_id,
                    account_id,
                ),
            )
            self._database.connection.execute(
                "UPDATE document_records SET status = 'ready', title = ?,"
                " parser_version = ?, page_count = ?, section_count = ?,"
                " chunk_count = ?, vector_enabled = ?, vector_indexed = ?,"
                " parse_cache_hit = ?, ocr_evidence_run_id = ?,"
                " ocr_pages_total = ?, ocr_pages_succeeded = ?, ocr_pages_failed = ?,"
                " failure_stage = NULL, failure_reason = NULL, updated_at = ?"
                " WHERE document_id = ? AND account_id = ?",
                (
                    parsed.title,
                    parsed.parser_version,
                    parsed.page_count,
                    parsed.section_count,
                    len(chunks),
                    1 if vector_enabled else 0,
                    1 if vector_indexed else 0,
                    1 if provenance.cache_hit else 0,
                    provenance.ocr_run_id,
                    provenance.pages_total,
                    provenance.pages_succeeded,
                    provenance.pages_failed,
                    _now(),
                    document_id,
                    account_id,
                ),
            )

    def _mark_empty(
        self,
        account_id: str,
        document_id: str,
        parsed: ParsedDocument,
        provenance: _ParseProvenance,
    ) -> None:
        with self._database.transaction():
            self._database.connection.execute(
                "DELETE FROM document_chunks WHERE document_id = ? AND account_id = ?",
                (document_id, account_id),
            )
            self._database.connection.execute(
                "UPDATE document_records SET status = 'empty', title = ?,"
                " parser_version = ?, page_count = ?, section_count = ?,"
                " chunk_count = 0, vector_enabled = 0, vector_indexed = 0,"
                " parse_cache_hit = ?, ocr_evidence_run_id = ?,"
                " ocr_pages_total = ?, ocr_pages_succeeded = ?, ocr_pages_failed = ?,"
                " failure_stage = NULL,"
                " failure_reason = '文档没有可索引的文本内容。', updated_at = ?"
                " WHERE document_id = ? AND account_id = ?",
                (
                    parsed.title,
                    parsed.parser_version,
                    parsed.page_count,
                    parsed.section_count,
                    1 if provenance.cache_hit else 0,
                    provenance.ocr_run_id,
                    provenance.pages_total,
                    provenance.pages_succeeded,
                    provenance.pages_failed,
                    _now(),
                    document_id,
                    account_id,
                ),
            )

    def _fail(
        self,
        account_id: str,
        document_id: str,
        stage: str,
        reason: str,
        *,
        permanent: bool = False,
    ) -> None:
        """标记失败；permanent 的失败不再自动重试（等用户手动重试）。

        永久失败（文件损坏、凭据缺失等）把自动重试计数推到上限，避免
        后台每轮重复发起真实 API 调用；瞬态失败按上限自动重试。
        """
        retry_cap = MAX_AUTO_RETRIES if permanent else 0
        with self._database.transaction():
            self._database.connection.execute(
                "UPDATE document_records SET status = 'error', failure_stage = ?,"
                " failure_reason = ?, retry_count = MAX(retry_count, ?), updated_at = ?"
                " WHERE document_id = ? AND account_id = ?",
                (stage, reason[:1000], retry_cap, _now(), document_id, account_id),
            )

    # ------------------------------------------------------------------
    # 清理：对象删除后移除摄取记录与派生数据
    # ------------------------------------------------------------------

    def _purge_orphaned(self) -> int:
        rows = self._database.connection.execute(
            "SELECT r.document_id, r.account_id FROM document_records r"
            " LEFT JOIN objects o ON o.object_id = r.object_id"
            " WHERE o.object_id IS NULL OR o.status = 'pending_cleanup'"
        ).fetchall()
        purged = 0
        for row in rows:
            document_id = str(row["document_id"])
            account_id = str(row["account_id"])
            with self._database.transaction():
                self._purge_document_chunks(account_id, document_id)
                self._database.connection.execute(
                    "DELETE FROM document_records WHERE document_id = ?",
                    (document_id,),
                )
            purged += 1
        return purged
