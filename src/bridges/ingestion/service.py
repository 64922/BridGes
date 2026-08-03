"""持久化文档摄取状态机（Issue 17）。

把账户内的安全对象转换为可追溯、可恢复的本地检索材料：入队 → 解析
（账户内同内容复用解析缓存）→ 哈希分块 → 向量化（按能力探测门）→
写入版本化全文/向量索引。失败保留原文件与中文原因，可从失败阶段
安全重试且不产生重复分块；后台执行器重启后按租约自动恢复未完成任务，
重复领取保持幂等。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from bridges.contracts.ingestion import (
    DocumentIngestionProjection,
    DocumentIngestionStatus,
    IndexStatusProjection,
)
from bridges.credentials.probes import CapabilityProbeService
from bridges.ingestion.chunker import TextChunk, chunk_document
from bridges.ingestion.embedding import (
    EmbeddingError,
    EmbeddingPort,
    embedding_availability,
)
from bridges.ingestion.index import IndexWriteError, VersionedIndex, build_index_status
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
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError
from bridges.storage.repository import BridgesObjectRepository

#: 每账户每轮最多领取的文档数（防止单轮阻塞过久）。
CLAIM_BATCH_SIZE = 5
#: 处理租约时长：领取后在该时间内必须完成，否则视为中断可重新领取。
LEASE_SECONDS = 1800
#: error 文档的自动重试上限：超过后不再自动领取（永久失败或凭据缺失），
#: 只等用户手动重试（mark_retry 重置计数）。
MAX_AUTO_RETRIES = 3
#: 摄取支持的文件媒体类型（与解析器覆盖范围一致）。
_SUPPORTED_MEDIA_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}


class IngestionError(Exception):
    """摄取领域错误；message 为面向用户的中文原因。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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

    API 进程只使用入队/重试/投影路径（只读数据库 + 探测快照）；
    后台执行器进程使用领取/处理/索引维护路径（解析、向量化与版本化索引）。
    """

    def __init__(
        self,
        *,
        database: BridgesDatabase,
        object_repository: BridgesObjectRepository,
        probe_service: CapabilityProbeService | None = None,
        embedding: EmbeddingPort | None = None,
        index: VersionedIndex | None = None,
    ) -> None:
        self._database = database
        self._objects = object_repository
        self._probes = probe_service
        self._embedding = embedding
        self._index = index

    # ------------------------------------------------------------------
    # API 进程：入队 / 重试 / 投影
    # ------------------------------------------------------------------

    def enqueue(self, account_id: str, object_id: str, conversation_id: str) -> None:
        """为已上传对象创建摄取记录（幂等；不支持的媒体类型不入队）。"""
        object_row = self._database.scoped(account_id).execute(
            "SELECT content_hash, media_type, original_filename FROM objects"
            " WHERE object_id = ? AND account_id = ? AND status = 'active'",
            (object_id, account_id),
        ).fetchone()
        if object_row is None:
            raise IngestionError(
                "ingestion_not_found", "附件不存在或没有访问权限。", 404
            )
        if str(object_row["media_type"]) not in _SUPPORTED_MEDIA_TYPES:
            return
        now = _now()
        try:
            with self._database.transaction():
                self._database.scoped(account_id).execute(
                    "INSERT OR IGNORE INTO document_records"
                    " (document_id, account_id, object_id, conversation_id, content_hash,"
                    "  parser_version, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                    (
                        f"doc-{object_id}",
                        account_id,
                        object_id,
                        conversation_id,
                        str(object_row["content_hash"]),
                        parser_version_for(str(object_row["media_type"])),
                        now,
                        now,
                    ),
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
        不依赖本实例是否挂载了索引写组件。
        """
        available, reason, probed = self._embedding_availability(account_id)
        return build_index_status(
            self._database,
            account_id,
            embedding_probed=probed,
            embedding_available=available,
            vector_unavailable_reason=reason,
        )

    def _embedding_availability(
        self, account_id: str
    ) -> tuple[bool, str | None, bool]:
        if self._probes is None:
            return False, "向量索引不可用：Embedding 能力探测未启用。", False
        return embedding_availability(self._probes, account_id)

    def _projection_from_row(
        self, account_id: str, row: sqlite3.Row
    ) -> DocumentIngestionProjection:
        raw_status = str(row["status"])
        lease = str(row["lease_expires_at"]) if row["lease_expires_at"] else None
        status = display_ingestion_status(raw_status, lease)
        _, vector_reason, _ = self._embedding_availability(account_id)
        rebuilding = self._index_rebuilding(account_id)
        return DocumentIngestionProjection(
            document_id=str(row["document_id"]),
            object_id=str(row["object_id"]),
            conversation_id=str(row["conversation_id"]),
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
    # 后台执行器：领取 / 处理 / 索引维护 / 清理
    # ------------------------------------------------------------------

    def process_pending(self) -> str:
        """执行一轮后台摄取：清理孤立记录 → 逐账户领取处理 → 索引维护。"""
        if self._index is None or self._embedding is None:
            return "worker: 摄取处理未启用（缺少索引或向量化组件）。"
        purged = self._purge_orphaned()
        accounts = self._accounts_with_records()
        processed = 0
        for account_id in accounts:
            processed += self._process_account(account_id)
        return (
            f"worker: 摄取完成：本轮处理 {processed} 份文档，"
            f"清理 {purged} 条孤立摄取记录。"
        )

    def _accounts_with_records(self) -> list[str]:
        rows = self._database.connection.execute(
            "SELECT DISTINCT account_id FROM document_records"
        ).fetchall()
        return [str(row["account_id"]) for row in rows]

    def _process_account(self, account_id: str) -> int:
        claimed = self._claim(account_id, CLAIM_BATCH_SIZE)
        processed = 0
        for document_id in claimed:
            if self._process_document(account_id, document_id):
                processed += 1
        self._maintain_account_index(account_id)
        return processed

    def _claim(self, account_id: str, limit: int) -> list[str]:
        """按租约领取可处理文档（幂等：已被领取且租约未过期的不再领取）。

        领取条件同时用于外层 UPDATE 与内层排序子查询：最旧的 limit 份
        不可领取（已就绪/租约未过期/超出自动重试上限）时不会饿死后续
        文档；error 且超过自动重试上限的文档只等用户手动重试。
        """
        now = datetime.now(UTC)
        lease_expires = now + timedelta(seconds=LEASE_SECONDS)
        now_text = now.isoformat(timespec="seconds")
        condition = (
            "status = 'queued'"
            f" OR (status = 'error' AND retry_count < {MAX_AUTO_RETRIES})"
            " OR (status IN ('parsing', 'processing')"
            "     AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)"
        )
        try:
            with self._database.transaction():
                cursor = self._database.connection.execute(
                    "UPDATE document_records SET status = 'parsing', claimed_at = ?,"
                    " lease_expires_at = ?, retry_count = retry_count + 1,"
                    " updated_at = ?"
                    " WHERE account_id = ? AND (" + condition + ")"
                    " AND document_id IN ("
                    "   SELECT document_id FROM document_records"
                    "   WHERE account_id = ? AND (" + condition + ")"
                    "   ORDER BY created_at, document_id LIMIT ?"
                    " )",
                    (
                        now_text,
                        lease_expires.isoformat(timespec="seconds"),
                        now_text,
                        account_id,
                        now_text,
                        account_id,
                        now_text,
                        limit,
                    ),
                )
                if cursor.rowcount == 0:
                    return []
                rows = self._database.connection.execute(
                    "SELECT document_id FROM document_records"
                    " WHERE account_id = ? AND status = 'parsing' AND claimed_at = ?"
                    " ORDER BY created_at, document_id",
                    (account_id, now_text),
                ).fetchall()
                return [str(row["document_id"]) for row in rows]
        except sqlite3.Error:
            return []

    def _process_document(self, account_id: str, document_id: str) -> bool:
        """处理一份文档：读对象 → 解析（缓存复用）→ 分块 → 向量化 → 索引。"""
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
            parsed = self._parse_with_cache(
                account_id, content_hash, content, filename, media_type
            )
        except ParseError as exc:
            # 损坏文件不会因重试变好：标记为永久失败，等用户手动重试。
            self._fail(account_id, document_id, "parse", exc.message, permanent=True)
            return False

        if not parsed.text.strip():
            self._mark_empty(account_id, document_id, parsed)
            return False

        chunks = chunk_document(parsed)
        if not chunks:
            self._mark_empty(account_id, document_id, parsed)
            return False

        if not self._write_chunk_rows(account_id, document_id, chunks):
            self._fail(account_id, document_id, "chunk", "分块写入失败，请检查数据目录。")
            return False

        available, _, _ = self._embedding_availability(account_id)
        vectors: list[list[float] | None]
        if available:
            try:
                assert self._embedding is not None
                embedded = self._embedding.embed(
                    account_id, [chunk.content for chunk in chunks]
                )
            except EmbeddingError as exc:
                # 凭据缺失等不可重试错误不消耗自动重试额度
                self._fail(
                    account_id, document_id, "embed", exc.message,
                    permanent=not exc.retryable,
                )
                return False
            vectors = list(embedded)
        else:
            vectors = [None] * len(chunks)

        if not self._index_document(account_id, document_id, chunks, vectors, available):
            return False

        self._mark_ready(
            account_id, document_id, parsed, chunks,
            vector_enabled=available,
            vector_indexed=available,
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
    ) -> ParsedDocument:
        """账户内同内容复用解析结果；缓存版本过期时重新解析并更新缓存。"""
        version = parser_version_for(media_type)
        cache = self._database.connection.execute(
            "SELECT parsed_json, parser_version FROM document_parse_cache"
            " WHERE account_id = ? AND content_hash = ?",
            (account_id, content_hash),
        ).fetchone()
        if cache is not None and str(cache["parser_version"]) == version:
            try:
                return ParsedDocument.from_json(str(cache["parsed_json"]))
            except (ValueError, TypeError, KeyError):
                pass  # 缓存损坏则重新解析
        parsed = parse_document(content, filename, media_type)
        with self._database.transaction():
            self._database.connection.execute(
                "INSERT INTO document_parse_cache"
                " (account_id, content_hash, parser_version, parsed_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(account_id, content_hash) DO UPDATE SET"
                "  parser_version = excluded.parser_version,"
                "  parsed_json = excluded.parsed_json",
                (account_id, content_hash, version, parsed.to_json(), _now()),
            )
        return parsed

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
        available, _, _ = self._embedding_availability(account_id)
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
                    _now(),
                    document_id,
                    account_id,
                ),
            )

    def _mark_empty(self, account_id: str, document_id: str, parsed: ParsedDocument) -> None:
        with self._database.transaction():
            self._database.connection.execute(
                "DELETE FROM document_chunks WHERE document_id = ? AND account_id = ?",
                (document_id, account_id),
            )
            self._database.connection.execute(
                "UPDATE document_records SET status = 'empty', title = ?,"
                " parser_version = ?, page_count = ?, section_count = ?,"
                " chunk_count = 0, vector_enabled = 0, vector_indexed = 0,"
                " failure_stage = NULL,"
                " failure_reason = '文档没有可索引的文本内容。', updated_at = ?"
                " WHERE document_id = ? AND account_id = ?",
                (
                    parsed.title,
                    parsed.parser_version,
                    parsed.page_count,
                    parsed.section_count,
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
