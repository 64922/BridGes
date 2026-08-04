"""持久化流式聊天服务（Issue 11 纵向切片）。

服务负责对话/消息的账户隔离持久化、生成状态机（streaming → done /
error / stopped）、用户级重试（每次重试新建助手尝试，历史原样保留）
与模型运行锁落库。真实供应商调用经由 ``ModelGateway.stream`` 完成；
失败以稳定错误码返回，由 API 层映射为可操作中文提示。
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Protocol

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamEvent
from bridges.chat.attachments import ChatAttachmentError, ChatAttachmentService
from bridges.chat.lifecycle import GenerationLifecycle
from bridges.chat.repository import (
    ConversationRecord,
    ConversationRepository,
    MessageRecord,
    ModeEventRecord,
)
from bridges.contracts.ai import ModelRunLock
from bridges.contracts.chat import (
    ChatConversationListProjection,
    ChatConversationProjection,
    ChatConversationSummary,
    ChatMessageProjection,
    ChatMessageRole,
    ChatMessageStatus,
    ChatMode,
    ChatModeEventProjection,
    ChatThinkingSummary,
)
from bridges.contracts.retrieval import (
    CitationProjection,
    RetrievalLayerStatus,
    RetrievalRoundProjection,
    RetrievalSourceLayer,
)
from bridges.contracts.workflows import RunContextEnvelope
from bridges.retrieval.service import LayeredRetrievalService

#: 核心对话能力的固定绑定（ADR-0009 固定模型矩阵）。
CHAT_CAPABILITY_NAME = "qwen_text_chat"
CHAT_CAPABILITY_VERSION = "1"

#: 对话双模式（ADR-0022）：普通新聊天默认日常陪伴，学习项目新建对话默认学习模式。
CHAT_MODE = ChatMode.COMPANION


class OrchestrationStep(Protocol):
    """模式编排中的一步（Issue 14 声明的代码级编排 seam）。

    每步向用户披露一条可公开进度（``describe``），并在后续 Issue 接入
    真实行为（教学证据充足性门、自动联网检索、理解检查、测验）时扩展
    ``run`` 钩子；当前全部步骤为声明式，行为零变化。
    """

    @property
    def name(self) -> str: ...

    def describe(self) -> str: ...


class DeclarativeStep:
    """声明式编排步骤：当前只提供可披露进度，不产生行为。

    后续 Issue 引入真实能力时，用带 ``run`` 的步骤替换对应声明项，
    或在此类上扩展可选钩子；生成主路径与思考摘要无需改写。
    """

    def __init__(self, name: str, description: str) -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    def describe(self) -> str:
        return self._description


class ModeContract:
    """对话模式的回答策略合同（Issue 14，AC-05/AC-04 的代码级编排接口）。

    每种模式一份合同，是后续能力接入的单一 seam：编排能力（画像、材料
    检索、教学规划、理解检查、测验）作为合同声明的步骤接入，不重写生成
    主路径；思考摘要的初始步骤从 ``steps`` 派生，合同是唯一来源。
    """

    def __init__(
        self,
        system_prompt: str,
        steps: tuple[OrchestrationStep, ...],
    ) -> None:
        self.system_prompt = system_prompt
        #: 合同声明的编排步骤（按执行顺序）；生成开始即全部披露为
        #: 可公开进度，生命周期只在其后追加质量检查结论。
        self.steps = steps


#: 两种模式的角色合同（不包含隐藏指令或原始思维链，只定义回答策略）。
#: 日常陪伴：自然、有分寸的个性化陪伴——识别当前情境信号但不形成心理诊断，
#: 不把单次情绪写成长期事实。
#: 学习模式：因材施教老师合同——把「界定目标 → 参考知识状态 → 循序讲解 →
#: 理解检查/适量测验」的编排阶段显式声明为合同步骤，后续画像、材料检索、
#: 教学规划、理解检查与测验功能在此合同上接入；当前实现只注入合同与编排
#: 约定，真实检索/画像/测验由后续 Issue 接入，不伪造工具结果。
_MODE_CONTRACTS: dict[ChatMode, ModeContract] = {
    ChatMode.COMPANION: ModeContract(
        system_prompt=(
            "你是 BridGes，一位长期科学学习与表达伙伴。当前使用「日常陪伴」合同："
            "回应自然、有分寸、有人情味，把用户当作一起长期学习的朋友。"
            "可以识别对话里出现的当前情境信号（如此刻的情绪、正在做的事），"
            "并温和地接住它们；但绝不据此形成心理诊断，也不把单次情绪写成长期事实。"
            "表达要简洁、真诚、不居高临下。涉及健康、心理等话题时，如实说明你能帮助的"
            "范围，需要专业意见时建议咨询专业人士。"
        ),
        steps=(
            DeclarativeStep("context", "理解你的问题与当前语境"),
            DeclarativeStep("answer", "组织并生成回答"),
        ),
    ),
    ChatMode.STUDY: ModeContract(
        system_prompt=(
            "你是 BridGes，一位因材施教的科学老师。当前使用「学习模式」合同，"
            "按以下编排约定组织每次回答："
            "1）先界定学习目标：区分概念理解、方法掌握与练习巩固，确定本次回答的层次；"
            "2）参考对话内已有的知识状态与学习进度，从学生当前水平出发循序渐进讲解；"
            "3）讲解中用具体例子连接新知识与已有认知，必要时主动安排理解检查"
            "（简短提问或请学生复述）与适量测验；"
            "4）回答结束给出下一步学习建议。"
            "本地教学材料不足时，如实说明缺失并建议可核实的补充来源，不得编造资料。"
            "后续的画像、材料检索、教学规划、理解检查与测验功能会逐步接入本合同；"
            "尚未接入的功能不得伪造结果。"
        ),
        # 编排步骤与提示词合同一致；后续教学证据充足性门、自动联网检索、
        # 理解检查与测验作为带 run 的步骤接入（Issue 23+），替换声明项即可。
        steps=(
            DeclarativeStep("teaching_objective", "按学习目标分析你的问题与已有知识"),
            DeclarativeStep("teaching_explain", "组织循序渐进的教学回答"),
        ),
    ),
}


def _contract(mode: ChatMode) -> ModeContract:
    return _MODE_CONTRACTS[mode]

#: 由首条用户消息推导对话标题的最大长度。
_TITLE_MAX = 24

#: 生成失败/断流时向用户展示的中文说明（稳定错误码 → 可操作提示）。
STREAM_INTERRUPTED_MESSAGE = "连接中断，已保留已接收内容，可点击重试。"

#: 稳定错误码 → 可操作中文提示（绝不输出供应商原文或调试字段）。
_ERROR_MESSAGES: dict[str, str] = {
    "rate_limit": "请求过于频繁（已触发限流），请稍后重试。",
    "transient": "连接中断或服务暂时不可用，请检查网络后重试。",
    "region_error": "无法连接 Qwen 服务，请检查网络后重试。",
    "auth_error": "Qwen API Key 无效或已失效，请前往「设置」更新密钥。",
    "stream_interrupted": STREAM_INTERRUPTED_MESSAGE,
    "unregistered_capability": "核心对话能力未就绪，请稍后重试。",
    "capability_not_verified": "核心对话能力未通过验证，请前往「设置」重新探测。",
    "no_adapter": "核心对话能力未就绪（缺少适配器），请检查服务配置。",
    "provider_rejected": "供应商拒绝了本次请求，请稍后重试。",
    "internal_error": "生成过程出现内部错误，请重试。",
}

#: 用户点击重试后有望成功的错误码（限流/瞬时故障/断流/内部错误）。
_RETRYABLE_CODES = frozenset(
    {"rate_limit", "transient", "region_error", "stream_interrupted", "internal_error"}
)


def user_facing_error(error_code: str | None, fallback: str | None = None) -> str:
    """把稳定错误码映射为可操作中文提示；未知码不泄漏内部细节。"""
    if error_code and error_code in _ERROR_MESSAGES:
        return _ERROR_MESSAGES[error_code]
    if error_code and error_code.startswith("client_error_"):
        # 供应商拒绝类错误（4xx 业务拒绝）单独成类，给出可操作说明
        return _ERROR_MESSAGES["provider_rejected"]
    return fallback or "生成失败，请稍后重试。"


def error_is_retryable(error_code: str | None) -> bool:
    """错误是否可通过"重试新建助手尝试"恢复。"""
    return error_code in _RETRYABLE_CODES


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
    ) -> None:
        self._repo = repository
        self._gateway = gateway
        self._attachments = attachment_service
        #: 分层本地检索（Issue 20）；未挂载时生成不检索、不产生引用。
        self._retrieval = retrieval_service
        #: 进行中生成的停止信号与活跃度跟踪（注册/续期/TTL/停止唯一入口）。
        self._lifecycle = GenerationLifecycle()

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------

    def create_conversation(
        self,
        account_id: str,
        title: str | None = None,
        mode: ChatMode = ChatMode.COMPANION,
        project_id: str | None = None,
    ) -> ChatConversationProjection:
        """新建对话；普通新聊天默认日常陪伴，学习项目传入 ``study``。"""
        now = datetime.now(UTC)
        conversation_id = secrets.token_urlsafe(16)
        self._repo.create_conversation(
            account_id=account_id,
            conversation_id=conversation_id,
            title=(title or "").strip(),
            mode=mode.value,
            created_at=now,
            project_id=project_id,
        )
        return self._project_conversation(
            account_id,
            conversation_id,
            title=(title or "").strip(),
            mode=mode,
            pinned=False,
            project_id=project_id,
            created_at=now,
            updated_at=now,
            messages=[],
            mode_events=[],
        )

    def set_conversation_mode(
        self, account_id: str, conversation_id: str, mode: ChatMode
    ) -> tuple[ChatConversationProjection, ChatModeEventProjection | None]:
        """切换对话模式：写入可见事件，只影响后续消息，不重写历史回答。

        相同模式切换幂等：不写事件，直接返回当前投影。跨账户访问返回
        ``conversation_not_found``，不泄漏存在性。
        """
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            raise ChatDomainError(
                "conversation_not_found", "对话不存在或没有访问权限。", 404
            )
        current = ChatMode(record.mode)
        event: ChatModeEventProjection | None = None
        now = datetime.now(UTC)
        if current != mode:
            event = ChatModeEventProjection(
                event_id=secrets.token_urlsafe(16),
                conversation_id=conversation_id,
                from_mode=current,
                to_mode=mode,
                created_at=now,
            )
            self._repo.insert_mode_event(
                event_id=event.event_id,
                conversation_id=conversation_id,
                account_id=account_id,
                from_mode=event.from_mode.value,
                to_mode=event.to_mode.value,
                created_at=now,
            )
            self._repo.set_conversation_mode(
                account_id, conversation_id, mode.value, now
            )
        return self._project_conversation(
            account_id,
            conversation_id,
            title=record.title,
            mode=mode,
            pinned=record.pinned,
            project_id=record.project_id,
            created_at=record.created_at,
            updated_at=now if current != mode else record.updated_at,
            messages=self._repo.list_messages(account_id, conversation_id),
            mode_events=self._repo.list_mode_events(account_id, conversation_id),
        ), event

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
                    pinned=record.pinned,
                    project_id=record.project_id,
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
    ) -> ChatConversationProjection:
        """改名或置顶自己的会话；跨账户目标统一返回安全 404。"""
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            raise ChatDomainError("conversation_not_found", "对话不存在或没有访问权限。", 404)
        normalized_title = title.strip() if title is not None else None
        if title is not None and not normalized_title:
            raise ChatDomainError("invalid_title", "对话标题不能为空。", 422)
        changed = normalized_title != record.title if normalized_title is not None else False
        changed = changed or (pinned is not None and pinned != record.pinned)
        now = datetime.now(UTC)
        if changed:
            self._repo.update_conversation(
                account_id,
                conversation_id,
                title=normalized_title,
                pinned=pinned,
                updated_at=now,
            )
            record = self._repo.get_conversation(account_id, conversation_id)
            assert record is not None
        return self._project_conversation(
            account_id,
            conversation_id,
            title=record.title,
            mode=ChatMode(record.mode),
            pinned=record.pinned,
            project_id=record.project_id,
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
        return self._project_conversation(
            record.account_id,
            record.conversation_id,
            title=record.title,
            mode=ChatMode(record.mode),
            pinned=record.pinned,
            project_id=record.project_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
            messages=self._repo.list_messages(record.account_id, record.conversation_id),
            mode_events=self._repo.list_mode_events(
                record.account_id, record.conversation_id
            ),
        )

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

        读取时收敛"生成中但早已无人写入"的陈旧消息（进程重启/断流后
        恢复场景）：仍处于 streaming 的消息落库为可重试错误，绝不把
        半截占位当回答。
        """
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            return None
        messages = self._repo.list_messages(account_id, conversation_id)
        now = datetime.now(UTC)
        for message in messages:
            if message.status == ChatMessageStatus.STREAMING:
                if self._lifecycle.is_active(message.message_id):
                    # 生成仍在进行：读取不打断流
                    continue
                stale_thinking = _failed_thinking(
                    message.thinking or _initial_thinking(CHAT_MODE),
                    "stream_interrupted",
                )
                self._finalize_message(
                    account_id,
                    message.message_id,
                    status=ChatMessageStatus.ERROR,
                    error_code="stream_interrupted",
                    error_message=STREAM_INTERRUPTED_MESSAGE,
                    duration_ms=max(1, int((now - message.created_at).total_seconds() * 1000)),
                    model_id=None,
                    run_lock_id=None,
                    started=_monotonic_of(message.created_at, now),
                    now=now,
                    thinking=stale_thinking,
                )
                message.status = ChatMessageStatus.ERROR
                message.error_code = "stream_interrupted"
                message.error_message = STREAM_INTERRUPTED_MESSAGE
                message.thinking = stale_thinking
        return self._project_conversation(
            account_id,
            record.conversation_id,
            title=record.title,
            mode=ChatMode(record.mode),
            pinned=record.pinned,
            project_id=record.project_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
            messages=messages,
            mode_events=self._repo.list_mode_events(account_id, conversation_id),
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
    ) -> tuple[ChatMessageProjection, ChatMessageProjection]:
        """原子创建用户消息与 streaming 状态的助手消息，返回两者投影。"""
        now = datetime.now(UTC)
        content = content.strip()
        if not content:
            raise ChatDomainError("empty_message", "消息内容不能为空。", 422)
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
        attachment_ids = list(attachment_ids or [])
        if attachment_ids:
            if self._attachments is None:
                raise ChatDomainError(
                    "attachments_unavailable", "附件服务未启用，请稍后重试。", 503
                )
            try:
                self._attachments.validate_unbound(
                    account_id, conversation_id, attachment_ids
                )
            except ChatAttachmentError as exc:
                raise ChatDomainError(exc.code, exc.message, exc.status_code) from exc

        thinking = _initial_thinking(ChatMode(record.mode))
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
        )
        if attachment_ids:
            self._repo.insert_messages_with_attachments(
                user_message, assistant_message, attachment_ids
            )
        else:
            self._repo.insert_message(user_message)
            self._repo.insert_message(assistant_message)
        self._repo.touch_conversation(account_id, conversation_id, now)
        # 预注册停止信号：生成一经创建即视为"活跃"，读取收敛不会误伤
        self._lifecycle.register(assistant_message.message_id)

        if not record.title:
            title = content if len(content) <= _TITLE_MAX else content[:_TITLE_MAX] + "…"
            self._repo.set_conversation_title(account_id, conversation_id, title, now)

        return (
            self._project_message(user_message),
            self._project_message(assistant_message),
        )

    def stream_generation(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        run_context: RunContextEnvelope,
        until_user_message_id: str | None = None,
        use_knowledge_base: bool = True,
    ) -> Iterator[StreamEvent]:
        """驱动一次生成：调用网关流式接口，边收边落库，结束时收敛状态。

        生成前执行一轮分层本地检索（Issue 20）：按「当前附件 → 当前项目
        文件 → 已授权全局知识库」确定候选作用域，把最终引用固化为消息的
        检索轮次，并注入最小上下文；关闭全局知识库时本轮不查询该层。
        检索失败不阻断生成（本轮无引用，仍按无依据呈现）。事件经此处
        原样透传给 API 层；停止信号（用户停止/切换账户导致的客户端断开）
        都会把消息收敛到明确终态，绝不留 streaming 僵尸。
        """
        # 生成器惰性启动：若在启动前已被停止/收敛（例如停止接口先行完成），
        # 直接退出，绝不重新唤起一条已经终态的消息；同时校验消息归属
        # 对话，杜绝跨对话用 message_id 驱动生成。
        current = self._repo.get_message(account_id, assistant_message_id)
        if (
            current is None
            or current.conversation_id != conversation_id
            or current.status != ChatMessageStatus.STREAMING
        ):
            return

        # 复用 start/retry 阶段预注册的停止信号（保证注册表在 start 即
        # 生效，读取陈旧收敛不会误伤进行中的流）；缺失时补注册。
        entry = self._lifecycle.signal_and_started(assistant_message_id)
        stop_event = (
            entry[0] if entry is not None else self._lifecycle.register(assistant_message_id)
        )
        content = ""
        started = time.monotonic()
        conversation = self._repo.get_conversation(account_id, conversation_id)
        thinking = _initial_thinking(
            ChatMode(conversation.mode) if conversation is not None else CHAT_MODE
        )
        try:
            history = self._model_history(
                account_id, conversation_id, until_user_message_id
            )
            # 生成前执行一轮分层检索：查询文本取所属用户消息正文；检索
            # 结果固化到消息投影（引用展示数据不漂移），并注入最小上下文。
            retrieval_round: RetrievalRoundProjection | None = None
            if self._retrieval is not None and not stop_event.is_set():
                messages = self._repo.list_messages(account_id, conversation_id)
                owner = _owner_user_message(messages, assistant_message_id)
                round_query = owner.content if owner is not None else ""
                retrieval_round = self._retrieval.run_round(
                    account_id,
                    conversation_id,
                    assistant_message_id,
                    until_user_message_id
                    or (owner.message_id if owner is not None else None),
                    round_query,
                    use_knowledge_base=use_knowledge_base,
                )
                if retrieval_round is not None:
                    thinking = _retrieval_thinking(thinking, retrieval_round)
            payload: dict[str, Any] = {
                "messages": history,
                "temperature": 0.7,
                "max_tokens": 1024,
            }
            if retrieval_round is not None and retrieval_round.citations:
                # 检索上下文以独立 system 块注入，与模式合同并存：模型只可
                # 引用本块提供的材料，不得声称存在未提供的文件或页码。
                payload["messages"].insert(
                    1,
                    {
                        "role": "system",
                        "content": _retrieval_context(retrieval_round.citations),
                    },
                )
            for event in self._gateway.stream(
                CHAT_CAPABILITY_NAME, CHAT_CAPABILITY_VERSION, run_context, payload
            ):
                if stop_event.is_set():
                    self._finalize_message(
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.STOPPED,
                        error_code=None,
                        error_message=None,
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=_stopped_thinking(thinking),
                    )
                    return
                if event.kind == "delta":
                    content += event.delta
                    self._repo.update_message_content(
                        account_id, assistant_message_id, content, datetime.now(UTC)
                    )
                    self._lifecycle.touch(assistant_message_id)
                    yield event
                elif event.kind == "error":
                    self._persist_lock(account_id, event.lock)
                    self._finalize_message(
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        error_code=event.error_code,
                        error_message=user_facing_error(event.error_code, event.error_message),
                        duration_ms=None,
                        model_id=self._lock_model_id(event.lock),
                        run_lock_id=self._lock_id(event.lock),
                        started=started,
                        now=datetime.now(UTC),
                        thinking=_failed_thinking(thinking, event.error_code),
                    )
                    yield event
                    return
                elif event.kind == "done":
                    self._persist_lock(account_id, event.lock)
                    self._finalize_message(
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.DONE,
                        error_code=None,
                        error_message=None,
                        duration_ms=None,
                        model_id=self._lock_model_id(event.lock),
                        run_lock_id=self._lock_id(event.lock),
                        started=started,
                        now=datetime.now(UTC),
                        thinking=_done_thinking(thinking),
                    )
                    yield event
                    return
        except GeneratorExit:
            # 客户端断开：收敛为可重试错误，保留已接收正文与已完成摘要。
            self._finalize_message(
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code="stream_interrupted",
                error_message=STREAM_INTERRUPTED_MESSAGE,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=datetime.now(UTC),
                thinking=_failed_thinking(thinking, "stream_interrupted"),
            )
            raise
        except Exception:  # noqa: BLE001 - 未分类异常也须收敛，绝不滞留 streaming 僵尸
            # 内部异常（如落库失败）：收敛为可重试错误并产出 error 事件，
            # 让 SSE 契约始终以终态事件结束，不向用户泄漏内部细节。
            self._finalize_message(
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code="internal_error",
                error_message="生成过程出现内部错误，请重试。",
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=datetime.now(UTC),
                thinking=_failed_thinking(thinking, "internal_error"),
            )
            yield StreamEvent(
                kind="error",
                error_code="internal_error",
                error_message="生成过程出现内部错误，请重试。",
            )
            return
        finally:
            self._lifecycle.unregister(assistant_message_id)

    def stop_generation(
        self, account_id: str, conversation_id: str, message_id: str
    ) -> ChatMessageProjection:
        """停止进行中的生成；幂等，已终态的消息直接返回当前状态。"""
        message = self._repo.get_message(account_id, message_id)
        if message is None or message.conversation_id != conversation_id:
            raise ChatDomainError(
                "message_not_found", "消息不存在或没有访问权限。", 404
            )
        if message.status != ChatMessageStatus.STREAMING:
            return self._project_message(message)

        entry = self._lifecycle.signal_and_started(message_id)
        stop_event = entry[0] if entry is not None else None
        started = entry[1] if entry is not None else None
        if stop_event is not None:
            stop_event.set()
        now = datetime.now(UTC)
        # 生成线程尚未启动（或跨进程遗留消息）时按创建时间估算耗时并直接
        # 以墙钟时长传入；运行时用真实单调起点由 _finalize_message 计算。
        duration_ms: int | None = None
        if started is None:
            duration_ms = max(1, int((now - message.created_at).total_seconds() * 1000))
            started = 0.0
        # 先收敛状态再注销活跃标记：避免并发读取在两者之间把仍处于
        # streaming 的消息误判为陈旧中断（终态由原子守卫保证单一写入）。
        # 停止同样保留已完成思考摘要并写入中文质量结论（Issue 14）。
        self._finalize_message(
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
            thinking=_stopped_thinking(message.thinking or _initial_thinking(CHAT_MODE)),
        )
        self._lifecycle.unregister(message_id)
        finalized = self._repo.get_message(account_id, message_id)
        if finalized is None:
            raise ChatDomainError(
                "message_not_found", "消息不存在或没有访问权限。", 404
            )
        return self._project_message(finalized)

    def retry_generation(
        self, account_id: str, conversation_id: str, message_id: str
    ) -> tuple[ChatMessageProjection, ChatMessageProjection]:
        """为已终态（失败/停止/完成）的助手消息创建新的助手尝试。

        尝试号递增，历史尝试原样保留（含错误状态），新尝试成为该轮的
        最新回答；绝不静默改写历史，也不把失败注册为成功。
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

        owner = _owner_user_message(existing, message_id)
        if owner is None:
            raise ChatDomainError(
                "not_retryable_message", "找不到该助手消息对应的用户消息。", 400
            )
        max_attempt = max(
            (m.attempt_number for m in _attempt_group(existing, owner.message_id)),
            default=0,
        )
        now = datetime.now(UTC)
        new_attempt = MessageRecord(
            message_id=secrets.token_urlsafe(16),
            conversation_id=conversation_id,
            account_id=account_id,
            role=ChatMessageRole.ASSISTANT,
            attempt_number=max_attempt + 1,
            status=ChatMessageStatus.STREAMING,
            content="",
            thinking=_initial_thinking(ChatMode(record.mode)),
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
        )
        self._repo.insert_message(new_attempt)
        self._repo.touch_conversation(account_id, conversation_id, now)
        self._lifecycle.register(new_attempt.message_id)
        return (
            self._project_message(owner),
            self._project_message(new_attempt),
        )

    def message_projection(
        self, account_id: str, message_id: str
    ) -> ChatMessageProjection | None:
        """返回单条消息投影（供流式完成事件与测试读取最新状态）。"""
        message = self._repo.get_message(account_id, message_id)
        if message is None:
            return None
        return self._project_message(message)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _model_history(
        self,
        account_id: str,
        conversation_id: str,
        until_user_message_id: str | None = None,
    ) -> list[dict[str, str]]:
        """组装发送给模型的会话历史。

        首条为当前对话模式的系统角色合同（companion/study，Issue 14）；
        每轮用户消息只带最新一条已完成（done）的助手回答；失败、停止与
        进行中的尝试不进上下文，避免把错误内容当成回答。``until_user_message_id``
        把历史截断到指定轮次（重试旧轮次失败消息时，新尝试的上下文不得
        包含其后的后续轮次）。
        """
        messages = self._repo.list_messages(account_id, conversation_id)
        record = self._repo.get_conversation(account_id, conversation_id)
        mode = ChatMode(record.mode) if record is not None else CHAT_MODE
        history: list[dict[str, str]] = [
            {"role": "system", "content": _contract(mode).system_prompt}
        ]
        latest_done: MessageRecord | None = None
        for message in messages:
            if message.role == ChatMessageRole.USER:
                if latest_done is not None:
                    history.append({"role": "assistant", "content": latest_done.content})
                    latest_done = None
                history.append({"role": "user", "content": message.content})
                if (
                    until_user_message_id is not None
                    and message.message_id == until_user_message_id
                ):
                    break
            elif message.status == ChatMessageStatus.DONE:
                latest_done = message
        else:
            if latest_done is not None:
                history.append({"role": "assistant", "content": latest_done.content})
        return history

    def _persist_lock(self, account_id: str, lock: ModelRunLock | None) -> None:
        if lock is None:
            return
        self._repo.insert_run_lock(account_id, lock)

    def _finalize_message(
        self,
        account_id: str,
        message_id: str,
        *,
        status: ChatMessageStatus,
        error_code: str | None,
        error_message: str | None,
        duration_ms: int | None,
        model_id: str | None,
        run_lock_id: str | None,
        started: float,
        now: datetime,
        thinking: dict[str, list[str]] | None = None,
    ) -> None:
        """原子收敛生成状态；仅当仍处于 streaming 时生效（防竞态双写）。"""
        measured = max(1, int((time.monotonic() - started) * 1000))
        self._repo.finalize_message(
            account_id,
            message_id,
            status=status,
            error_code=error_code,
            error_message=error_message,
            duration_ms=duration_ms or measured,
            model_id=model_id,
            run_lock_id=run_lock_id,
            updated_at=now,
            thinking=thinking,
        )

    @staticmethod
    def _lock_model_id(lock: ModelRunLock | None) -> str | None:
        return lock.actual_model_id if lock is not None else None

    @staticmethod
    def _lock_id(lock: ModelRunLock | None) -> str | None:
        return lock.lock_id if lock is not None else None

    # ------------------------------------------------------------------
    # 投影
    # ------------------------------------------------------------------

    def _project_message(self, message: MessageRecord) -> ChatMessageProjection:
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
            error_code=message.error_code,
            error_message=message.error_message,
            duration_ms=message.duration_ms,
            model_id=message.model_id,
            run_lock_id=message.run_lock_id,
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
    ) -> ChatConversationProjection:
        return ChatConversationProjection(
            conversation_id=conversation_id,
            title=title,
            mode=mode,
            pinned=pinned,
            project_id=project_id,
            created_at=created_at,
            updated_at=updated_at,
            messages=[self._project_message(message) for message in messages],
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


def _monotonic_of(created_at: datetime, now: datetime) -> float:
    """由创建时间估算生成已进行的秒数（用于陈旧消息的耗时）。"""
    return max(0.0, (now - created_at).total_seconds())


def _owner_user_message(
    messages: list[MessageRecord], assistant_message_id: str
) -> MessageRecord | None:
    """返回某条助手消息所属（其之前最近的）用户消息。"""
    owner: MessageRecord | None = None
    for message in messages:
        if message.role == ChatMessageRole.USER:
            owner = message
        elif message.message_id == assistant_message_id:
            return owner
    return None


def _attempt_group(
    messages: list[MessageRecord], user_message_id: str
) -> list[MessageRecord]:
    """返回某轮用户消息之下的全部助手尝试（不含后续其他轮次）。"""
    group: list[MessageRecord] = []
    in_group = False
    for message in messages:
        if message.role == ChatMessageRole.USER:
            in_group = message.message_id == user_message_id
            continue
        if in_group:
            group.append(message)
    return group


# ---------------------------------------------------------------------------
# 分层检索的思考摘要与上下文（Issue 20）
# ---------------------------------------------------------------------------
#
# 思考摘要的「引用依据」（evidence）与「工具进度」（tools）由本轮检索
# 结果构造，与「模型组织说明」（steps，来自模式合同）严格区分；引用
# 展示数据在检索时固化，索引重建不会让历史引用静默漂移。

#: 来源层的中文呈现（思考摘要工具条目与上下文说明共用）。
_LAYER_NAMES: dict[RetrievalSourceLayer, str] = {
    RetrievalSourceLayer.ATTACHMENT: "当前附件",
    RetrievalSourceLayer.PROJECT: "学习项目文件",
    RetrievalSourceLayer.KNOWLEDGE_BASE: "全局知识库",
}
#: 思考摘要中引用片段的最大长度。
_EVIDENCE_SNIPPET_MAX = 160
#: 注入模型的最小上下文总长度上限。
_CONTEXT_MAX_CHARS = 2400


def _citation_location(
    citation: CitationProjection, *, snippet_max: int = 0
) -> str:
    """引用位置的面向用户中文描述（页码/章节/原文片段）。"""
    parts: list[str] = []
    if citation.page_number is not None:
        parts.append(f"第 {citation.page_number} 页")
    if citation.section_title:
        parts.append(f"章节：{citation.section_title}")
    if parts:
        return " · ".join(parts)
    return "原文片段"


def _retrieval_thinking(
    thinking: dict[str, list[str]], retrieval_round: RetrievalRoundProjection
) -> dict[str, list[str]]:
    """把一轮检索结果并入思考摘要：evidence=引用依据，tools=检索进度。"""
    evidence: list[str] = []
    for citation in retrieval_round.citations:
        snippet = citation.snippet
        if len(snippet) > _EVIDENCE_SNIPPET_MAX:
            snippet = snippet[:_EVIDENCE_SNIPPET_MAX] + "…"
        evidence.append(
            f"引用了「{citation.filename}」{_citation_location(citation)}"
            f"：{snippet}"
        )
    tools: list[str] = []
    for layer in retrieval_round.layers:
        if layer.status == RetrievalLayerStatus.OK and layer.candidates > 0:
            tools.append(
                f"已检索{_LAYER_NAMES[layer.layer]}：{layer.candidates} 条候选"
            )
    if not tools:
        tools.append(_sufficiency_tool(retrieval_round))
    return {**thinking, "evidence": evidence, "tools": tools}


def _sufficiency_tool(retrieval_round: RetrievalRoundProjection) -> str:
    """无候选时面向用户的检索进度说明（结构化，不以空候选表示成功）。

    直接复用检索轮次的中文注记（``note`` 是充足性信号的唯一文案来源），
    避免同一信号在后端多份措辞漂移。
    """
    if retrieval_round.note:
        return retrieval_round.note
    if retrieval_round.citations:
        return "已检索本地材料。"
    return "本轮未检索到匹配的本地材料。"


def _retrieval_context(citations: list[CitationProjection]) -> str:
    """构造注入模型的最小检索上下文（固定格式，可测试）。

    材料只作为参考：模型引用时必须注明来源编号，不得声称存在未提供的
    文件或页码——引用芯片本身由检索轮次提供，杜绝幻觉来源。
    """
    lines = [
        "以下是本轮检索到的本地材料（仅作参考；引用时注明来源编号，"
        "不得声称存在未提供的文件或页码）："
    ]
    used = 0
    for index, citation in enumerate(citations, start=1):
        snippet = citation.snippet
        if len(snippet) > _EVIDENCE_SNIPPET_MAX:
            snippet = snippet[:_EVIDENCE_SNIPPET_MAX] + "…"
        line = f"[{index}]「{citation.filename}」{_citation_location(citation)}：{snippet}"
        if used + len(line) > _CONTEXT_MAX_CHARS:
            break
        used += len(line)
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 思考摘要（Issue 14）
# ---------------------------------------------------------------------------
#
# 「思考摘要」是面向用户的过程说明，不是模型隐藏推理。全部由结构化进度
# 事件与可披露结果构造：步骤（处理过程）、证据（采用的来源）、工具（调用
# 进度）、质量（检查结论）。绝不包含原始 Chain-of-Thought、系统提示、
# 隐藏指令或逐 token 推理。当前切片没有检索/工具能力，证据与工具数组
# 为空；后续 Issue 接入时在对应生命周期事件处追加。

def _initial_thinking(mode: ChatMode) -> dict[str, list[str]]:
    """生成开始时的初始摘要（前端据此自动展开思考区域）。

    初始即含该模式的完整编排步骤（来自合同的 ``steps``，合同是唯一
    来源）；生命周期只在其后追加质量检查结论，杜绝「首 delta 才补
    第二步」的时序缺口。
    """
    return {
        "steps": [step.describe() for step in _contract(mode).steps],
        "evidence": [],
        "tools": [],
        "quality": [],
    }


def _done_thinking(thinking: dict[str, list[str]]) -> dict[str, list[str]]:
    """完成时的摘要：质量检查结论（耗时由消息 duration_ms 呈现）。"""
    return {
        **thinking,
        "quality": ["回答已完整生成并保存"],
    }


def _failed_thinking(
    thinking: dict[str, list[str]], error_code: str | None
) -> dict[str, list[str]]:
    """失败/断流/内部错误时的摘要：保留已完成步骤并给出中文质量结论。"""
    return {
        **thinking,
        "quality": [user_facing_error(error_code, "生成失败，已保留已完成部分。")],
    }


def _stopped_thinking(thinking: dict[str, list[str]]) -> dict[str, list[str]]:
    """用户停止时的摘要：保留已完成步骤并给出中文质量结论。"""
    return {
        **thinking,
        "quality": ["已停止生成，保留已生成内容。"],
    }
