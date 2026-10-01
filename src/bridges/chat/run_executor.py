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
from typing import TYPE_CHECKING

from bridges.ai.adapters import StreamEvent
from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository
from bridges.chat.terminal import internal_error_outcome, stopped_outcome
from bridges.contracts.chat import (
    ChatMessageStatus,
    ChatRunStatus,
    ChatStreamDeltaData,
)
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.runtime.loop import supervised_loop
from bridges.runtime.queue import Claim, TaskQueue
from bridges.storage.database import BridgesDatabase

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.service import ChatService
    from bridges.profiles.automatic import AutomaticProfileService

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
        profile_extraction_service: AutomaticProfileService | None = None,
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
        self._profile_extraction = profile_extraction_service
        #: 生成终态 module（Issue 01/02）：执行器只提供标识与兜底结果，
        #: 跨对象提交顺序、判重与重放都在 module 内部。实例由服务持有，
        #: 图边界提前终止、排队期停止与停止接口兜底共用同一实例。
        self._terminal = service.terminal
        #: 最近一轮执行的中文摘要（受监督循环输出）。
        self._last_summary = "generation: 执行器就绪。"

    # ------------------------------------------------------------------
    # 受监督循环
    # ------------------------------------------------------------------

    def run_tick(self) -> str:
        """执行一轮：先收尸失联运行，再领取一件生成运行并执行到终态。

        收尸不只改运行表：被收尸运行的消息、终态事件与运行由终态 module
        一次收敛——订阅端回放完即结束，绝不因缺失终态事件悬挂，也不会
        在消息其实已提交结果时追加矛盾的失败终态。
        """
        now = datetime.now(UTC)
        overdue = self._repo.list_overdue_runs(self._max_attempts, now)
        reaped = 0
        reap_failure = ""
        for run in overdue:
            try:
                self._terminal.reap_lost_run(run)
                reaped += 1
                # 改进工单 09：收尸即终态——关闭预算账本，额度计数封存
                #（用户重试会创建新运行、新账本）。
                RunBudgetLedgerRepository(self._database).close(
                    account_id=run.account_id,
                    run_id=run.run_id,
                    now=datetime.now(UTC),
                )
            except Exception as exc:  # noqa: BLE001 - 收尸故障不终止执行器
                # 收尸失败留下的仍是「待收敛」形态（该运行没有写入终态），
                # 下一轮按同一上限重试；绝不因一个运行让整个执行器停摆，
                # 否则其余运行将永远无人收敛。
                reap_failure = f"generation: 运行 {run.run_id} 收尸失败：{exc}"
        claim = self._queue.claim_next(GENERATION_QUEUE, self._worker_name)
        if claim is None:
            if reap_failure:
                self._last_summary = reap_failure
            elif reaped:
                self._last_summary = f"generation: 收尸 {reaped} 个失联运行。"
            else:
                self._last_summary = "generation: 无待处理运行。"
            if self._profile_extraction is not None:
                try:
                    profile_summary = self._profile_extraction.run_retry_tick()
                except Exception as exc:  # noqa: BLE001 - 后台重试不应终止生成 worker
                    profile_summary = f"profile-extraction: 本轮处理出错：{exc}"
                self._last_summary = f"{self._last_summary}；{profile_summary}"
            return self._last_summary
        try:
            self._execute(claim)
        except Exception as exc:  # noqa: BLE001 - 单轮失败记录但不退出循环
            self._last_summary = f"generation: 本轮运行处理出错：{exc}"
        if self._profile_extraction is not None:
            try:
                profile_summary = self._profile_extraction.run_retry_tick()
            except Exception as exc:  # noqa: BLE001 - 后台重试不应终止生成 worker
                profile_summary = f"profile-extraction: 本轮处理出错：{exc}"
            self._last_summary = f"{self._last_summary}；{profile_summary}"
        return self._last_summary

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
            if run.status == ChatRunStatus.RUNNING.value:
                # 另一执行器正在执行且租约未过期：不动运行，也不动队列行。
                # 该运行还有恢复预算（收尸只选取预算耗尽的运行），唯一的重
                # 领通道就是这行队列：提前完成会让它既领不回也收不了尸，
                # 永久停在 running（Issue 03）。行留给持有者或后续领取者。
                self._last_summary = f"generation: 运行 {run_id} 由其他执行器执行，跳过。"
                return
            # 运行已终态：队列行是收尾遗留，直接完成
            RunBudgetLedgerRepository(self._database).close(
                account_id=account_id, run_id=run_id, now=datetime.now(UTC)
            )
            self._queue.complete(claim)
            self._last_summary = f"generation: 运行 {run_id} 已终态，跳过。"
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
        双写终态。恢复时结果已确定的运行（消息已提交终态，或消息已随
        会话删除）不经模型重新生成：只补齐终态事件与运行，再确认队列。
        """
        run = self._repo.get_generation_run(account_id, run_id)
        if run is None:
            self._queue.complete(claim)
            return
        committed = self._terminal.recover_committed_result(account_id, run)
        if committed is not None:
            RunBudgetLedgerRepository(self._database).close(
                account_id=account_id, run_id=run_id, now=datetime.now(UTC)
            )
            self._last_summary = (
                f"generation: 运行 {run_id} 结果已提交"
                f"（{committed.outcome.status.value}），只补齐终态。"
            )
            self._queue.complete(claim)
            return
        if run.stop_requested:
            # 排队期间已请求停止：不调用模型，经终态 module 按停止收敛
            #（消息、终态事件与运行一次到位），队列随后确认完成。
            self._terminal.converge(
                account_id,
                run_id,
                run.assistant_message_id,
                fallback=stopped_outcome(),
            )
            self._last_summary = f"generation: 运行 {run_id} 已在排队时请求停止。"
            RunBudgetLedgerRepository(self._database).close(
                account_id=account_id, run_id=run_id, now=datetime.now(UTC)
            )
            self._queue.complete(claim)
            return
        stop_event = self._service._lifecycle.register(run.assistant_message_id)  # noqa: SLF001
        heartbeat = _RunHeartbeat(
            self._repo, account_id, run_id, stop_event, self._lease_seconds
        )
        heartbeat.start()
        started = time.monotonic()
        try:
            # V2 Issue 02：本轮经日常 LangGraph 父图执行（固定节点链 +
            # 检查点持久化 + 可取消节点边界）。事件（node 进度与编排管线
            # 事件）经回调实时持久化为游标事件；停止信号沿用既有看门狗。
            self._service.run_graph_turn(
                run,
                on_event=lambda event: self._persist_event(
                    account_id, run_id, run.assistant_message_id, event
                ),
                stop_event=stop_event,
            )
        finally:
            heartbeat.stop()
            heartbeat.join(timeout=STOP_POLL_SECONDS + 0.5)
            self._service._lifecycle.unregister(run.assistant_message_id)  # noqa: SLF001
        duration_ms = max(1, int((time.monotonic() - started) * 1000))
        # 共同终态 module（Issue 01）：结果从已提交消息派生，终态事件与运行
        # 状态按固定顺序补齐；turn 的停止/空产出路径不发终态事件，残留
        # streaming 由兜底结果收敛。重复收尾不改写已提交结果。
        commit = self._terminal.converge(
            account_id,
            run_id,
            run.assistant_message_id,
            fallback=internal_error_outcome(),
            run_duration_ms=duration_ms,
        )
        # 改进工单 09：运行进入终态即关闭预算账本（只读封存）；租约恢复
        # 重跑在收敛前读取的是同一账本行——截止与已耗计数不因恢复重置。
        RunBudgetLedgerRepository(self._database).close(
            account_id=account_id, run_id=run_id, now=datetime.now(UTC)
        )
        label = {
            ChatMessageStatus.DONE: "完成",
            ChatMessageStatus.STOPPED: "已停止",
        }.get(commit.outcome.status, "失败")
        suffix = (
            f"（{commit.outcome.error_code}）"
            if commit.outcome.status == ChatMessageStatus.ERROR
            else ""
        )
        self._last_summary = f"generation: 运行 {run_id} {label}{suffix}。"
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
            payload = self._terminal.runtime_error_payload(
                account_id,
                message_id,
                code=event.error_code,
                error_message=event.error_message,
            )
        elif kind == "done":
            payload = self._terminal.done_payload(account_id, message_id)
        else:
            data = getattr(event, kind, None)
            if data is None:
                return
            payload = data.model_dump(mode="json")
        self._repo.append_generation_event(
            account_id, run_id, kind, payload, datetime.now(UTC)
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
