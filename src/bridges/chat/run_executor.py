"""持久化生成运行的后台执行器（Issue 02 纵向切片）。

ADR-0013 要求长生成运行在独立、受监督的本地后台执行器中；Issue 02
把「生成运行」作为持久化作业落地：HTTP/SSE 只负责创建运行、订阅事件
和显式停止，不再拥有运行生命周期。本模块是生成运行的执行侧——受监督
循环经统一领取契约（``TaskQueue``，Issue 43）领取运行，消费回合编排
的生成事件流并持久化为单调游标事件，同时维护租约（崩溃恢复）与停止
看门狗（DB 停止请求 → 内存信号）。

运行状态机：``queued → running → done | failed | stopped``。租约语义：
queued 直接领取；running 且租约过期按崩溃恢复重新领取（attempt_count
递增）；尝试达到上限（默认 2 = 1 次原始 + 1 次恢复）的运行不再领取，
由收尸收敛为可重试失败，绝不永久 running。终态提交经原子守卫保证
最多一个尝试生效。

执行器由 API 进程装配为受监督线程（与 worker 进程共用同一数据目录
与队列契约；本切片不在独立 worker 进程中重建聊天依赖图，检索/画像/
SKILL/MCP 等编排依赖随 API 进程装配）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bridges.ai.adapters import StreamEvent
from bridges.chat.turn import (
    CHAT_MODE,
    error_is_retryable,
    failed_thinking,
    finalize_message,
    initial_thinking,
    stopped_thinking,
    user_facing_error,
)
from bridges.contracts.chat import (
    ChatMessageStatus,
    ChatMode,
    ChatRunStatus,
    ChatStreamDeltaData,
    ChatStreamDoneData,
    ChatStreamErrorData,
    ChatStreamErrorDetail,
    ChatStreamEventKind,
)
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.runtime.loop import supervised_loop
from bridges.runtime.queue import Claim, TaskQueue
from bridges.storage.database import BridgesDatabase

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository, GenerationRunRecord
    from bridges.chat.service import ChatService

#: 领取循环默认轮询间隔（秒）——停止请求在 ≤2 秒内可见。
DEFAULT_POLL_INTERVAL_SECONDS = 0.5
#: 运行租约默认时长（秒）：长于单次模型调用的增量间隔，短于可接受
#: 的恢复窗口；执行期间由心跳线程持续续期。
DEFAULT_LEASE_SECONDS = 90.0
#: 执行尝试上限：1 次原始执行 + 1 次租约恢复。
MAX_EXECUTION_ATTEMPTS = 2
#: 停止看门狗轮询间隔（秒）。
STOP_POLL_SECONDS = 0.3
#: 心跳续租间隔（秒）。
HEARTBEAT_INTERVAL_SECONDS = 30.0
#: 排队队列名（与 task_claims 一致）。
GENERATION_QUEUE = "generation"


def chat_run_context(account_id: str, conversation_id: str, run_id: str) -> RunContextEnvelope:
    """为生成运行构造合成运行上下文（对话即作用域容器）。

    与旧 API 层构造的语义一致；run_id 即持久化生成运行标识（运行记录
    与模型运行锁在同一标识空间下可追溯）。
    """
    return RunContextEnvelope(
        run_id=run_id,
        account_id=account_id,
        project_id=conversation_id,
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


class GenerationRunExecutor:
    """受监督的生成运行执行器：收尸 → 领取 → 执行 → 终态。"""

    def __init__(
        self,
        service: ChatService,
        database: BridgesDatabase,
        *,
        queue: TaskQueue | None = None,
        worker_name: str = "generation-executor",
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        max_attempts: int = MAX_EXECUTION_ATTEMPTS,
    ) -> None:
        self._service = service
        self._repo = service._repo  # noqa: SLF001 - 执行器是服务编排的组成部分
        self._database = database
        self._queue = queue or TaskQueue(database)
        self._queue.set_lease_seconds(GENERATION_QUEUE, lease_seconds)
        self._worker_name = worker_name
        self._poll_interval = poll_interval
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        #: 最近一轮执行的中文摘要（受监督循环输出）。
        self._last_summary = "generation: 执行器就绪。"

    # ------------------------------------------------------------------
    # 受监督循环
    # ------------------------------------------------------------------

    def run_tick(self) -> str:
        """执行一轮：先收尸失联运行，再领取一件生成运行并执行到终态。

        收尸不只改运行表：被收尸运行的消息同步收敛为可重试错误并补发
        终态事件——订阅端回放完即结束，绝不因缺失终态事件悬挂。
        """
        now = datetime.now(UTC)
        expired = self._repo.expire_overdue_runs(self._max_attempts, now)
        for run in expired:
            self._reap_message(run)
        claim = self._queue.claim_next(GENERATION_QUEUE, self._worker_name)
        if claim is None:
            if expired:
                self._last_summary = f"generation: 收尸 {len(expired)} 个失联运行。"
            else:
                self._last_summary = "generation: 无待处理运行。"
            return self._last_summary
        try:
            self._execute(claim)
        except Exception as exc:  # noqa: BLE001 - 单轮失败记录但不退出循环
            self._last_summary = f"generation: 本轮运行处理出错：{exc}"
        return self._last_summary

    def _reap_message(self, run: GenerationRunRecord) -> None:
        """把失联运行对应的消息收敛为可重试错误并补发终态事件。"""
        message = self._repo.get_message(run.account_id, run.assistant_message_id)
        if message is not None and message.status == ChatMessageStatus.STREAMING:
            conversation = self._repo.get_conversation(run.account_id, run.conversation_id)
            mode = (
                ChatMode(conversation.mode) if conversation is not None else CHAT_MODE
            )
            finalize_message(
                self._repo,
                run.account_id,
                run.assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code=run.error_code or "generation_worker_lost",
                error_message=run.error_message
                or "生成进程意外退出，已保留已接收内容，可点击重试。",
                duration_ms=run.duration_ms,
                model_id=None,
                run_lock_id=None,
                started=time.monotonic(),
                now=datetime.now(UTC),
                thinking=failed_thinking(
                    initial_thinking(mode), run.error_code or "generation_worker_lost"
                ),
            )
        self._append_error_event(
            run.account_id,
            run.run_id,
            run.assistant_message_id,
            code=run.error_code or "generation_worker_lost",
            message=run.error_message or "生成进程意外退出，已保留已接收内容，可点击重试。",
            retryable=error_is_retryable(run.error_code),
        )

    def run_loop(
        self,
        *,
        stop: threading.Event | None = None,
        emit: Callable[[str], None] = print,
    ) -> None:
        """受监督循环：每轮执行一次领取，收到停止信号后平滑退出。"""
        supervised_loop(
            tick=self.run_tick,
            stop=stop or threading.Event(),
            interval=self._poll_interval,
            emit=emit,
        )

    # ------------------------------------------------------------------
    # 领取与执行
    # ------------------------------------------------------------------

    def _execute(self, claim: Claim) -> None:
        """领取并执行一个生成运行；并发竞争由运行租约原子守卫。"""
        payload = claim.payload or {}
        run_id = payload.get("run_id")
        account_id = payload.get("account_id")
        if not run_id or not account_id:
            self._queue.complete(claim)
            self._last_summary = "generation: 队列载荷缺运行标识，已跳过。"
            return
        run = self._repo.get_generation_run(account_id, run_id)
        if run is None:
            # 运行已被删除（会话/消息删除竞态）：队列行直接完成
            self._queue.complete(claim)
            self._last_summary = f"generation: 运行 {run_id} 已不存在，跳过。"
            return
        lease_expired = (
            run.status == ChatRunStatus.RUNNING.value
            and run.lease_expires_at is not None
            and run.lease_expires_at < datetime.now(UTC)
        )
        if run.status != ChatRunStatus.QUEUED.value and not lease_expired:
            # 另一执行器已领取且租约未过期：不动运行，队列行交由持有者完成
            self._queue.complete(claim)
            self._last_summary = f"generation: 运行 {run_id} 已被执行，跳过。"
            return
        lease = datetime.now(UTC) + timedelta(seconds=self._lease_seconds)
        if not self._repo.claim_generation_run(
            account_id,
            run_id,
            owner=self._worker_name,
            lease_expires_at=lease,
            max_attempts=self._max_attempts,
        ):
            self._queue.complete(claim)
            self._last_summary = f"generation: 运行 {run_id} 领取失败，跳过。"
            return
        # Issue 02 规格：运行保存当前阶段（终态时随 finalize 清空）
        self._repo.update_generation_stage(account_id, run_id, "executing")
        self._run_turn(run_id, account_id, claim)

    def _run_turn(self, run_id: str, account_id: str, claim: Claim) -> None:
        """执行一次生成（复用回合编排），事件持久化并收敛运行终态。

        幂等语义：运行领取后只有本执行器能提交运行终态（终态更新限定
        queued/running）；崩溃后租约过期由另一执行器恢复，重复领取不会
        双写终态。
        """
        run = self._repo.get_generation_run(account_id, run_id)
        if run is None:
            self._queue.complete(claim)
            return
        if run.stop_requested:
            # 排队期间已请求停止：不调用模型，直接按停止收敛
            self._converge(run, status=ChatMessageStatus.STOPPED, claim=claim)
            self._last_summary = f"generation: 运行 {run_id} 已在排队时请求停止。"
            return
        stop_event = self._service._lifecycle.register(run.assistant_message_id)  # noqa: SLF001
        heartbeat = _RunHeartbeat(
            self._repo, account_id, run_id, stop_event, self._lease_seconds
        )
        heartbeat.start()
        started = time.monotonic()
        try:
            config = run.config or {}
            stream = self._service.stream_generation(
                account_id,
                run.conversation_id,
                run.assistant_message_id,
                chat_run_context(account_id, run.conversation_id, run_id),
                until_user_message_id=run.user_message_id,
                use_knowledge_base=config.get("use_knowledge_base", True),
                use_profile=config.get("use_profile", True),
            )
            last_kind: str | None = None
            for event in stream:
                self._persist_event(account_id, run_id, run.assistant_message_id, event)
                last_kind = event.kind
            # turn 的停止/空产出路径不发事件：按消息终态补发诚实终态事件
            self._ensure_terminal_event(account_id, run_id, run.assistant_message_id, last_kind)
        finally:
            heartbeat.stop()
            heartbeat.join(timeout=STOP_POLL_SECONDS + 0.5)
            self._service._lifecycle.unregister(run.assistant_message_id)  # noqa: SLF001
        message = self._repo.get_message(account_id, run.assistant_message_id)
        duration_ms = max(1, int((time.monotonic() - started) * 1000))
        if message is not None and message.status == ChatMessageStatus.DONE:
            self._finalize_run(
                account_id, run_id, ChatRunStatus.DONE.value, None, None, duration_ms
            )
            self._last_summary = f"generation: 运行 {run_id} 完成。"
        elif message is not None and message.status == ChatMessageStatus.STOPPED:
            self._finalize_run(
                account_id, run_id, ChatRunStatus.STOPPED.value, None, None, duration_ms
            )
            self._last_summary = f"generation: 运行 {run_id} 已停止。"
        elif message is not None:
            self._finalize_run(
                account_id,
                run_id,
                ChatRunStatus.FAILED.value,
                message.error_code or "internal_error",
                message.error_message or "生成过程出现内部错误，请重试。",
                duration_ms,
            )
            self._last_summary = f"generation: 运行 {run_id} 失败（{message.error_code}）。"
        else:
            self._finalize_run(
                account_id,
                run_id,
                ChatRunStatus.FAILED.value,
                "internal_error",
                "生成过程出现内部错误，请重试。",
                duration_ms,
            )
            self._last_summary = f"generation: 运行 {run_id} 消息缺失，按失败收敛。"
        self._queue.complete(claim)

    # ------------------------------------------------------------------
    # 事件持久化
    # ------------------------------------------------------------------

    def _persist_event(
        self, account_id: str, run_id: str, message_id: str, event: StreamEvent
    ) -> None:
        """把回合编排事件转为持久化游标事件（SSE 订阅回放的唯一真相源）。

        payload 保存事件数据部分（与既有 SSE 帧 ``event: kind`` +
        ``data: {数据}`` 的载荷一致），订阅端点按记录 kind 直接编码帧。
        """
        kind = event.kind
        if kind == "delta":
            payload = ChatStreamDeltaData(
                message_id=message_id, delta=event.delta
            ).model_dump(mode="json")
        elif kind == "error":
            payload = self._error_event_payload(
                account_id,
                message_id,
                code=event.error_code or "generation_failed",
                message=user_facing_error(event.error_code, event.error_message),
                retryable=error_is_retryable(event.error_code),
            )
        elif kind == "done":
            final = self._service.message_projection(account_id, message_id)
            payload = ChatStreamDoneData(
                message_id=message_id, message=final
            ).model_dump(mode="json")
        else:
            data = getattr(event, kind, None)
            if data is None:
                return
            payload = data.model_dump(mode="json")
        self._repo.append_generation_event(
            account_id, run_id, kind, payload, datetime.now(UTC)
        )

    def _ensure_terminal_event(
        self,
        account_id: str,
        run_id: str,
        message_id: str,
        last_kind: str | None,
    ) -> None:
        """turn 未产出终态事件时按消息终态补发（停止/空产出路径）。"""
        if last_kind in {ChatStreamEventKind.DONE.value, ChatStreamEventKind.ERROR.value}:
            return
        message = self._repo.get_message(account_id, message_id)
        if message is None:
            return
        if message.status == ChatMessageStatus.DONE:
            final = self._service.message_projection(account_id, message_id)
            payload = ChatStreamDoneData(
                message_id=message_id, message=final
            ).model_dump(mode="json")
            self._repo.append_generation_event(
                account_id, run_id, ChatStreamEventKind.DONE.value, payload, datetime.now(UTC)
            )
        elif message.status == ChatMessageStatus.STOPPED:
            self._append_error_event(
                account_id,
                run_id,
                message_id,
                code="stopped",
                message="生成已停止。",
                retryable=True,
            )
        elif message.status == ChatMessageStatus.ERROR:
            self._append_error_event(
                account_id,
                run_id,
                message_id,
                code=message.error_code or "generation_failed",
                message=message.error_message or "生成失败，请稍后重试。",
                retryable=error_is_retryable(message.error_code),
            )
        else:
            # 消息仍残留 streaming（执行器兜底路径）：收敛为可重试错误
            conversation = self._repo.get_conversation(account_id, message.conversation_id)
            mode = ChatMode(conversation.mode) if conversation is not None else CHAT_MODE
            finalize_message(
                self._repo,
                account_id,
                message_id,
                status=ChatMessageStatus.ERROR,
                error_code="internal_error",
                error_message="生成过程出现内部错误，请重试。",
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=time.monotonic(),
                now=datetime.now(UTC),
                thinking=failed_thinking(initial_thinking(mode), "internal_error"),
            )
            self._append_error_event(
                account_id,
                run_id,
                message_id,
                code="internal_error",
                message="生成过程出现内部错误，请重试。",
                retryable=True,
            )

    def _error_event_payload(
        self,
        account_id: str,
        message_id: str,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> dict[str, Any]:
        """构造 error 事件载荷（执行器各终态路径共用，保留思考/耗时/投影）。"""
        final = self._service.message_projection(account_id, message_id)
        return ChatStreamErrorData(
            message_id=message_id,
            error=ChatStreamErrorDetail(
                code=code, message=message, retryable=retryable
            ),
            thinking=final.thinking if final is not None else None,
            duration_ms=final.duration_ms if final is not None else None,
            web_search=final.web_search if final is not None else None,
            arxiv_search=final.arxiv_search if final is not None else None,
            teaching=final.teaching if final is not None else None,
        ).model_dump(mode="json")

    def _append_error_event(
        self,
        account_id: str,
        run_id: str,
        message_id: str,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        payload = self._error_event_payload(
            account_id, message_id, code=code, message=message, retryable=retryable
        )
        self._repo.append_generation_event(
            account_id, run_id, ChatStreamEventKind.ERROR.value, payload, datetime.now(UTC)
        )

    # ------------------------------------------------------------------
    # 运行终态
    # ------------------------------------------------------------------

    def _converge(
        self,
        run: GenerationRunRecord,
        *,
        status: ChatMessageStatus,
        claim: Claim,
    ) -> None:
        """排队期停止等快速收敛路径：消息与运行直接落到终态。

        与常规终态路径同一契约：终态事件必须持久化（订阅者据此收敛），
        绝不悬挂——started 之后必有 done/error。
        """
        now = datetime.now(UTC)
        message = self._repo.get_message(run.account_id, run.assistant_message_id)
        if message is not None and message.status == ChatMessageStatus.STREAMING:
            conversation = self._repo.get_conversation(
                run.account_id, run.conversation_id
            )
            mode = (
                ChatMode(conversation.mode) if conversation is not None else CHAT_MODE
            )
            finalize_message(
                self._repo,
                run.account_id,
                run.assistant_message_id,
                status=status,
                error_code=None,
                error_message=None,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=time.monotonic(),
                now=now,
                thinking=stopped_thinking(initial_thinking(mode)),
            )
        self._append_error_event(
            run.account_id,
            run.run_id,
            run.assistant_message_id,
            code="stopped",
            message="生成已停止。",
            retryable=True,
        )
        self._finalize_run(
            run.account_id,
            run.run_id,
            ChatRunStatus.STOPPED.value,
            None,
            None,
            None,
        )
        self._queue.complete(claim)

    def _finalize_run(
        self,
        account_id: str,
        run_id: str,
        status: str,
        error_code: str | None,
        error_message: str | None,
        duration_ms: int | None,
    ) -> None:
        """原子收敛运行终态；仅 queued/running → 目标状态（守卫唯一提交）。"""
        self._repo.finalize_generation_run(
            account_id,
            run_id,
            status=status,
            error_code=error_code,
            error_message=error_message,
            duration_ms=duration_ms,
            now=datetime.now(UTC),
        )


class _RunHeartbeat(threading.Thread):
    """运行心跳：DB 停止请求 → 内存信号 + 周期续租（daemon）。"""

    def __init__(
        self,
        repo: ConversationRepository,
        account_id: str,
        run_id: str,
        stop_event: threading.Event,
        lease_seconds: float,
    ) -> None:
        super().__init__(daemon=True, name=f"generation-heartbeat-{run_id}")
        self._repo = repo
        self._account_id = account_id
        self._run_id = run_id
        self._stop_event = stop_event
        self._lease_seconds = lease_seconds
        self._shutdown = threading.Event()

    def run(self) -> None:
        last_renew = time.monotonic()
        while not self._shutdown.wait(STOP_POLL_SECONDS):
            run = self._repo.get_generation_run(self._account_id, self._run_id)
            if run is None or run.status != ChatRunStatus.RUNNING.value:
                return
            if run.stop_requested:
                self._stop_event.set()
            if time.monotonic() - last_renew >= HEARTBEAT_INTERVAL_SECONDS:
                self._repo.renew_generation_lease(
                    self._account_id,
                    self._run_id,
                    datetime.now(UTC) + timedelta(seconds=self._lease_seconds),
                )
                last_renew = time.monotonic()

    def stop(self) -> None:
        self._shutdown.set()
