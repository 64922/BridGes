"""持久化对话、消息与生成状态的公开契约（Issue 11）。

这些模型定义聊天纵向切片对外可见的数据面：对话、消息（含助手尝试）、
生成状态、耗时与错误分类。所有资源都绑定稳定账户 ID，任何投影都不包含
凭据、工作流节点 ID、调试字段或系统提示。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from bridges.arxiv_mcp.contracts import ArxivSearchProjection
from bridges.contracts.retrieval import RetrievalRoundProjection
from bridges.web_search.contracts import WebSearchProjection


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
    ingestion_status: str = Field(
        default="none",
        description=(
            "文档摄取状态：queued/processing/ready/empty/error/recovery/none"
            "（none 表示该类型不支持索引或尚无摄取记录）。"
        ),
    )
    ingestion_error: str | None = Field(
        default=None, description="摄取失败的中文原因（无失败时为 None）。"
    )
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
    retrieval: RetrievalRoundProjection | None = Field(
        default=None,
        description="本条助手消息绑定的分层检索轮次（Issue 20）；无轮次为 None。",
    )
    web_search: WebSearchProjection | None = Field(
        default=None,
        description="本条助手消息绑定的公网搜索状态与真实引用（Issue 21）。",
    )
    arxiv_search: ArxivSearchProjection | None = Field(
        default=None,
        description="本条助手消息绑定的 arXiv 论文搜索状态与真实论文引用（Issue 22）。",
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
    """更新对话标题、置顶状态或学习项目归属；至少提供一个字段。

    ``project_id`` 字段缺省表示归属不变；显式传 null 表示解除归属。
    """

    title: str | None = Field(default=None, min_length=1, max_length=120, description="新标题。")
    pinned: bool | None = Field(default=None, description="是否置顶。")
    project_id: str | None = Field(
        default=None, max_length=200, description="目标学习项目标识；显式 null 解除归属。"
    )

    @model_validator(mode="after")
    def require_an_update(self) -> ChatConversationUpdateRequest:
        if (
            self.title is None
            and self.pinned is None
            and "project_id" not in self.model_fields_set
        ):
            raise ValueError("至少提供标题、置顶状态或学习项目归属。")
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
    """发送一条用户消息。

    ``use_knowledge_base`` 为本轮开关：关闭后本轮请求、检索记录与引用
    均不包含全局知识库候选；当前明确附加的文件仍视为本轮授权。
    """

    content: str = Field(min_length=1, max_length=4000, description="用户消息正文。")
    attachment_ids: list[str] = Field(
        default_factory=list, max_length=10, description="已上传且待绑定到本条消息的对象标识。"
    )
    use_knowledge_base: bool = Field(
        default=True, description="本轮是否启用全局知识库层（可在发送前关闭）。"
    )


class ChatStopResponse(BaseModel):
    """停止生成的结果投影。"""

    message: ChatMessageProjection = Field(description="停止后的消息状态。")


class ChatStreamEventKind(StrEnum):
    """SSE 流事件类型（Issue 11/14 起稳定的事件名）。"""

    STARTED = "started"
    DELTA = "delta"
    ERROR = "error"
    DONE = "done"


class ChatStreamStartedData(BaseModel):
    """started 事件载荷：消息已落库、生成开始，附带初始思考摘要。"""

    kind: Literal["started"] = "started"
    conversation_id: str = Field(description="对话标识。")
    user_message_id: str = Field(description="本轮用户消息标识。")
    message_id: str = Field(description="助手消息标识。")
    attempt_number: int = Field(description="助手尝试序号。")
    thinking: ChatThinkingSummary | None = Field(
        default=None, description="初始可公开思考摘要；前端据此展开思考区域。"
    )
    web_search: WebSearchProjection | None = Field(
        default=None, description="公网搜索初始状态；无触发时为 None。"
    )
    arxiv_search: ArxivSearchProjection | None = Field(
        default=None, description="arXiv 论文搜索初始状态；无触发时为 None。"
    )


class ChatStreamDeltaData(BaseModel):
    """delta 事件载荷：一段增量正文。"""

    kind: Literal["delta"] = "delta"
    message_id: str = Field(description="助手消息标识。")
    delta: str = Field(description="增量正文片段。")


class ChatStreamErrorDetail(BaseModel):
    """error 事件的错误分类：稳定码 + 可操作中文说明 + 是否可重试。"""

    code: str = Field(description="稳定错误码。")
    message: str = Field(description="可操作的中文提示。")
    retryable: bool = Field(description="是否可重试。")


class ChatStreamErrorData(BaseModel):
    """error 事件载荷：保留已接收正文、思考摘要与真实耗时。"""

    kind: Literal["error"] = "error"
    message_id: str = Field(description="助手消息标识。")
    error: ChatStreamErrorDetail = Field(description="错误分类。")
    thinking: ChatThinkingSummary | None = Field(
        default=None, description="失败/停止时保留的已完成思考摘要。"
    )
    duration_ms: int | None = Field(default=None, description="本次生成耗时（毫秒）。")
    web_search: WebSearchProjection | None = Field(
        default=None, description="失败或取消时的公网搜索状态。"
    )
    arxiv_search: ArxivSearchProjection | None = Field(
        default=None, description="失败或取消时的 arXiv 论文搜索状态。"
    )


class ChatStreamDoneData(BaseModel):
    """done 事件载荷：完整消息投影（权威终态）。"""

    kind: Literal["done"] = "done"
    message_id: str = Field(description="助手消息标识。")
    message: ChatMessageProjection | None = Field(
        default=None, description="终态消息投影。"
    )


class ChatStreamEvent(BaseModel):
    """一次 SSE 流事件的公开契约（前端类型与事件名从此模型生成）。

    ``data`` 以 ``kind`` 判别式联合建模，保证前端可从载荷判别事件类型，
    与帧头事件名保持一致；载荷形状由契约单一来源定义，不再由生成器
    手写字典与前端类型互相镜像。
    """

    event: ChatStreamEventKind = Field(description="事件名（SSE 帧头）。")
    data: Annotated[
        ChatStreamStartedData | ChatStreamDeltaData | ChatStreamErrorData | ChatStreamDoneData,
        Field(discriminator="kind", description="事件载荷。"),
    ] = Field(description="事件载荷。")
