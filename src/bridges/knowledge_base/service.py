"""全局本地知识库的材料上传、下载与账户授权访问（Issue 18）。

知识库材料是账户级全局检索材料：上传复用聊天附件的文件名校验、
媒体类型嗅探与大小限制，对象归账户所有；不绑定对话，上传即入队
摄取（conversation_id=None，source=knowledge_base），解析、分块与
版本化索引由后台执行器完成。跨账户或未知材料返回同一中文 404，
不泄漏资源是否存在。
"""

from __future__ import annotations

import hashlib

from bridges.chat.attachments import (
    MAX_ATTACHMENT_BYTES,
    ChatAttachmentError,
    sniff_media_type,
    validate_filename,
)
from bridges.contracts.knowledge_base import KnowledgeBaseMaterialProjection
from bridges.ingestion.service import (
    SUPPORTED_MEDIA_TYPES,
    IngestionError,
    IngestionService,
)
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError
from bridges.storage.repository import BridgesObjectRepository


class KnowledgeBaseError(Exception):
    """面向 API 的知识库错误；message 为面向用户的中文原因。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _not_found() -> KnowledgeBaseError:
    return KnowledgeBaseError("material_not_found", "材料不存在或没有访问权限。", 404)


class KnowledgeBaseService:
    """知识库材料的写模型与授权面；摄取细节委托给 :class:`IngestionService`。"""

    def __init__(
        self,
        database: BridgesDatabase,
        object_repository: BridgesObjectRepository,
        ingestion_service: IngestionService,
    ) -> None:
        self._database = database
        self._objects = object_repository
        self._ingestion = ingestion_service

    def upload(
        self, account_id: str, original_filename: str, content: bytes
    ) -> tuple[KnowledgeBaseMaterialProjection, bool]:
        """上传一份知识库材料并入队摄取；返回投影与是否为新建记录。"""
        try:
            filename = validate_filename(original_filename)
        except ChatAttachmentError as exc:
            raise KnowledgeBaseError(exc.code, exc.message, exc.status_code) from exc
        if not content:
            raise KnowledgeBaseError("empty_file", "文件为空，无法上传。")
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise KnowledgeBaseError(
                "file_too_large", "文件超过 10 MB 大小限制，请压缩后重试。", 413
            )
        try:
            media_type = sniff_media_type(filename, content)
        except ChatAttachmentError as exc:
            raise KnowledgeBaseError(exc.code, exc.message, exc.status_code) from exc
        if media_type not in SUPPORTED_MEDIA_TYPES:
            raise KnowledgeBaseError(
                "unsupported_media_type",
                "该文件类型暂不支持加入知识库，请使用 PDF、DOCX、TXT、Markdown 或常见图片。",
            )
        # 同账户同名同内容的活跃材料直接复用：重复上传幂等，不产生重复摄取。
        content_hash = hashlib.sha256(content).hexdigest()
        duplicate = self._find_active_material(account_id, filename, content_hash)
        if duplicate is not None:
            projection = self._ingestion.material_projection(account_id, duplicate)
            assert projection is not None
            return projection, False
        try:
            stored = self._objects.create_object(
                account_id, filename, content, media_type=media_type
            )
        except StorageError as exc:
            raise KnowledgeBaseError(
                "material_save_failed", "材料保存失败，请稍后重试。", 503
            ) from exc
        try:
            self._ingestion.enqueue(account_id, stored.object_id, None)
        except IngestionError as exc:
            raise KnowledgeBaseError(exc.code, exc.message, exc.status_code) from exc
        projection = self._ingestion.material_projection(account_id, stored.object_id)
        assert projection is not None
        return projection, True

    def list_materials(self, account_id: str) -> list[KnowledgeBaseMaterialProjection]:
        return self._ingestion.list_materials(account_id)

    def get_material(
        self, account_id: str, object_id: str
    ) -> KnowledgeBaseMaterialProjection:
        projection = self._ingestion.material_projection(account_id, object_id)
        if projection is None:
            raise _not_found()
        return projection

    def download(
        self, account_id: str, object_id: str
    ) -> tuple[KnowledgeBaseMaterialProjection, bytes]:
        projection = self.get_material(account_id, object_id)
        try:
            content = self._objects.get_content(account_id, object_id)
        except StorageError as exc:
            raise KnowledgeBaseError(
                "material_unavailable", "材料内容当前不可读取，请稍后重试。", 503
            ) from exc
        return projection, content

    def retry(self, account_id: str, object_id: str) -> KnowledgeBaseMaterialProjection:
        """把失败材料重新入队（重置自动重试计数）；非失败状态幂等。"""
        self.get_material(account_id, object_id)
        try:
            self._ingestion.mark_retry(account_id, object_id)
        except IngestionError as exc:
            raise KnowledgeBaseError(exc.code, exc.message, exc.status_code) from exc
        return self.get_material(account_id, object_id)

    def rebuild(self, account_id: str, object_id: str) -> KnowledgeBaseMaterialProjection:
        """显式触发版本化重建；处理中冲突与幂等语义由摄取服务保证。"""
        self.get_material(account_id, object_id)
        try:
            self._ingestion.rebuild_material(account_id, object_id)
        except IngestionError as exc:
            raise KnowledgeBaseError(exc.code, exc.message, exc.status_code) from exc
        return self.get_material(account_id, object_id)

    def delete(self, account_id: str, object_id: str) -> None:
        """级联删除材料及其派生索引数据；处理中冲突由摄取服务保证。"""
        self.get_material(account_id, object_id)
        try:
            self._ingestion.delete_material(account_id, object_id)
        except IngestionError as exc:
            raise KnowledgeBaseError(exc.code, exc.message, exc.status_code) from exc

    def _find_active_material(
        self, account_id: str, filename: str, content_hash: str
    ) -> str | None:
        row = self._database.scoped(account_id).execute(
            "SELECT r.object_id FROM document_records r"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND r.source = 'knowledge_base'"
            " AND o.original_filename = ? AND o.content_hash = ? AND o.status = 'active'"
            " ORDER BY r.created_at LIMIT 1",
            (account_id, filename, content_hash),
        ).fetchone()
        return str(row["object_id"]) if row is not None else None
