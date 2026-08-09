"""文件夹式学习项目服务（Issue 19）。

学习项目是账户内的历史项目文件夹：项目与项目文件仅保留只读兼容和迁移
查询，新的文件来源统一由全局知识库管理。所有查询经
``db.scoped(account_id)``，跨账户访问与不存在返回同一中文 404，不泄漏
资源是否存在。
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from bridges.chat.repository import ConversationRecord, ConversationRepository
from bridges.contracts.chat import ChatMode
from bridges.contracts.knowledge_base import KnowledgeBaseMaterialProjection
from bridges.contracts.learning_projects import (
    LearningProjectConversation,
    LearningProjectDetail,
    LearningProjectFile,
    LearningProjectSummary,
)
from bridges.ingestion.service import (
    IngestionService,
)
from bridges.learning_projects.migration import project_writes_frozen
from bridges.storage.database import BridgesDatabase, ScopedConnection
from bridges.storage.errors import StorageError
from bridges.storage.repository import (
    OBJECT_STATUS_PENDING_CLEANUP,
    BridgesObjectRepository,
)


class LearningProjectError(Exception):
    """面向 API 的学习项目错误；message 为面向用户的中文原因。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _project_not_found() -> LearningProjectError:
    return LearningProjectError(
        "project_not_found", "学习项目不存在或没有访问权限。", 404
    )


def _file_not_found() -> LearningProjectError:
    return LearningProjectError(
        "file_not_found", "项目文件不存在或没有访问权限。", 404
    )


class _Unset:
    """update_project 的字段哨兵类型：区分"未提供"与"显式 None（清空）"。"""


UNSET = _Unset()


@dataclass
class LearningProjectRecord:
    """学习项目行的领域记录。"""

    project_id: str
    account_id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()


#: 项目摘要投影的共享查询（含对话/文件计数子查询）；调用方只追加
#: WHERE 与 ORDER BY，列表与单条摘要永远使用同一份统计口径。
_PROJECT_SUMMARY_SELECT = (
    "SELECT p.project_id, p.account_id, p.name, p.description,"
    " p.created_at, p.updated_at,"
    " (SELECT COUNT(*) FROM conversations c"
    "   WHERE c.account_id = p.account_id AND c.project_id = p.project_id)"
    "   AS conversation_count,"
    " (SELECT COUNT(*) FROM document_records r"
    "   WHERE r.account_id = p.account_id AND r.project_id = p.project_id"
    "   AND r.source = 'project_file') AS file_count"
    " FROM learning_projects p"
)


