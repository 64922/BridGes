"""持久化对话、消息与生成状态的公开契约（Issue 11）。

这些模型定义聊天纵向切片对外可见的数据面：对话、消息（含助手尝试）、
生成状态、耗时与错误分类。所有资源都绑定稳定账户 ID，任何投影都不包含
凭据、工作流节点 ID、调试字段或系统提示。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bridges.arxiv_mcp.contracts import ArxivSearchProjection
from bridges.contracts.career import (
    CareerPlanningProcessState,
    CareerPlanningProjection,
)
from bridges.contracts.humanizer import (
    HumanizerProcessState,
    HumanizerResultProjection,
    HumanizerSkillInput,
)
from bridges.contracts.image import ImageTaskKind, ImageTaskProjection
from bridges.contracts.mcp import McpDataSlice, McpSensitiveConfirmation
from bridges.contracts.profile_extraction import ProfilePrivacyNotice
from bridges.contracts.profiles import ProfileNotification
from bridges.contracts.retrieval import RetrievalRoundProjection
from bridges.contracts.speech import ReadAloudProjection
from bridges.contracts.teaching import TeachingTurnProjection
from bridges.contracts.video import VideoTaskProjection
from bridges.routing import CapabilityRoute
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


class ContextNoteState(StrEnum):
    """上下文说明的呈现状态（Issue 27）。

    - ``ready``：本轮使用了画像切片，披露完整可用；
    - ``empty``：启用画像但没有匹配的任务相关记录（合法空态，不表示错误）；
    - ``off``：用户发送前关闭了画像使用，本轮无任何画像内容；
    - ``error``：切片编译失败，本轮已安全降级为不注入画像（回答照常）。
    """

    READY = "ready"
    EMPTY = "empty"
    OFF = "off"
    ERROR = "error"


class ContextNoteProfileItem(BaseModel):
    """上下文说明中的一条画像切片披露。

    披露记录的是回答当时使用的快照（值摘要、状态与版本），修正后历史
    回答保留此快照；``assertion_id`` 是来源记录链接，可跳转画像中心。
    """

    assertion_id: str = Field(description="来源画像记录标识（链接到画像中心）。")
    dimension: str = Field(description="画像类别（ProfileDimension 值）。")
    dimension_label: str = Field(description="画像类别中文标签。")
    value_summary: str = Field(description="本次使用的值摘要（截断，不超长）。")
    inclusion_reason: str = Field(description="用途：为什么本轮使用这条记录。")
    used_at: datetime = Field(description="本次使用时间（切片编译时间）。")
    status: str = Field(description="使用时的记录状态快照（active/frozen/...）。")
    version: int = Field(description="使用时的记录版本快照（可对比当前版本）。")
    applicable_scenes: list[str] = Field(
        default_factory=list,
        description="使用时的适用场景快照（修正时回传，不漂移授权范围）。",
    )


class ContextNoteProjection(BaseModel):
    """「本次上下文说明」可展开披露（Issue 27，ADR-0015）。

    回答展示使用的画像类别、材料类别、用途与来源链接；不暴露系统提示、
    隐藏提示或原始思维链。画像正文不复制到审计日志，这里只披露摘要。
    """

    state: ContextNoteState = Field(description="披露状态（ready/empty/off/error）。")
    profile_enabled: bool = Field(description="本轮是否启用了画像使用。")
    mode: ChatMode = Field(description="回答时的对话模式。")
    used_at: datetime = Field(description="披露生成时间。")
    profile_items: list[ContextNoteProfileItem] = Field(
        default_factory=list, description="本轮使用的画像切片披露列表。"
    )
    material_categories: list[str] = Field(
        default_factory=list, description="本轮使用的材料类别（检索层/联网来源等中文名）。"
    )
    excluded_count: int = Field(
        default=0, description="因范围/敏感/过期/冻结/撤回等排除的记录数。"
    )
    note: str = Field(description="面向用户的中文说明（含各状态的合法文案）。")


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
    route: CapabilityRoute | None = Field(
        default=None,
        description="本条消息绑定的自然语言能力路由快照（Issue 06）。",
    )
    teaching: TeachingTurnProjection | None = Field(
        default=None,
        description="本条学习模式消息的教学编排与证据门投影（Issue 23）。",
    )
    context_note: ContextNoteProjection | None = Field(
        default=None,
        description="本条助手消息的「本次上下文说明」披露（Issue 27）；无披露为 None。",
    )
    skill: dict[str, Any] | None = Field(
        default=None,
        description="用户消息的 SKILL 载荷快照（标识+任务契约，重试沿用）；普通消息为 None。",
    )
    humanizer: HumanizerResultProjection | None = Field(
        default=None,
        description="助手消息的 bridges-humanizer 结果投影（Issue 28）；非人味化消息为 None。",
    )
    career_planning: CareerPlanningProjection | None = Field(
        default=None,
        description="助手消息的生涯规划结果投影（Issue 29）；非规划消息为 None。",
    )
    image: ImageTaskProjection | None = Field(
        default=None,
        description="助手消息的图片任务/资产状态快照（Issue 31）；进行中渲染"
        "任务卡，成功后渲染资产卡；普通消息为 None。",
    )
    video: VideoTaskProjection | None = Field(
        default=None,
        description="助手消息的视频任务/资产状态快照（Issue 32）；进行中渲染"
        "任务卡，成功后渲染资产卡；普通消息为 None。",
    )
    mcp_call: McpCallMessageProjection | None = Field(
        default=None,
        description="助手消息的 MCP 插件调用结果投影（Issue 36）；非 MCP "
        "调用消息为 None。",
    )
    read_aloud: ReadAloudProjection | None = Field(
        default=None,
        description="本条助手消息的朗读状态快照（Issue 30）；未请求过朗读为 None。",
    )
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文错误说明。")
    duration_ms: int | None = Field(default=None, description="本次生成耗时（毫秒）。")
    model_id: str | None = Field(default=None, description="实际使用的固定模型快照。")
    run_lock_id: str | None = Field(default=None, description="绑定的模型运行锁标识。")
    # Issue 02：消息关联的持久化生成运行（生成中/已终态均可携带）。
    # 页面重开时据此判断是否需要恢复订阅（status + cursor），发送响应
    # 与读取投影共用同一来源，绝不重复创建用户消息或模型调用。
    active_run: ChatRunView | None = Field(
        default=None, description="关联的持久化生成运行视图（未运行过为 None）。"
    )
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近更新时间。")


class ChatPluginSelectionItem(BaseModel):
    """对话级插件选择条目（Issue 36）。

    ``kind`` 区分 SKILL 插件（Issue 34，含内置与用户包）与 MCP 服务器
    （Issue 35）；``plugin_id`` 为插件/服务器的稳定标识。选择随会话持久
    化，服务端逐项校验「当前账户已安装且启用」，失效项清洗并解释影响。
    """

    kind: Literal["skill", "mcp"] = Field(description="插件类别：skill 或 mcp。")
    plugin_id: str = Field(
        min_length=1, max_length=200, description="SKILL 插件标识或 MCP 服务器标识。"
    )


class RemovedPluginSelection(BaseModel):
    """被服务端清洗出对话选择的失效插件（含影响解释）。

    插件被停用、卸载或权限撤回后，从当前账户可用集合消失；读取会话或
    发送消息时按此解释影响，前端向用户说明后不再注入上下文。
    """

    kind: Literal["skill", "mcp"] = Field(description="插件类别。")
    plugin_id: str = Field(description="插件标识。")
    name: str = Field(description="插件显示名（读取时快照，已卸载也可解释）。")
    reason: str = Field(description="移除原因（中文，可操作）。")


class ChatConversationSummary(BaseModel):
    """对话列表项（不含消息正文）。"""

    conversation_id: str = Field(description="稳定对话标识。")
    title: str = Field(default="", description="对话标题。")
    mode: ChatMode = Field(default=ChatMode.COMPANION, description="对话当前模式。")
    mode_locked: bool = Field(
        default=False,
        description="首条用户消息提交后是否已锁定当前模式。",
    )
    pinned: bool = Field(default=False, description="是否置顶。")
    project_id: str | None = Field(default=None, description="所属学习项目标识（可选）。")
    legacy_project_name: str | None = Field(
        default=None, description="迁移前的历史学习项目名称快照。"
    )
    message_count: int = Field(default=0, description="消息条数（含所有尝试）。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近活动时间。")


class ChatModeEventProjection(BaseModel):
    """历史模式切换事件的只读投影；迁移与兼容窗口不再写入新事件。"""

    event_id: str = Field(description="稳定事件标识。")
    conversation_id: str = Field(description="所属对话标识。")
    from_mode: ChatMode = Field(description="切换前模式。")
    to_mode: ChatMode = Field(description="切换后模式。")
    created_at: datetime = Field(description="切换时间。")


class ChatConversationListProjection(BaseModel):
    """当前账户的对话列表，按最近活动倒序。"""

    conversations: list[ChatConversationSummary] = Field(default_factory=list)


class ChatConversationProjection(BaseModel):
    """单个对话的完整只读投影（含消息历史与历史模式事件）。"""

    conversation_id: str = Field(description="稳定对话标识。")
    title: str = Field(default="", description="对话标题。")
    mode: ChatMode = Field(default=ChatMode.COMPANION, description="对话当前模式。")
    mode_locked: bool = Field(
        default=False,
        description="首条用户消息提交后是否已锁定当前模式。",
    )
    pinned: bool = Field(default=False, description="是否置顶。")
    project_id: str | None = Field(default=None, description="所属学习项目标识（可选）。")
    legacy_project_name: str | None = Field(
        default=None, description="迁移前的历史学习项目名称快照。"
    )
    plugin_selection: list[ChatPluginSelectionItem] = Field(
        default_factory=list,
        description="本对话选中的有效插件（Issue 36）：SKILL 与 MCP 的"
        "当前账户可用集合子集，随对话持久化；停用/卸载/撤权后清洗。",
    )
    removed_selections: list[RemovedPluginSelection] = Field(
        default_factory=list,
        description="本次读取时从选择中清洗的失效插件（含中文影响解释）。",
    )
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近活动时间。")
    messages: list[ChatMessageProjection] = Field(default_factory=list)
    mode_events: list[ChatModeEventProjection] = Field(
        default_factory=list, description="按时间排序的历史只读模式事件。"
    )


class ChatCreateRequest(BaseModel):
    """新建对话请求；标题可选，缺省由首条消息自动推导。

    ``mode`` 缺省为日常陪伴；学习项目新建学习对话时显式传 ``study``。
    ``plugin_selection`` 为初始插件选择（新聊天首页先选插件再建对话）。
    """

    title: str | None = Field(default=None, max_length=120, description="可选标题。")
    mode: ChatMode = Field(default=ChatMode.COMPANION, description="对话初始模式。")
    project_id: str | None = Field(default=None, max_length=200, description="可选学习项目标识。")
    plugin_selection: list[ChatPluginSelectionItem] = Field(
        default_factory=list, max_length=20, description="初始插件选择（可选，逐项校验可用）。"
    )


class ChatConversationUpdateRequest(BaseModel):
    """更新对话标题、置顶状态、学习项目归属或插件选择；至少提供一个字段。

    ``project_id`` 字段缺省表示归属不变；显式传 null 表示解除归属。
    ``plugin_selection`` 全量替换当前选择；显式传空数组表示清空全部选择。
    """

    title: str | None = Field(default=None, min_length=1, max_length=120, description="新标题。")
    pinned: bool | None = Field(default=None, description="是否置顶。")
    project_id: str | None = Field(
        default=None, max_length=200, description="目标学习项目标识；显式 null 解除归属。"
    )
    plugin_selection: list[ChatPluginSelectionItem] | None = Field(
        default=None,
        max_length=20,
        description="插件选择全量替换；显式 [] 清空；缺省表示不变。",
    )

    @model_validator(mode="after")
    def require_an_update(self) -> ChatConversationUpdateRequest:
        if (
            self.title is None
            and self.pinned is None
            and "project_id" not in self.model_fields_set
            and "plugin_selection" not in self.model_fields_set
        ):
            raise ValueError("至少提供标题、置顶状态、学习项目归属或插件选择。")
        return self


class ChatModeSwitchRequest(BaseModel):
    """兼容窗口内的旧切换请求；接口已退役，服务端返回 HTTP 410。"""

    mode: ChatMode = Field(description="目标模式。")


class McpCallRequestPayload(BaseModel):
    """聊天内对选中 MCP 插件的真实调用载荷（Issue 36）。

    只允许调用当前对话已选中的 MCP 服务器（选择器持久化到会话）；工具
    名与入参由用户在调用对话框中明确指定，不依赖模型臆造。``data_slice``
    只携带本调用明确授权的文本与附件片段，不含画像、完整聊天历史或
    项目数据。
    """

    mcp_id: str = Field(
        min_length=1, max_length=200, description="目标 MCP 服务器标识（须被本对话选中）。"
    )
    tool: str = Field(min_length=1, max_length=120, description="要调用的工具名。")
    input: dict[str, Any] = Field(
        default_factory=dict, description="工具入参（不含秘密与私人正文）。"
    )
    data_slice: McpDataSlice = Field(
        default_factory=McpDataSlice, description="本次调用明确授权的数据切片。"
    )


class McpCallStatus(StrEnum):
    """消息内 MCP 调用结果的状态机（Issue 36）。"""

    LOADING = "loading"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SENSITIVE_PENDING = "sensitive_pending"
    DENIED = "denied"


class McpCallMessageProjection(BaseModel):
    """助手消息的 MCP 调用投影（随消息持久化，刷新可恢复）。

    成功只保留结果摘要与工具名，不保存服务器返回的完整私人正文；敏感
    挂起携带确认载荷供前端再次确认；拒绝后结果为 denied 终态。
    """

    status: McpCallStatus = Field(description="调用结果状态。")
    mcp_id: str = Field(description="被调用的 MCP 标识。")
    mcp_name: str | None = Field(default=None, description="MCP 显示名快照。")
    tool: str = Field(description="工具名。")
    input_summary: str = Field(
        default="", description="入参摘要（仅调用时快照，最长 200 字符）。"
    )
    result_summary: str | None = Field(
        default=None, description="成功结果摘要（最长 500 字符，不含完整正文）。"
    )
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文提示。")
    confirmation: McpSensitiveConfirmation | None = Field(
        default=None, description="敏感操作挂起的确认载荷。"
    )
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近更新时间。")


class ChatMessageCreateRequest(BaseModel):
    """发送一条用户消息。

    ``use_knowledge_base`` 为本轮开关：关闭后本轮请求、检索记录与引用
    均不包含全局知识库候选；当前明确附加的文件仍视为本轮授权。
    ``use_profile`` 为画像使用开关（Issue 27）：关闭后本轮模型请求、
    审计与上下文说明均不含任何画像切片，回答不个性化。
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000, description="用户消息正文。")
    use_knowledge_base: bool = Field(
        default=True, description="本轮是否启用全局知识库层（可在发送前关闭）。"
    )
    use_profile: bool = Field(
        default=True, description="本轮是否使用画像切片（可在发送前关闭）。"
    )
    skill_id: str | None = Field(
        default=None,
        description="内置 SKILL 注册标识（Issue 28）；携带时本轮走 SKILL 编排而非普通回答。",
    )
    skill_input: HumanizerSkillInput | None = Field(
        default=None,
        description="SKILL 任务载荷（契约模型校验，标识须为内置注册）。",
    )
    image: ImageRequestPayload | None = Field(
        default=None,
        description="图片生成/编辑请求载荷（Issue 31）；携带时本轮创建图片"
        "异步任务而非普通回答。",
    )
    video: VideoRequestPayload | None = Field(
        default=None,
        description="文生视频请求载荷（Issue 32）；携带时本轮创建视频异步"
        "任务而非普通回答。",
    )
    mcp_call: McpCallRequestPayload | None = Field(
        default=None,
        description="对选中 MCP 插件的调用载荷（Issue 36）；携带时本轮执行"
        "真实 MCP 调用而非普通回答，与 SKILL/图片/视频载荷互斥。",
    )


