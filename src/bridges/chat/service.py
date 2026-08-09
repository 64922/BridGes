"""持久化聊天服务（Issue 11 纵向切片）。

服务负责对话/消息的账户隔离持久化、生成状态机（streaming → done /
error / stopped）、用户级重试（每次重试新建助手尝试，历史原样保留）
与模型运行锁落库。回合编排（模式路由/检索/切片编译/提示词组装/流式
收敛）已收敛到深模块 ``bridges.chat.turn.TurnOrchestrator``（Issue 42
架构加深），本服务经 ``stream_generation`` 委托；真实供应商调用经由
``ModelGateway.stream`` 完成，失败以稳定错误码返回，由 API 层映射为
可操作中文提示。
"""

from __future__ import annotations

import contextlib
import secrets
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from bridges import __version__
from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamEvent
from bridges.arxiv_mcp.contracts import ArxivSearchProjection, ArxivSearchStatus
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.chat.attachments import ChatAttachmentService
from bridges.chat.lifecycle import GenerationLifecycle
from bridges.chat.routing import NaturalLanguageImageRouter, image_request_from_decision
from bridges.chat.repository import (
    ConversationModeLockConflict,
    ConversationRecord,
    ConversationRepository,
    GenerationEventRecord,
    GenerationRunRecord,
    MessageRecord,
    ModeEventRecord,
)
from bridges.chat.selections import (
    ChatSelectionsService,
    SelectionResolution,
    selection_key,
)
from bridges.chat.turn import (
    CHAT_MODE,
    STREAM_INTERRUPTED_MESSAGE,
    CareerPlannerOrchestrator,
    HumanizerOrchestrator,
    ImageOrchestrator,
    TurnOrchestrator,
    VideoOrchestrator,
    attempt_group,
    cancelled_arxiv_search,
    cancelled_web_search,
    # error_is_retryable / user_facing_error 在此 re-export，保持 api/chat.py
    # 的既有导入路径不变（定义在 chat/turn.py）。
    error_is_retryable,  # noqa: F401 - re-export
    failed_thinking,
    finalize_message,
    initial_thinking,
    owner_user_message,
    result_summary,
    capability_route_from,
    stopped_teaching_projection,
    stopped_thinking,
    user_facing_error,  # noqa: F401 - re-export
)
from bridges.contracts.career import CareerPlanningProjection
from bridges.contracts.chat import (
    ChatConversationListProjection,
    ChatConversationProjection,
    ChatConversationSummary,
    ChatFirstTurnResponse,
    ChatMessageProjection,
    ChatMessageRole,
    ChatMessageStatus,
    ChatMode,
    ChatModeEventProjection,
    ChatPluginSelectionItem,
    ChatRunStatus,
    ChatRunView,
    ChatStreamEventKind,
    ChatStreamProfileData,
    ChatStreamStartedData,
    ChatThinkingSummary,
    ContextNoteProjection,
    ImageRequestPayload,
    McpCallMessageProjection,
    McpCallRequestPayload,
    McpCallStatus,
    RemovedPluginSelection,
    VideoRequestPayload,
)
from bridges.routing import CapabilityRoute, MainCapability, NaturalLanguageRouter, RouteStatus
from bridges.contracts.feedback import (
    AnswerFeedback,
    AnswerFeedbackRequest,
    FeedbackKind,
    FeedbackStatus,
)
from bridges.contracts.humanizer import (
    HumanizerResultProjection,
    HumanizerSkillInput,
)
from bridges.contracts.image import ImageTaskKind, ImageTaskProjection
from bridges.contracts.routing import RouteDecision, RouteOperation
from bridges.contracts.mcp import McpError
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.profile_extraction import ProfilePreprocessResult
from bridges.contracts.profiles import ProfileNotification
from bridges.contracts.speech import ReadAloudProjection
from bridges.contracts.teaching import TeachingTurnProjection
from bridges.contracts.video import VideoTaskProjection
from bridges.contracts.workflows import RunContextEnvelope
from bridges.learning.teaching_gate import TeachingTurnService
from bridges.mcp.service import McpService
from bridges.observability.service import ObservabilityService
from bridges.profiles.automatic import AutomaticProfileService
from bridges.profiles.service import ProfileService
from bridges.retrieval.service import LayeredRetrievalService
from bridges.skills.humanizer.intent import route_humanizer_message
from bridges.web_search.contracts import WebSearchProjection
from bridges.web_search.service import WebSearchService

#: 由首条用户消息推导对话标题的最大长度。
_TITLE_MAX = 24


def _started_event_payload(
    *,
    conversation_id: str,
    user_message_id: str,
    message_id: str,
    attempt_number: int,
    message: MessageRecord,
) -> dict[str, Any]:
    """构造 started 事件载荷（发送/重试共用；思考/搜索/教学投影随事件持久化）。

    载荷与订阅回放的 SSE 帧 ``data`` 部分一致（不含外层 event 包装）。
    """
    return ChatStreamStartedData(
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        message_id=message_id,
        attempt_number=attempt_number,
        thinking=(
            ChatThinkingSummary(**message.thinking)
            if message.thinking is not None
            else None
        ),
        web_search=(
            WebSearchProjection(**message.web_search)
            if message.web_search is not None
            else None
        ),
        arxiv_search=(
            ArxivSearchProjection(**message.arxiv_search)
            if message.arxiv_search is not None
            else None
        ),
        route=_route_projection(message.route),
        teaching=(
            TeachingTurnProjection.model_validate(message.teaching)
            if message.teaching is not None
            else None
        ),
    ).model_dump(mode="json")


def _route_projection(
    route: dict[str, Any] | None,
) -> CapabilityRoute | RouteDecision | None:
    if route is None:
        return None
    try:
        return RouteDecision.model_validate(route)
    except ValidationError:
        return CapabilityRoute.model_validate(route)


