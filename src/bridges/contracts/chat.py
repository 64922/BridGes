"""持久化对话、消息与生成状态的公开契约（Issue 11）。

这些模型定义聊天纵向切片对外可见的数据面：对话、消息（含助手尝试）、
生成状态、耗时与错误分类。所有资源都绑定稳定账户 ID，任何投影都不包含
凭据、工作流节点 ID、调试字段或系统提示。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class ChatMessageRole(StrEnum):
    """消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"


class ChatMode(StrEnum):
    """对话模式（ADR-0022：按对话持久化的双模式）。

    - ``companion``：日常陪伴——自然、有分寸的个性化陪伴；
    - ``study``：学习模式——因材施教老师合同，为后续画像、材料检索、
      教学规划、理解检查与适量测验保留编排接口。
    """

    COMPANION = "companion"
    STUDY = "study"


class ChatThinkingSummary(BaseModel):
    """面向用户的可公开思考摘要（Issue 14）。

    由结构化进度事件与可披露结果构造，绝不包含原始 Chain-of-Thought、
    系统提示、隐藏指令或逐 token 推理。四类内容均可为空列表。
    """

    steps: list[str] = Field(
        default_factory=list, description="可公开的处理步骤（进行中会增长）。"
    )
    evidence: list[str] = Field(
        default_factory=list, description="回答采用的证据/来源说明（可为空）。"
    )
    tools: list[str] = Field(
        default_factory=list, description="工具调用进度说明（可为空）。"
    )
    quality: list[str] = Field(
        default_factory=list, description="质量检查结论（完成/失败/停止的中文状态）。"
    )


class ChatMessageStatus(StrEnum):
    """助手消息的生成状态。

    - ``streaming``：正在生成（收到停止/断流/完成前都可能离开此状态）；
    - ``done``：完整回答已落库；
    - ``error``：生成失败（含断流），保留已接收正文，可点击重试；
    - ``stopped``：用户主动停止，保留已接收正文。
    """

    STREAMING = "streaming"
    DONE = "done"
    ERROR = "error"
    STOPPED = "stopped"


class ChatError(BaseModel):
    """聊天 API 的错误响应体（非泄漏、可操作的中文说明）。"""

    error: str = Field(description="稳定错误码，供前端分类处理。")
    message: str = Field(description="可操作的中文提示。")


class ChatAttachmentProjection(BaseModel):
    """聊天附件的安全公开投影；不包含宿主路径或对象存储密钥。"""

    object_id: str = Field(description="稳定对象标识。")
    original_filename: str = Field(description="用户选择时的显示文件名。")
    media_type: str = Field(description="服务端内容嗅探得到的安全媒体类型。")
    content_length: int = Field(description="文件大小（字节）。")
    content_hash: str = Field(description="内容 SHA-256 摘要。")
    conversation_id: str = Field(description="所属对话标识。")
    message_id: str | None = Field(default=None, description="绑定的用户消息标识。")
    status: str = Field(description="uploaded 或 bound。")
    created_at: datetime = Field(description="上传时间。")
    updated_at: datetime = Field(description="最近更新时间。")


class ChatMessageProjection(BaseModel):
    """单条消息的公开投影。

    助手消息的 ``attempt_number`` 从 1 开始，每次重试生成新的助手消息行
    （尝试号递增），历史尝试原样保留，绝不静默改写。
    """

    message_id: str = Field(description="稳定消息标识。")
    conversation_id: str = Field(description="所属对话标识。")
    role: ChatMessageRole = Field(description="用户或助手消息。")
    attempt_number: int = Field(default=1, description="助手尝试序号（用户消息恒为 1）。")
    status: ChatMessageStatus = Field(description="生成状态。")
    content: str = Field(default="", description="消息正文；失败/停止时保留已接收部分。")
    attachments: list[ChatAttachmentProjection] = Field(
        default_factory=list, description="该用户消息关联的安全附件。"
    )
    thinking: ChatThinkingSummary | None = Field(
        default=None,
        description="可公开的思考摘要；失败/停止/断流时保留已完成部分。",
    )
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文错误说明。")
    duration_ms: int | None = Field(default=None, description="本次生成耗时（毫秒）。")
    model_id: str | None = Field(default=None, description="实际使用的固定模型快照。")
    run_lock_id: str | None = Field(default=None, description="绑定的模型运行锁标识。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近更新时间。")


