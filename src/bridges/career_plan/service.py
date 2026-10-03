"""职业规划模块编排服务（由日常父图在显式派发时调用，改进工单 28／29）。

子图按持久节点内核执行：``career.parse → career.plan → career.collect →
career.filter → career.analyze → career.background → career.gap →
career.advise → career.verify``。每个节点在自己的局部事务里提交产物与完成收据，
恢复时先读收据：输入未变则回填产物（不重复外部读取），输入变化（例如任务城市
条件从上海改成杭州）则重算该节点及其下游，旧统计绝不被当成当前数据复用。

每一步都只写真实发生的事：实际查询词与筛选条件、真实读到的岗位字段、逐条
剔除依据。主样本只收公开可读、岗位名或职责原文命中目标、城市与经验条件都
可核对的岗位；读不到页面时如实降级为「未核实链接」。个人规划分支只经登记
的背景提供者读取当前陈述与允许使用的最小切片，公开检索从不携带背景正文，
岗位结果也不写回画像。本模块不调用模型。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NoReturn

from bridges.career_plan.background import CareerBackgroundProvider
from bridges.career_plan.contracts import (
    CareerCandidateLink,
    CareerPlanProjection,
    CareerPlanStatus,
    CareerQueryPlanItem,
    CareerRejectedSample,
    CareerRequestAnalysis,
)
from bridges.career_plan.kernel import (
    CAREER_GATE_HANDLERS,
    CAREER_NODE_LABELS,
    CAREER_RECIPE_ID,
    CAREER_RECIPE_VERSION,
    FAILED_QUERY_STATUSES,
    NODE_ADVISE,
    NODE_ANALYZE,
    NODE_BACKGROUND,
    NODE_COLLECT,
    NODE_FILTER,
    NODE_GAP,
    NODE_PARSE,
    NODE_PLAN,
    NODE_VERIFY,
    READ_DEADLINE_SECONDS,
    READS_PER_SOURCE,
    SEARCH_DEADLINE_SECONDS,
    CareerBudget,
    CareerNodeFlow,
    _build_projection,
    career_recipe_registry,
)
from bridges.career_plan.parsing import pending_payload
from bridges.career_plan.presenting import (
    render_clarification_content,
    render_empty_content,
    render_links_only_content,
    render_result_content,
    render_stopped_content,
)
from bridges.career_plan.searching import CareerSearchPort
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState
from bridges.kernel.contracts import KernelResult, KernelStatus, RecipeInputs
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.task_materials import ModuleTaskContext
    from bridges.contracts.workflows import RunContextEnvelope

#: 显式模块标识与等待原因（父图与前端都依赖）。
CAREER_MODULE_ID = "career"
WAIT_KIND_CLARIFICATION = "clarification"
WAIT_REASON_CLARIFICATION = "career_clarification"


class CareerModuleError(Exception):
    """模块内的稳定失败（定位到具体节点）。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


class CareerSupersededError(Exception):
    """迟到结果：租约/版本/消息归属已变化，本轮不写任何交付终态。"""


class _CareerDeliveryError(Exception):
    """失败交付信号：在守卫事务内原子收敛，提交后再通知父图。"""

    def __init__(
        self, error: CareerModuleError, projection: CareerPlanProjection | None
    ) -> None:
        super().__init__(error.message)
        self.error = error
        self.projection = projection


@dataclass(frozen=True)
class CareerRunOutcome:
    """一轮职业规划分析的收敛结果（父图据此写等待原因与判断终态）。"""

    status: CareerPlanStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)


