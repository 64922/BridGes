"""贴吧模块编排服务（由日常父图在显式派发时调用）。

节点顺序 ``tieba.parse → tieba.plan → tieba.verify_official → tieba.search
→ tieba.read → tieba.synthesize → tieba.verify``（由节点内核持久化执行）。
每一步都只写真实发生的事：查询词、候选、实际读到的页数与楼层、失败分类。
确认属于目标贴吧的唯一依据是真的读到了帖子页面，因此读取被访问限制挡住时
结果如实降级为「仅帖链」，不会把搜索摘要当页面内容。规定类先核对官方页面，
体验类不追加官方核验，混合类两路独立取证并共享同一运行预算。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bridges.contracts.ai import ModelCallResult
from bridges.contracts.modules import ModuleWaitState
from bridges.kernel.contracts import (
    RecipeInputs,
)
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository
from bridges.tieba.budget import TiebaBudget, bind_request_guard
from bridges.tieba.contracts import (
    TiebaQuestionAnalysis,
    TiebaResearchProjection,
    TiebaResearchStatus,
)
from bridges.tieba.delivery import TiebaResultDelivery, stopped_result
from bridges.tieba.evidence import READ_DEADLINE_SECONDS
from bridges.tieba.evidence_official import OFFICIAL_DEADLINE_SECONDS
from bridges.tieba.evidence_search import SEARCH_DEADLINE_SECONDS, search_candidates
from bridges.tieba.kernel import TiebaNodeFlow
from bridges.tieba.message_audit import verify_tieba_message
from bridges.tieba.official import TiebaOfficialReader
from bridges.tieba.parsing import is_continue_request
from bridges.tieba.progress import (
    AuthWaitProgress,
    bind_auth_wait_progress,
    reset_auth_wait_progress,
)
from bridges.tieba.quality_gates import TIEBA_GATE_HANDLERS
from bridges.tieba.reading import TiebaThreadReader
from bridges.tieba.recipe import (
    NODE_PARSE,
    NODE_SEARCH,
    NODE_SYNTHESIZE,
    NODE_VERIFY,
    TIEBA_RECIPE_ID,
    TIEBA_RECIPE_VERSION,
    build_tieba_recipe,
    tieba_recipe_registry,
)
from bridges.tieba.searching import TiebaSearchPort
from bridges.tieba.service_contract import (
    TIEBA_MODULE_ID,
    TIEBA_NODE_LABELS,
    TIEBA_TOTAL_BUDGET_MS,
    WAIT_KIND_AUTH,
    WAIT_KIND_CLARIFICATION,
    WAIT_REASON_AUTH,
    WAIT_REASON_CLARIFICATION,
    TiebaModuleError,
    TiebaRunOutcome,
)
from bridges.tieba.summary import TiebaSummaryGenerator

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.task_materials import ModuleTaskContext

#: 节点名称与中文标签（进度事件与失败定位共用）。

#: 显式模块标识与等待原因（父图与前端都依赖）。

#: 各阶段的墙钟预算（节点内核按剩余运行预算再收紧）。


def _browser_request_block_code(guard: RunCommitGuard) -> str | None:
    decision = guard.verify()
    if decision.ok:
        return None
    if decision.code == "run_stopped":
        return "tieba_browser_cancelled"
    if decision.code == "task_version_changed":
        return "tieba_browser_task_changed"
    if decision.code == "account_deleting":
        return "tieba_browser_account_deleting"
    return "tieba_browser_run_invalidated"


def _account_deletion_error() -> TiebaModuleError:
    return TiebaModuleError(
        NODE_PARSE,
        "account_deleting",
        "账户正在删除，贴吧搜集已停止。",
        retryable=False,
    )


def _pending_matches_task(
    pending: ModuleWaitState, task_ref: tuple[str | None, int | None] | None
) -> bool:
    """旧等待只在原任务版本仍有效时续接；任务变化后不复活旧话题。"""
    context = pending.context if isinstance(pending.context, dict) else {}
    if "task_id" not in context and "task_version" not in context:
        return task_ref is None
    expected = task_ref if task_ref is not None else (None, None)
    return (context.get("task_id"), context.get("task_version")) == expected


class TiebaResearchService:
    """贴吧信息搜集编排服务（节点内核驱动）。"""

    def __init__(
        self,
        *,
        search: TiebaSearchPort,
        reader: TiebaThreadReader,
        reader_for_account: Callable[[str], TiebaThreadReader] | None = None,
        official_reader: TiebaOfficialReader | None = None,
        search_deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        read_deadline_seconds: float = READ_DEADLINE_SECONDS,
        official_deadline_seconds: float = OFFICIAL_DEADLINE_SECONDS,
        task_version_provider: (
            Callable[[str, str], tuple[str | None, int | None] | None] | None
        ) = None,
        clock: Callable[[], datetime] | None = None,
        model_gateway: Any = None,
        account_deleting: Callable[[str], bool] | None = None,
    ) -> None:
        self._search = search
        self._reader = reader
        self._reader_for_account = reader_for_account
        self._official_reader = official_reader
        self._search_deadline_seconds = search_deadline_seconds
        self._read_deadline_seconds = read_deadline_seconds
        self._official_deadline_seconds = official_deadline_seconds
        self._task_version_provider = task_version_provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._model_gateway = model_gateway
        self._account_deleting = account_deleting
        self._registry = tieba_recipe_registry()
        self._recipe = build_tieba_recipe()
        self._delivery = TiebaResultDelivery(clock=self._clock)

    def close(self) -> None:
        for candidate in (self._reader, self._official_reader):
            closer = getattr(candidate, "close", None)
            if callable(closer):
                closer()

    # -- 对外入口 --------------------------------------------------------

    def run(
        self,
        *,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        run_context: object | None = None,
        run_model_id: str | None = None,
        model_quota: Any = None,
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
        module_context: ModuleTaskContext | None = None,
        official_blocked_reason: str | None = None,
    ) -> TiebaRunOutcome:
        """执行一轮贴吧信息搜集；终态全部写回同一条助手消息。"""

        def account_deletion_code() -> str | None:
            if self._account_deleting is not None and self._account_deleting(account_id):
                return "account_deleting"
            return None

        if account_deletion_code() is not None:
            raise _account_deletion_error()
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise TiebaModuleError(
                NODE_PARSE,
                "message_not_found",
                "消息不存在或没有访问权限。",
                retryable=False,
            )
        run_id = getattr(run_context, "run_id", None)
        run_started_at = self._clock()
        task_ref = self._current_task_ref(account_id, conversation_id)
        pending = self._pending_wait(repo, account_id, conversation_id)
        if pending is not None and not _pending_matches_task(pending, task_ref):
            pending = None
        if (
            pending is not None
            and pending.kind == WAIT_KIND_AUTH
            and not is_continue_request(user_message.content)
        ):
            pending = None
        remaining_budget_ms = None
        if (
            pending is not None
            and pending.kind == WAIT_KIND_AUTH
            and isinstance(pending.context, dict)
        ):
            remaining_budget_ms = pending.context.get("remaining_work_ms")

        guard = RunCommitGuard(
            repo,
            account_id=account_id,
            run_id=run_id or "",
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            task_ref=task_ref,
            task_version_provider=self._task_version_provider,
            stop_event=stop_event,
            clock=self._clock,
            external_guard=account_deletion_code,
        )
        budget = self._load_budget(
            repo,
            account_id,
            run_id,
            max_work_ms=remaining_budget_ms,
            request_guard=lambda: _browser_request_block_code(guard),
        )
        summary_attempt = 0

        def record_summary_call(call: ModelCallResult) -> None:
            nonlocal summary_attempt
            # 回调仅绑定当前运行，不能放在跨账户共享的服务实例上。
            if call.lock is None:
                return  # 网关在调用前拒绝时没有实际模型运行锁。
            if call.lock.account_id != account_id or call.lock.run_id != (run_id or ""):
                raise ValueError("贴吧模型审计锁与当前账户或运行不一致")
            summary_attempt += 1
            repo.insert_run_lock(
                account_id,
                call.lock,
                object_id=assistant_message_id,
                operation="tieba_summary",
                attempt_ordinal=summary_attempt,
                is_primary=False,
            )

        flow = TiebaNodeFlow(
            search=self._search,
            reader=(
                self._reader_for_account(account_id) if self._reader_for_account else self._reader
            ),
            official_reader=self._official_reader,
            clock=self._clock,
            module_context=module_context,
            pending_wait=pending,
            official_blocked_reason=official_blocked_reason,
            budget=budget,
            stop_event=stop_event,
            search_deadline_seconds=self._search_deadline_seconds,
            read_deadline_seconds=self._read_deadline_seconds,
            official_deadline_seconds=self._official_deadline_seconds,
            repository=NodeKernelRepository(repo.database),
            checkpoint_guard=lambda: guard.verify().ok,
            summary_generator=(
                TiebaSummaryGenerator(self._model_gateway, on_result=record_summary_call)
                if self._model_gateway is not None
                else None
            ),
            run_context=run_context,
            run_model_id=run_model_id,
            model_quota=model_quota,
            run_started_at=run_started_at,
            summary_call_count=lambda: summary_attempt,
        )
        kernel = NodeKernel(
            registry=self._registry,
            repository=NodeKernelRepository(repo.database),
            guard=guard,
            gates=TIEBA_GATE_HANDLERS,
            runner=flow.run_node,
            clock=self._clock,
        )
        progress = AuthWaitProgress(emit_node)
        with bind_request_guard(lambda: _browser_request_block_code(guard)):
            progress_token = bind_auth_wait_progress(progress)
            try:
                result = kernel.execute(
                    recipe=self._recipe,
                    inputs=RecipeInputs(
                        account_id=account_id,
                        conversation_id=conversation_id,
                        run_id=run_id or "",
                        user_message_id=user_message_id,
                        user_content=user_message.content,
                        task_id=task_ref[0] if task_ref is not None else None,
                        task_version=task_ref[1] if task_ref is not None else None,
                        wait_identity=self._wait_identity(pending),
                        artifacts={},
                        prior_digest=flow.prior_digest,
                    ),
                    remaining_budget_ms=(
                        budget.remaining_work_ms() if budget is not None else None
                    ),
                    event_sink=progress.emit,
                    stop_event=stop_event,
                )
            finally:
                reset_auth_wait_progress(progress_token)
        # 最终消息写入与守卫复核共用写事务，拒绝核验后转租约或改版本的结果。
        with NodeKernelRepository(repo.database).transaction():
            decision = guard.verify()
            if not decision.ok:
                if decision.code == "account_deleting":
                    raise _account_deletion_error()
                if decision.code != "run_stopped":
                    raise TiebaModuleError(
                        NODE_PARSE,
                        decision.code,
                        "本轮结果在提交前已失效（运行已转交或任务已变更），未写入。",
                        retryable=True,
                    )
                result = stopped_result(result)
            try:
                return self._delivery.deliver(
                    repo,
                    account_id=account_id,
                    assistant_message_id=assistant_message_id,
                    result=result,
                    stop_event=stop_event,
                    remaining_budget_ms=(
                        budget.remaining_work_ms() if budget is not None else None
                    ),
                    task_ref=task_ref,
                )
            except TiebaModuleError as error:
                # 失败投影提交后，再通知父图收敛错误，避免异常回滚投影。
                delivery_error = error
        raise delivery_error

    def verify_message(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        run_id: str,
        conversation_id: str,
        assistant_message_id: str,
    ) -> bool:
        """父图根据本轮完成收据复验可信产物与最终正文。"""
        return verify_tieba_message(
            repo,
            account_id=account_id,
            run_id=run_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
        )

    # -- 恢复与预算 ------------------------------------------------------

    def _search_candidates(
        self,
        account_id: str,
        analysis: TiebaQuestionAnalysis,
        *,
        stop_event: threading.Event | None,
    ) -> Any:
        """兼容入口：直接执行有界检索（不经过运行账本，供确定性单测使用）。"""
        return search_candidates(
            self._search,
            account_id,
            analysis,
            stop_event=stop_event,
            deadline_seconds=self._search_deadline_seconds,
        )

    def _pending_wait(
        self, repo: ConversationRepository, account_id: str, conversation_id: str
    ) -> ModuleWaitState | None:
        """读取本会话最近一次尚未被后续结果取代的澄清等待状态。"""
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            stored = message.tieba_research
            if not isinstance(stored, dict):
                continue
            try:
                projection = TiebaResearchProjection.model_validate(stored)
            except ValueError:
                continue
            if projection.pending is not None:
                return projection.pending
            if projection.status in {
                TiebaResearchStatus.SUCCESS,
                TiebaResearchStatus.LINKS_ONLY,
                TiebaResearchStatus.EMPTY,
            }:
                return None
        return None

    def _wait_identity(self, pending: ModuleWaitState | None) -> str | None:
        if pending is None:
            return None
        return (
            f"{pending.module_id}:{pending.kind}:{pending.origin_message_id}:"
            f"{pending.created_at.isoformat()}"
        )

    def _current_task_ref(
        self, account_id: str, conversation_id: str
    ) -> tuple[str | None, int | None] | None:
        if self._task_version_provider is None:
            return None
        return self._task_version_provider(account_id, conversation_id)

    def _load_budget(
        self,
        repo: ConversationRepository,
        account_id: str,
        run_id: str | None,
        *,
        max_work_ms: int | None = None,
        request_guard: Callable[[], str | None] | None = None,
    ) -> TiebaBudget | None:
        """从持久账本加载共享预算（缺失行时按无预算模式保留本地截止）。"""
        if run_id is None:
            return None
        # 局部导入：chat 包（预算账本属主）在模块级导入会与父图形成循环。
        from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository

        ledger = RunBudgetLedgerRepository(repo.database)
        snapshot = ledger.load(account_id, run_id)
        if snapshot is None:
            return None
        ledger.recover_external_calls(account_id, run_id, now=self._clock())
        snapshot = ledger.load(account_id, run_id)
        if snapshot is None:
            return None
        from bridges.chat.budget import RunBudget

        now = self._clock()
        work_deadline = snapshot.plan.deadline_at - timedelta(
            milliseconds=snapshot.plan.verify_deliver_reserve_ms
        )
        total_budget_ms = snapshot.plan.total_budget_ms
        deadline_at = snapshot.plan.deadline_at
        if max_work_ms is not None:
            max_work_ms = max(0, min(TIEBA_TOTAL_BUDGET_MS, int(max_work_ms)))
            work_deadline = min(work_deadline, now + timedelta(milliseconds=max_work_ms))
            total_budget_ms = max_work_ms + snapshot.plan.verify_deliver_reserve_ms
            deadline_at = min(
                deadline_at,
                now + timedelta(milliseconds=total_budget_ms),
            )
        model_budget = RunBudget.from_ledger_snapshot(
            run_id,
            total_budget_ms=total_budget_ms,
            deadline_utc=deadline_at,
            reserve_ms=snapshot.plan.verify_deliver_reserve_ms,
            ledger=ledger,
            account_id=account_id,
            active=snapshot.active,
        )
        return TiebaBudget(
            ledger=ledger,
            account_id=account_id,
            run_id=run_id,
            work_deadline=work_deadline,
            model_budget=model_budget,
            request_guard=request_guard,
        )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


__all__ = [
    "NODE_PARSE",
    "NODE_SEARCH",
    "NODE_SYNTHESIZE",
    "NODE_VERIFY",
    "TIEBA_MODULE_ID",
    "TIEBA_NODE_LABELS",
    "TIEBA_RECIPE_ID",
    "TIEBA_RECIPE_VERSION",
    "TiebaModuleError",
    "TiebaResearchService",
    "TiebaRunOutcome",
    "WAIT_KIND_CLARIFICATION",
    "WAIT_KIND_AUTH",
    "WAIT_REASON_CLARIFICATION",
    "WAIT_REASON_AUTH",
]
