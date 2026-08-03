"""持久化流式聊天服务（Issue 11 纵向切片）。

服务负责对话/消息的账户隔离持久化、生成状态机（streaming → done /
error / stopped）、用户级重试（每次重试新建助手尝试，历史原样保留）
与模型运行锁落库。真实供应商调用经由 ``ModelGateway.stream`` 完成；
失败以稳定错误码返回，由 API 层映射为可操作中文提示。
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from bridges.ai import ModelGateway
from bridges.ai.streaming import StreamEvent
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.contracts.ai import ModelRunLock
from bridges.contracts.chat import (
    ChatConversationListProjection,
    ChatConversationProjection,
    ChatConversationSummary,
    ChatMessageProjection,
    ChatMessageRole,
    ChatMessageStatus,
)
from bridges.contracts.workflows import RunContextEnvelope

#: 核心对话能力的固定绑定（ADR-0009 固定模型矩阵）。
CHAT_CAPABILITY_NAME = "qwen_text_chat"
CHAT_CAPABILITY_VERSION = "1"

#: 对话模式：本切片只交付日常陪伴，学习模式由后续 Issue 扩展。
CHAT_MODE = "companion"

#: 由首条用户消息推导对话标题的最大长度。
_TITLE_MAX = 24

#: 进行中的生成在注册表中活跃超过该时长仍未被收敛时，视为僵死可清理。
_STALE_STREAMING_TTL_SECONDS = 60.0

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

    def __init__(self, repository: ConversationRepository, gateway: ModelGateway) -> None:
        self._repo = repository
        self._gateway = gateway
        #: 进行中生成的停止信号（message_id → 事件与生成启动时刻）。
        self._stops: dict[str, tuple[threading.Event, float]] = {}
        self._stops_guard = threading.Lock()

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------

    def create_conversation(
        self, account_id: str, title: str | None = None
    ) -> ChatConversationProjection:
        now = datetime.now(UTC)
        conversation_id = secrets.token_urlsafe(16)
        self._repo.create_conversation(
            account_id=account_id,
            conversation_id=conversation_id,
            title=(title or "").strip(),
            mode=CHAT_MODE,
            created_at=now,
        )
        return self._project_conversation(
            account_id,
            conversation_id,
            title=(title or "").strip(),
            mode=CHAT_MODE,
            created_at=now,
            updated_at=now,
            messages=[],
        )

    def list_conversations(self, account_id: str) -> ChatConversationListProjection:
        records = self._repo.list_conversations(account_id)
        summaries: list[ChatConversationSummary] = []
        for record in records:
            summaries.append(
                ChatConversationSummary(
                    conversation_id=record.conversation_id,
                    title=record.title,
                    mode=record.mode,
                    message_count=self._repo.message_count(account_id, record.conversation_id),
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
        return ChatConversationListProjection(conversations=summaries)

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
                if self._is_active_generation(message.message_id, now):
                    # 生成仍在进行：读取不打断流
                    continue
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
                )
                message.status = ChatMessageStatus.ERROR
                message.error_code = "stream_interrupted"
                message.error_message = STREAM_INTERRUPTED_MESSAGE
        return self._project_conversation(
            account_id,
            record.conversation_id,
            title=record.title,
            mode=record.mode,
            created_at=record.created_at,
            updated_at=record.updated_at,
            messages=messages,
        )

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------

    def start_generation(
        self, account_id: str, conversation_id: str, content: str
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

        user_message = MessageRecord(
            message_id=secrets.token_urlsafe(16),
            conversation_id=conversation_id,
            account_id=account_id,
            role=ChatMessageRole.USER,
            attempt_number=1,
            status=ChatMessageStatus.DONE,
            content=content,
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
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            created_at=now,
            updated_at=now,
        )
        self._repo.insert_message(user_message)
        self._repo.insert_message(assistant_message)
        self._repo.touch_conversation(account_id, conversation_id, now)
        # 预注册停止事件：生成一经创建即视为"活跃"，读取收敛不会误伤
        self._register_stop(assistant_message.message_id)

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
    ) -> Iterator[StreamEvent]:
        """驱动一次生成：调用网关流式接口，边收边落库，结束时收敛状态。

        事件经此处原样透传给 API 层；停止信号（用户停止/切换账户导致
        的客户端断开）都会把消息收敛到明确终态，绝不留 streaming 僵尸。
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

        # 复用 start/retry 阶段预注册的停止事件（保证注册表在 start 即
        # 生效，读取陈旧收敛不会误伤进行中的流）；缺失时补注册。
        with self._stops_guard:
            entry = self._stops.get(assistant_message_id)
            if entry is not None:
                stop_event, _ = entry
            else:
                stop_event = threading.Event()
                self._stops[assistant_message_id] = (stop_event, time.monotonic())
        content = ""
        started = time.monotonic()
        try:
            history = self._model_history(account_id, conversation_id)
            payload: dict[str, Any] = {
                "messages": history,
                "temperature": 0.7,
                "max_tokens": 1024,
            }
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
                    )
                    return
                if event.kind == "delta":
                    content += event.delta
                    self._repo.update_message_content(
                        account_id, assistant_message_id, content, datetime.now(UTC)
                    )
                    self._touch_stop(assistant_message_id)
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
                    )
                    yield event
                    return
        except GeneratorExit:
            # 客户端断开：收敛为可重试错误，保留已接收正文。
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
            )
            yield StreamEvent(
                kind="error",
                error_code="internal_error",
                error_message="生成过程出现内部错误，请重试。",
            )
            return
        finally:
            self._unregister_stop(assistant_message_id)

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

        stop_event: threading.Event | None = None
        started: float | None = None
        with self._stops_guard:
            entry = self._stops.get(message_id)
            if entry is not None:
                stop_event, started = entry
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
        )
        self._unregister_stop(message_id)
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
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            created_at=now,
            updated_at=now,
        )
        self._repo.insert_message(new_attempt)
        self._repo.touch_conversation(account_id, conversation_id, now)
        self._register_stop(new_attempt.message_id)
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

    def _register_stop(self, message_id: str) -> None:
        """预注册一条生成的停止信号（生成一经创建即视为活跃）。"""
        with self._stops_guard:
            if message_id not in self._stops:
                self._stops[message_id] = (threading.Event(), time.monotonic())

    def _touch_stop(self, message_id: str) -> None:
        """刷新活跃时间戳：每个增量产出都续期，避免长时间流被误判僵死。"""
        with self._stops_guard:
            entry = self._stops.get(message_id)
            if entry is not None:
                self._stops[message_id] = (entry[0], time.monotonic())

    def _unregister_stop(self, message_id: str) -> None:
        with self._stops_guard:
            self._stops.pop(message_id, None)

    def _is_active_generation(self, message_id: str, now: datetime) -> bool:
        """消息是否处于"进行中"的生成（读取陈旧收敛时用于豁免活跃流）。

        注册表中存在且未超过 TTL 视为活跃；进程重启后注册表为空，遗留的
        streaming 消息一律收敛。``now`` 仅用于保持调用方语义，TTL 判定
        使用单调时钟。
        """
        del now
        with self._stops_guard:
            entry = self._stops.get(message_id)
            if entry is None:
                return False
            return time.monotonic() - entry[1] < _STALE_STREAMING_TTL_SECONDS

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _model_history(self, account_id: str, conversation_id: str) -> list[dict[str, str]]:
        """组装发送给模型的会话历史。

        每轮用户消息只带最新一条已完成（done）的助手回答；失败、停止与
        进行中的尝试不进上下文，避免把错误内容当成回答。
        """
        messages = self._repo.list_messages(account_id, conversation_id)
        history: list[dict[str, str]] = []
        latest_done: MessageRecord | None = None
        for message in messages:
            if message.role == ChatMessageRole.USER:
                if latest_done is not None:
                    history.append({"role": "assistant", "content": latest_done.content})
                    latest_done = None
                history.append({"role": "user", "content": message.content})
            elif message.status == ChatMessageStatus.DONE:
                latest_done = message
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

    @staticmethod
    def _project_message(message: MessageRecord) -> ChatMessageProjection:
        return ChatMessageProjection(
            message_id=message.message_id,
            conversation_id=message.conversation_id,
            role=message.role,
            attempt_number=message.attempt_number,
            status=message.status,
            content=message.content,
            error_code=message.error_code,
            error_message=message.error_message,
            duration_ms=message.duration_ms,
            model_id=message.model_id,
            run_lock_id=message.run_lock_id,
            created_at=message.created_at,
            updated_at=message.updated_at,
        )

    @classmethod
    def _project_conversation(
        cls,
        account_id: str,
        conversation_id: str,
        *,
        title: str,
        mode: str,
        created_at: datetime,
        updated_at: datetime,
        messages: list[MessageRecord],
    ) -> ChatConversationProjection:
        del account_id  # 投影不含账户标识，避免向前端泄漏内部 ID 语义
        return ChatConversationProjection(
            conversation_id=conversation_id,
            title=title,
            mode=mode,
            created_at=created_at,
            updated_at=updated_at,
            messages=[cls._project_message(message) for message in messages],
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
