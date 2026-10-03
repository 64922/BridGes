"""论文模块编排：登记持久节点内核执行 + 同一消息终态收敛（工单 24）。

工单 11 的三份合同继续有效，并在工单 24 迁移到共享执行内核：

1. **证据合同**：每次外部调用产出 ``ModuleQueryRecord``（查询/证据/时间/错误），
   公开检索只发送最小查询词，私有上下文不进外部服务；筛选以需求到标题/摘要
   证据的对应为准，关键词不是通过条件。
2. **等待合同**：缺失或歧义只问一项，提问随助手消息落库（``ModuleWaitState``），
   下一轮从该处恢复读取权威记录与实际回复；``paper.parse``/``paper.screen``
   是登记的持久节点，恢复时只重跑未完成节点。
3. **失败与停止合同**：查询有超时、有界调整（整次运行一轮）与取消；失败保留
   查询词与真实分类、可重试；停止在节点边界生效并把状态写回同一条消息。
4. **产物身份**：选定论文携带可核实身份（arXiv/DOI/年份/链接/内容哈希）进入
   消息投影，供 GitHub 等后续模块精确引用；旧投影继续可读、可导出。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bridges.ai.model_quota import RunModelQuota
from bridges.ai.payload_budget import CallMaterialManifest
from bridges.contracts.ai import ModelRunLock
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState
from bridges.contracts.workflows import RunContextEnvelope
from bridges.kernel.contracts import (
    KernelResult,
    KernelStatus,
    NodeArtifact,
    RecipeInputs,
)
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository
from bridges.paper.contracts import (
    PaperIdentity,
    PaperQueryPlan,
    PaperRecommendation,
    PaperSearchProjection,
    PaperSearchStatus,
    PaperTermAnalysis,
)
from bridges.paper.kernel import (
    NODE_ENRICH,
    NODE_EVALUATE,
    NODE_PARSE,
    NODE_PLAN,
    NODE_READ,
    NODE_SCREEN,
    NODE_SEARCH,
    NODE_VERIFY,
    PAPER_GATE_HANDLERS,
    PAPER_NODE_LABELS,
    PAPER_RECIPE_ID,
    PAPER_RECIPE_VERSION,
    PaperBudget,
    PaperFlowContext,
    PaperNodeFlow,
    build_paper_recipe,
    paper_recipe_registry,
)
from bridges.paper.lexicon import AMBIGUOUS_TERMS
from bridges.paper.parsing import parse_paper_request, pending_payload
from bridges.paper.planning import plan_queries
from bridges.paper.presenting import (
    ExpressionPolicy,
    PaperSummaryGenerator,
    render_clarification_content,
    render_empty_content,
    render_mismatch_content,
    render_result_content,
    render_stopped_content,
)
from bridges.paper.ranking import cover_original_phrase
from bridges.paper.reading import FullTextReader, ReadCoordinator
from bridges.paper.screening import PaperRelevanceJudge
from bridges.paper.sources import ArxivPaperSource, MetadataEnricher

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.task_materials import ModuleTaskContext

#: 单轮检索与补充的墙钟预算（秒）：超时如实失败，绝不无限等待上游。
SEARCH_DEADLINE_SECONDS = 25.0
ENRICH_DEADLINE_SECONDS = 12.0

#: 澄清等待状态的类型标识（等待合同的一部分）。
WAIT_KIND_CLARIFICATION = "clarification"

PAPER_MODULE_ID = "paper"

#: 父图运行表的等待原因（可观测的持久化等待状态）。
WAIT_REASON_CLARIFICATION = "paper_clarification"


class PaperModuleError(Exception):
    """论文模块失败：节点位置、稳定错误码、可操作中文说明与是否可重试。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


class PaperSupersededError(Exception):
    """迟到/失效结果：本轮不再写交付终态，交给当前执行者收尾。"""


@dataclass(frozen=True)
class PaperRunOutcome:
    """一轮论文模块的收敛结果（父图据此写等待原因与判断终态）。"""

    status: PaperSearchStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)


@dataclass
class _PreparedDelivery:
    """事务外准备好的交付内容（事务内只做守卫复核与原子写入）。"""

    projection: PaperSearchProjection
    content: str
    message_status: ChatMessageStatus
    lock: ModelRunLock | None = None
    failure: PaperModuleError | None = None