class ChatDomainError(Exception):
    """聊天领域的可预期失败（由 API 层映射为 HTTP 状态与错误体）。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ChatService:
    """对话与生成编排服务；所有操作都限定在传入的账户 ID 内。"""

    def __init__(
        self,
        repository: ConversationRepository,
        gateway: ModelGateway,
        attachment_service: ChatAttachmentService | None = None,
        retrieval_service: LayeredRetrievalService | None = None,
        web_search_service: WebSearchService | None = None,
        arxiv_search_service: ArxivSearchService | None = None,
        teaching_service: TeachingTurnService | None = None,
        profile_service: ProfileService | None = None,
        automatic_profile_service: AutomaticProfileService | None = None,
        observability_service: ObservabilityService | None = None,
        humanizer_service: HumanizerOrchestrator | None = None,
        career_planner_service: CareerPlannerOrchestrator | None = None,
        image_service: ImageOrchestrator | None = None,
        video_service: VideoOrchestrator | None = None,
        selections_service: ChatSelectionsService | None = None,
        mcp_service: McpService | None = None,
        natural_language_router: NaturalLanguageImageRouter | None = None,
    ) -> None:
        self._repo = repository
        self._gateway = gateway
        self._attachments = attachment_service
        #: 分层本地检索（Issue 20）；未挂载时生成不检索、不产生引用。
        self._retrieval = retrieval_service
        #: 明确联网/时效/核查请求的固定 DuckDuckGo 搜索（Issue 21）。
        self._web_search = web_search_service
        #: 受限内置 arXiv MCP（Issue 22）；结果失败时不调用模型兜底。
        self._arxiv_search = arxiv_search_service
        #: 学习模式教学证据门与统一聊天教学轮次（Issue 23）。
        self._teaching = teaching_service or TeachingTurnService()
        #: 画像记忆意图处理（Issue 26）；未挂载时聊天不产生画像通知。
        self._profiles = profile_service
        #: Issue 15：默认自动抽取；启用后不再走旧的画像写入通知路径。
        self._automatic_profiles = automatic_profile_service
        #: 云端披露审计（Issue 27）；未挂载时跳过审计，不阻断生成。
        self._observability = observability_service
        #: 内置 bridges-humanizer SKILL 编排（Issue 28）；未挂载时携带
        #: SKILL 载荷的消息按普通消息处理（测试/内存环境）。
        self._humanizer = humanizer_service
        #: 生涯规划编排（Issue 29）；未挂载时规划意图按普通消息处理。
        self._career_planner = career_planner_service
        #: 图片生成与编辑编排（Issue 31）；未挂载时携带 image 载荷的
        #: 消息按错误收敛（测试/内存环境不假装生成）。
        self._image = image_service
        #: 文生视频编排（Issue 32，Wan 固定绑定）；未挂载时携带 video
        #: 载荷的消息按错误收敛（测试/内存环境不假装生成）。
        self._video = video_service
        #: 对话级插件选择域（Issue 36）：选择校验、失效清洗与工具上下文
        #: 编译；未挂载时不注入工具集合、不校验选择（内存测试环境）。
        self._selections = selections_service
        #: MCP 服务器服务（Issue 36）：聊天内对选中 MCP 的真实调用与
        #: 敏感确认；未挂载时携带 mcp_call 载荷的消息按错误收敛。
        self._mcp = mcp_service
        #: 新聊天自然语言主能力路由；结果在消息上持久化后才允许外部调用。
        self._router = NaturalLanguageRouter()
        #: 普通自然语言图片路由（Issue 08）；路由快照随用户消息落库，
        #: 未挂载时保留既有显式能力载荷兼容路径。
        self._natural_language_router = natural_language_router
        #: 进行中生成的停止信号与活跃度跟踪（注册/续期/TTL/停止唯一入口）。
        self._lifecycle = GenerationLifecycle()
        #: 回合编排深模块（Issue 42）：生成管线（模式路由/检索/切片编译/
        #: 提示词组装/流式收敛）收敛于此，本服务只保留对话/消息持久化与投影。
        self._turn = TurnOrchestrator(
            repository=self._repo,
            lifecycle=self._lifecycle,
            retrieval_service=self._retrieval,
            web_search_service=self._web_search,
            arxiv_search_service=self._arxiv_search,
            teaching_service=self._teaching,
            profile_service=self._profiles,
            automatic_profile_service=self._automatic_profiles,
            observability_service=self._observability,
            humanizer_service=self._humanizer,
            career_planner_service=self._career_planner,
            image_service=self._image,
            video_service=self._video,
            selections_service=self._selections,
            mcp_service=self._mcp,
        )

    def _ensure_extension_payload_allowed(
        self,
        *,
        plugin_selection: list[ChatPluginSelectionItem] | None = None,
        skill_id: str | None = None,
        skill_input: dict[str, Any] | None = None,
        mcp_call: dict[str, Any] | None = None,
    ) -> None:
        if (
            plugin_selection
            or mcp_call is not None
            or skill_id not in {None, "bridges-humanizer"}
            or (skill_input is not None and skill_id != "bridges-humanizer")
        ):
            raise ChatDomainError(
                "user_extensions_retired",
                "用户 SKILL、插件与通用 MCP 已退役，请返回聊天或知识库。",
                410,
            )

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------

    def create_conversation(
        self,
        account_id: str,
        title: str | None = None,
        mode: ChatMode = ChatMode.COMPANION,
        project_id: str | None = None,
        plugin_selection: list[ChatPluginSelectionItem] | None = None,
    ) -> ChatConversationProjection:
        """新建对话；普通新聊天默认日常陪伴，学习项目传入 ``study``。

        ``plugin_selection`` 为新对话的初始插件选择（新聊天首页先选
        插件再建对话）；调用方负责逐项校验可用性，这里原样持久化。
        """
        self._ensure_extension_payload_allowed(plugin_selection=plugin_selection)
        if project_id is not None:
            raise ChatDomainError(
                "legacy_file_source_retired",
                "学习项目归属已退役，请使用全局知识库。",
                410,
            )
        project_id = None
        now = datetime.now(UTC)
        conversation_id = secrets.token_urlsafe(16)
        self._repo.create_conversation(
            account_id=account_id,
            conversation_id=conversation_id,
            title=(title or "").strip(),
            mode=mode.value,
            created_at=now,
            project_id=project_id,
            plugin_selection=(
                [item.model_dump() for item in plugin_selection]
                if plugin_selection
                else None
            ),
        )
        return self._project_conversation(
            account_id,
            conversation_id,
            title=(title or "").strip(),
            mode=mode,
            mode_locked=False,
            pinned=False,
            project_id=project_id,
            plugin_selection=list(plugin_selection or []),
            removed_selections=[],
            created_at=now,
            updated_at=now,
            messages=[],
            mode_events=[],
        )

    def set_conversation_mode(
        self,
        account_id: str,
        conversation_id: str,
        mode: ChatMode,
        *,
        traffic_class: str = "real",
    ) -> None:
        """兼容窗口内拒绝旧模式切换写请求。"""
        del account_id, conversation_id, mode
        if self._observability is not None:
            self._observability.record_compatibility_410(
                endpoint_id="chat.conversation_mode_switch",
                service_version=__version__,
                traffic_class=traffic_class,
            )
        raise ChatDomainError(
            "conversation_mode_switch_retired",
            "模式已在首条消息提交时锁定；请新建另一个会话以使用其他模式。",
            410,
        )

    def list_conversations(self, account_id: str) -> ChatConversationListProjection:
        records = self._repo.list_conversations(account_id)
        summaries: list[ChatConversationSummary] = []
        for record in records:
            message_count = self._repo.message_count(account_id, record.conversation_id)
            # 新聊天页在发送前会先创建一个无标题空草稿；它不是可回访的
            # 最近会话，不能伪装成已存在的临时聊天。
            if not record.title and message_count == 0:
                continue
            summaries.append(
                ChatConversationSummary(
                    conversation_id=record.conversation_id,
                    title=record.title,
                    mode=ChatMode(record.mode),
                    mode_locked=record.mode_locked,
                    pinned=record.pinned,
                    project_id=record.project_id,
                    legacy_project_name=record.legacy_project_name,
                    message_count=message_count,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
        return ChatConversationListProjection(conversations=summaries)

    def update_conversation(
        self,
        account_id: str,
        conversation_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        plugin_selection: list[ChatPluginSelectionItem] | None = None,
    ) -> ChatConversationProjection:
        """改名、置顶或替换插件选择；跨账户目标统一返回安全 404。

        ``plugin_selection`` 全量替换当前选择；显式提交的选择逐项校验
        可用性（已停用/已卸载/撤权项 422 拒绝并说明原因，不静默清洗
        用户意图）；读取路径的失效清洗由 ``get_conversation`` 负责。
        """
        self._ensure_extension_payload_allowed(plugin_selection=plugin_selection)
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            raise ChatDomainError("conversation_not_found", "对话不存在或没有访问权限。", 404)
        normalized_title = title.strip() if title is not None else None
        if title is not None and not normalized_title:
            raise ChatDomainError("invalid_title", "对话标题不能为空。", 422)
        changed = normalized_title != record.title if normalized_title is not None else False
        changed = changed or (pinned is not None and pinned != record.pinned)
        selection = None
        removed: list[RemovedPluginSelection] = []
        if plugin_selection is not None:
            if self._selections is None:
                raise ChatDomainError(
                    "selections_unavailable", "插件选择服务未启用，请稍后重试。", 503
                )
            result = self._selections.validate_items(account_id, plugin_selection)
            if result.removed:
                reasons = "；".join(
                    f"「{entry.name}」{entry.reason}" for entry in result.removed
                )
                raise ChatDomainError("plugin_not_available", reasons, 422)
            current = _selection_set(record.plugin_selection)
            target = {selection_key(item) for item in result.valid}
            changed = changed or current != target
            if current != target:
                selection = result.valid
            removed = result.removed
        now = datetime.now(UTC)
        if changed:
            self._repo.update_conversation(
                account_id,
                conversation_id,
                title=normalized_title,
                pinned=pinned,
                plugin_selection=(
                    [item.model_dump() for item in selection]
                    if selection is not None
                    else None
                ),
                updated_at=now,
            )
            record = self._repo.get_conversation(account_id, conversation_id)
            assert record is not None
        valid_selection = (
            selection
            if selection is not None
            else _record_selection(record.plugin_selection)
        )
        return self._project_conversation(
            account_id,
            conversation_id,
            title=record.title,
            mode=ChatMode(record.mode),
            mode_locked=record.mode_locked,
            pinned=record.pinned,
            project_id=record.project_id,
            plugin_selection=valid_selection,
            removed_selections=removed,
            created_at=record.created_at,
            updated_at=record.updated_at,
            messages=self._repo.list_messages(account_id, conversation_id),
            mode_events=self._repo.list_mode_events(account_id, conversation_id),
        )

    def projection_from_record(
        self, record: ConversationRecord
    ) -> ChatConversationProjection:
        """由会话记录构造完整投影（与 update_conversation 同一响应形状）。

        组合更新路径（标题/置顶 + 学习项目归属在同一事务内完成，见
        ``LearningProjectService.update_conversation_metadata``）用已更新
        的记录投影响应，不触发读取路径的陈旧流收敛。
        """
        resolution = self._resolve_selections(record.account_id, record.conversation_id)
        return self._project_conversation(
            record.account_id,
            record.conversation_id,
            title=record.title,
            mode=ChatMode(record.mode),
            mode_locked=record.mode_locked,
            pinned=record.pinned,
            project_id=record.project_id,
            plugin_selection=resolution.valid,
            removed_selections=resolution.removed,
            created_at=record.created_at,
            updated_at=record.updated_at,
            messages=self._repo.list_messages(record.account_id, record.conversation_id),
            mode_events=self._repo.list_mode_events(
                record.account_id, record.conversation_id
            ),
        )

    def _resolve_selections(
        self, account_id: str, conversation_id: str
    ) -> SelectionResolution:
        """读取会话选择并校验清洗（Issue 36）；未挂载选择服务时原样返回。"""
        if self._selections is None:
            record = self._repo.get_conversation(account_id, conversation_id)
            return SelectionResolution(
                valid=_record_selection(record.plugin_selection)
                if record is not None
                else []
            )
        return self._selections.resolve(account_id, conversation_id)

    def delete_conversation(self, account_id: str, conversation_id: str) -> None:
        """删除自己的会话及其历史；生成中会话先拒绝，避免删除流状态。"""
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            raise ChatDomainError("conversation_not_found", "对话不存在或没有访问权限。", 404)
        if any(
            message.status == ChatMessageStatus.STREAMING
            for message in self._repo.list_messages(account_id, conversation_id)
        ):
            raise ChatDomainError(
                "generation_in_progress", "回答仍在生成中，请先停止后再删除。", 409
            )
        deleted = self._repo.delete_conversation(account_id, conversation_id)
        if deleted != 1:
            raise ChatDomainError("conversation_not_found", "对话不存在或没有访问权限。", 404)
        if self._attachments is not None:
            self._attachments.delete_for_conversation(account_id, conversation_id)

    def get_conversation(
        self, account_id: str, conversation_id: str
    ) -> ChatConversationProjection | None:
        """返回对话完整投影；不存在的对话返回 None。

        读取时以持久化生成为活跃判定源（Issue 02）：仍有 queued/running
        运行的消息视为进行中，读取不打断；运行已终态而消息仍残留
        streaming（执行器异常退出后的兜底）按运行终态收敛；完全没有
        运行记录的遗留 streaming 消息（旧版本数据）保持 stream_interrupted
        兜底。绝不把半截占位当回答，也绝不误伤可恢复的运行。
        """
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            return None
        messages = self._repo.list_messages(account_id, conversation_id)
        now = datetime.now(UTC)
        active_runs = {
            run.assistant_message_id: run
            for run in self._repo.list_active_runs(account_id, conversation_id)
        }
        run_views: dict[str, ChatRunView] = {}
        for message in messages:
            if message.status != ChatMessageStatus.STREAMING:
                continue
            active = active_runs.get(message.message_id)
            if active is not None:
                # 运行尚未终态：生成仍在进行（或等待执行器领取），不打断
                run_views[message.message_id] = self._run_view(active, account_id)
                continue
            run = self._repo.get_run_by_message(account_id, message.message_id)
            if run is not None and run.status == ChatRunStatus.STOPPED.value:
                # 运行已停止但消息残留 streaming（异常）：按停止语义收敛
                self._reconcile_stale_message(
                    account_id,
                    message,
                    status=ChatMessageStatus.STOPPED,
                    error_code=None,
                    error_message=None,
                    duration_ms=run.duration_ms,
                    thinking=stopped_thinking(
                        ChatThinkingSummary(**message.thinking)
                        if message.thinking is not None
                        else initial_thinking(CHAT_MODE)
                    ).model_dump(mode="json"),
                    now=now,
                )
            elif run is not None and run.status == ChatRunStatus.DONE.value:
                # 运行已成功但消息残留 streaming（异常半写）：按内部错误收敛
                self._reconcile_stale_message(
                    account_id,
                    message,
                    status=ChatMessageStatus.ERROR,
                    error_code="internal_error",
                    error_message="生成过程出现内部错误，请重试。",
                    duration_ms=max(
                        1, int((now - message.created_at).total_seconds() * 1000)
                    ),
                    thinking=failed_thinking(
                        ChatThinkingSummary(**message.thinking)
                        if message.thinking is not None
                        else initial_thinking(CHAT_MODE),
                        "internal_error",
                    ).model_dump(mode="json"),
                    now=now,
                )
            elif run is not None:
                # 运行以失败终态结束而消息未被执行器收敛：按运行错误码收敛
                self._reconcile_stale_message(
                    account_id,
                    message,
                    status=ChatMessageStatus.ERROR,
                    error_code=run.error_code or "internal_error",
                    error_message=run.error_message
                    or "生成过程出现内部错误，请重试。",
                    duration_ms=run.duration_ms,
                    thinking=failed_thinking(
                        ChatThinkingSummary(**message.thinking)
                        if message.thinking is not None
                        else initial_thinking(CHAT_MODE),
                        run.error_code or "internal_error",
                    ).model_dump(mode="json"),
                    now=now,
                )
            else:
                # 旧版本遗留：无运行记录的 streaming 消息按断流收敛
                self._reconcile_stale_message(
                    account_id,
                    message,
                    status=ChatMessageStatus.ERROR,
                    error_code="stream_interrupted",
                    error_message=STREAM_INTERRUPTED_MESSAGE,
                    duration_ms=max(
                        1, int((now - message.created_at).total_seconds() * 1000)
                    ),
                    thinking=failed_thinking(
                        ChatThinkingSummary(**message.thinking)
                        if message.thinking is not None
                        else initial_thinking(CHAT_MODE),
                        "stream_interrupted",
                    ).model_dump(mode="json"),
                    now=now,
                )
        resolution = self._resolve_selections(account_id, conversation_id)
        return self._project_conversation(
            account_id,
            record.conversation_id,
            title=record.title,
            mode=ChatMode(record.mode),
            mode_locked=record.mode_locked,
            pinned=record.pinned,
            project_id=record.project_id,
            plugin_selection=resolution.valid,
            removed_selections=resolution.removed,
            created_at=record.created_at,
            updated_at=record.updated_at,
            messages=messages,
            mode_events=self._repo.list_mode_events(account_id, conversation_id),
            run_views=run_views,
        )

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------

    def start_generation(
        self,
        account_id: str,
        conversation_id: str,
        content: str,
        attachment_ids: list[str] | None = None,
        skill_id: str | None = None,
        skill_input: dict[str, Any] | None = None,
        image: dict[str, Any] | None = None,
        video: dict[str, Any] | None = None,
        mcp_call: dict[str, Any] | None = None,
        use_knowledge_base: bool = True,
        use_profile: bool = True,
    ) -> tuple[ChatMessageProjection, ChatMessageProjection]:
        """原子创建用户消息、助手占位与 queued 运行，返回两者投影。

        Issue 02：同一事务创建用户消息、streaming 助手占位与持久化生成
        运行（queued）并登记进统一领取队列——发送返回即完成创建，生成
        由后台执行器领取执行，页面/SSE 不再拥有运行生命周期。运行快照
        本轮发送参数（``use_knowledge_base``/``use_profile``），执行器
        与重试按同一份任务契约执行。

        ``skill_id``/``skill_input``（Issue 28）：携带时本轮走内置 SKILL
        编排（bridges-humanizer）；载荷随用户消息落库，重试沿用同一份
        任务契约。SKILL 载荷必须通过注册校验，未注册标识直接拒绝。
        ``image``（Issue 31）：图片生成/编辑请求载荷；与 SKILL 载荷
        互斥，携带时本轮创建图片异步任务而非普通回答。
        ``video``（Issue 32）：文生视频请求载荷；与 SKILL 载荷互斥，
        携带时本轮创建视频异步任务而非普通回答。
        ``mcp_call``（Issue 36）：对选中 MCP 插件的调用载荷；与 SKILL
        载荷互斥，携带时本轮执行真实 MCP 调用而非普通回答。目标 MCP
        必须被本对话选中（选择器持久化），否则流式阶段拒绝。
        """
        self._ensure_extension_payload_allowed(
            skill_id=skill_id,
            skill_input=skill_input,
            mcp_call=mcp_call,
        )
        now = datetime.now(UTC)
        content = content.strip()
        if not content:
            raise ChatDomainError("empty_message", "消息内容不能为空。", 422)
        skill_id, skill_input, use_knowledge_base = (
            self._apply_natural_language_humanizer_route(
                content,
                skill_id=skill_id,
                skill_input=skill_input,
                image=image,
                video=video,
                mcp_call=mcp_call,
                attachment_ids=attachment_ids,
                use_knowledge_base=use_knowledge_base,
            )
        )
        image_route = self._route_natural_language(
            account_id,
            content,
            explicit_payload=any(
                value is not None for value in (skill_id, image, video, mcp_call)
            ),
        )
        routed_image = image_request_from_decision(image_route) if image_route else None
        skill_payload, image_payload, video_payload, mcp_call_payload = (
            self._validate_turn_payloads(
                skill_id, skill_input, image or routed_image, video, mcp_call
            )
        )
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            raise ChatDomainError(
                "conversation_not_found", "对话不存在或没有访问权限。", 404
            )
        existing = self._repo.list_messages(account_id, conversation_id)
        if any(message.status == ChatMessageStatus.STREAMING for message in existing):
            raise ChatDomainError(
                "generation_in_progress",
                "上一轮回答仍在生成中，请先停止或等待完成。",
                409,
            )
        if attachment_ids:
            raise ChatDomainError(
                "legacy_file_source_retired",
                "聊天附件已退役，请先将材料加入全局知识库。",
                410,
            )
        attachment_ids = None
        mode = ChatMode(record.mode)
        capability_route = self._route_for_turn(
            content,
            skill_payload=skill_payload,
            image_payload=image_payload,
            video_payload=video_payload,
            mcp_call_payload=mcp_call_payload,
        )
        (
            user_message,
            assistant_message,
            run_record,
            started_payload,
        ) = self._assemble_generation_records(
            account_id=account_id,
            conversation_id=conversation_id,
            content=content,
            mode=mode,
            skill_payload=skill_payload,
            image_payload=image_payload,
            video_payload=video_payload,
            mcp_call_payload=mcp_call_payload,
            route=capability_route,
            image_route=image_route,
            use_knowledge_base=use_knowledge_base,
            use_profile=use_profile,
            now=now,
        )
        try:
            self._repo.insert_generation_turn(
                user_message,
                assistant_message,
                run_record,
                [(ChatStreamEventKind.STARTED.value, started_payload)],
                attachment_ids or None,
                lock_mode=not record.mode_locked,
            )
        except ConversationModeLockConflict as exc:
            raise ChatDomainError(
                "conversation_mode_locked",
                "该会话模式已锁定，请继续使用当前模式或新建会话。",
                409,
            ) from exc
        self._repo.touch_conversation(account_id, conversation_id, now)

        if not record.title:
            title = content if len(content) <= _TITLE_MAX else content[:_TITLE_MAX] + "…"
            self._repo.set_conversation_title(account_id, conversation_id, title, now)

        self._process_profile_effects(
            account_id=account_id,
            conversation_id=conversation_id,
            user_message_id=user_message.message_id,
            content=content,
            mode=mode,
            run_id=run_record.run_id,
            now=now,
        )

        run_view = self._run_view(run_record, account_id)
        return (
            self._project_message(user_message),
            self._project_message(assistant_message, run_view),
        )

    def _apply_natural_language_humanizer_route(
        self,
        content: str,
        *,
        skill_id: str | None,
        skill_input: dict[str, Any] | None,
        image: dict[str, Any] | None,
        video: dict[str, Any] | None,
        mcp_call: dict[str, Any] | None,
        attachment_ids: list[str] | None,
        use_knowledge_base: bool,
    ) -> tuple[str | None, dict[str, Any] | None, bool]:
        """把普通消息编译为既有人味化 SKILL 载荷。

        只有未携带旧载荷或其他能力载荷时才尝试路由；显式 SKILL 仍按
        兼容合同校验。自然语言路由不接受聊天附件，避免把历史附件或
        未经知识库迁移的文件偷偷重新送入模型。
        """
        if any(
            payload is not None
            for payload in (skill_id, skill_input, image, video, mcp_call)
        ):
            return skill_id, skill_input, use_knowledge_base
        routed = route_humanizer_message(content)
        if routed is None:
            return skill_id, skill_input, use_knowledge_base
        if attachment_ids:
            raise ChatDomainError(
                "humanizer_attachment_not_supported",
                "自然语言人味化不接收聊天附件，请先把原文件上传到当前账户知识库后再引用。",
                422,
            )
        return (
            routed.skill_input.skill_id,
            routed.skill_input.model_dump(mode="json"),
            routed.use_knowledge_base,
        )

    def _validate_skill_attachment_consistency(
        self,
        skill_payload: dict[str, Any] | None,
        attachment_ids: list[str],
    ) -> None:
        """Issue 04：技能任务契约引用的附件必须与消息绑定集合一致。

        前端把附件 ID 同时放在请求顶层（绑定）与任务契约（技能读取），
        两者必须逐一对齐；不一致说明提交链路丢字段（历史缺陷：首页
        包装回调丢弃 ``attachmentIds``），直接返回可理解错误而非静默
        继续——绝不出现「消息投影无附件但任务读取了文件」或反之。
        """
        if skill_payload is None:
            return
        contract_ids = list(
            (skill_payload.get("contract") or {}).get("knowledge_base_object_ids") or []
        )
        if set(contract_ids) != set(attachment_ids):
            raise ChatDomainError(
                "attachment_contract_mismatch",
                "任务引用的附件与消息附加的附件不一致，请重新选择文件后重试。",
                422,
            )

    def _route_natural_language(
        self, account_id: str, content: str, *, explicit_payload: bool
    ) -> RouteDecision | None:
        """只对无显式能力载荷的普通消息做一次本地路由。"""

        if explicit_payload or self._natural_language_router is None:
            return None
        return self._natural_language_router.route(account_id, content)

    @staticmethod
    def _reject_natural_image_attachments(
        route: RouteDecision | None, attachment_ids: list[str]
    ) -> None:
        if (
            route is not None
            and route.capability.value == "image"
            and route.operation in {RouteOperation.GENERATE, RouteOperation.EDIT}
            and attachment_ids
        ):
            raise ChatDomainError(
                "image_source_not_allowed",
                "自然语言图片编辑只允许使用当前账户知识库中的图片，不能使用聊天附件。",
                422,
            )

    def _validate_turn_payloads(
        self,
        skill_id: str | None,
        skill_input: dict[str, Any] | None,
        image: dict[str, Any] | None,
        video: dict[str, Any] | None,
        mcp_call: dict[str, Any] | None,
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        """校验一轮载荷：SKILL 注册、图片/视频/MCP 结构与四类互斥。

        供续轮（``start_generation``）与原子首轮（``start_first_turn``）
        共用，失败按 422 拒绝而不是静默丢弃（失败不伪装成功）。
        """
        skill_payload = self._validate_skill_payload(skill_id, skill_input)
        # Issue 31/32/36：SKILL / 图片 / 视频 / MCP 调用四类载荷互斥——
        # 同一轮只允许一种载荷驱动生成，并发携带按 422 拒绝而不是静默
        # 丢弃（失败不伪装成功，契约单一来源）。
        has_skill = skill_payload is not None
        image_payload = _validate_image_payload(image, has_skill)
        video_payload = _validate_video_payload(
            video, has_skill or image_payload is not None
        )
        mcp_call_payload = _validate_mcp_call_payload(
            mcp_call,
            has_skill or image_payload is not None or video_payload is not None,
        )
        return skill_payload, image_payload, video_payload, mcp_call_payload

    def _route_for_turn(
        self,
        content: str,
        *,
        skill_payload: dict[str, Any] | None,
        image_payload: dict[str, Any] | None,
        video_payload: dict[str, Any] | None,
        mcp_call_payload: dict[str, Any] | None,
    ) -> CapabilityRoute:
        """让显式能力载荷优先于文本中的相邻意图。"""
        explicit_capability = None
        reason = None
        if skill_payload is not None:
            explicit_capability = MainCapability.HUMANIZER
            reason = "已选择 Humanizer 能力"
        elif image_payload is not None:
            explicit_capability = MainCapability.IMAGE
            reason = "已提交图片能力载荷"
        elif video_payload is not None:
            payload = VideoRequestPayload.model_validate(video_payload)
            return self._router.route_explicit_video(
                payload.prompt,
                aspect_ratio=payload.aspect_ratio,
                duration_seconds=payload.duration_seconds,
            )
        elif mcp_call_payload is not None:
            return CapabilityRoute(
                status=RouteStatus.ORDINARY,
                main_capability=MainCapability.ORDINARY_CHAT,
                confidence=1.0,
                reason="已提交显式 MCP 调用载荷",
                knowledge_base_allowed=False,
                web_search_allowed=False,
            )
        if explicit_capability is None:
            return self._router.classify(content)
        return CapabilityRoute(
            status=RouteStatus.MATCHED,
            main_capability=explicit_capability,
            confidence=1.0,
            reason=reason or "已提交显式能力载荷",
            knowledge_base_allowed=False,
            web_search_allowed=False,
        )

    def _assemble_generation_records(
        self,
        *,
        account_id: str,
        conversation_id: str,
        content: str,
        mode: ChatMode,
        skill_payload: dict[str, Any] | None,
        image_payload: dict[str, Any] | None,
        video_payload: dict[str, Any] | None,
        mcp_call_payload: dict[str, Any] | None,
        route: CapabilityRoute,
        image_route: RouteDecision | None = None,
        use_knowledge_base: bool,
        use_profile: bool,
        now: datetime,
    ) -> tuple[MessageRecord, MessageRecord, GenerationRunRecord, dict[str, Any]]:
        """组装一轮的用户消息、助手占位、queued 运行与 started 载荷。

        供续轮（``start_generation``）与原子首轮（``start_first_turn``）
        共用；调用方负责事务写入与幂等语义。运行创建即视为"活跃"，
        读取收敛不会误伤（判定源为运行表）。
        """
        thinking = initial_thinking(mode).model_dump(mode="json")
        # Issue 28：SKILL 任务不需要搜索/教学初始计划（其证据合同由
        # 人味化编排按需执行），避免把任务摘要误当搜索查询。
        web_search = (
            self._web_search.initial_projection(self._web_search.plan(content, mode))
            if self._web_search is not None
            and skill_payload is None
            and not route.is_paper_search
            and route.status not in {RouteStatus.CLARIFY, RouteStatus.REJECTED}
            else None
        )
        arxiv_search = (
            self._arxiv_search.initial_projection(
                self._arxiv_search.plan_from_route(route)
            )
            if self._arxiv_search is not None
            and skill_payload is None
            and route.is_paper_search
            else None
        )
        teaching = (
            self._teaching.initial(content)
            if (
                mode == ChatMode.STUDY
                and skill_payload is None
                and not route.is_paper_search
            )
            else None
        )
        user_message = MessageRecord(
            message_id=secrets.token_urlsafe(16),
            conversation_id=conversation_id,
            account_id=account_id,
            role=ChatMessageRole.USER,
            attempt_number=1,
            status=ChatMessageStatus.DONE,
            content=content,
            thinking=None,
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            created_at=now,
            updated_at=now,
            skill=skill_payload,
            image=image_payload,
            video=video_payload,
            mcp_call=mcp_call_payload,
            route=(
                image_route.model_dump(mode="json")
                if image_route is not None
                else route.model_dump(mode="json")
            ),
        )
        assistant_message = MessageRecord(
            message_id=secrets.token_urlsafe(16),
            conversation_id=conversation_id,
            account_id=account_id,
            role=ChatMessageRole.ASSISTANT,
            attempt_number=1,
            status=ChatMessageStatus.STREAMING,
            content="",
            thinking=thinking,
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            created_at=now,
            updated_at=now,
            web_search=(web_search.model_dump(mode="json") if web_search else None),
            arxiv_search=(arxiv_search.model_dump(mode="json") if arxiv_search else None),
            teaching=(teaching.model_dump(mode="json") if teaching else None),
            route=route.model_dump(mode="json"),
        )
        # Issue 02：同一事务创建消息、queued 运行、started 事件与队列行。
        run_id = secrets.token_urlsafe(16)
        run_record = GenerationRunRecord(
            run_id=run_id,
            account_id=account_id,
            conversation_id=conversation_id,
            user_message_id=user_message.message_id,
            assistant_message_id=assistant_message.message_id,
            attempt_number=assistant_message.attempt_number,
            status=ChatRunStatus.QUEUED.value,
            config={"use_knowledge_base": use_knowledge_base, "use_profile": use_profile},
            stage=None,
            lease_owner=None,
            lease_expires_at=None,
            attempt_count=0,
            stop_requested=False,
            error_code=None,
            error_message=None,
            duration_ms=None,
            created_at=now,
            updated_at=now,
        )
        started_payload = _started_event_payload(
            conversation_id=conversation_id,
            user_message_id=user_message.message_id,
            message_id=assistant_message.message_id,
            attempt_number=assistant_message.attempt_number,
            message=assistant_message,
        )
        return user_message, assistant_message, run_record, started_payload

    def _process_profile_effects(
        self,
        *,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        content: str,
        mode: ChatMode,
        run_id: str,
        now: datetime,
    ) -> None:
        """首轮/续轮的画像记忆副作用：处理消息并把通知追加为 profile 事件。

        Issue 26：确定性记忆意图处理（明确记住/不记/仅会话、许可内自动
        写入、敏感候选、单次情绪提示）。画像处理失败绝不阻断聊天主流程：
        记忆能力独立于回答生成，通知留待重试轮次补发。
        Issue 02：画像通知作为 profile 事件随运行持久化（started 之后），
        订阅者从游标回放即可即时展示，重开页面不重复下发。
        """
        automatic_result: ProfilePreprocessResult | None = None
        if self._automatic_profiles is not None:
            with contextlib.suppress(Exception):
                automatic_result = self._automatic_profiles.preprocess_message(
                    account_id,
                    conversation_id=conversation_id,
                    message_id=user_message_id,
                    content=content,
                    run_id=run_id,
                    mode=mode.value,
                )
        elif self._profiles is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001 - 辅助路径静默降级
                self._profiles.process_conversation_message(
                    account_id,
                    message_id=user_message_id,
                    conversation_id=conversation_id,
                    content=content,
                    mode=mode.value,
                )
        notifications = self.profile_notifications_for_message(
            account_id, conversation_id, user_message_id
        )
        privacy_notice = (
            automatic_result.privacy_notice if automatic_result is not None else None
        )
        if notifications or privacy_notice is not None:
            profile_payload = ChatStreamProfileData(
                message_id=user_message_id,
                notifications=notifications,
                privacy_notice=privacy_notice,
            ).model_dump(mode="json")
            self._repo.append_generation_event(
                account_id, run_id, ChatStreamEventKind.PROFILE.value, profile_payload, now
            )

    def start_first_turn(
        self,
        account_id: str,
        *,
        content: str,
        idempotency_key: str,
        conversation_id: str | None = None,
        mode: ChatMode = ChatMode.COMPANION,
        project_id: str | None = None,
        plugin_selection: list[ChatPluginSelectionItem] | None = None,
        attachment_ids: list[str] | None = None,
        skill_id: str | None = None,
        skill_input: dict[str, Any] | None = None,
        image: dict[str, Any] | None = None,
        video: dict[str, Any] | None = None,
        mcp_call: dict[str, Any] | None = None,
        use_knowledge_base: bool = True,
        use_profile: bool = True,
    ) -> ChatFirstTurnResponse:
        """原子创建新会话首轮（Issue 03）：会话、用户消息、助手占位与
        queued 运行在同一事务内落库，返回完整投影。

        首页发送第一条消息或调用任一功能时使用，成功响应后再导航——
        ``sessionStorage`` 不再承担业务真相。``idempotency_key`` 抵御
        双击与网络重放：同账户同键重放（含并发）只产生一份数据，返回
        既有会话且 ``idempotent_replay=True``（HTTP 200）。``conversation_id``
        可选指定已预建的空会话（附件上传路径先建会话再发送）；缺省在
        事务内新建。``mode``/``project_id``/``plugin_selection`` 随首轮
        写入会话。失败不留任何会话/消息（整事务回滚），不产生空草稿。
        """
        self._ensure_extension_payload_allowed(
            plugin_selection=plugin_selection,
            skill_id=skill_id,
            skill_input=skill_input,
            mcp_call=mcp_call,
        )
        if project_id is not None:
            raise ChatDomainError(
                "legacy_file_source_retired",
                "学习项目归属已退役，请使用全局知识库。",
                410,
            )
        project_id = None
        now = datetime.now(UTC)
        content = content.strip()
        if not content:
            raise ChatDomainError("empty_message", "消息内容不能为空。", 422)
        skill_id, skill_input, use_knowledge_base = (
            self._apply_natural_language_humanizer_route(
                content,
                skill_id=skill_id,
                skill_input=skill_input,
                image=image,
                video=video,
                mcp_call=mcp_call,
                attachment_ids=attachment_ids,
                use_knowledge_base=use_knowledge_base,
            )
        )
        image_route = self._route_natural_language(
            account_id,
            content,
            explicit_payload=any(
                value is not None for value in (skill_id, image, video, mcp_call)
            ),
        )
        routed_image = image_request_from_decision(image_route) if image_route else None
        skill_payload, image_payload, video_payload, mcp_call_payload = (
            self._validate_turn_payloads(
                skill_id, skill_input, image or routed_image, video, mcp_call
            )
        )
        # 指定会话（附件路径）必须存在且属于当前账户；缺省新建会话没有
        # 这个问题。「会话已有消息」的检查在事务内（幂等查找之后）执行：
        # 同键重放（含预建会话路径）必须 200 返回既有数据，不能被 409
        # 短路；并发不同键双发同一预建会话由事务内检查拦截。
        if conversation_id is not None:
            # 共享 SQLite 连接不能在其他线程提交首轮原子事务时并发读取。
            with self._repo.connection_lock():
                record = self._repo.get_conversation(account_id, conversation_id)
            if record is None:
                raise ChatDomainError(
                    "conversation_not_found", "对话不存在或没有访问权限。", 404
                )
            if record.mode_locked and record.mode != mode.value:
                raise ChatDomainError(
                    "conversation_mode_locked",
                    "该会话模式已锁定，请新建另一个会话以使用其他模式。",
                    409,
                )
        if attachment_ids:
            raise ChatDomainError(
                "legacy_file_source_retired",
                "聊天附件已退役，请先将材料加入全局知识库。",
                410,
            )
        capability_route = self._route_for_turn(
            content,
            skill_payload=skill_payload,
            image_payload=image_payload,
            video_payload=video_payload,
            mcp_call_payload=mcp_call_payload,
        )
        attachment_ids = None
        title = content if len(content) <= _TITLE_MAX else content[:_TITLE_MAX] + "…"
        target_conversation_id = conversation_id or secrets.token_urlsafe(16)
        (
            user_message,
            assistant_message,
            run_record,
            started_payload,
        ) = self._assemble_generation_records(
            account_id=account_id,
            conversation_id=target_conversation_id,
            content=content,
            mode=mode,
            skill_payload=skill_payload,
            image_payload=image_payload,
            video_payload=video_payload,
            mcp_call_payload=mcp_call_payload,
            route=capability_route,
            image_route=image_route,
            use_knowledge_base=use_knowledge_base,
            use_profile=use_profile,
            now=now,
        )
        created_id, created, conflict_not_empty = self._repo.insert_first_turn(
            account_id=account_id,
            conversation_id=target_conversation_id,
            idempotency_key=idempotency_key,
            title=title,
            mode=mode.value,
            created_at=now,
            project_id=project_id,
            plugin_selection=(
                [item.model_dump() for item in plugin_selection]
                if plugin_selection
                else None
            ),
            user_record=user_message,
            assistant_record=assistant_message,
            run_record=run_record,
            events=[(ChatStreamEventKind.STARTED.value, started_payload)],
            attachment_ids=attachment_ids or None,
        )
        if conflict_not_empty:
            with self._repo.connection_lock():
                record = self._repo.get_conversation(account_id, target_conversation_id)
            if record is not None and record.mode != mode.value:
                raise ChatDomainError(
                    "conversation_mode_locked",
                    "该会话模式已锁定，请新建另一个会话以使用其他模式。",
                    409,
                )
            raise ChatDomainError(
                "conversation_not_empty",
                "该对话已有消息，不能作为首轮发送目标。",
                409,
            )
        if not created:
            # 幂等命中：返回已存在数据，不执行任何首轮副作用（消息已
            # 落库、profile 事件已随原首轮持久化）。
            with self._repo.connection_lock():
                conversation = self.get_conversation(account_id, created_id)
            user_projection = next(
                (m for m in conversation.messages if m.role == ChatMessageRole.USER),
                None,
            )
            assistant_projection = next(
                (m for m in conversation.messages if m.role == ChatMessageRole.ASSISTANT),
                None,
            )
            if user_projection is None or assistant_projection is None:
                raise ChatDomainError(
                    "first_turn_replay_inconsistent",
                    "首轮数据不完整，请刷新后重试。",
                    409,
                )
            # 运行视图按消息查询：终态运行同样有记录（active_run 只在活跃
            # 时附加到投影），因此 run 完成/失败/停止后重放仍返回 200。
            run_view = self.run_view_of(account_id, assistant_projection.message_id)
            if run_view is None:
                raise ChatDomainError(
                    "first_turn_replay_inconsistent",
                    "首轮数据不完整，请刷新后重试。",
                    409,
                )
            return ChatFirstTurnResponse(
                conversation=conversation,
                run_id=run_view.run_id,
                cursor=run_view.cursor,
                user_message=user_projection,
                assistant_message=assistant_projection,
                idempotent_replay=True,
            )
        self._process_profile_effects(
            account_id=account_id,
            conversation_id=created_id,
            user_message_id=user_message.message_id,
            content=content,
            mode=mode,
            run_id=run_record.run_id,
            now=now,
        )
        run_view = self._run_view(run_record, account_id)
        with self._repo.connection_lock():
            conversation = self.get_conversation(account_id, created_id)
        return ChatFirstTurnResponse(
            conversation=conversation,
            run_id=run_record.run_id,
            cursor=run_view.cursor,
            user_message=self._project_message(user_message),
            assistant_message=self._project_message(assistant_message, run_view),
            idempotent_replay=False,
        )

    def stream_generation(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        run_context: RunContextEnvelope,
        until_user_message_id: str | None = None,
        use_knowledge_base: bool = True,
        use_profile: bool = True,
    ) -> Iterator[StreamEvent]:
        """驱动一次生成（委托给回合编排深模块，接口与语义不变）。

        管线（分层检索、教学证据门、联网/论文搜索、最小画像切片编译与
        披露、提示词组装、流式收敛）由 ``TurnOrchestrator.stream_turn``
        执行（Issue 42 架构加深）；事件经此处原样透传给 API 层。停止
        信号与终态收敛语义不变：用户停止/切换账户导致的客户端断开都会
        把消息收敛到明确终态，绝不留 streaming 僵尸。
        """
        yield from self._turn.stream_turn(
            account_id,
            conversation_id,
            assistant_message_id,
            run_context,
            until_user_message_id=until_user_message_id,
            use_knowledge_base=use_knowledge_base,
            use_profile=use_profile,
            gateway=self._gateway,
        )

    def stop_generation(
        self, account_id: str, conversation_id: str, message_id: str
    ) -> ChatMessageProjection:
        """停止进行中的生成（显式操作）；幂等，已终态直接返回当前状态。

        Issue 02：停止写入运行表的 stop_requested（跨进程真相源），并
        设置同进程停止信号；后台执行器在安全检查点收敛为 stopped。本
        方法等待执行器收敛（≤4 秒）；无执行器运行（测试环境/执行器被
        长模型调用阻塞）时按既有语义兜底收敛，终态由原子守卫保证唯一
        写入，绝不会被执行器迟到的终态改写成 done。
        """
        message = self._repo.get_message(account_id, message_id)
        if message is None or message.conversation_id != conversation_id:
            raise ChatDomainError(
                "message_not_found", "消息不存在或没有访问权限。", 404
            )
        if message.status != ChatMessageStatus.STREAMING:
            return self._project_message(message)

        with self._repo.connection_lock():
            run = self._repo.get_run_by_message(account_id, message_id)
        if run is not None and run.status in {
            ChatRunStatus.QUEUED.value,
            ChatRunStatus.RUNNING.value,
        }:
            self._repo.request_generation_stop(account_id, run.run_id)
            entry = self._lifecycle.signal_and_started(message_id)
            if entry is not None:
                entry[0].set()
            # 等待执行器在安全检查点收敛；终态后直接返回（含真实耗时）。
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                current = self._repo.get_message(account_id, message_id)
                if current is None or current.status != ChatMessageStatus.STREAMING:
                    if current is not None:
                        self._lifecycle.unregister(message_id)
                        return self._project_message(current)
                    break
                time.sleep(0.1)
            # 超时：执行器不存在或仍被长调用阻塞——按既有语义兜底收敛，
            # 执行器下个安全检查点发现消息已终态后停止并收敛运行。
            message = self._repo.get_message(account_id, message_id)
            if message is None:
                raise ChatDomainError(
                    "message_not_found", "消息不存在或没有访问权限。", 404
                )

        entry = self._lifecycle.signal_and_started(message_id)
        stop_event = entry[0] if entry is not None else None
        started = entry[1] if entry is not None else None
        if stop_event is not None:
            stop_event.set()
        now = datetime.now(UTC)
        # 生成线程尚未启动（或跨进程遗留消息）时按创建时间估算耗时并直接
        # 以墙钟时长传入；运行时用真实单调起点由 finalize_message 计算。
        duration_ms: int | None = None
        if started is None:
            duration_ms = max(1, int((now - message.created_at).total_seconds() * 1000))
            started = 0.0
        # 先收敛状态再注销活跃标记：避免并发读取在两者之间把仍处于
        # streaming 的消息误判为陈旧中断（终态由原子守卫保证单一写入）。
        # 停止同样保留已完成思考摘要并写入中文质量结论（Issue 14）。
        stopped_teaching = stopped_teaching_projection(message.teaching)
        finalize_message(
            self._repo,
            account_id,
            message_id,
            status=ChatMessageStatus.STOPPED,
            error_code=None,
            error_message=None,
            duration_ms=duration_ms,
            model_id=None,
            run_lock_id=None,
            started=started,
            now=now,
            thinking=stopped_thinking(
                ChatThinkingSummary(**message.thinking)
                if message.thinking is not None
                else initial_thinking(CHAT_MODE)
            ),
            web_search=cancelled_web_search(message.web_search, now),
            arxiv_search=cancelled_arxiv_search(message.arxiv_search, now),
            teaching=(
                stopped_teaching.model_dump(mode="json")
                if stopped_teaching is not None
                else None
            ),
        )
        self._lifecycle.unregister(message_id)
        finalized = self._repo.get_message(account_id, message_id)
        if finalized is None:
            raise ChatDomainError(
                "message_not_found", "消息不存在或没有访问权限。", 404
            )
        return self._project_message(finalized)

    def stop_account_generations(self, account_id: str) -> int:
        """停止该账户全部进行中的生成（账户删除编排调用）。

        Issue 02：先向全部非终态运行写入 stop_requested（跨进程真相
        源），再对仍处于 streaming 的消息逐个发同进程停止信号；后台
        执行器在下次收敛点按既有终止语义收尾。返回停止的消息数量。
        """
        self._repo.request_stop_account_runs(account_id, datetime.now(UTC))
        stopped = 0
        for conversation in self._repo.list_conversations(account_id):
            for message in self._repo.list_messages(account_id, conversation.conversation_id):
                if message.status != ChatMessageStatus.STREAMING:
                    continue
                entry = self._lifecycle.signal_and_started(message.message_id)
                if entry is not None:
                    entry[0].set()
                stopped += 1
        return stopped

    def retry_generation(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
        use_knowledge_base: bool = True,
        use_profile: bool = True,
    ) -> tuple[ChatMessageProjection, ChatMessageProjection]:
        """为已终态（失败/停止/完成）的助手消息创建新的助手尝试。

        尝试号递增，历史尝试原样保留（含错误状态），新尝试成为该轮的
        最新回答；绝不静默改写历史，也不把失败注册为成功。Issue 02：
        新尝试在同一事务创建 queued 运行并持久化 started 事件，由后台
        执行器领取执行（与发送同一外壳）。``use_knowledge_base``/
        ``use_profile`` 沿用旧尝试轮次的开关，随运行快照落库。
        """
        message = self._repo.get_message(account_id, message_id)
        if message is None or message.conversation_id != conversation_id:
            raise ChatDomainError(
                "message_not_found", "消息不存在或没有访问权限。", 404
            )
        if message.role != ChatMessageRole.ASSISTANT:
            raise ChatDomainError(
                "not_retryable_message", "只有助手消息可以重试。", 400
            )
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            raise ChatDomainError(
                "conversation_not_found", "对话不存在或没有访问权限。", 404
            )
        existing = self._repo.list_messages(account_id, conversation_id)
        if any(m.status == ChatMessageStatus.STREAMING for m in existing):
            raise ChatDomainError(
                "generation_in_progress",
                "上一轮回答仍在生成中，请先停止或等待完成。",
                409,
            )

        owner = owner_user_message(existing, message_id)
        if owner is None:
            raise ChatDomainError(
                "not_retryable_message", "找不到该助手消息对应的用户消息。", 400
            )
        if owner.skill:
            legacy_attachment_ids = (owner.skill.get("contract") or {}).get(
                "attachment_ids"
            )
            if legacy_attachment_ids:
                raise ChatDomainError(
                    "historical_attachment_not_retryable",
                    "历史聊天附件不能作为人味化重试输入，请先把原文件上传到当前账户知识库后再重试。",
                    422,
                )
        max_attempt = max(
            (m.attempt_number for m in attempt_group(existing, owner.message_id)),
            default=0,
        )
        now = datetime.now(UTC)
        mode = ChatMode(record.mode)
        route = capability_route_from(owner.route)
        reusable_arxiv_search: ArxivSearchProjection | None = None
        for previous_attempt in reversed(attempt_group(existing, owner.message_id)):
            if previous_attempt.arxiv_search is None:
                continue
            previous_projection = ArxivSearchProjection(**previous_attempt.arxiv_search)
            if previous_projection.status == ArxivSearchStatus.SUCCESS:
                reusable_arxiv_search = previous_projection
                break
        web_search = (
            self._web_search.initial_projection(self._web_search.plan(owner.content, mode))
            if self._web_search is not None
            and route is not None
            and not route.is_paper_search
            and route.status not in {RouteStatus.CLARIFY, RouteStatus.REJECTED}
            else None
        )
        arxiv_search = (
            reusable_arxiv_search
            or self._arxiv_search.initial_projection(
                self._arxiv_search.plan_from_route(route), recovery=True
            )
            if self._arxiv_search is not None
            and route is not None
            and route.is_paper_search
            else None
        )
        teaching = (
            self._teaching.initial(owner.content, recovery=True)
            if (
                mode == ChatMode.STUDY
                and not (route is not None and route.is_paper_search)
            )
            else None
        )
        new_attempt = MessageRecord(
            message_id=secrets.token_urlsafe(16),
            conversation_id=conversation_id,
            account_id=account_id,
            role=ChatMessageRole.ASSISTANT,
            attempt_number=max_attempt + 1,
            status=ChatMessageStatus.STREAMING,
            content="",
            thinking=initial_thinking(mode).model_dump(mode="json"),
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            # 重试尝试保持所属轮次的时间戳：排序时按 attempt_number 归入
            # 该轮（避免 created_at=now 把新尝试排到对话末尾，导致渲染分组
            # 与模型上下文错配到后续轮次）。
            created_at=owner.created_at,
            updated_at=now,
            web_search=(web_search.model_dump(mode="json") if web_search else None),
            arxiv_search=(arxiv_search.model_dump(mode="json") if arxiv_search else None),
            teaching=(teaching.model_dump(mode="json") if teaching else None),
            route=(route.model_dump(mode="json") if route is not None else owner.route),
        )
        # Issue 02：新尝试同一事务创建 queued 运行与 started 事件并入队，
        # 由后台执行器领取执行（重试沿用旧轮次的知识库/画像开关）。
        run_id = secrets.token_urlsafe(16)
        run_record = GenerationRunRecord(
            run_id=run_id,
            account_id=account_id,
            conversation_id=conversation_id,
            user_message_id=owner.message_id,
            assistant_message_id=new_attempt.message_id,
            attempt_number=new_attempt.attempt_number,
            status=ChatRunStatus.QUEUED.value,
            config={"use_knowledge_base": use_knowledge_base, "use_profile": use_profile},
            stage=None,
            lease_owner=None,
            lease_expires_at=None,
            attempt_count=0,
            stop_requested=False,
            error_code=None,
            error_message=None,
            duration_ms=None,
            created_at=now,
            updated_at=now,
        )
        started_payload = _started_event_payload(
            conversation_id=conversation_id,
            user_message_id=owner.message_id,
            message_id=new_attempt.message_id,
            attempt_number=new_attempt.attempt_number,
            message=new_attempt,
        )
        self._repo.insert_generation_attempt(
            new_attempt,
            run_record,
            [(ChatStreamEventKind.STARTED.value, started_payload)],
        )
        self._repo.touch_conversation(account_id, conversation_id, now)
        return (
            self._project_message(owner),
            self._project_message(new_attempt, self._run_view(run_record, account_id)),
        )

    # ------------------------------------------------------------------
    # Issue 28：SKILL 载荷发送校验（编排路径见 chat/turn.py）
    # ------------------------------------------------------------------

    def _validate_skill_payload(
        self,
        skill_id: str | None,
        skill_input: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """校验发送请求的 SKILL 载荷；通过后返回落库 JSON（用户消息快照）。"""
        if skill_id is None and skill_input is None:
            return None
        if skill_id is None or skill_input is None:
            raise ChatDomainError(
                "invalid_skill_payload",
                "SKILL 请求必须同时提供标识与任务载荷。",
                422,
            )
        if self._humanizer is None:
            raise ChatDomainError(
                "skill_unavailable", "SKILL 能力暂不可用，请稍后重试。", 503
            )
        try:
            parsed = HumanizerSkillInput.model_validate(
                {**skill_input, "skill_id": skill_id}
            )
        except ValidationError:
            raise ChatDomainError(
                "invalid_skill_payload", "SKILL 任务载荷不合法，请重新填写。", 422
            ) from None
        # 注册校验：标识必须内置、版本固定（resolve_skill 内部校验）。
        # 未注册标识拒绝发送，绝不当作普通消息静默处理。
        try:
            self._humanizer.resolve_skill(parsed)
        except Exception as exc:  # noqa: BLE001 - 注册表错误统一映射为可操作失败
            code = getattr(exc, "code", "skill_unavailable")
            raise ChatDomainError(
                code,
                getattr(exc, "message", "SKILL 能力暂不可用，请稍后重试。"),
                404 if code == "skill_not_found" else 422,
            ) from exc
        return parsed.model_dump(mode="json")


    def approve_mcp_confirmation(
        self, account_id: str, conversation_id: str, message_id: str, confirmation_id: str
    ) -> ChatMessageProjection:
        """聊天内敏感操作确认（approve）：调 MCP 服务 → 更新消息投影。

        只允许确认本人消息上真实挂起的敏感调用；结果写回消息 mcp_call
        列（终态后也可更新），刷新/恢复历史对话不丢失。
        """
        self._ensure_extension_payload_allowed(mcp_call={})
        return self._resolve_mcp_confirmation(
            account_id, conversation_id, message_id, confirmation_id, denied=False
        )

    def deny_mcp_confirmation(
        self, account_id: str, conversation_id: str, message_id: str, confirmation_id: str
    ) -> ChatMessageProjection:
        """聊天内敏感操作拒绝（deny）：调用安全终止并落库 denied 终态。"""
        self._ensure_extension_payload_allowed(mcp_call={})
        return self._resolve_mcp_confirmation(
            account_id, conversation_id, message_id, confirmation_id, denied=True
        )

    def _resolve_mcp_confirmation(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
        confirmation_id: str,
        *,
        denied: bool,
    ) -> ChatMessageProjection:
        if self._mcp is None:
            raise ChatDomainError("mcp_unavailable", "MCP 服务不可用，请稍后重试。", 503)
        message = self._repo.get_message(account_id, message_id)
        if message is None or message.conversation_id != conversation_id:
            raise ChatDomainError("message_not_found", "消息不存在或没有访问权限。", 404)
        if not message.mcp_call:
            raise ChatDomainError(
                "no_mcp_call", "该消息没有 MCP 调用，无法确认。", 409
            )
        call = McpCallMessageProjection.model_validate(message.mcp_call)
        if (
            call.status != McpCallStatus.SENSITIVE_PENDING
            or call.confirmation is None
            or call.confirmation.confirmation_id != confirmation_id
        ):
            raise ChatDomainError(
                "no_pending_confirmation", "该调用没有待确认的敏感操作。", 409
            )
        try:
            if denied:
                result = self._mcp.deny(account_id, call.mcp_id, confirmation_id)
            else:
                result = self._mcp.approve(account_id, call.mcp_id, confirmation_id)
        except McpError as exc:
            raise ChatDomainError(exc.code, exc.message, exc.status_code) from exc
        if denied:
            updated = call.model_copy(
                update={
                    "status": McpCallStatus.DENIED,
                    "error_code": "sensitive_denied",
                    "error_message": "已拒绝本次敏感操作，调用安全终止。",
                    "updated_at": datetime.now(UTC),
                }
            )
        elif result.status == "success":
            updated = call.model_copy(
                update={
                    "status": McpCallStatus.SUCCEEDED,
                    "result_summary": result_summary(result.result),
                    "confirmation": None,
                    "updated_at": datetime.now(UTC),
                }
            )
        else:
            updated = call.model_copy(
                update={
                    "status": McpCallStatus.FAILED,
                    "error_code": result.error_code,
                    "error_message": result.error_message or "调用失败，请重试。",
                    "confirmation": None,
                    "updated_at": datetime.now(UTC),
                }
            )
        self._repo.update_message_mcp_call(
            account_id, message_id, updated.model_dump(mode="json"), datetime.now(UTC)
        )
        refreshed = self._repo.get_message(account_id, message_id)
        assert refreshed is not None
        return self._project_message(refreshed)


    def profile_notifications_for_message(
        self, account_id: str, conversation_id: str, message_id: str
    ) -> list[ProfileNotification]:
        """返回本轮用户消息触发的画像通知（供 SSE profile 事件即时展示）。

        通知在 ``start_generation`` 时已持久化并按账户隔离；重试轮次读取
        同一份来源，不会重复写入。
        """
        if self._profiles is None:
            return []
        source_ref = f"{conversation_id}:{message_id}"
        return [
            notification
            for notification in self._profiles.list_notifications(account_id)
            if notification.source_ref == source_ref
        ]

    def message_projection(
        self, account_id: str, message_id: str
    ) -> ChatMessageProjection | None:
        """返回单条消息投影（供流式完成事件与测试读取最新状态）。"""
        message = self._repo.get_message(account_id, message_id)
        if message is None:
            return None
        return self._project_message(message)

    def _model_history(
        self,
        account_id: str,
        conversation_id: str,
        until_user_message_id: str | None = None,
    ) -> list[dict[str, str]]:
        """组装发送给模型的会话历史（委托给回合编排模块，语义不变）。

        首条为当前对话模式的系统角色合同；每轮用户消息只带最新一条已完成
        的助手回答；失败、停止与进行中的尝试不进上下文。``until_user_message_id``
        把历史截断到指定轮次（重试旧轮次失败消息时，新尝试的上下文不得
        包含其后的后续轮次）。
        """
        return self._turn._model_history(  # noqa: SLF001 - 编排内部契约经服务暴露
            account_id, conversation_id, until_user_message_id
        )

    # -- 反馈闭环 -----------------------------------------------------------

    def submit_feedback(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
        request: AnswerFeedbackRequest,
    ) -> AnswerFeedback:
        """记录一条回答反馈（幂等，失败重试不重复写入，不丢失反馈）。

        ``answer_inappropriate`` 反馈针对回答本身；``profile_incorrect``
        定位到上下文说明披露中的画像记录。反馈按账户隔离持久化。
        """
        message = self._repo.get_message(account_id, message_id)
        if (
            message is None
            or message.conversation_id != conversation_id
            or message.role != ChatMessageRole.ASSISTANT
        ):
            raise ChatDomainError(
                "message_not_found", "消息不存在或没有访问权限。", 404
            )
        if request.kind == FeedbackKind.PROFILE_INCORRECT:
            if request.assertion_id is None:
                raise ChatDomainError(
                    "assertion_required",
                    "标记画像有误时请先选择对应的画像记录。",
                    422,
                )
            if self._profiles is not None:
                try:
                    self._profiles.get_assertion(account_id, request.assertion_id)
                except Exception as exc:  # noqa: BLE001 - 跨账户/不存在统一安全 404
                    raise ChatDomainError(
                        "assertion_not_found", "画像记录不存在或没有访问权限。", 404
                    ) from exc
        career_item_ref = (
            request.career_item_ref.strip() if request.career_item_ref else None
        )
        if career_item_ref is not None and message.career_planning is None:
            raise ChatDomainError(
                "career_item_not_found",
                "该消息不是生涯规划结果，无法定位到具体条目。",
                422,
            )
        existing = self._repo.find_duplicate_feedback(
            account_id,
            message_id,
            request.kind,
            request.assertion_id,
            career_item_ref,
            request.feedback_text.strip(),
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        feedback = AnswerFeedback(
            feedback_id=secrets.token_urlsafe(16),
            account_id=account_id,
            conversation_id=conversation_id,
            message_id=message_id,
            kind=request.kind,
            feedback_text=request.feedback_text.strip(),
            preference=(
                request.preference.strip() if request.preference is not None else None
            ),
            assertion_id=request.assertion_id,
            career_item_ref=career_item_ref,
            status=FeedbackStatus.SUBMITTED,
            created_at=now,
            updated_at=now,
        )
        saved = self._repo.save_feedback(feedback)
        if self._observability is not None:
            self._observability.log_audit(
                actor_account_id=account_id,
                action=AuditAction.ANSWER_FEEDBACK,
                result=AuditResult.SUCCESS,
                object_refs=[message_id, feedback.feedback_id],
                reason="用户提交回答反馈。",
                details={
                    "kind": request.kind.value,
                    "assertion_id": request.assertion_id,
                    "career_item_ref": career_item_ref,
                    "has_preference": request.preference is not None,
                },
            )
        return saved

    def list_feedback(
        self, account_id: str, conversation_id: str
    ) -> list[AnswerFeedback]:
        """返回对话内该账户的反馈记录（最新在前，供恢复与闭环查看）。"""
        return self._repo.list_feedback(account_id, conversation_id)

    def resolve_feedback(
        self,
        account_id: str,
        feedback_id: str,
        resolution_note: str,
    ) -> AnswerFeedback:
        """把一条反馈标记为已处理并记录修正说明（幂等）。"""
        feedback = self._repo.get_feedback(account_id, feedback_id)
        if feedback is None:
            raise ChatDomainError("feedback_not_found", "反馈不存在或没有访问权限。", 404)
        if feedback.status == FeedbackStatus.RESOLVED:
            return feedback
        updated = self._repo.mark_feedback_status(
            account_id,
            feedback_id,
            FeedbackStatus.RESOLVED,
            resolution_note.strip(),
            datetime.now(UTC),
        )
        if updated is None:
            raise ChatDomainError("feedback_not_found", "反馈不存在或没有访问权限。", 404)
        if self._observability is not None:
            self._observability.log_audit(
                actor_account_id=account_id,
                action=AuditAction.FEEDBACK_RESOLVED,
                result=AuditResult.SUCCESS,
                object_refs=[feedback_id],
                reason="回答反馈已按修正说明处理。",
                details={"resolution_note": resolution_note.strip()},
            )
        return updated

    # ------------------------------------------------------------------
    # 投影
    # ------------------------------------------------------------------

    def _reconcile_stale_message(
        self,
        account_id: str,
        message: MessageRecord,
        *,
        status: ChatMessageStatus,
        error_code: str | None,
        error_message: str | None,
        duration_ms: int | None,
        thinking: dict[str, list[str]],
        now: datetime,
    ) -> None:
        """把残留 streaming 消息按指定终态收敛（读取路径陈旧收敛唯一实现）。

        原子守卫保证最多一个写入生效；同步回填记录字段，投影与落库一致。
        """
        finalize_message(
            self._repo,
            account_id,
            message.message_id,
            status=status,
            error_code=error_code,
            error_message=error_message,
            duration_ms=duration_ms,
            model_id=None,
            run_lock_id=None,
            started=_monotonic_of(message.created_at, now),
            now=now,
            thinking=thinking,
        )
        message.status = status
        message.error_code = error_code
        message.error_message = error_message
        message.duration_ms = duration_ms
        message.thinking = thinking

    def run_view_of(
        self, account_id: str, message_id: str
    ) -> ChatRunView | None:
        """返回消息关联运行的外部视图（游标/状态），未运行过返回 None。"""
        run = self._repo.get_run_by_message(account_id, message_id)
        if run is None:
            return None
        return self._run_view(run, account_id)

    def generation_events(
        self, account_id: str, run_id: str, after_seq: int
    ) -> list[GenerationEventRecord]:
        """按游标读取运行上的持久化事件（SSE 订阅回放；账户隔离）。"""
        with self._repo.connection_lock():
            return self._repo.list_generation_events(account_id, run_id, after_seq)

    def generation_run(
        self, account_id: str, run_id: str
    ) -> GenerationRunRecord | None:
        """按账户读取运行记录（订阅端点判断终态；跨账户返回 None）。"""
        with self._repo.connection_lock():
            return self._repo.get_generation_run(account_id, run_id)

    def performance_summary(
        self, account_id: str, since: datetime | None = None
    ) -> dict[str, Any]:
        """本地性能摘要（Issue 06 T6）：p50/p95、超时率、阶段占比、重试。

        只聚合脱敏指标（毫秒/状态/类别），绝不返回用户内容；不建立任何
        遥测外传。用于本地性能观测与防代码回归（本地确定性适配器 p95
        首 token ≤ 2s、终态 ≤ 5s）。
        """
        return self._repo.performance_summary(account_id, since=since)

    def _run_view(self, run: GenerationRunRecord, account_id: str) -> ChatRunView:
        """由运行记录构造外部视图；游标取已持久化的最后事件 seq。"""
        return ChatRunView(
            run_id=run.run_id,
            status=ChatRunStatus(run.status),
            stage=run.stage,
            cursor=self._repo.last_generation_event_seq(account_id, run.run_id),
            attempt_count=run.attempt_count,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def _project_message(
        self,
        message: MessageRecord,
        run_view: ChatRunView | None = None,
    ) -> ChatMessageProjection:
        attachments = (
            [
                attachment.projection()
                for attachment in self._attachments.list_for_message(
                    message.account_id, message.conversation_id, message.message_id
                )
            ]
            if self._attachments is not None and message.role == ChatMessageRole.USER
            else []
        )
        return ChatMessageProjection(
            message_id=message.message_id,
            conversation_id=message.conversation_id,
            role=message.role,
            attempt_number=message.attempt_number,
            status=message.status,
            content=message.content,
            attachments=attachments,
            thinking=(
                ChatThinkingSummary(**message.thinking)
                if message.thinking is not None
                else None
            ),
            retrieval=(
                self._retrieval.round_projection(
                    message.account_id, message.message_id
                )
                if self._retrieval is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            web_search=(
                WebSearchProjection(**message.web_search)
                if message.web_search is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            arxiv_search=(
                ArxivSearchProjection(**message.arxiv_search)
                if message.arxiv_search is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            route=_route_projection(message.route),
            teaching=(
                TeachingTurnProjection.model_validate(message.teaching)
                if message.teaching is not None and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            context_note=(
                ContextNoteProjection.model_validate(message.context_note)
                if message.context_note is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            skill=message.skill,
            # 助手消息的 skill 列只承载人味化结果投影（输入快照只在用户消息），
            # 直接按结果投影解析，无需魔数判别。
            humanizer=(
                HumanizerResultProjection.model_validate(message.skill)
                if message.skill is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            career_planning=(
                CareerPlanningProjection.model_validate(message.career_planning)
                if message.career_planning is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            read_aloud=(
                ReadAloudProjection.model_validate(message.read_aloud)
                if message.read_aloud is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            # 助手消息的 image 列只承载任务/资产状态快照（请求载荷只在
            # 用户消息，由 stream 分支按角色读取，不外发为任务投影）。
            image=(
                ImageTaskProjection.model_validate(message.image)
                if message.image is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            # 助手消息的 video 列同样只承载任务/资产状态快照（Issue 32）。
            video=(
                VideoTaskProjection.model_validate(message.video)
                if message.video is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            # 助手消息的 mcp_call 列只承载调用结果投影（请求载荷只在用户
            # 消息，由 stream 分支按角色读取，不外发为结果投影）。
            mcp_call=(
                McpCallMessageProjection.model_validate(message.mcp_call)
                if message.mcp_call is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            error_code=message.error_code,
            error_message=message.error_message,
            duration_ms=message.duration_ms,
            model_id=message.model_id,
            run_lock_id=message.run_lock_id,
            active_run=run_view,
            created_at=message.created_at,
            updated_at=message.updated_at,
        )

    def _project_conversation(
        self,
        account_id: str,
        conversation_id: str,
        *,
        title: str,
        mode: ChatMode,
        pinned: bool,
        project_id: str | None,
        created_at: datetime,
        updated_at: datetime,
        messages: list[MessageRecord],
        mode_events: list[ModeEventRecord] | None = None,
        plugin_selection: list[ChatPluginSelectionItem] | None = None,
        removed_selections: list[RemovedPluginSelection] | None = None,
        run_views: dict[str, ChatRunView] | None = None,
        mode_locked: bool = False,
    ) -> ChatConversationProjection:
        return ChatConversationProjection(
            conversation_id=conversation_id,
            title=title,
            mode=mode,
            mode_locked=mode_locked,
            pinned=pinned,
            project_id=project_id,
            legacy_project_name=self._repo.legacy_project_name(
                account_id, conversation_id
            ),
            plugin_selection=list(plugin_selection or []),
            removed_selections=list(removed_selections or []),
            created_at=created_at,
            updated_at=updated_at,
            messages=[
                self._project_message(message, (run_views or {}).get(message.message_id))
                for message in messages
            ],
            mode_events=[
                ChatModeEventProjection(
                    event_id=event.event_id,
                    conversation_id=event.conversation_id,
                    from_mode=ChatMode(event.from_mode),
                    to_mode=ChatMode(event.to_mode),
                    created_at=event.created_at,
                )
                for event in (mode_events or [])
            ],
        )


def _selection_set(
    entries: list[dict[str, Any]] | None,
) -> set[tuple[str, str]]:
    """由持久化 JSON 条目提取选择键集合（去重比较用）。"""
    result: set[tuple[str, str]] = set()
    for entry in entries or []:
        if isinstance(entry, dict) and "kind" in entry and "plugin_id" in entry:
            result.add((str(entry["kind"]), str(entry["plugin_id"])))
    return result


def _record_selection(
    entries: list[dict[str, Any]] | None,
) -> list[ChatPluginSelectionItem]:
    """把持久化 JSON 条目转为契约模型（非法条目跳过）。"""
    result: list[ChatPluginSelectionItem] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        try:
            result.append(ChatPluginSelectionItem(**entry))
        except (TypeError, ValueError):
            continue
    return result


def _monotonic_of(created_at: datetime, now: datetime) -> float:
    """由创建时间估算生成已进行的秒数（用于陈旧消息的耗时）。"""
    return max(0.0, (now - created_at).total_seconds())


def _validate_image_payload(
    image: dict[str, Any] | None, has_skill: bool
) -> dict[str, Any] | None:
    """校验图片请求载荷并落库为用户消息快照（与 SKILL 载荷互斥）。"""
    if image is None:
        return None
    if has_skill:
        raise ChatDomainError(
            "conflicting_payload", "SKILL 与图片请求不能同时携带。", 422
        )
    try:
        payload = ImageRequestPayload.model_validate(image)
    except ValidationError as exc:
        raise ChatDomainError(
            "invalid_image_request", "图片请求载荷无效。", 422
        ) from exc
    if payload.kind == ImageTaskKind.EDIT and not payload.source_object_id:
        raise ChatDomainError(
            "invalid_image_request", "编辑必须且只能选择一个来源图片。", 422
        )
    return payload.model_dump(mode="json")


def _validate_video_payload(
    video: dict[str, Any] | None, has_skill: bool
) -> dict[str, Any] | None:
    """校验文生视频请求载荷并落库为用户消息快照（与 SKILL 载荷互斥）。"""
    if video is None:
        return None
    if has_skill:
        raise ChatDomainError(
            "conflicting_payload", "SKILL 与视频请求不能同时携带。", 422
        )
    try:
        payload = VideoRequestPayload.model_validate(video)
    except ValidationError as exc:
        raise ChatDomainError(
            "invalid_video_request", "视频请求载荷无效。", 422
        ) from exc
    return payload.model_dump(mode="json")


def _validate_mcp_call_payload(
    mcp_call: dict[str, Any] | None, has_skill: bool
) -> dict[str, Any] | None:
    """校验 MCP 调用载荷并落库为用户消息快照（与 SKILL 载荷互斥）。

    与 image/video 载荷的互斥校验一致：同一轮只允许一种载荷驱动生成。
    """
    if mcp_call is None:
        return None
    if has_skill:
        raise ChatDomainError(
            "conflicting_payload", "SKILL 与 MCP 调用不能同时携带。", 422
        )
    try:
        payload = McpCallRequestPayload.model_validate(mcp_call)
    except ValidationError as exc:
        raise ChatDomainError(
            "invalid_mcp_call", "MCP 调用载荷无效。", 422
        ) from exc
    return payload.model_dump(mode="json")

