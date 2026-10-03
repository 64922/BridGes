"""资料子图编排：八节点配方由持久节点内核执行（改进工单 25）。

模块不再把解析、检索、排序与呈现包在一个黑盒函数里：配方与节点定义在
:mod:`bridges.resources.kernel`，每个节点在自己的事务中提交类型化产物与
完成收据；书与视频两路检索在同一并行组内并发（上限 2）、共用运行预算；
恢复先读收据，输入一致则回填产物，输入变化只重跑该节点及其下游。本模块
只负责把内核结果翻译成既有消息投影，并复用既有终态收敛路径统一提交。

本模块不调用模型：正文完全来自真实检索与读取到的证据，不存在用模型记忆
补造书目或视频的路径（``run_model_id`` 仅保持调用签名一致）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState
from bridges.contracts.workflows import RunContextEnvelope
from bridges.kernel.contracts import (
    KernelResult,
    KernelStatus,
    RecipeInputs,
)
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository
from bridges.resources.contracts import (
    LearningResourcesProjection,
    ResourcesQueryPlan,
    ResourcesStatus,
    ResourcesTermAnalysis,
)
from bridges.resources.kernel import (
    NODE_ORGANIZE,
    NODE_PARSE,
    NODE_PLAN,
    NODE_READ,
    NODE_VERIFY,
    RESOURCES_GATE_HANDLERS,
    RESOURCES_NODE_LABELS,
    SEARCH_DEADLINE_SECONDS,
    VERIFY_DEADLINE_SECONDS,
    ResourcesBudget,
    ResourcesNodeFlow,
    build_resources_recipe,
    resources_recipe_registry,
)
from bridges.resources.parsing import (
    level_label,
    pending_payload,
)
from bridges.resources.presenting import (
    render_clarification_content,
    render_empty_content,
    render_error_content,
    render_result_content,
    render_stopped_content,
)
from bridges.resources.reading import BookInsightReader
from bridges.resources.sources import (
    BilibiliVideoDiscoverer,
    BilibiliVideoVerifier,
    OpenAlexBookSource,
    OpenLibraryBookSource,
)

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.task_materials import ModuleTaskContext

#: 澄清等待状态的类型标识（等待合同的一部分）。
WAIT_KIND_CLARIFICATION = "clarification"

RESOURCES_MODULE_ID = "resources"

#: 父图运行表的等待原因（可观测的持久化等待状态）。
WAIT_REASON_CLARIFICATION = "resources_clarification"


class ResourcesModuleError(Exception):
    """资料子图失败：节点位置、稳定错误码、可操作中文说明与是否可重试。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


class ResourcesSupersededError(Exception):
    """迟到结果：租约/版本/消息归属已变化，本轮不写任何交付终态。"""


@dataclass(frozen=True)
class ResourcesRunOutcome:
    """一轮资料模块的收敛结果（父图据此写等待原因与判断终态）。"""

    status: ResourcesStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)