class PaperSearchService:
    """论文模块编排服务（由日常父图在显式派发时调用）。"""

    def __init__(
        self,
        *,
        source: ArxivPaperSource,
        enricher: MetadataEnricher | None = None,
        summarizer: PaperSummaryGenerator | None = None,
        deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        enrich_deadline_seconds: float = ENRICH_DEADLINE_SECONDS,
        task_version_provider: Callable[
            [str, str], tuple[str | None, int | None] | None
        ]
        | None = None,
        reader: FullTextReader | None = None,
        judge: PaperRelevanceJudge | None = None,
        reviewer: Callable[
            [list[PaperRecommendation], list[str]], Mapping[str, Any] | None
        ]
        | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = source
        self._enricher = enricher
        self._summarizer = summarizer
        self._deadline_seconds = deadline_seconds
        self._enrich_deadline_seconds = enrich_deadline_seconds
        self._task_version_provider = task_version_provider
        self._reader = reader
        self._judge = judge
        self._reviewer = reviewer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._registry = paper_recipe_registry()
        self._recipe = build_paper_recipe()

    def close(self) -> None:
        if self._enricher is not None:
            self._enricher.close()

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
        run_model_id: str | None,
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
        module_context: ModuleTaskContext | None = None,
        model_quota: RunModelQuota | None = None,
        manifest_sink: Callable[[CallMaterialManifest], None] | None = None,
        writing_policy: Mapping[str, Any] | None = None,
    ) -> PaperRunOutcome:
        """执行一轮论文模块；终态（完成/澄清/空/失败/停止）全部写回同一消息。"""
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise PaperModuleError(
                NODE_PARSE,
                "message_not_found",
                "消息不存在或没有访问权限。",
                retryable=False,
            )
        pending = self._pending_wait(repo, account_id, conversation_id)
        task_scope_used = bool(
            module_context is not None and module_context.used_task_scope
        )
        if module_context is None:
            prior = self._prior_user_messages(
                repo, account_id, conversation_id, user_message_id
            )
            topic_hint = None
            task_conditions: tuple[Any, ...] = ()
        else:
            topic_hint = module_context.topic_hint
            if task_scope_used:
                prior = list(module_context.prior_messages)
                task_conditions = tuple(module_context.effective_conditions)
            else:
                prior = [
                    condition.text
                    for condition in module_context.effective_conditions
                    if condition.kind in {"domain", "topic"}
                ]
                task_conditions = ()
        context = PaperFlowContext(
            prior_context=tuple(prior),
            topic_hint=topic_hint,
            task_conditions=task_conditions,
            task_scope_used=task_scope_used,
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
            clock=self._clock,
        )
        flow = PaperNodeFlow(
            source=self._source,
            enricher=self._enricher,
            reader=ReadCoordinator(
                self._reader,
                deep_read_max=(budget.deep_read_max if budget is not None else 3),
                deadline_seconds=self._enrich_deadline_seconds,
            ),
            judge=self._judge,
            reviewer=self._reviewer,
            context=context,
            pending_wait=pending,
            budget=budget,
            stop_event=stop_event,
            deadline_seconds=self._deadline_seconds,
            enrich_deadline_seconds=self._enrich_deadline_seconds,
            clock=self._clock,
        )
        kernel = NodeKernel(
            registry=self._registry,
            repository=NodeKernelRepository(repo.database),
            guard=guard,
            gates=PAPER_GATE_HANDLERS,
            runner=flow.run_node,
            clock=self._clock,
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
        prepared = self._build_delivery(
            assistant_message_id=assistant_message_id,
            user_message=user_message,
            result=result,
            stopped_records=flow.external_records,
            run_context=run_context,
            run_model_id=run_model_id,
            model_quota=model_quota,
            manifest_sink=manifest_sink,
            writing_policy=writing_policy,
        )
        # 最终消息写入与守卫复核共用写事务：转租约、改任务版本或停止后的
        # 迟到结果不得覆盖新状态；停止由仍持有租约的执行者如实收敛。
        with NodeKernelRepository(repo.database).transaction():
            decision = guard.verify()
            if not decision.ok:
                if decision.code != "run_stopped":
                    raise PaperSupersededError(decision.code)
                prepared = self._build_delivery(
                    assistant_message_id=assistant_message_id,
                    user_message=user_message,
                    result=_as_stopped(result),
                    stopped_records=flow.external_records,
                    run_context=run_context,
                    run_model_id=run_model_id,
                    model_quota=model_quota,
                    manifest_sink=manifest_sink,
                    writing_policy=writing_policy,
                )
            outcome = self._deliver(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                prepared=prepared,
                result=result,
            )
        if prepared.failure is not None:
            raise prepared.failure
        return outcome

    # -- 交付准备（事务外） ----------------------------------------------

    def _build_delivery(
        self,
        *,
        assistant_message_id: str,
        user_message: Any,
        result: KernelResult,
        stopped_records: Sequence[ModuleQueryRecord] = (),
        run_context: RunContextEnvelope,
        run_model_id: str | None,
        model_quota: RunModelQuota | None,
        manifest_sink: Callable[[CallMaterialManifest], None] | None,
        writing_policy: Mapping[str, Any] | None,
    ) -> _PreparedDelivery:
        if result.status is KernelStatus.REJECTED:
            raise PaperSupersededError(result.rejection_code or "generation_superseded")
        if result.status is KernelStatus.COMPLETED:
            evaluate = result.artifact(NODE_EVALUATE)
            outcome = evaluate.payload.get("outcome") if evaluate is not None else None
            if outcome == "success" and evaluate is not None:
                return self._prepare_success(
                    result,
                    evaluate=evaluate,
                    run_context=run_context,
                    run_model_id=run_model_id,
                    model_quota=model_quota,
                    manifest_sink=manifest_sink,
                    writing_policy=writing_policy,
                )
            if outcome == "topic_mismatch":
                return self._prepare_clarification(
                    result,
                    assistant_message_id=assistant_message_id,
                    mismatch=True,
                )
            return self._prepare_empty(result)
        if result.status is KernelStatus.NEEDS_INPUT:
            screen = result.artifact(NODE_SCREEN)
            return self._prepare_clarification(
                result,
                assistant_message_id=assistant_message_id,
                mismatch=bool(
                    screen is not None and screen.payload.get("topic_mismatch")
                ),
            )
        if result.status in {KernelStatus.STOPPED, KernelStatus.INVALIDATED}:
            return self._prepare_stopped(
                result, user_message=user_message, records=stopped_records
            )
        return self._prepare_failure(result)

    def _prepare_success(
        self,
        result: KernelResult,
        *,
        evaluate: NodeArtifact,
        run_context: RunContextEnvelope,
        run_model_id: str | None,
        model_quota: RunModelQuota | None,
        manifest_sink: Callable[[CallMaterialManifest], None] | None,
        writing_policy: Mapping[str, Any] | None,
    ) -> _PreparedDelivery:
        parse = result.artifact(NODE_PARSE)
        plan_artifact = result.artifact(NODE_PLAN)
        analysis = _analysis(parse)
        plan = _plan(plan_artifact, analysis)
        recommendations = [
            PaperRecommendation.model_validate(item)
            for item in evaluate.payload.get("recommendations") or []
        ]
        if not cover_original_phrase(analysis, recommendations):
            projection = self._failure_projection(
                result,
                code="paper_topic_mismatch",
                message="推荐结果未覆盖你的原始术语，已停止生成。",
                retryable=False,
            )
            return _PreparedDelivery(
                projection=projection,
                content="",
                message_status=ChatMessageStatus.DONE,
                failure=PaperModuleError(
                    NODE_VERIFY,
                    "paper_topic_mismatch",
                    "推荐结果未覆盖你的原始术语，已停止生成。",
                    retryable=False,
                ),
            )
        search_records = _records(result.artifact(NODE_SEARCH))
        enrich_records = _records(result.artifact(NODE_ENRICH))
        notes = _notes(result)
        abstracts = _abstracts(result.artifact(NODE_SEARCH))
        summary_map, summary_note, lock, policy_version = self._summarize(
            recommendations,
            abstracts,
            run_context=run_context,
            run_model_id=run_model_id,
            model_quota=model_quota,
            manifest_sink=manifest_sink,
            writing_policy=writing_policy,
        )
        if summary_map:
            for paper in recommendations:
                text = summary_map.get(paper.arxiv_id or "")
                if text:
                    paper.summary_zh = text
        else:
            notes.append(
                summary_note
                or "未生成中文概述，本轮只依据来源返回的标题、摘要与元数据给出理由。"
            )
        now = self._clock()
        projection = PaperSearchProjection(
            status=PaperSearchStatus.SUCCESS,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            context_label=analysis.context_label,
            queries=[*search_records, *enrich_records],
            final_query=plan.query,
            papers=recommendations,
            selected=[
                PaperIdentity.model_validate(item)
                for item in evaluate.payload.get("selection") or []
            ],
            requested_count=plan.target_count,
            artifacts=_artifact_refs(result),
            expression_policy_version=policy_version,
            evidence_notes=notes,
            searched_at=now,
        )
        return _PreparedDelivery(
            projection=projection,
            content=render_result_content(analysis, plan, projection),
            message_status=ChatMessageStatus.DONE,
            lock=lock,
        )

    def _prepare_empty(self, result: KernelResult) -> _PreparedDelivery:
        analysis = _analysis(result.artifact(NODE_PARSE))
        plan = _plan(result.artifact(NODE_PLAN), analysis)
        notes = _notes(result)
        now = self._clock()
        projection = PaperSearchProjection(
            status=PaperSearchStatus.EMPTY,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            context_label=analysis.context_label,
            queries=[
                *_records(result.artifact(NODE_SEARCH)),
                *_records(result.artifact(NODE_ENRICH)),
            ],
            final_query=plan.query,
            artifacts=_artifact_refs(result),
            evidence_notes=notes,
            searched_at=now,
        )
        return _PreparedDelivery(
            projection=projection,
            content=render_empty_content(analysis, plan, notes),
            message_status=ChatMessageStatus.DONE,
        )

    def _prepare_clarification(
        self,
        result: KernelResult,
        *,
        assistant_message_id: str,
        mismatch: bool,
    ) -> _PreparedDelivery:
        parse = result.artifact(NODE_PARSE)
        analysis = _analysis(parse)
        plan = _plan(result.artifact(NODE_PLAN), analysis)
        if mismatch:
            notes = _notes(result)
            question = render_mismatch_content(analysis, plan, notes)
            queries = [
                *_records(result.artifact(NODE_SEARCH)),
                *_records(result.artifact(NODE_ENRICH)),
            ]
            plan_value: PaperQueryPlan | None = plan
        else:
            clarification = parse.payload.get("clarification") if parse else None
            question = (
                str(clarification.get("question"))
                if isinstance(clarification, Mapping)
                else analysis.final_query
            )
            queries = []
            plan_value = None
        now = self._clock()
        projection = PaperSearchProjection(
            status=PaperSearchStatus.CLARIFICATION,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            context_label=analysis.context_label,
            queries=queries,
            final_query=(
                plan_value.query if plan_value is not None else analysis.final_query
            ),
            artifacts=_artifact_refs(result),
            evidence_notes=_notes(result) if plan_value is not None else [],
            pending=ModuleWaitState(
                module_id=PAPER_MODULE_ID,
                kind=WAIT_KIND_CLARIFICATION,
                question=question,
                origin_message_id=assistant_message_id,
                context=_clarification_payload(analysis),
                created_at=now,
            ),
        )
        return _PreparedDelivery(
            projection=projection,
            content=render_clarification_content(analysis) or question,
            message_status=ChatMessageStatus.DONE,
        )

    def _prepare_stopped(
        self,
        result: KernelResult,
        *,
        user_message: Any,
        records: Sequence[ModuleQueryRecord] = (),
    ) -> _PreparedDelivery:
        parse = result.artifact(NODE_PARSE)
        if parse is None:
            # 停止可能发生在任何节点边界之前：用确定性解析渲染停止态，
            # 不调用模型，也不把停止写成失败或空结果。
            analysis = parse_paper_request(user_message.content)
            plan = plan_queries(analysis)[0]
        else:
            analysis = _analysis(parse)
            plan = _plan(result.artifact(NODE_PLAN), analysis)
        queries = list(records) or _records(result.artifact(NODE_SEARCH))
        now = self._clock()
        projection = PaperSearchProjection(
            status=PaperSearchStatus.STOPPED,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            final_query=plan.query,
            queries=queries,
            artifacts=_artifact_refs(result),
            evidence_notes=["用户停止了本轮检索，未生成的步骤不会补做。"],
            searched_at=now,
        )
        return _PreparedDelivery(
            projection=projection,
            content=render_stopped_content(analysis, plan),
            message_status=ChatMessageStatus.STOPPED,
        )

    def _prepare_failure(self, result: KernelResult) -> _PreparedDelivery:
        failure = result.failure
        code = failure.code if failure is not None else "paper_search_failed"
        message = (
            failure.message if failure is not None else "论文检索失败，请稍后重试。"
        )
        retryable = failure.retryable if failure is not None else True
        node = failure.node if failure is not None else NODE_SEARCH
        projection = self._failure_projection(
            result, code=code, message=message, retryable=retryable
        )
        return _PreparedDelivery(
            projection=projection,
            content="",
            message_status=ChatMessageStatus.DONE,
            failure=PaperModuleError(node, code, message, retryable=retryable),
        )

    def _failure_projection(
        self,
        result: KernelResult,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> PaperSearchProjection:
        analysis = _analysis(result.artifact(NODE_PARSE))
        plan = _plan(result.artifact(NODE_PLAN), analysis)
        records = [
            *_records(result.artifact(NODE_SEARCH)),
            *_records(result.artifact(NODE_ENRICH)),
        ]
        return PaperSearchProjection(
            status=PaperSearchStatus.ERROR,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            context_label=analysis.context_label,
            final_query=plan.query,
            queries=records,
            artifacts=_artifact_refs(result),
            searched_at=self._clock(),
            error_code=code,
            error_message=message,
            retryable=retryable,
        )

    # -- 事务内写入 ------------------------------------------------------

    def _deliver(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        prepared: _PreparedDelivery,
        result: KernelResult,
    ) -> PaperRunOutcome:
        projection = prepared.projection
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=(
                ChatMessageStatus.ERROR
                if prepared.failure is not None
                else prepared.message_status
            ),
            projection=projection,
            content=prepared.content,
            now=self._clock(),
            lock=prepared.lock,
        )
        return PaperRunOutcome(
            status=projection.status,
            wait_reason=(
                WAIT_REASON_CLARIFICATION
                if projection.status is PaperSearchStatus.CLARIFICATION
                else None
            ),
            queries=list(projection.queries),
            artifacts=_artifact_refs(result),
        )

    # -- 概述调用（工单 21 表达策略 + 工单 04 最终预算） ------------------

    def _summarize(
        self,
        recommendations: list[PaperRecommendation],
        abstracts: dict[str, str],
        *,
        run_context: RunContextEnvelope,
        run_model_id: str | None,
        model_quota: RunModelQuota | None,
        manifest_sink: Callable[[CallMaterialManifest], None] | None,
        writing_policy: Mapping[str, Any] | None,
    ) -> tuple[dict[str, str] | None, str | None, ModelRunLock | None, str | None]:
        if self._summarizer is None:
            return None, None, None, None
        expression = _expression_snapshot(writing_policy)
        result = self._summarizer.generate(
            run_context,
            recommendations,
            abstracts=abstracts,
            model_id=run_model_id,
            model_quota=model_quota,
            expression=expression,
        )
        if result.manifest is not None and manifest_sink is not None:
            manifest_sink(result.manifest)
        return (
            result.summaries or None,
            result.note,
            result.lock,
            expression.version if expression is not None else None,
        )

    # -- 恢复与等待 ------------------------------------------------------

    def _pending_wait(
        self, repo: ConversationRepository, account_id: str, conversation_id: str
    ) -> ModuleWaitState | None:
        """读取本会话最近一次尚未被后续结果取代的澄清等待状态。"""
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            projection = message.paper_search
            if not isinstance(projection, dict):
                continue
            try:
                stored = PaperSearchProjection.model_validate(projection)
            except ValueError:
                continue
            if stored.pending is not None:
                return stored.pending
            if stored.status in {PaperSearchStatus.SUCCESS, PaperSearchStatus.EMPTY}:
                # 更晚的完成结果已经取代等待状态：不再恢复。
                return None
        return None

    def _prior_user_messages(
        self,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
    ) -> list[str]:
        """已确认的会话前文（最近若干条用户消息），只用于语境消歧。"""
        prior: list[str] = []
        for message in repo.list_messages(account_id, conversation_id):
            if message.message_id == user_message_id:
                break
            if message.role.value == "user" and message.content.strip():
                prior.append(message.content)
        return prior[-6:]

    def _current_task_ref(
        self, account_id: str, conversation_id: str
    ) -> tuple[str | None, int | None] | None:
        if self._task_version_provider is None:
            return None
        return self._task_version_provider(account_id, conversation_id)

    def _wait_identity(self, pending: ModuleWaitState | None) -> str | None:
        if pending is None:
            return None
        return (
            f"{pending.module_id}:{pending.kind}:{pending.origin_message_id}:"
            f"{pending.created_at.isoformat()}"
        )

    def _load_budget(
        self, repo: ConversationRepository, account_id: str, run_id: str
    ) -> PaperBudget | None:
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
        return PaperBudget(
            ledger=ledger,
            account_id=account_id,
            run_id=run_id,
            work_deadline=work_deadline,
            screen_max=snapshot.plan.candidate_screen_max,
            deep_read_max=snapshot.plan.deep_read_max,
        )

    # -- 落库 ------------------------------------------------------------

    def _finalize(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        status: ChatMessageStatus,
        projection: PaperSearchProjection,
        content: str,
        now: datetime,
        lock: ModelRunLock | None = None,
    ) -> None:
        # 正文与终态、论文投影在同一事务内一次提交（finalize_message 的
        # final_content 字段），不先走流式增量接口——外层已持有提交事务。
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
            run_lock_id=lock.lock_id if lock is not None else None,
            lock=lock,
            started=time.monotonic(),
            now=now,
            paper_search=projection.model_dump(mode="json"),
            final_content=content or None,
        )