class ChatFirstTurnRequest(BaseModel):
    """原子创建新会话首轮（Issue 03）。

    首页发送第一条消息或调用任一功能时使用：服务端在同一事务内创建
    会话、用户消息、助手占位与 queued 运行，返回完整投影。``idempotency_key``
    抵御双击与网络重放（同键并发只产生一份数据）；``conversation_id``
    可选指定已预建的空会话（附件上传路径先建会话再发送），缺省新建。
    ``mode``/``project_id``/``plugin_selection`` 随首轮写入会话，不再
    依赖跳转前的 PATCH 往返。载荷互斥与校验语义同发送消息。
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000, description="首条用户消息正文。")
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        description="客户端生成的一次性幂等键；同账户同键重放返回同一份数据。",
    )
    conversation_id: str | None = Field(
        default=None,
        max_length=200,
        description="已预建的空会话标识（附件上传路径）；缺省在事务内新建会话。",
    )
    mode: ChatMode = Field(default=ChatMode.COMPANION, description="会话初始模式。")
    project_id: str | None = Field(
        default=None, max_length=200, description="可选学习项目标识。"
    )
    plugin_selection: list[ChatPluginSelectionItem] = Field(
        default_factory=list,
        max_length=20,
        description="初始插件选择（可选，逐项校验可用）。",
    )
    use_knowledge_base: bool = Field(
        default=True, description="首轮是否启用全局知识库层。"
    )
    use_profile: bool = Field(
        default=True, description="首轮是否使用画像切片（可在发送前关闭）。"
    )
    skill_id: str | None = Field(
        default=None,
        description="内置 SKILL 注册标识（Issue 28）；携带时首轮走 SKILL 编排。",
    )
    skill_input: HumanizerSkillInput | None = Field(
        default=None,
        description="SKILL 任务载荷（契约模型校验，标识须为内置注册）。",
    )
    image: ImageRequestPayload | None = Field(
        default=None,
        description="图片生成/编辑请求载荷（Issue 31）；携带时首轮创建图片任务。",
    )
    video: VideoRequestPayload | None = Field(
        default=None,
        description="文生视频请求载荷（Issue 32）；携带时首轮创建视频任务。",
    )
    mcp_call: McpCallRequestPayload | None = Field(
        default=None,
        description="对选中 MCP 插件的调用载荷（Issue 36）；与 SKILL/图片/视频载荷互斥。",
    )


class ChatFirstTurnResponse(BaseModel):
    """原子首轮的创建响应（Issue 03）。

    客户端收到成功响应后再导航到会话页：``conversation`` 为完整投影
    （含首轮消息与运行视图），``run_id``/``cursor`` 供立即订阅已持久化
    事件。``idempotent_replay`` 指示本次是幂等重放（HTTP 200）而非新建
    （HTTP 201），前端无须区分即可恢复同一会话。
    """

    conversation: ChatConversationProjection = Field(description="首轮后的完整会话投影。")
    run_id: str = Field(description="首轮生成运行标识。")
    cursor: int = Field(description="创建时已持久化的事件游标（started/profile）。")
    user_message: ChatMessageProjection = Field(description="首条用户消息投影。")
    assistant_message: ChatMessageProjection = Field(description="助手占位消息投影。")
    idempotent_replay: bool = Field(
        default=False, description="是否为同键重放（重放不产生新数据）。"
    )



class VideoRequestPayload(BaseModel):
    """文生视频请求（Issue 32）。

    只提供 ``prompt``：所有请求固定绑定 wan2.7-t2v-2026-06-12 与全局
    百炼运行凭据（ADR-0007：Wan 是模型矩阵唯一非 Qwen 系列例外），
    界面不提供模型选择。请求只携带提示词，不携带完整项目目录、画像
    或任何账户秘密。
    """

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(
        min_length=1, max_length=2000, description="视频生成要求。"
    )
    aspect_ratio: Literal["16:9", "9:16"] = Field(
        default="16:9", description="视频画面比例。"
    )
    size: Literal["1280*720", "720*1280"] = Field(
        default="1280*720", description="视频画面尺寸。"
    )
    duration_seconds: Literal[5, 10] = Field(
        default=5, description="视频时长（秒）。"
    )

    @model_validator(mode="after")
    def validate_video_parameters(self) -> VideoRequestPayload:
        expected_size = "720*1280" if self.aspect_ratio == "9:16" else "1280*720"
        if self.size != expected_size:
            raise ValueError("视频画面比例与尺寸不匹配。")
        return self


class ImageRequestPayload(BaseModel):
    """图片生成/编辑请求（Issue 31）。

    生成：只提供 ``prompt``；编辑：提供 ``prompt`` 且恰好提供一个来源
    （当前账户全局知识库图片材料 ``source_object_id``）。编辑来源归属
    在服务层校验，跨账户一律 404；历史 ``source_version_id`` 仅保留在
    投影和历史任务兼容模型中。
    请求只携带提示与来源引用，不携带完整项目目录、画像或任何账户秘密。
    """

    model_config = ConfigDict(extra="forbid")

    kind: ImageTaskKind = Field(description="生成或编辑。")
    prompt: str = Field(
        min_length=1, max_length=2000, description="生成要求或编辑指令。"
    )
    source_object_id: str | None = Field(
        default=None, description="编辑来源全局知识库图片对象标识（kind=edit 时可选）。"
    )


class ChatStopResponse(BaseModel):
    """停止生成的结果投影。"""

    message: ChatMessageProjection = Field(description="停止后的消息状态。")


class ChatRunStatus(StrEnum):
    """持久化生成运行的终态/进行态（Issue 02 运行状态机）。"""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"


class ChatRunView(BaseModel):
    """消息上对外暴露的运行视图（页面恢复订阅的游标来源）。"""

    run_id: str = Field(description="持久化运行标识。")
    status: ChatRunStatus = Field(description="运行状态。")
    stage: str | None = Field(
        default=None, description="运行当前阶段（Issue 06 统一阶段枚举）。"
    )
    cursor: int = Field(description="已持久化的最后事件游标；从下一游标恢复订阅。")
    attempt_count: int = Field(default=0, description="领取执行次数（租约恢复递增）。")
    created_at: datetime = Field(description="运行创建时间。")
    updated_at: datetime = Field(description="运行最近更新时间。")


class ChatRunStartedResponse(BaseModel):
    """发送/重试的创建响应：消息已落库、运行已入队，不再持有生成生命周期。

    客户端随后以 ``run_id``/``cursor`` 订阅已持久化事件（GET events 端点）；
    页面断开、刷新或切换会话都不会改变运行状态，只有显式停止或领域
    生命周期操作才会取消运行。
    """

    run_id: str = Field(description="持久化运行标识。")
    cursor: int = Field(description="创建时已持久化的事件游标（started/profile）。")
    user_message: ChatMessageProjection = Field(description="本轮用户消息投影。")
    assistant_message: ChatMessageProjection = Field(description="助手消息投影。")


class ChatStreamEventKind(StrEnum):
    """SSE 流事件类型（Issue 11/14 起稳定的事件名）。"""

    STARTED = "started"
    STAGE = "stage"
    DELTA = "delta"
    ERROR = "error"
    DONE = "done"
    PROFILE = "profile"
    HUMANIZER = "humanizer"
    CAREER = "career"
    IMAGE = "image"
    VIDEO = "video"
    MCP_CALL = "mcp_call"


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
    route: CapabilityRoute | None = Field(
        default=None, description="本轮自然语言能力路由快照。"
    )
    teaching: TeachingTurnProjection | None = Field(
        default=None, description="学习模式教学卡片初始状态。"
    )


class ChatStreamDeltaData(BaseModel):
    """delta 事件载荷：一段增量正文。"""

    kind: Literal["delta"] = "delta"
    message_id: str = Field(description="助手消息标识。")
    delta: str = Field(description="增量正文片段。")


class ChatStreamStageData(BaseModel):
    """stage 事件载荷：统一阶段转换（Issue 06 阶段埋点）。

    只携带阶段枚举、状态与脱敏耗时，绝不携带消息/文档/搜索正文；前端
    据此渲染真实阶段（检索/生成/检查/收尾），替代笼统"思考中"。
    """

    kind: Literal["stage"] = "stage"
    message_id: str = Field(description="助手消息标识。")
    stage: str = Field(description="统一阶段枚举值（queued/local_retrieval/…）。")
    status: Literal["active", "done", "timeout", "failed", "skipped"] = Field(
        description="阶段状态：active 进入；done 正常完成；timeout/failed/skipped 降级。"
    )
    duration_ms: int | None = Field(default=None, description="阶段耗时（毫秒，done 起携带）。")
    first_token_ms: int | None = Field(
        default=None, description="模型首可见块耗时（毫秒，仅 model_generation 阶段）。"
    )


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
    teaching: TeachingTurnProjection | None = Field(
        default=None, description="失败或取消时的学习模式教学卡片状态。"
    )


class ChatStreamDoneData(BaseModel):
    """done 事件载荷：完整消息投影（权威终态）。"""

    kind: Literal["done"] = "done"
    message_id: str = Field(description="助手消息标识。")
    message: ChatMessageProjection | None = Field(
        default=None, description="终态消息投影。"
    )


class ChatStreamProfileData(BaseModel):
    """profile 事件载荷：一次性隐私说明或兼容期画像通知。

    Issue 15 的自动写入不发送写入通知；``privacy_notice`` 只在账户首次
    触发自动画像时出现一次。``notifications`` 仅保留旧画像兼容测试和
    历史事件的读取形状。
    """

    kind: Literal["profile"] = "profile"
    message_id: str = Field(description="本轮用户消息标识。")
    notifications: list[ProfileNotification] = Field(
        default_factory=list, description="本轮产生的画像通知。"
    )
    privacy_notice: ProfilePrivacyNotice | None = Field(
        default=None, description="账户级首次自动画像隐私说明。"
    )


class ChatStreamHumanizerData(BaseModel):
    """humanizer 事件载荷：驱动人味化过程卡五态（Issue 28）。

    loading/empty/error/permission/recovery 五态的中文状态与当前步骤
    说明由此载荷下发；终态由 done 事件携带完整结果投影。
    """

    kind: Literal["humanizer"] = "humanizer"
    message_id: str = Field(description="助手消息标识。")
    state: HumanizerProcessState = Field(description="过程卡状态。")
    step_label: str = Field(description="当前步骤中文说明。")
    detail: str | None = Field(default=None, description="补充中文说明。")
    retryable: bool = Field(default=False, description="是否可重试。")
    progress_steps: list[str] = Field(
        default_factory=list, description="已完成的步骤中文轨迹。"
    )


class ChatStreamCareerData(BaseModel):
    """career 事件载荷：驱动生涯规划过程卡五态（Issue 29）。

    loading/empty/error/permission/recovery 五态的中文状态与当前步骤
    说明由此载荷下发；终态由 done 事件携带完整规划投影。
    """

    kind: Literal["career"] = "career"
    message_id: str = Field(description="助手消息标识。")
    state: CareerPlanningProcessState = Field(description="过程卡状态。")
    step_label: str = Field(description="当前步骤中文说明。")
    detail: str | None = Field(default=None, description="补充中文说明。")
    retryable: bool = Field(default=False, description="是否可重试。")
    progress_steps: list[str] = Field(
        default_factory=list, description="已完成的步骤中文轨迹。"
    )


class ChatStreamVideoData(BaseModel):
    """video 事件载荷：驱动消息内视频任务卡（Issue 32）。

    任务提交时下发 queued 状态快照；任务完成/失败/取消经后台执行器
    写回消息投影，前端刷新消息列表即可恢复（任务表是权威、消息投影
    是快照，刷新与重启后可恢复查询）。
    """

    kind: Literal["video"] = "video"
    message_id: str = Field(description="助手消息标识。")
    task: VideoTaskProjection = Field(description="任务状态快照。")


class ChatStreamImageData(BaseModel):
    """image 事件载荷：驱动消息内图片任务卡（Issue 31）。

    任务提交时下发 queued 状态快照；任务完成/失败/取消经后台执行器
    写回消息投影，前端刷新消息列表即可恢复（任务表是权威、消息投影
    是快照，刷新与重启后可恢复查询）。
    """

    kind: Literal["image"] = "image"
    message_id: str = Field(description="助手消息标识。")
    task: ImageTaskProjection = Field(description="任务状态快照。")


class ChatStreamMcpData(BaseModel):
    """mcp_call 事件载荷：驱动消息内 MCP 调用卡（Issue 36）。

    调用为同步执行：loading 状态随 started 后下发，成功/失败/敏感挂起
    为终态投影（写入消息列，刷新可恢复）；敏感挂起由前端确认对话框
    继续（approve/deny 走 chat 域路由，结果写回同一投影）。
    """

    kind: Literal["mcp_call"] = "mcp_call"
    message_id: str = Field(description="助手消息标识。")
    call: McpCallMessageProjection = Field(description="调用状态投影。")


class ChatStreamEvent(BaseModel):
    """一次 SSE 流事件的公开契约（前端类型与事件名从此模型生成）。

    ``data`` 以 ``kind`` 判别式联合建模，保证前端可从载荷判别事件类型，
    与帧头事件名保持一致；载荷形状由契约单一来源定义，不再由生成器
    手写字典与前端类型互相镜像。
    """

    event: ChatStreamEventKind = Field(description="事件名（SSE 帧头）。")
    data: Annotated[
        ChatStreamStartedData
        | ChatStreamStageData
        | ChatStreamDeltaData
        | ChatStreamErrorData
        | ChatStreamDoneData
        | ChatStreamProfileData
        | ChatStreamHumanizerData
        | ChatStreamCareerData
        | ChatStreamImageData
        | ChatStreamVideoData
        | ChatStreamMcpData,
        Field(discriminator="kind", description="事件载荷。"),
    ] = Field(description="事件载荷。")