class LearningResourcesService:
    """学习资料推荐模块编排服务（由日常父图在显式派发时调用）。"""

    def __init__(
        self,
        *,
        books: Sequence[OpenLibraryBookSource | OpenAlexBookSource] = (),
        discoverer: BilibiliVideoDiscoverer | None = None,
        verifier: BilibiliVideoVerifier | None = None,
        insight_reader: BookInsightReader | None = None,
        deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        verify_deadline_seconds: float = VERIFY_DEADLINE_SECONDS,
        task_version_provider: Callable[
            [str, str], tuple[str | None, int | None] | None
        ]
        | None = None,
    ) -> None:
        self._books = list(books)
        self._discoverer = discoverer
        self._verifier = verifier
        self._insight_reader = insight_reader
        self._deadline_seconds = deadline_seconds
        self._verify_deadline_seconds = verify_deadline_seconds
        self._task_version_provider = task_version_provider
        self._registry = resources_recipe_registry()
        self._recipe = build_resources_recipe()

    def close(self) -> None:
        for source in self._books:
            source.close()
        if self._verifier is not None:
            self._verifier.close()
        if self._insight_reader is not None and hasattr(self._insight_reader, "close"):
            self._insight_reader.close()

    # -- 对外入口 --------------------------------------------------------

    def run(
        self,
        *,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        run_context: RunContextEnvelope,
        run_model_id: str | None = None,
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
        module_context: ModuleTaskContext | None = None,
    ) -> ResourcesRunOutcome:
        """执行一轮资料模块：持久节点内核执行，结果统一提交回同一消息。"""
        # 资料推荐不调用模型：本轮模型锁与本模块无关（签名保持一致）。
        del run_model_id
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise ResourcesModuleError(
                NODE_PARSE, "message_not_found", "消息不存在或没有访问权限。", retryable=False
            )
        pending = self._pending_wait(repo, account_id, conversation_id)
        prior = (
            list(module_context.prior_messages)
            if module_context is not None
            else self._prior_user_messages(
                repo, account_id, conversation_id, user_message_id
            )
        )
        run_id = run_context.run_id
        budget = self._load_budget(repo, account_id, run_id)
        task_ref = self._current_task_ref(account_id, conversation_id)
        guard = RunCommitGuard(
            repo,
            account_id=account_id,
            run_id=run_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            task_ref=task_ref,
            task_version_provider=self._task_version_provider,
            stop_event=stop_event,
            clock=lambda: datetime.now(UTC),
        )
        flow = ResourcesNodeFlow(
            books=self._books,
            discoverer=self._discoverer,
            verifier=self._verifier,
            reader=self._insight_reader,
            clock=lambda: datetime.now(UTC),
            prior_context=prior,
            module_context=module_context,
            pending_wait=pending,
            budget=budget,
            stop_event=stop_event,
            deadline_seconds=self._deadline_seconds,
            verify_deadline_seconds=self._verify_deadline_seconds,
        )
        kernel = NodeKernel(
            registry=self._registry,
            repository=NodeKernelRepository(repo.database),
            guard=guard,
            gates=RESOURCES_GATE_HANDLERS,
            runner=flow.run_node,
            clock=lambda: datetime.now(UTC),
        )
        result = kernel.execute(
            recipe=self._recipe,
            inputs=RecipeInputs(
                account_id=account_id,
                conversation_id=conversation_id,
                run_id=run_id,
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
            event_sink=emit_node,
            stop_event=stop_event,
        )
        # 最终消息写入与守卫复核共用写事务，拒绝核验后转租约或改版本的结果。
        with NodeKernelRepository(repo.database).transaction():
            decision = guard.verify()
            if not decision.ok:
                if decision.code != "run_stopped":
                    raise ResourcesSupersededError(decision.code)
                result = replace(result, status=KernelStatus.STOPPED)
            try:
                return self._deliver(
                    repo,
                    account_id=account_id,
                    assistant_message_id=assistant_message_id,
                    result=result,
                    stop_event=stop_event,
                )
            except ResourcesModuleError as error:
                # 失败投影提交后，再通知父图收敛错误，避免异常回滚投影。
                delivery_error = error
        raise delivery_error

    # -- 交付（既有终态收敛路径） ----------------------------------------

    def _deliver(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
        stop_event: threading.Event | None,
    ) -> ResourcesRunOutcome:
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
            raise ResourcesSupersededError(
                result.rejection_code or "generation_superseded"
            )
        return self._deliver_failure(repo, account_id, assistant_message_id, result)

    def _deliver_completed(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> ResourcesRunOutcome:
        artifact = result.delivery
        if artifact is None or artifact.node != NODE_VERIFY:
            raise ResourcesModuleError(
                NODE_VERIFY,
                "resources_delivery_missing",
                "资料结果没有形成待交付产物，本轮未提交。",
                retryable=True,
            )
        projection = LearningResourcesProjection.model_validate(
            artifact.payload["projection"]
        )
        if projection.status is ResourcesStatus.ERROR:
            return self._deliver_failure(
                repo, account_id, assistant_message_id, result, projection=projection
            )
        analysis = self._analysis(result)
        plan = self._plan(result)
        now = datetime.now(UTC)
        content = (
            render_result_content(analysis, plan, projection)
            if projection.status is ResourcesStatus.SUCCESS
            and analysis is not None
            and plan is not None
            else render_empty_content(
                analysis, plan, list(projection.evidence_notes)
            )
            if analysis is not None and plan is not None
            else ""
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=content,
            now=now,
        )
        return ResourcesRunOutcome(
            status=projection.status, queries=list(projection.queries)
        )

    def _deliver_clarification(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> ResourcesRunOutcome:
        analysis = self._analysis(result)
        if analysis is None or analysis.clarification is None:
            raise ResourcesModuleError(
                NODE_PARSE,
                "resources_clarification_missing",
                "资料澄清状态缺少恢复载荷，本轮未提交。",
                retryable=True,
            )
        now = datetime.now(UTC)
        projection = LearningResourcesProjection(
            status=ResourcesStatus.CLARIFICATION,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            goal=analysis.goal,
            goal_kind=analysis.goal_kind,
            media=analysis.media,
            assumptions=list(analysis.assumptions),
            needs_practice_project=analysis.needs_practice_project,
            level_basis=analysis.level_basis,
            language=analysis.language,
            time_budget=analysis.time_budget,
            basis_evidence=analysis.basis_evidence,
            final_query=analysis.final_query,
            pending=ModuleWaitState(
                module_id=RESOURCES_MODULE_ID,
                kind=WAIT_KIND_CLARIFICATION,
                question=analysis.clarification.question,
                origin_message_id=assistant_message_id,
                context=pending_payload(
                    analysis, missing=analysis.clarification.missing
                ),
                created_at=now,
            ),
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
        return ResourcesRunOutcome(
            status=ResourcesStatus.CLARIFICATION,
            wait_reason=WAIT_REASON_CLARIFICATION,
        )

    def _deliver_stopped(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> ResourcesRunOutcome:
        analysis = self._analysis(result)
        plan = self._plan(result)
        queries = self._queries(result)
        now = datetime.now(UTC)
        projection = LearningResourcesProjection(
            status=ResourcesStatus.STOPPED,
            original_phrase=analysis.original_phrase if analysis else "",
            normalized_term=analysis.normalized_term if analysis else "",
            expansions=list(analysis.expansions) if analysis else [],
            confidence=analysis.confidence if analysis else 0.0,
            goal=analysis.goal if analysis else None,
            goal_kind=analysis.goal_kind if analysis else None,
            media=analysis.media if analysis else None,
            assumptions=list(analysis.assumptions) if analysis else [],
            needs_practice_project=analysis.needs_practice_project if analysis else False,
            level_label=level_label(analysis.level) if analysis else None,
            level_basis=analysis.level_basis if analysis else None,
            language=analysis.language if analysis else None,
            time_budget=analysis.time_budget if analysis else None,
            basis_evidence=analysis.basis_evidence if analysis else None,
            queries=queries,
            final_query=plan.book_query if plan else "",
            requested_books=plan.target_books if plan else 0,
            requested_videos=plan.target_videos if plan else 0,
            parallel_limit=plan.parallel_limit if plan else 0,
            evidence_notes=["用户停止了本轮检索，未生成的步骤不会补做。"],
            searched_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            projection=projection,
            content=render_stopped_content(plan),
            now=now,
        )
        return ResourcesRunOutcome(status=ResourcesStatus.STOPPED, queries=queries)

    def _deliver_failure(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
        *,
        projection: LearningResourcesProjection | None = None,
    ) -> ResourcesRunOutcome:
        analysis = self._analysis(result)
        plan = self._plan(result)
        queries = self._queries(result)
        failure = result.failure
        failure_payload = self._failure_payload(result)
        node = (
            failure.node
            if failure is not None
            else str((failure_payload or {}).get("node") or NODE_VERIFY)
        )
        code = (
            (failure.code if failure is not None else "")
            or str((failure_payload or {}).get("code") or "")
            or "resources_search_failed"
        )
        message = (
            (failure.message if failure is not None else "")
            or str((failure_payload or {}).get("message") or "")
            or "学习资料检索失败，请稍后重试。"
        )
        retryable = (
            failure.retryable
            if failure is not None
            else bool((failure_payload or {}).get("retryable", True))
        )
        if projection is None:
            now = datetime.now(UTC)
            projection = LearningResourcesProjection(
                status=ResourcesStatus.ERROR,
                original_phrase=analysis.original_phrase if analysis else "",
                normalized_term=analysis.normalized_term if analysis else "",
                expansions=list(analysis.expansions) if analysis else [],
                confidence=analysis.confidence if analysis else 0.0,
                goal=analysis.goal if analysis else None,
                goal_kind=analysis.goal_kind if analysis else None,
                media=analysis.media if analysis else None,
                assumptions=list(analysis.assumptions) if analysis else [],
                needs_practice_project=analysis.needs_practice_project if analysis else False,
                level_label=level_label(analysis.level) if analysis else None,
                level_basis=analysis.level_basis if analysis else None,
                language=analysis.language if analysis else None,
                time_budget=analysis.time_budget if analysis else None,
                basis_evidence=analysis.basis_evidence if analysis else None,
                queries=queries,
                final_query=plan.book_query if plan else "",
                requested_books=plan.target_books if plan else 0,
                requested_videos=plan.target_videos if plan else 0,
                parallel_limit=plan.parallel_limit if plan else 0,
                evidence_notes=self._query_notes(queries),
                searched_at=now,
                error_code=code,
                error_message=message,
                retryable=retryable,
            )
        now = datetime.now(UTC)
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.ERROR,
            projection=projection,
            content=render_error_content(projection),
            now=now,
        )
        raise ResourcesModuleError(node, code, message, retryable=retryable)

    # -- 内核结果读取 -----------------------------------------------------

    def _analysis(self, result: KernelResult) -> ResourcesTermAnalysis | None:
        artifact = result.artifact(NODE_PARSE)
        if artifact is None:
            return None
        payload = artifact.payload.get("analysis")
        if not isinstance(payload, dict):
            return None
        return ResourcesTermAnalysis.model_validate(payload)

    def _plan(self, result: KernelResult) -> ResourcesQueryPlan | None:
        artifact = result.artifact(NODE_PLAN)
        if artifact is None:
            return None
        payload = artifact.payload.get("plan")
        if not isinstance(payload, dict):
            return None
        return ResourcesQueryPlan.model_validate(payload)

    def _queries(self, result: KernelResult) -> list[ModuleQueryRecord]:
        artifact = result.artifact(NODE_READ)
        if artifact is None:
            return []
        return [
            ModuleQueryRecord.model_validate(item)
            for item in [
                *artifact.payload.get("book_records", []),
                *artifact.payload.get("video_records", []),
            ]
        ]

    def _failure_payload(self, result: KernelResult) -> dict[str, Any] | None:
        artifact = result.artifact(NODE_ORGANIZE)
        if artifact is None:
            return None
        failure = artifact.payload.get("failure")
        return dict(failure) if isinstance(failure, dict) else None

    # -- 恢复与等待 ------------------------------------------------------

    def _pending_wait(
        self,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
    ) -> ModuleWaitState | None:
        """本会话最近一条资料消息的等待状态（唯一权威来源）。

        只有最后一条带资料投影的消息能代表当前等待：它若已经完成、空结果、
        失败或停止，上一轮的澄清问题就已被这轮结果取代——下一条消息按全新
        请求解析，不再从更早的历史里翻出旧等待。
        """
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            projection = message.learning_resources
            if not isinstance(projection, dict):
                continue
            try:
                stored = LearningResourcesProjection.model_validate(projection)
            except ValueError:
                continue
            return stored.pending
        return None

    def _prior_user_messages(
        self,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
    ) -> list[str]:
        """已确认的会话前文（最近若干条用户消息），只用于补足学习层次。"""
        prior: list[str] = []
        for message in repo.list_messages(account_id, conversation_id):
            if message.message_id == user_message_id:
                break
            if message.role.value == "user" and message.content.strip():
                prior.append(message.content)
        return prior[-6:]

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
    ) -> ResourcesBudget | None:
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
        return ResourcesBudget(
            ledger=ledger,
            account_id=account_id,
            run_id=run_id,
            work_deadline=work_deadline,
        )

    # -- 落库 ------------------------------------------------------------

    def _query_notes(self, queries: Sequence[ModuleQueryRecord]) -> list[str]:
        return [
            f"{record.source} 查询「{record.query}」：{record.status.value}"
            + (f"（{record.error_message}）" if record.error_message else "")
            for record in queries
        ] or ["本轮没有发出可记录的外部查询。"]

    def _finalize(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        status: ChatMessageStatus,
        projection: LearningResourcesProjection,
        content: str,
        now: datetime,
    ) -> None:
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
            started=time.monotonic(),
            now=now,
            learning_resources=projection.model_dump(mode="json"),
            final_content=content,
        )


__all__ = [
    "RESOURCES_MODULE_ID",
    "RESOURCES_NODE_LABELS",
    "WAIT_KIND_CLARIFICATION",
    "WAIT_REASON_CLARIFICATION",
    "LearningResourcesService",
    "ResourcesModuleError",
    "ResourcesRunOutcome",
    "ResourcesSupersededError",
]