# ---------------------------------------------------------------------------
# 产物读取与派生
# ---------------------------------------------------------------------------


def _analysis(artifact: NodeArtifact | None) -> PaperTermAnalysis:
    if artifact is None:
        return PaperTermAnalysis(
            original_phrase="", normalized_term="", confidence=0.0, final_query=""
        )
    return PaperTermAnalysis.model_validate(artifact.payload["analysis"])


def _plan(artifact: NodeArtifact | None, analysis: PaperTermAnalysis) -> PaperQueryPlan:
    if artifact is None:
        return plan_queries(analysis)[0]
    plans = artifact.payload.get("plans") or []
    if not plans:
        return plan_queries(analysis)[0]
    return PaperQueryPlan.model_validate(plans[0])


def _records(artifact: NodeArtifact | None) -> list[ModuleQueryRecord]:
    if artifact is None:
        return []
    return [
        ModuleQueryRecord.model_validate(item)
        for item in artifact.payload.get("records") or []
    ]


def _notes(result: KernelResult) -> list[str]:
    """汇总各节点如实声明的证据边界与未确认项（用户可见，不粉饰）。"""
    notes: list[str] = []
    for node in (
        NODE_SEARCH,
        NODE_SCREEN,
        NODE_READ,
        NODE_ENRICH,
        NODE_EVALUATE,
        NODE_VERIFY,
    ):
        artifact = result.artifact(node)
        if artifact is None:
            continue
        payload = artifact.payload
        for key in ("notes", "unconfirmed"):
            for item in payload.get(key) or ():
                text = (
                    str(item.get("message") or item.get("text") or "")
                    if isinstance(item, Mapping)
                    else str(item).strip()
                )
                if text:
                    notes.append(text)
    return list(dict.fromkeys(notes))


