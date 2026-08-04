"""文件夹式学习项目的公开契约（Issue 19）。

学习项目是账户内的项目文件夹：对话可选归属到项目（移动不改变消息
历史），项目级文件与知识库材料、聊天附件共用同一摄取状态机。所有
资源都绑定稳定账户 ID，任何投影都不包含对象库路径、原文内容或凭据。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from bridges.contracts.chat import ChatMode
from bridges.contracts.ingestion import DocumentIngestionStatus


def _strip_required(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("项目名称不能为空。")
    return normalized


class LearningProjectCreateRequest(BaseModel):
    """新建学习项目请求；名称必填，描述可选。"""

    name: str = Field(min_length=1, max_length=120, description="项目名称。")
    description: str | None = Field(default=None, max_length=2000, description="可选项目描述。")

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return _strip_required(value)


class LearningProjectUpdateRequest(BaseModel):
    """更新项目名称或描述；至少提供一个字段。

    字段缺省表示保持不变；``description`` 显式传 null 表示清空描述；
    ``name`` 显式传 null 非法（名称永远不能为空），按 422 拒绝。
    """

    name: str | None = Field(default=None, min_length=1, max_length=120, description="新名称。")
    description: str | None = Field(
        default=None, max_length=2000, description="新描述；显式 null 表示清空。"
    )

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _strip_required(value)

    @model_validator(mode="after")
    def require_an_update(self) -> LearningProjectUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("至少提供名称或描述。")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("项目名称不能为空。")
        return self


class LearningProjectSummary(BaseModel):
    """学习项目列表项（含对话与文件计数，不含具体内容）。"""

    project_id: str = Field(description="稳定项目标识。")
    name: str = Field(description="项目名称。")
    description: str = Field(default="", description="项目描述。")
    conversation_count: int = Field(default=0, description="归属对话数。")
    file_count: int = Field(default=0, description="项目文件数。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近更新时间。")


class LearningProjectListProjection(BaseModel):
    """当前账户的学习项目列表，按最近更新倒序。"""

    projects: list[LearningProjectSummary] = Field(default_factory=list)


class LearningProjectConversation(BaseModel):
    """项目详情中的对话条目（不含消息正文）。"""

    conversation_id: str = Field(description="稳定对话标识。")
    title: str = Field(default="", description="对话标题。")
    mode: ChatMode = Field(default=ChatMode.COMPANION, description="对话当前模式。")
    pinned: bool = Field(default=False, description="是否置顶。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近活动时间。")


class LearningProjectDetail(BaseModel):
    """单个学习项目的完整投影（含归属对话列表）。"""

    project_id: str = Field(description="稳定项目标识。")
    name: str = Field(description="项目名称。")
    description: str = Field(default="", description="项目描述。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近更新时间。")
    conversations: list[LearningProjectConversation] = Field(default_factory=list)


class LearningProjectFile(BaseModel):
    """项目文件的列表投影（摄取状态与知识库材料共用同一词汇）。"""

    object_id: str = Field(description="稳定对象标识。")
    filename: str = Field(description="原始文件名。")
    content_length: int = Field(description="文件大小（字节）。")
    media_type: str = Field(description="服务端嗅探确认的媒体类型。")
    status: DocumentIngestionStatus = Field(description="摄取状态。")
    error: str | None = Field(default=None, description="摄取失败的中文原因。")
    created_at: datetime = Field(description="上传入队时间。")


class LearningProjectFileListProjection(BaseModel):
    """项目文件列表（最新上传在前）。"""

    files: list[LearningProjectFile] = Field(default_factory=list)
