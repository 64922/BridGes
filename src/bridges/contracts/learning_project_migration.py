"""学习项目资料迁移的公开契约（Issue 02）。

迁移投影只包含稳定标识、状态、计数和失败原因，不包含原文、对象路径或
跨账户信息。旧项目文件在兼容期仍由历史来源投影读取，新的目标记录只使用
``knowledge_base`` 来源。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ProjectMigrationStatus(StrEnum):
    """单个来源文件的可审计迁移状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    WAITING_INGESTION = "waiting_ingestion"
    COMPLETED = "completed"
    FAILED = "failed"
    SOURCE_DELETED = "source_deleted"
    TARGET_DELETED = "target_deleted"


class ProjectMigrationRunStatus(StrEnum):
    """账户级迁移运行状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class LearningProjectMigrationConversation(BaseModel):
    """解除项目归属前保存的历史对话关联。"""

    conversation_id: str = Field(description="历史对话标识。")
    project_id: str = Field(description="历史学习项目标识。")
    project_name: str = Field(description="迁移时的项目名称快照。")
    detached_at: datetime = Field(description="解除运行时项目归属的时间。")


class LearningProjectMigrationItem(BaseModel):
    """一个旧项目文件的迁移审计投影。"""

    migration_id: str = Field(description="稳定迁移审计标识。")
    source_document_id: str = Field(description="旧项目摄取记录标识。")
    source_object_id: str = Field(description="旧项目原始对象标识。")
    project_id: str = Field(description="旧学习项目标识。")
    project_name: str = Field(description="迁移时的项目名称快照。")
    filename: str = Field(description="旧文件原始文件名。")
    content_hash: str = Field(description="旧对象内容 SHA-256。")
    content_length: int = Field(description="旧对象字节数。")
    target_document_id: str | None = Field(default=None, description="目标知识库文档标识。")
    target_object_id: str | None = Field(default=None, description="目标知识库对象标识。")
    status: ProjectMigrationStatus = Field(description="当前迁移状态。")
    failure_stage: str | None = Field(default=None, description="失败阶段。")
    failure_reason: str | None = Field(default=None, description="可重试失败原因。")
    retry_count: int = Field(default=0, description="迁移重试次数。")
    created_at: datetime = Field(description="迁移审计创建时间。")
    updated_at: datetime = Field(description="最近迁移更新时间。")


class LearningProjectMigrationSummary(BaseModel):
    """账户级迁移摘要，可供机器读取、恢复与发布闸门使用。"""

    run_id: str = Field(description="账户级迁移运行标识。")
    status: ProjectMigrationRunStatus = Field(description="运行终态或进行中状态。")
    total_files: int = Field(description="纳管的旧项目文件数。")
    pending_files: int = Field(description="尚未处理的文件数。")
    processing_files: int = Field(description="处理中或等待摄取的文件数。")
    completed_files: int = Field(description="已完成并校验的文件数。")
    failed_files: int = Field(description="可重试失败的文件数。")
    source_deleted_files: int = Field(description="源文件已撤回或删除的文件数。")
    target_deleted_files: int = Field(description="目标被删除、由墓碑阻止复活的文件数。")
    total_bytes: int = Field(description="旧文件总字节数。")
    completed_bytes: int = Field(description="已完成迁移的字节数。")
    failure_count: int = Field(description="失败与被阻止项目总数。")
    detached_conversation_count: int = Field(description="已解除项目归属的历史对话数。")
    compatibility_window: str = Field(description="旧入口只读兼容窗口版本。")
    started_at: datetime = Field(description="迁移开始时间。")
    completed_at: datetime | None = Field(default=None, description="迁移完成时间。")
    items: list[LearningProjectMigrationItem] = Field(
        default_factory=list, description="逐文件迁移摘要。"
    )
    conversations: list[LearningProjectMigrationConversation] = Field(
        default_factory=list, description="历史对话解除归属审计。"
    )


class LearningProjectMigrationRetryRequest(BaseModel):
    """迁移失败项的手动重试请求。"""

    source_document_id: str | None = Field(
        default=None, description="可选的源文件标识；缺省时重试账户内所有失败项。"
    )


__all__ = [
    "LearningProjectMigrationConversation",
    "LearningProjectMigrationItem",
    "LearningProjectMigrationSummary",
    "LearningProjectMigrationRetryRequest",
    "ProjectMigrationRunStatus",
    "ProjectMigrationStatus",
]