class CareerPlanService:
    """职业规划编排服务（持久节点内核执行）。"""

    def __init__(
        self,
        *,
        search: CareerSearchPort,
        reader: Any,
        search_deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        read_deadline_seconds: float = READ_DEADLINE_SECONDS,
        reads_per_source: int = READS_PER_SOURCE,
        task_version_provider: Callable[
            [str, str], tuple[str | None, int | None] | None
        ]
        | None = None,
        background_provider: CareerBackgroundProvider | None = None,
    ) -> None:
        self._search = search
        self._reader = reader
        self._search_deadline_seconds = search_deadline_seconds
        self._read_deadline_seconds = read_deadline_seconds
        self._reads_per_source = reads_per_source
        self._task_version_provider = task_version_provider
        self._background_provider = background_provider
        self._registry = career_recipe_registry()
        self._recipe = self._registry.get(CAREER_RECIPE_ID)

    def close(self) -> None:
        closer = getattr(self._reader, "close", None)
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
        run_context: RunContextEnvelope | None = None,
        run_model_id: str | None = None,
        request_text: str | None = None,
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
        module_context: ModuleTaskContext | None = None,
    ) -> CareerRunOutcome:
        """执行一轮职业规划分析；终态全部写回同一条助手消息。"""
        del run_model_id  # 本模块不调用模型，不存在模型生成的断言。
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise CareerModuleError(
                NODE_PARSE,
                "message_not_found",
                "消息不存在或没有访问权限。",
                retryable=False,
            )
        content = request_text if request_text is not None else user_message.content
        pending = self._pending_wait(repo, account_id, conversation_id)
        run_id = self._run_id(repo, account_id, assistant_message_id, run_context)
        if run_id is None:
            raise CareerModuleError(
                NODE_PARSE,
                "run_context_missing",
                "缺少本轮运行上下文，未提交职业规划结果。",
                retryable=True,
            )
        budget = self._load_budget(repo, account_id, run_id)
        task_ref = self._current_task_ref(account_id, conversation_id)
        clock: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731
        guard = RunCommitGuard(
            repo,
            account_id=account_id,
            run_id=run_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            task_ref=task_ref,
            task_version_provider=self._task_version_provider,
            stop_event=stop_event,
            clock=clock,
        )
        flow = CareerNodeFlow(
            search=self._search,
            reader=self._reader,
            clock=clock,
            module_context=module_context,
            pending_wait=pending,
            budget=budget,
            stop_event=stop_event,
            search_deadline_seconds=self._search_deadline_seconds,
            read_deadline_seconds=self._read_deadline_seconds,
            reads_per_source=self._reads_per_source,
            background_provider=self._background_provider,
        )
        kernel = NodeKernel(
            registry=self._registry,
            repository=NodeKernelRepository(repo.database),
            guard=guard,
            gates=CAREER_GATE_HANDLERS,
            runner=flow.run_node,
            clock=clock,
        )
        result = kernel.execute(
            recipe=self._recipe,
            inputs=RecipeInputs(
                account_id=account_id,
                conversation_id=conversation_id,
                run_id=run_id,
                user_message_id=user_message_id,
                user_content=content,
                task_id=task_ref[0] if task_ref is not None else None,
                task_version=task_ref[1] if task_ref is not None else None,
                wait_identity=self._wait_identity(pending),
                artifacts={},
                prior_digest=flow.prior_digest,
            ),
            remaining_budget_ms=(
                budget.remaining_work_ms() if budget is not None else None
            ),
            event_sink=emit_node,
            stop_event=stop_event,
        )
        # 最终消息写入与守卫复核共用写事务，拒绝核验后转租约或改版本的结果。
        delivery_failure: _CareerDeliveryError | None = None
        with NodeKernelRepository(repo.database).transaction():
            decision = guard.verify()
            if not decision.ok:
                if decision.code != "run_stopped":
                    raise CareerSupersededError(decision.code)
                result = replace(result, status=KernelStatus.STOPPED)
            try:
                return self._deliver(
                    repo,
                    account_id=account_id,
                    assistant_message_id=assistant_message_id,
                    result=result,
                    stop_event=stop_event,
                )
            except _CareerDeliveryError as failure:
                delivery_failure = failure
                if failure.projection is not None:
                    self._finalize(
                        repo,
                        account_id=account_id,
                        assistant_message_id=assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        projection=failure.projection,
                        content=render_empty_content(failure.projection),
                        now=clock(),
                    )
        # 错误在事务提交之后抛出，避免已通过守卫的失败投影被异常回滚。
        assert delivery_failure is not None
        raise delivery_failure.error

    # -- 交付（终态收敛路径） --------------------------------------------

    def _deliver(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
        stop_event: threading.Event | None,
    ) -> CareerRunOutcome:
        if result.status is KernelStatus.COMPLETED:
            return self._deliver_completed(
                repo, account_id, assistant_message_id, result
            )
        if result.status is KernelStatus.NEEDS_INPUT:
            return self._deliver_clarification(
                repo, account_id, assistant_message_id, result
            )
        if result.status in {KernelStatus.STOPPED, KernelStatus.INVALIDATED} or (
            stop_event is not None and stop_event.is_set()
        ):
            return self._deliver_stopped(repo, account_id, assistant_message_id, result)
        if result.status is KernelStatus.REJECTED:
            raise CareerSupersededError(
                result.rejection_code or "generation_superseded"
            )
        return self._deliver_failure(result)

    def _deliver_completed(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> CareerRunOutcome:
        artifact = result.delivery
        if artifact is None or artifact.node != NODE_VERIFY:
            raise CareerModuleError(
                NODE_VERIFY,
                "career_delivery_missing",
                "职业规划结果没有形成待交付产物，本轮未提交。",
                retryable=True,
            )
        projection = CareerPlanProjection.model_validate(
            artifact.payload["projection"]
        )
        if projection.status is CareerPlanStatus.ERROR:
            return self._deliver_failure(result, projection=projection)
        now = datetime.now(UTC)
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=_content_for(projection),
            now=now,
        )
        return CareerRunOutcome(
            status=projection.status,
            wait_reason=None,
            queries=list(projection.queries),
        )

    def _deliver_clarification(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> CareerRunOutcome:
        analysis = self._parse_analysis(result)
        clarification = analysis.clarification if analysis is not None else None
        if analysis is None or clarification is None:
            raise CareerModuleError(
                NODE_PARSE,
                "career_clarification_missing",
                "职业规划澄清状态缺少恢复载荷，本轮未提交。",
                retryable=True,
            )
        now = datetime.now(UTC)
        projection = CareerPlanProjection(
            status=CareerPlanStatus.CLARIFICATION,
            topic=_topic_of(analysis),
            original_request=analysis.original_request,
            job_terms=list(analysis.job_terms),
            family_title=analysis.family_title,
            stage=analysis.stage,
            graduation_year=analysis.graduation_year,
            cities=list(analysis.cities),
            constraints=list(analysis.constraints),
            experience_hint=analysis.experience_hint,
            pending=ModuleWaitState(
                module_id=CAREER_MODULE_ID,
                kind=WAIT_KIND_CLARIFICATION,
                question=clarification.question,
                origin_message_id=assistant_message_id,
                context=pending_payload(analysis),
                created_at=now,
            ),
            completed_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=render_clarification_content(analysis),
            now=now,
        )
        return CareerRunOutcome(
            status=projection.status,
            wait_reason=WAIT_REASON_CLARIFICATION,
            queries=[],
        )

    def _deliver_stopped(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> CareerRunOutcome:
        analysis = self._parse_analysis(result)
        records = self._records(result)
        now = datetime.now(UTC)
        if analysis is None:
            projection = CareerPlanProjection(
                status=CareerPlanStatus.STOPPED,
                topic="",
                original_request="",
                queries=records,
                evidence_boundary=["你已停止本轮分析，未继续检索与读取岗位页。"],
                completed_at=now,
            )
        else:
            projection = _build_projection(
                analysis=analysis,
                plan=self._plan_items(result),
                records=records,
                samples=[],
                rejected=self._rejected(result),
                unconfirmed=self._unconfirmed(result),
                unread_links=self._unread_links(result),
                report=None,
                advices=[],
                adjacent=[],
                reads_per_source=self._reads_per_source,
            ).model_copy(
                update={
                    "status": CareerPlanStatus.STOPPED,
                    "evidence_boundary": [
                        "你已停止本轮分析，未继续检索与读取岗位页。"
                    ],
                    "empty_reason": None,
                    "completed_at": now,
                }
            )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            projection=projection,
            content=render_stopped_content(projection),
            now=now,
        )
        return CareerRunOutcome(
            status=CareerPlanStatus.STOPPED, wait_reason=None, queries=records
        )

    def _deliver_failure(
        self,
        result: KernelResult,
        *,
        projection: CareerPlanProjection | None = None,
    ) -> NoReturn:
        """失败先写回同一条消息（失败分类与查询词都留在投影里），再交给父图收敛。"""
        node = NODE_VERIFY
        code = "career_search_failed"
        message = "岗位检索失败，请稍后重试。"
        retryable = True
        if projection is not None:
            # 全部来源失败：投影本身已带失败分类，失败定位固定在采集节点。
            node = NODE_COLLECT
            code = projection.error_code or code
            message = projection.error_message or message
            retryable = projection.retryable
        else:
            failure = result.failure
            if failure is not None:
                node = failure.node
                code = failure.code or code
                message = failure.message or message
                retryable = failure.retryable
            projection = self._failure_projection(
                result, code=code, message=message, retryable=retryable
            )
        raise _CareerDeliveryError(
            CareerModuleError(node, code, message, retryable=retryable), projection
        )

    def _failure_projection(
        self,
        result: KernelResult,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> CareerPlanProjection | None:
        """按已提交产物组装失败投影：实际查询与剔除依据都保留。"""
        analysis = self._parse_analysis(result)
        if analysis is None:
            return None
        projection = _build_projection(
            analysis=analysis,
            plan=self._plan_items(result),
            records=self._records(result),
            samples=[],
            rejected=self._rejected(result),
            unconfirmed=self._unconfirmed(result),
            unread_links=self._unread_links(result),
            report=None,
            advices=[],
            adjacent=[],
            reads_per_source=self._reads_per_source,
        )
        return projection.model_copy(
            update={
                "status": CareerPlanStatus.ERROR,
                "samples": [],
                "analysis": None,
                "advices": [],
                "adjacent_suggestions": [],
                "empty_reason": None,
                "retryable": retryable,
                "error_code": code,
                "error_message": message,
            }
        )

    # -- 内核产物读取 -----------------------------------------------------

    def _parse_analysis(self, result: KernelResult) -> CareerRequestAnalysis | None:
        artifact = result.artifact(NODE_PARSE)
        if artifact is None:
            return None
        payload = artifact.payload.get("analysis")
        if not isinstance(payload, dict):
            return None
        return CareerRequestAnalysis.model_validate(payload)

    def _plan_items(self, result: KernelResult) -> list[CareerQueryPlanItem]:
        artifact = result.artifact(NODE_PLAN)
        if artifact is None:
            return []
        return [
            CareerQueryPlanItem.model_validate(item)
            for item in artifact.payload.get("plan") or []
        ]

    def _records(self, result: KernelResult) -> list[ModuleQueryRecord]:
        artifact = result.artifact(NODE_COLLECT)
        if artifact is None:
            return []
        return [
            ModuleQueryRecord.model_validate(item)
            for item in artifact.payload.get("records") or []
        ]

    def _unread_links(self, result: KernelResult) -> list[dict[str, Any]]:
        artifact = result.artifact(NODE_COLLECT)
        if artifact is None:
            return []
        return [dict(item) for item in artifact.payload.get("unread_links") or []]

    def _filter_payload(self, result: KernelResult) -> dict[str, Any]:
        artifact = result.artifact(NODE_FILTER)
        return dict(artifact.payload) if artifact is not None else {}

    def _rejected(self, result: KernelResult) -> list[CareerRejectedSample]:
        return [
            CareerRejectedSample.model_validate(item)
            for item in self._filter_payload(result).get("rejected") or []
        ]

    def _unconfirmed(self, result: KernelResult) -> list[CareerCandidateLink]:
        return [
            CareerCandidateLink.model_validate(item)
            for item in self._filter_payload(result).get("unconfirmed") or []
        ]

    # -- 恢复与等待 ------------------------------------------------------

    def _pending_wait(
        self, repo: ConversationRepository, account_id: str, conversation_id: str
    ) -> ModuleWaitState | None:
        """读取本会话最近一次尚未被后续结果取代的澄清等待状态。"""
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            stored = message.career_plan
            if not isinstance(stored, dict):
                continue
            try:
                projection = CareerPlanProjection.model_validate(stored)
            except ValueError:
                continue
            if projection.pending is not None:
                return projection.pending
            # 失败与停止同样是「本轮结束了」（前端卡片与等待恢复语义一致）：
            # 不再把更早那条澄清当成待续问题，否则用户的下一条消息会被接上
            # 旧请求的城市与阶段。
            if projection.status in {
                CareerPlanStatus.SUCCESS,
                CareerPlanStatus.LINKS_ONLY,
                CareerPlanStatus.EMPTY,
                CareerPlanStatus.ERROR,
                CareerPlanStatus.STOPPED,
            }:
                return None
        return None

    def _run_id(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        run_context: RunContextEnvelope | None,
    ) -> str | None:
        run_id = getattr(run_context, "run_id", None) if run_context is not None else None
        if run_id:
            return str(run_id)
        record = repo.get_run_by_message(account_id, assistant_message_id)
        return record.run_id if record is not None else None

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
        self, repo: ConversationRepository, account_id: str, run_id: str
    ) -> CareerBudget | None:
        """从持久账本加载共享预算（缺失行时按无预算模式保留本地截止）。"""
        # 局部导入：chat 包（预算账本属主）在模块级导入会与父图形成循环。
        from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository

        ledger = RunBudgetLedgerRepository(repo.database)
        snapshot = ledger.load(account_id, run_id)
        if snapshot is None:
            return None
        ledger.recover_external_calls(account_id, run_id, now=datetime.now(UTC))
        work_deadline = snapshot.plan.deadline_at - timedelta(
            milliseconds=snapshot.plan.verify_deliver_reserve_ms
        )
        return CareerBudget(
            ledger=ledger,
            account_id=account_id,
            run_id=run_id,
            work_deadline=work_deadline,
        )

    # -- 落库 ------------------------------------------------------------

    def _finalize(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        status: ChatMessageStatus,
        projection: CareerPlanProjection,
        content: str,
        now: datetime,
    ) -> None:
        projection = projection.model_copy(
            update={"completed_at": projection.completed_at or now}
        )
        # 局部导入：模块子图与 chat 服务互相引用（父图调用子图、子图复用消息
        # 终态收敛），模块级导入会形成包级循环。
        from bridges.chat.turn import finalize_message

        finalize_message(
            repo,
            account_id,
            assistant_message_id,
            status=status,
            error_code=projection.error_code,
            error_message=projection.error_message,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            lock=None,
            started=time.monotonic(),
            now=now,
            career_plan=projection.model_dump(mode="json"),
            final_content=content,
        )


def _content_for(projection: CareerPlanProjection) -> str:
    if projection.samples:
        return render_result_content(projection)
    if projection.status is CareerPlanStatus.LINKS_ONLY:
        return render_links_only_content(projection)
    return render_empty_content(projection)


def _topic_of(analysis: CareerRequestAnalysis) -> str:
    job = analysis.job_title or " ".join(analysis.job_terms) or "未确定岗位"
    parts = [job]
    if analysis.cities:
        parts.append("、".join(analysis.cities))
    if analysis.stage:
        parts.append(analysis.stage)
    return " · ".join(parts)


__all__ = [
    "CAREER_MODULE_ID",
    "CAREER_NODE_LABELS",
    "CAREER_RECIPE_ID",
    "CAREER_RECIPE_VERSION",
    "FAILED_QUERY_STATUSES",
    "NODE_ADVISE",
    "NODE_ANALYZE",
    "NODE_BACKGROUND",
    "NODE_COLLECT",
    "NODE_FILTER",
    "NODE_GAP",
    "NODE_PARSE",
    "NODE_PLAN",
    "NODE_VERIFY",
    "READ_DEADLINE_SECONDS",
    "READS_PER_SOURCE",
    "SEARCH_DEADLINE_SECONDS",
    "CareerModuleError",
    "CareerPlanService",
    "CareerRunOutcome",
    "CareerSupersededError",
    "WAIT_KIND_CLARIFICATION",
    "WAIT_REASON_CLARIFICATION",
]