class LearningProjectService:
    """学习项目的写模型与授权面；摄取细节委托给 :class:`IngestionService`。"""

    def __init__(
        self,
        database: BridgesDatabase,
        object_repository: BridgesObjectRepository,
        ingestion_service: IngestionService,
        conversation_repository: ConversationRepository,
    ) -> None:
        self._database = database
        self._objects = object_repository
        self._ingestion = ingestion_service
        self._conversations = conversation_repository

    # ------------------------------------------------------------------
    # 项目
    # ------------------------------------------------------------------

    def create_project(
        self, account_id: str, name: str, description: str | None
    ) -> LearningProjectSummary:
        self._ensure_project_writes_enabled(account_id)
        """新建学习项目并返回摘要投影。"""
        normalized_name = name.strip()
        if not normalized_name:
            raise LearningProjectError("invalid_name", "项目名称不能为空。", 422)
        project_id = secrets.token_urlsafe(16)
        now = _now()
        try:
            with self._database.transaction():
                self._database.scoped(account_id).execute(
                    "INSERT INTO learning_projects"
                    "(project_id, account_id, name, description, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        project_id,
                        account_id,
                        normalized_name,
                        (description or "").strip(),
                        now,
                        now,
                    ),
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转换为领域错误
            raise LearningProjectError(
                "project_save_failed", "学习项目保存失败，请稍后重试。", 503
            ) from exc
        return LearningProjectSummary(
            project_id=project_id,
            name=normalized_name,
            description=(description or "").strip(),
            conversation_count=0,
            file_count=0,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def list_projects(self, account_id: str) -> list[LearningProjectSummary]:
        """列出账户全部学习项目摘要（最近更新在前）。"""
        rows = self._database.scoped(account_id).execute(
            _PROJECT_SUMMARY_SELECT
            + " WHERE p.account_id = ?"
            " ORDER BY p.updated_at DESC, p.created_at DESC, p.project_id",
            (account_id,),
        ).fetchall()
        return [self._summary_from_row(row) for row in rows]

    def get_project(self, account_id: str, project_id: str) -> LearningProjectRecord:
        """读取项目记录；跨账户或不存在统一 404。"""
        row = self._database.scoped(account_id).execute(
            "SELECT project_id, account_id, name, description, created_at, updated_at"
            " FROM learning_projects WHERE project_id = ? AND account_id = ?",
            (project_id, account_id),
        ).fetchone()
        if row is None:
            raise _project_not_found()
        return self._record_from_row(row)

    def ensure_project_assignment_allowed(
        self, account_id: str, project_id: str
    ) -> None:
        """校验旧项目仍可被读取，但迁移后不可建立新的项目归属。"""
        self.get_project(account_id, project_id)
        self._ensure_project_writes_enabled(account_id)

    def get_detail(self, account_id: str, project_id: str) -> LearningProjectDetail:
        """返回项目详情：项目本身 + 归属对话（与最近会话同一排序）。"""
        record = self.get_project(account_id, project_id)
        rows = self._database.scoped(account_id).execute(
            "SELECT conversation_id, title, mode, pinned, created_at, updated_at"
            " FROM conversations WHERE account_id = ? AND project_id = ?"
            " ORDER BY pinned DESC, updated_at DESC, created_at DESC, conversation_id",
            (account_id, project_id),
        ).fetchall()
        return LearningProjectDetail(
            project_id=record.project_id,
            name=record.name,
            description=record.description,
            created_at=record.created_at,
            updated_at=record.updated_at,
            conversations=[
                LearningProjectConversation(
                    conversation_id=str(row["conversation_id"]),
                    title=str(row["title"]),
                    mode=ChatMode(str(row["mode"])),
                    pinned=bool(row["pinned"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                    updated_at=datetime.fromisoformat(str(row["updated_at"])),
                )
                for row in rows
            ],
        )

    def update_project(
        self,
        account_id: str,
        project_id: str,
        *,
        name: str | _Unset = UNSET,
        description: str | None | _Unset = UNSET,
    ) -> LearningProjectSummary:
        self._ensure_project_writes_enabled(account_id)
        """更新名称或描述（显式 None 清空描述）；写入即刷新 updated_at。"""
        self.get_project(account_id, project_id)
        assignments: list[str] = []
        values: list[str] = []
        if not isinstance(name, _Unset):
            normalized = name.strip()
            if not normalized:
                raise LearningProjectError("invalid_name", "项目名称不能为空。", 422)
            assignments.append("name = ?")
            values.append(normalized)
        if not isinstance(description, _Unset):
            assignments.append("description = ?")
            values.append((description or "").strip())
        if not assignments:
            return self._get_summary(account_id, project_id)
        assignments.append("updated_at = ?")
        values.append(_now())
        values.extend([project_id, account_id])
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE learning_projects SET "
                + ", ".join(assignments)
                + " WHERE project_id = ? AND account_id = ?",
                values,
            )
        return self._get_summary(account_id, project_id)

    def delete_project(
        self,
        account_id: str,
        project_id: str,
        *,
        contents: Literal["keep", "delete"],
    ) -> None:
        self._ensure_project_writes_enabled(account_id)
        """删除项目；``keep`` 保留对话（解除归属），``delete`` 连同对话删除。

        数据库级联在单个事务内完成：``transaction()`` 不支持嵌套，因此这里
        直接组合账户作用域 SQL 与摄取服务的事务内原语
        （``purge_document_rows``），而不是调用各自开启事务的服务级方法
        （``ChatService.delete_conversation``、``delete_for_conversation``、
        ``delete_material``）；对象物理清理在事务提交后走既有
        ``pending_cleanup`` 路径（可观察、可重试）。
        """
        self.get_project(account_id, project_id)
        scoped = self._database.scoped(account_id)
        with self._database.transaction():
            # streaming 检查必须在删除事务内执行：检查与删除之间的竞态窗口
            # 内新生成的回答会导致对话在生成中途被删（TOCTOU）。
            if contents == "delete":
                self._ensure_no_streaming(account_id, project_id)
            if contents == "keep":
                # 只解除归属：消息、模式事件与附件原样保留。
                scoped.execute(
                    "UPDATE conversations SET project_id = NULL"
                    " WHERE account_id = ? AND project_id = ?",
                    (account_id, project_id),
                )
            else:
                conversation_ids = [
                    str(row["conversation_id"])
                    for row in scoped.execute(
                        "SELECT conversation_id FROM conversations"
                        " WHERE account_id = ? AND project_id = ?",
                        (account_id, project_id),
                    ).fetchall()
                ]
                for conversation_id in conversation_ids:
                    self._delete_conversation_rows(scoped, account_id, conversation_id)
            self._delete_project_file_rows(scoped, account_id, project_id)
            scoped.execute(
                "DELETE FROM learning_projects"
                " WHERE project_id = ? AND account_id = ?",
                (project_id, account_id),
            )
        self._objects.run_pending_cleanups()

    def update_conversation_metadata(
        self,
        account_id: str,
        conversation_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        project_id: str | None | _Unset = UNSET,
    ) -> ConversationRecord:
        """在同一事务内更新对话的标题、置顶与学习项目归属。

        ``project_id`` 缺省（UNSET）表示归属不变；显式 None 解除归属。
        组合 PATCH（如同时改名并移动项目）只产生一条 UPDATE：任一字段
        校验失败或写入失败都不会留下"已移动但未改名"的半更新状态。
        与 ``ChatService.update_conversation`` 同一约定：仅在实际变化时
        写入并刷新 ``updated_at``；跨账户会话/项目统一安全 404，错误码与
        消息保持 ``conversation_not_found`` / ``invalid_title`` /
        ``project_not_found`` 不变。移动只改归属：消息、模式事件与附件
        绝不被触碰。
        """
        record = self._conversations.get_conversation(account_id, conversation_id)
        if record is None:
            raise LearningProjectError(
                "conversation_not_found", "对话不存在或没有访问权限。", 404
            )
        normalized_title = title.strip() if title is not None else None
        if title is not None and not normalized_title:
            raise LearningProjectError("invalid_title", "对话标题不能为空。", 422)
        if not isinstance(project_id, _Unset) and project_id is not None:
            self.get_project(account_id, project_id)
            self._ensure_project_writes_enabled(account_id)
        assignments: list[str] = []
        values: list[object] = []
        if normalized_title is not None and normalized_title != record.title:
            assignments.append("title = ?")
            values.append(normalized_title)
        if pinned is not None and pinned != record.pinned:
            assignments.append("pinned = ?")
            values.append(1 if pinned else 0)
        if not isinstance(project_id, _Unset) and project_id != record.project_id:
            assignments.append("project_id = ?")
            values.append(project_id)
        if assignments:
            assignments.append("updated_at = ?")
            values.append(_now())
            values.extend([conversation_id, account_id])
            with self._database.transaction():
                self._database.scoped(account_id).execute(
                    "UPDATE conversations SET "
                    + ", ".join(assignments)
                    + " WHERE conversation_id = ? AND account_id = ?",
                    values,
                )
            refreshed = self._conversations.get_conversation(account_id, conversation_id)
            assert refreshed is not None
            return refreshed
        return record

    # ------------------------------------------------------------------
    # 项目文件
    # ------------------------------------------------------------------

    def upload_file(
        self, account_id: str, project_id: str, data: bytes, filename: str
    ) -> tuple[LearningProjectFile, bool]:
        """拒绝新的项目文件写入；历史文件仅通过读取接口兼容。"""
        raise LearningProjectError(
            "legacy_file_source_retired",
            "项目文件已退役，请改用全局知识库。",
            410,
        )

    def list_files(self, account_id: str, project_id: str) -> list[LearningProjectFile]:
        """列出项目全部文件投影（最新上传在前）。"""
        self.get_project(account_id, project_id)
        return [
            self._file_from_projection(projection)
            for projection in self._ingestion.list_project_materials(
                account_id, project_id
            )
        ]

    def download_file(
        self, account_id: str, project_id: str, object_id: str
    ) -> tuple[LearningProjectFile, bytes]:
        """通过账户与项目授权下载文件原文，不暴露对象库路径。"""
        projection = self._ingestion.project_material_projection(
            account_id, project_id, object_id
        )
        if projection is None:
            raise _file_not_found()
        try:
            content = self._objects.get_content(account_id, object_id)
        except StorageError as exc:
            raise LearningProjectError(
                "file_unavailable", "文件内容当前不可读取，请稍后重试。", 503
            ) from exc
        return self._file_from_projection(projection), content

    def delete_file(self, account_id: str, project_id: str, object_id: str) -> None:
        """拒绝新的项目文件删除；物理清理由独立收缩审计处理。"""
        raise LearningProjectError(
            "legacy_file_source_retired",
            "项目文件已退役，请改用全局知识库。",
            410,
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _ensure_project_writes_enabled(self, account_id: str) -> None:
        if project_writes_frozen(self._database, account_id):
            raise LearningProjectError(
                "project_files_frozen",
                "学习项目已进入迁移兼容窗口，请改用全局知识库；历史项目仅支持读取。",
                409,
            )

    def _get_summary(self, account_id: str, project_id: str) -> LearningProjectSummary:
        rows = self._database.scoped(account_id).execute(
            _PROJECT_SUMMARY_SELECT
            + " WHERE p.account_id = ? AND p.project_id = ?",
            (account_id, project_id),
        ).fetchall()
        assert len(rows) == 1
        return self._summary_from_row(rows[0])

    def _ensure_no_streaming(self, account_id: str, project_id: str) -> None:
        """项目内任一对话有生成中的消息时拒绝级联删除（409）。"""
        row = self._database.scoped(account_id).execute(
            "SELECT 1 FROM messages m"
            " JOIN conversations c ON c.conversation_id = m.conversation_id"
            " WHERE m.account_id = ? AND c.project_id = ? AND m.status = 'streaming'"
            " LIMIT 1",
            (account_id, project_id),
        ).fetchone()
        if row is not None:
            raise LearningProjectError(
                "generation_in_progress",
                "项目中仍有回答正在生成，请先停止后再删除。",
                409,
            )

    def _delete_conversation_rows(
        self, scoped: ScopedConnection, account_id: str, conversation_id: str
    ) -> None:
        """在调用方事务内删除一个对话的全部行（消息/模式事件/附件级联）。

        与 ``ConversationRepository.delete_conversation`` +
        ``ChatAttachmentService.delete_for_conversation`` 同一顺序与语义，
        但不自行开启事务；附件的摄取记录由后台执行器的孤立记录清理接管
        （与对话删除的既有行为一致）。
        """
        scoped.execute(
            "DELETE FROM mode_events WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        )
        scoped.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        )
        attachment_rows = scoped.execute(
            "SELECT object_id FROM chat_attachments"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        ).fetchall()
        # 一并清理本会话的取消标记，避免残留无主记录。
        scoped.execute(
            "DELETE FROM chat_attachment_cancellations"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        )
        if attachment_rows:
            scoped.execute(
                "DELETE FROM chat_attachments"
                " WHERE account_id = ? AND conversation_id = ?",
                (account_id, conversation_id),
            )
            now = _now()
            for row in attachment_rows:
                scoped.execute(
                    "UPDATE objects SET status = ?, updated_at = ?"
                    " WHERE object_id = ? AND account_id = ?",
                    (
                        OBJECT_STATUS_PENDING_CLEANUP,
                        now,
                        str(row["object_id"]),
                        account_id,
                    ),
                )
        scoped.execute(
            "DELETE FROM conversations WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        )

    def _delete_project_file_rows(
        self, scoped: ScopedConnection, account_id: str, project_id: str
    ) -> None:
        """在调用方事务内级联删除项目全部文件（与 delete_material 同一语义）。

        分块/FTS/向量派生行经摄取服务的事务内原语清理；共享内容哈希的
        解析缓存只在无其他记录引用时移除；对象标记 pending_cleanup 后由
        调用方在事务提交后统一物理清理。
        """
        rows = scoped.execute(
            "SELECT document_id, object_id, content_hash FROM document_records"
            " WHERE account_id = ? AND project_id = ? AND source = 'project_file'",
            (account_id, project_id),
        ).fetchall()
        now = _now()
        for row in rows:
            document_id = str(row["document_id"])
            content_hash = str(row["content_hash"])
            self._ingestion.purge_document_rows(account_id, document_id)
            shared = scoped.execute(
                "SELECT 1 FROM document_records"
                " WHERE account_id = ? AND content_hash = ? AND document_id != ?"
                " LIMIT 1",
                (account_id, content_hash, document_id),
            ).fetchone()
            if shared is None:
                scoped.execute(
                    "DELETE FROM document_parse_cache"
                    " WHERE account_id = ? AND content_hash = ?",
                    (account_id, content_hash),
                )
            scoped.execute(
                "DELETE FROM document_records"
                " WHERE account_id = ? AND document_id = ?",
                (account_id, document_id),
            )
            scoped.execute(
                "UPDATE objects SET status = ?, updated_at = ?"
                " WHERE object_id = ? AND account_id = ?",
                (OBJECT_STATUS_PENDING_CLEANUP, now, str(row["object_id"]), account_id),
            )

    def _find_active_file(
        self, account_id: str, project_id: str, filename: str, content_hash: str
    ) -> str | None:
        row = self._database.scoped(account_id).execute(
            "SELECT r.object_id FROM document_records r"
            " JOIN objects o ON o.object_id = r.object_id"
            " WHERE r.account_id = ? AND r.source = 'project_file'"
            " AND r.project_id = ?"
            " AND o.original_filename = ? AND o.content_hash = ? AND o.status = 'active'"
            " ORDER BY r.created_at LIMIT 1",
            (account_id, project_id, filename, content_hash),
        ).fetchone()
        return str(row["object_id"]) if row is not None else None

    @staticmethod
    def _file_from_projection(
        projection: KnowledgeBaseMaterialProjection,
    ) -> LearningProjectFile:
        return LearningProjectFile(
            object_id=projection.object_id,
            filename=projection.filename,
            content_length=projection.content_length,
            media_type=projection.media_type,
            status=projection.status,
            error=projection.failure_reason,
            created_at=projection.created_at,
        )

    @staticmethod
    def _summary_from_row(row: sqlite3.Row) -> LearningProjectSummary:
        return LearningProjectSummary(
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            conversation_count=int(row["conversation_count"]),
            file_count=int(row["file_count"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> LearningProjectRecord:
        return LearningProjectRecord(
            project_id=str(row["project_id"]),
            account_id=str(row["account_id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