def _abstracts(artifact: NodeArtifact | None) -> dict[str, str]:
    if artifact is None:
        return {}
    return {
        str(item.get("arxiv_id")): str(item.get("abstract") or "")
        for item in artifact.payload.get("candidates") or []
    }


def _artifact_refs(result: KernelResult) -> dict[str, str]:
    return {artifact.node: artifact.artifact_id for artifact in result.artifacts}


def _as_stopped(result: KernelResult) -> KernelResult:
    return replace(result, status=KernelStatus.STOPPED)


def _expression_snapshot(policy: Mapping[str, Any] | None) -> ExpressionPolicy | None:
    if not policy:
        return None
    try:
        from bridges.chat.global_writing_policy import GlobalWritingPolicySnapshot

        return GlobalWritingPolicySnapshot.model_validate(policy)
    except Exception:  # noqa: BLE001 - 旧快照或非法快照回退安全基线
        return None


def _clarification_payload(analysis: PaperTermAnalysis) -> dict[str, object]:
    return pending_payload(analysis, ambiguous_term=_ambiguous_key(analysis))


def _ambiguous_key(analysis: PaperTermAnalysis) -> str | None:
    """恢复澄清时按歧义术语的候选语境判定（术语键取自原始短语）。"""
    lowered = analysis.original_phrase.lower()
    for term in AMBIGUOUS_TERMS:
        if any(alias.lower() in lowered for alias in term.aliases):
            return term.term
    return None


__all__ = [
    "NODE_ENRICH",
    "NODE_EVALUATE",
    "NODE_PARSE",
    "NODE_PLAN",
    "NODE_READ",
    "NODE_SCREEN",
    "NODE_SEARCH",
    "NODE_VERIFY",
    "PAPER_MODULE_ID",
    "PAPER_NODE_LABELS",
    "PAPER_RECIPE_ID",
    "PAPER_RECIPE_VERSION",
    "WAIT_KIND_CLARIFICATION",
    "WAIT_REASON_CLARIFICATION",
    "PaperModuleError",
    "PaperRunOutcome",
    "PaperSearchService",
    "PaperSupersededError",
]
