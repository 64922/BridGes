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
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState
from bridges.kernel.contracts import (
    KernelResult,
    KernelStatus,
    RecipeInputs,
)
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository
from bridges.tieba.contracts import (
    TiebaQuestionAnalysis,
    TiebaResearchProjection,
    TiebaResearchStatus,
)
from bridges.tieba.evidence import (
    FORUM_NAME,
    topic_of,
)
from bridges.tieba.evidence import (
    apply_time_condition as _apply_time_condition,
)
from bridges.tieba.evidence import (
    official_candidates as _official_candidates,
)
from bridges.tieba.evidence import (
    time_filter_state as _time_filter_state,
)
from bridges.tieba.kernel import (
    NODE_PARSE,
    NODE_SEARCH,
    NODE_SYNTHESIZE,
    NODE_VERIFY,
    TIEBA_GATE_HANDLERS,
    TIEBA_RECIPE_ID,
    TIEBA_RECIPE_VERSION,
    TiebaBudget,
    TiebaNodeFlow,
    build_tieba_recipe,
    tieba_recipe_registry,
)
from bridges.tieba.official import TiebaOfficialReader
from bridges.tieba.parsing import pending_payload
from bridges.tieba.presenting import (
    render_clarification_content,
    render_empty_content,
    render_result_content,
    render_stopped_content,
)
from bridges.tieba.reading import TiebaThreadReader
from bridges.tieba.searching import TiebaSearchPort

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.task_materials import ModuleTaskContext

#: 节点名称与中文标签（进度事件与失败定位共用）。
TIEBA_NODE_LABELS: dict[str, str] = {
    "tieba.parse": "贴吧解析",
    "tieba.plan": "取证计划",
    "tieba.verify_official": "官方核验",
    "tieba.search": "贴吧搜索",
    "tieba.read": "贴吧读取",
    "tieba.synthesize": "贴吧归纳",
    "tieba.verify": "证据核验",
}

#: 显式模块标识与等待原因（父图与前端都依赖）。
TIEBA_MODULE_ID = "tieba"
WAIT_KIND_CLARIFICATION = "clarification"
WAIT_REASON_CLARIFICATION = "tieba_clarification"

#: 各阶段的墙钟预算（节点内核按剩余运行预算再收紧）。
SEARCH_DEADLINE_SECONDS = 25.0
READ_DEADLINE_SECONDS = 12.0
OFFICIAL_DEADLINE_SECONDS = 12.0