class ChatConversationSummary(BaseModel):
    """对话列表项（不含消息正文）。"""

    conversation_id: str = Field(description="稳定对话标识。")
    title: str = Field(default="", description="对话标题。")
    mode: ChatMode = Field(default=ChatMode.COMPANION, description="对话当前模式。")
    pinned: bool = Field(default=False, description="是否置顶。")
    project_id: str | None = Field(default=None, description="所属学习项目标识（可选）。")
    message_count: int = Field(default=0, description="消息条数（含所有尝试）。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近活动时间。")


class ChatModeEventProjection(BaseModel):
    """可见的模式切换事件（写入消息流，只影响后续消息）。"""

    event_id: str = Field(description="稳定事件标识。")
    conversation_id: str = Field(description="所属对话标识。")
    from_mode: ChatMode = Field(description="切换前模式。")
    to_mode: ChatMode = Field(description="切换后模式。")
    created_at: datetime = Field(description="切换时间。")


class ChatConversationListProjection(BaseModel):
    """当前账户的对话列表，按最近活动倒序。"""

    conversations: list[ChatConversationSummary] = Field(default_factory=list)


class ChatConversationProjection(BaseModel):
    """单个对话的完整投影（含消息历史与模式切换事件）。"""

    conversation_id: str = Field(description="稳定对话标识。")
    title: str = Field(default="", description="对话标题。")
    mode: ChatMode = Field(default=ChatMode.COMPANION, description="对话当前模式。")
    pinned: bool = Field(default=False, description="是否置顶。")
    project_id: str | None = Field(default=None, description="所属学习项目标识（可选）。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近活动时间。")
    messages: list[ChatMessageProjection] = Field(default_factory=list)
    mode_events: list[ChatModeEventProjection] = Field(
        default_factory=list, description="按时间排序的可见模式切换事件。"
    )


class ChatCreateRequest(BaseModel):
    """新建对话请求；标题可选，缺省由首条消息自动推导。

    ``mode`` 缺省为日常陪伴；学习项目新建学习对话时显式传 ``study``。
    """

    title: str | None = Field(default=None, max_length=120, description="可选标题。")
    mode: ChatMode = Field(default=ChatMode.COMPANION, description="对话初始模式。")
    project_id: str | None = Field(default=None, max_length=200, description="可选学习项目标识。")


class ChatConversationUpdateRequest(BaseModel):
    """更新对话标题或置顶状态；至少提供一个字段。"""

    title: str | None = Field(default=None, min_length=1, max_length=120, description="新标题。")
    pinned: bool | None = Field(default=None, description="是否置顶。")

    @model_validator(mode="after")
    def require_an_update(self) -> ChatConversationUpdateRequest:
        if self.title is None and self.pinned is None:
            raise ValueError("至少提供标题或置顶状态。")
        return self


class ChatModeSwitchRequest(BaseModel):
    """切换对话模式的请求。切换只影响后续消息，不重写历史回答。"""

    mode: ChatMode = Field(description="目标模式。")


class ChatModeSwitchResponse(BaseModel):
    """模式切换结果：切换后的对话投影与本次可见事件。"""

    conversation: ChatConversationProjection = Field(description="切换后的对话投影。")
    event: ChatModeEventProjection | None = Field(
        default=None, description="本次写入的可见事件；相同模式幂等切换时为 None。"
    )


class ChatMessageCreateRequest(BaseModel):
    """发送一条用户消息。"""

    content: str = Field(min_length=1, max_length=4000, description="用户消息正文。")
    attachment_ids: list[str] = Field(
        default_factory=list, max_length=10, description="已上传且待绑定到本条消息的对象标识。"
    )


class ChatStopResponse(BaseModel):
    """停止生成的结果投影。"""

    message: ChatMessageProjection = Field(description="停止后的消息状态。")