class TiebaModuleError(Exception):
    """模块内的稳定失败（定位到具体节点）。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class TiebaRunOutcome:
    """一轮贴吧模块的收敛结果（父图据此写等待原因与判断终态）。"""

    status: TiebaResearchStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)


class TiebaResearchService:
    """贴吧信息搜集编排服务（节点内核驱动）。"""

    def __init__(
        self,
        *,
        search: TiebaSearchPort,
        reader: TiebaThreadReader,
        official_reader: TiebaOfficialReader | None = None,
        search_deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        read_deadline_seconds: float = READ_DEADLINE_SECONDS,
        official_deadline_seconds: float = OFFICIAL_DEADLINE_SECONDS,
        task_version_provider: (
            Callable[[str, str], tuple[str | None, int | None] | None] | None
        ) = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._search = search
        self._reader = reader
        self._official_reader = official_reader
        self._search_deadline_seconds = search_deadline_seconds
        self._read_deadline_seconds = read_deadline_seconds
        self._official_deadline_seconds = official_deadline_seconds
        self._task_version_provider = task_version_provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._registry = tieba_recipe_registry()
        self._recipe = build_tieba_recipe()

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
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
        module_context: ModuleTaskContext | None = None,
        official_blocked_reason: str | None = None,
    ) -> TiebaRunOutcome:
        """执行一轮贴吧信息搜集；终态全部写回同一条助手消息。"""
        del run_model_id  # 本模块不调用模型，不存在模型生成的断言。
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise TiebaModuleError(
                NODE_PARSE,
                "message_not_found",
                "消息不存在或没有访问权限。",
                retryable=False,
            )
        run_id = getattr(run_context, "run_id", None)
        pending = self._pending_wait(repo, account_id, conversation_id)
        budget = self._load_budget(repo, account_id, run_id)
        task_ref = self._current_task_ref(account_id, conversation_id)
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
        )
        flow = TiebaNodeFlow(
            search=self._search,
            reader=self._reader,
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
        )
        kernel = NodeKernel(
            registry=self._registry,
            repository=NodeKernelRepository(repo.database),
            guard=guard,
            gates=TIEBA_GATE_HANDLERS,
            runner=flow.run_node,
            clock=self._clock,
        )
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
            event_sink=emit_node,
            stop_event=stop_event,
        )
        # 最终消息写入与守卫复核共用写事务，拒绝核验后转租约或改版本的结果。
        with NodeKernelRepository(repo.database).transaction():
            decision = guard.verify()
            if not decision.ok:
                if decision.code != "run_stopped":
                    raise TiebaModuleError(
                        NODE_PARSE,
                        decision.code,
                        "本轮结果在提交前已失效（运行已转交或任务已变更），未写入。",
                        retryable=True,
                    )
                result = _stopped_result(result)
            try:
                return self._deliver(
                    repo,
                    account_id=account_id,
                    assistant_message_id=assistant_message_id,
                    result=result,
                    stop_event=stop_event,
                )
            except TiebaModuleError as error:
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
    ) -> TiebaRunOutcome:
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
            raise TiebaModuleError(
                result.stopped_at or NODE_PARSE,
                result.rejection_code or "generation_superseded",
                "本轮结果在提交前已失效（运行已转交或任务已变更），未写入。",
                retryable=True,
            )
        return self._deliver_failure(repo, account_id, assistant_message_id, result)

    def _deliver_completed(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> TiebaRunOutcome:
        projection = _projection_of(result)
        if projection is None:
            raise TiebaModuleError(
                NODE_VERIFY,
                "tieba_delivery_missing",
                "贴吧模块缺少已核验的结果产物，本轮未提交。",
                retryable=True,
            )
        if projection.status is TiebaResearchStatus.ERROR:
            self._finalize(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                status=ChatMessageStatus.ERROR,
                projection=projection,
                content=render_empty_content(projection),
            )
            raise TiebaModuleError(
                NODE_SEARCH,
                projection.error_code or "tieba_search_failed",
                projection.error_message or "贴吧检索失败，请稍后重试。",
                retryable=projection.retryable,
            )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=(
                render_result_content(projection)
                if projection.confirmed_posts
                else render_empty_content(projection)
            ),
        )
        return TiebaRunOutcome(
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
    ) -> TiebaRunOutcome:
        analysis = _analysis_of(result)
        if analysis is None or analysis.clarification is None:
            return self._deliver_failure(
                repo, account_id, assistant_message_id, result
            )
        now = self._clock()
        projection = _base_projection(
            analysis,
            status=TiebaResearchStatus.CLARIFICATION,
            pending=ModuleWaitState(
                module_id=TIEBA_MODULE_ID,
                kind=WAIT_KIND_CLARIFICATION,
                question=analysis.clarification.question,
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
        )
        return TiebaRunOutcome(
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
    ) -> TiebaRunOutcome:
        analysis = _analysis_of(result)
        if analysis is None:
            analysis = _empty_analysis()
        projection = _base_projection(
            analysis,
            status=TiebaResearchStatus.STOPPED,
            queries=_records_of(result),
            confirmed=_confirmed_of(result),
            evidence_boundary=["你已停止本轮搜集，未继续读取页面。"],
            completed_at=self._clock(),
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            projection=projection,
            content=render_stopped_content(projection),
        )
        return TiebaRunOutcome(
            status=TiebaResearchStatus.STOPPED,
            wait_reason=None,
            queries=list(projection.queries),
        )

    def _deliver_failure(
        self,
        repo: ConversationRepository,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> TiebaRunOutcome:
        analysis = _analysis_of(result) or _empty_analysis()
        projection = _projection_of(result)
        if projection is None:
            failure = result.failure
            projection = _base_projection(
                analysis,
                status=TiebaResearchStatus.ERROR,
                queries=_records_of(result),
                completed_at=self._clock(),
                error_code=(failure.code if failure else "") or "tieba_failed",
                error_message=(failure.message if failure else "")
                or "贴吧信息搜集失败，请稍后重试。",
                retryable=failure.retryable if failure else True,
            )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.ERROR,
            projection=projection,
            content=render_empty_content(projection),
        )
        raise TiebaModuleError(
            (result.failure.node if result.failure else NODE_SEARCH),
            projection.error_code or "tieba_failed",
            projection.error_message or "贴吧信息搜集失败，请稍后重试。",
            retryable=projection.retryable,
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
        from bridges.tieba.evidence import search_candidates

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
        self, repo: ConversationRepository, account_id: str, run_id: str | None
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
        work_deadline = snapshot.plan.deadline_at - timedelta(
            milliseconds=snapshot.plan.verify_deliver_reserve_ms
        )
        return TiebaBudget(
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
        projection: TiebaResearchProjection,
        content: str,
    ) -> None:
        now = self._clock()
        projection = projection.model_copy(
            update={"completed_at": projection.completed_at or now}
        )
        # 局部导入：模块子图与 chat 服务互相引用（父图调用子图、子图复用消息
        # 终态收敛），模块级导入会形成包级循环。``finalize_message`` 会并入
        # 外层提交守卫事务（存在时），因此这里的终态与产物同一事务提交。
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
            final_content=content or None,
            tieba_research=projection.model_dump(mode="json"),
        )


# ---------------------------------------------------------------------------
# 结果读取与基础投影
# ---------------------------------------------------------------------------


def _delivery_artifact(result: KernelResult) -> Any:
    return result.artifact(NODE_VERIFY) or result.artifact(NODE_SYNTHESIZE)


def _projection_of(result: KernelResult) -> TiebaResearchProjection | None:
    artifact = _delivery_artifact(result)
    if artifact is None:
        return None
    raw = artifact.payload.get("projection")
    if not isinstance(raw, dict):
        return None
    try:
        return TiebaResearchProjection.model_validate(raw)
    except ValueError:
        return None


def _analysis_of(result: KernelResult) -> TiebaQuestionAnalysis | None:
    artifact = result.artifact(NODE_PARSE)
    if artifact is None:
        return None
    raw = artifact.payload.get("analysis")
    if not isinstance(raw, dict):
        return None
    try:
        return TiebaQuestionAnalysis.model_validate(raw)
    except ValueError:
        return None


def _records_of(result: KernelResult) -> list[ModuleQueryRecord]:
    artifact = result.artifact(NODE_SEARCH)
    if artifact is None:
        return []
    records: list[ModuleQueryRecord] = []
    for raw in artifact.payload.get("records", []):
        try:
            records.append(ModuleQueryRecord.model_validate(raw))
        except ValueError:
            continue
    return records


def _confirmed_of(result: KernelResult) -> list[Any]:
    from bridges.tieba.contracts import TiebaPostProjection

    artifact = result.artifact("tieba.read")
    if artifact is None:
        return []
    posts: list[TiebaPostProjection] = []
    for raw in artifact.payload.get("confirmed", []):
        try:
            posts.append(TiebaPostProjection.model_validate(raw))
        except ValueError:
            continue
    return posts


def _base_projection(
    analysis: TiebaQuestionAnalysis,
    *,
    status: TiebaResearchStatus,
    queries: Sequence[ModuleQueryRecord] = (),
    confirmed: Sequence[Any] = (),
    evidence_boundary: Sequence[str] = (),
    pending: ModuleWaitState | None = None,
    completed_at: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    retryable: bool = False,
) -> TiebaResearchProjection:
    return TiebaResearchProjection(
        status=status,
        topic=topic_of(analysis) or FORUM_NAME,
        original_question=analysis.original_question,
        topic_terms=list(analysis.topic_terms),
        place_or_event=list(analysis.place_or_event),
        question_kind=analysis.question_kind,
        campus_terms=list(analysis.campus_terms),
        time_filter=_time_filter_state(analysis, list(confirmed)),
        queries=list(queries),
        confirmed_posts=list(confirmed),
        official_check_requested=analysis.needs_official_check,
        evidence_boundary=list(evidence_boundary),
        pending=pending,
        completed_at=completed_at,
        error_code=error_code,
        error_message=error_message,
        retryable=retryable,
    )


def _empty_analysis() -> TiebaQuestionAnalysis:
    return TiebaQuestionAnalysis(original_question="", topic_terms=[])


def _stopped_result(result: KernelResult) -> KernelResult:
    from dataclasses import replace

    return replace(result, status=KernelStatus.STOPPED)


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
    "WAIT_REASON_CLARIFICATION",
    # 兼容既有测试与调用方的确定性规则入口。
    "_apply_time_condition",
    "_official_candidates",
    "_time_filter_state",
]
