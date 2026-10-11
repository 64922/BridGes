"""论文模块共享执行内核编排与提交守卫。"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bridges.ai.model_quota import RunModelQuota
from bridges.ai.payload_budget import CallMaterialManifest
from bridges.contracts.workflows import RunContextEnvelope
from bridges.kernel.contracts import RecipeInputs
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository
from bridges.paper.continuation import resolve_paper_continuation
from bridges.paper.contracts import PaperRecommendation
from bridges.paper.delivery_commit import _deliver, _package_delivery
from bridges.paper.delivery_success import _build_delivery
from bridges.paper.delivery_types import ENRICH_DEADLINE_SECONDS, SEARCH_DEADLINE_SECONDS
from bridges.paper.delivery_types import PAPER_MODULE_ID as PAPER_MODULE_ID
from bridges.paper.delivery_types import WAIT_KIND_CLARIFICATION as WAIT_KIND_CLARIFICATION
from bridges.paper.delivery_types import WAIT_REASON_CLARIFICATION as WAIT_REASON_CLARIFICATION
from bridges.paper.delivery_types import PaperModuleError as PaperModuleError
from bridges.paper.delivery_types import PaperRunOutcome as PaperRunOutcome
from bridges.paper.delivery_types import PaperSupersededError as PaperSupersededError
from bridges.paper.enrichment import MetadataEnricher
from bridges.paper.flow_context import PaperFlowContext
from bridges.paper.kernel import PaperNodeFlow
from bridges.paper.presenting import PaperSummaryGenerator
from bridges.paper.projection import _as_stopped
from bridges.paper.reading import FullTextReader, ReadCoordinator
from bridges.paper.recipe import NODE_ENRICH as NODE_ENRICH
from bridges.paper.recipe import NODE_EVALUATE as NODE_EVALUATE
from bridges.paper.recipe import NODE_PARSE as NODE_PARSE
from bridges.paper.recipe import NODE_PLAN as NODE_PLAN
from bridges.paper.recipe import NODE_READ as NODE_READ
from bridges.paper.recipe import NODE_SCREEN as NODE_SCREEN
from bridges.paper.recipe import NODE_SEARCH as NODE_SEARCH
from bridges.paper.recipe import NODE_VERIFY as NODE_VERIFY
from bridges.paper.recipe import PAPER_GATE_HANDLERS, build_paper_recipe, paper_recipe_registry
from bridges.paper.recipe import PAPER_NODE_LABELS as PAPER_NODE_LABELS
from bridges.paper.recipe import PAPER_RECIPE_ID as PAPER_RECIPE_ID
from bridges.paper.recipe import PAPER_RECIPE_VERSION as PAPER_RECIPE_VERSION
from bridges.paper.runtime import (
    _current_task_ref,
    _load_budget,
    _pending_wait,
    _prior_user_messages,
    _resolve_assessors,
    _resolve_intent_analyzer,
    _wait_identity,
)
from bridges.paper.screening import BatchPaperRelevanceJudge, PaperRelevanceJudge
from bridges.paper.sources import ArxivPaperSource

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.task_materials import ModuleTaskContext



class PaperSearchService:
    """论文模块编排服务（由日常父图在显式派发时调用）。"""

    def __init__(
        self,
        *,
        source: ArxivPaperSource,
        sources: Mapping[str, Any] | None = None,
        enricher: MetadataEnricher | None = None,
        summarizer: PaperSummaryGenerator | None = None,
        deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        enrich_deadline_seconds: float = ENRICH_DEADLINE_SECONDS,
        task_version_provider: Callable[
            [str, str], tuple[str | None, int | None] | None
        ]
        | None = None,
        reader: FullTextReader | None = None,
        judge: PaperRelevanceJudge | BatchPaperRelevanceJudge | None = None,
        reviewer: Callable[
            [list[PaperRecommendation], list[str]], Mapping[str, Any] | None
        ]
        | None = None,
        gateway: Any | None = None,
        intent_analyzer_factory: Callable[..., Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = source
        self._sources = dict(sources or {"arxiv": source})
        self._enricher = enricher
        self._summarizer = summarizer
        self._deadline_seconds = deadline_seconds
        self._enrich_deadline_seconds = enrich_deadline_seconds
        self._task_version_provider = task_version_provider
        self._reader = reader
        self._judge = judge
        self._reviewer = reviewer
        #: 已登记的结构化模型网关：装配生产 judge/reviewer（共享运行额度、
        #: 载荷预算与审计）；未装配时按注入接缝或如实标注未复核处理。
        self._gateway = gateway
        self._intent_analyzer_factory = intent_analyzer_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._registry = paper_recipe_registry()
        self._recipe = build_paper_recipe()

    def close(self) -> None:
        closed: set[int] = set()
        for source in self._sources.values():
            close = getattr(source, "close", None)
            if callable(close) and id(source) not in closed:
                close()
                closed.add(id(source))
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
        assessment_manifest_sink: Callable[[CallMaterialManifest], None] | None = None,
        writing_policy: Mapping[str, Any] | None = None,
        defer_finalization: bool = False,
    ) -> PaperRunOutcome:
        """执行一轮论文模块；终态（完成/澄清/空/失败/停止）全部写回同一消息。

        ``defer_finalization``（工单 37）为真时不写消息终态，只返回
        :class:`~bridges.contracts.modules.ModuleDelivery` 交给复合调度器统一核验
        与提交；失败也作为交付返回（迟到/失效仍抛 ``PaperSupersededError``）。
        """
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise PaperModuleError(
                NODE_PARSE,
                "message_not_found",
                "消息不存在或没有访问权限。",
                retryable=False,
            )
        pending = _pending_wait(self, repo, account_id, conversation_id)
        task_scope_used = bool(
            module_context is not None and module_context.used_task_scope
        )
        if module_context is None:
            prior = _prior_user_messages(self,
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
        run_snapshot = repo.get_generation_run(account_id, run_context.run_id)
        continuation = resolve_paper_continuation(
            repo.list_messages(account_id, conversation_id),
            user_message_id=user_message_id, request=user_message.content,
        )
        context = PaperFlowContext(
            citation_seed=continuation.seed,
            continuation_error=continuation.error,
            prior_context=tuple(prior),
            topic_hint=topic_hint,
            task_conditions=task_conditions,
            task_scope_used=task_scope_used,
            reference_time=run_snapshot.created_at if run_snapshot is not None else self._clock(),
        )
        run_id = run_context.run_id
        budget = _load_budget(self, repo, account_id, run_id)
        judge, reviewer = _resolve_assessors(self,
            repo,
            account_id=account_id,
            run_id=run_id,
            run_context=run_context,
            run_model_id=run_model_id,
            model_quota=model_quota,
            assessment_manifest_sink=(
                assessment_manifest_sink or manifest_sink
            ),
        )
        intent_analyzer = _resolve_intent_analyzer(self,
            repo,
            account_id=account_id,
            run_id=run_id,
            run_context=run_context,
            run_model_id=run_model_id,
            model_quota=model_quota,
            manifest_sink=manifest_sink,
        )
        task_ref = _current_task_ref(self, account_id, conversation_id)
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
            sources=self._sources,
            intent_analyzer=intent_analyzer,
            enricher=self._enricher,
            reader=ReadCoordinator(
                self._reader,
                deep_read_max=(budget.deep_read_max if budget is not None else 3),
                deadline_seconds=self._enrich_deadline_seconds,
            ),
            judge=judge,
            reviewer=reviewer,
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
                wait_identity=_wait_identity(self, pending),
                artifacts={},
                prior_digest=flow.prior_digest,
            ),
            remaining_budget_ms=(
                budget.remaining_work_ms() if budget is not None else None
            ),
            event_sink=emit_node,
            stop_event=stop_event,
        )
        prepared = _build_delivery(self,
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
                prepared = _build_delivery(self,
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
            if defer_finalization:
                return _package_delivery(self, prepared=prepared, result=result)
            outcome = _deliver(self,
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








    # -- 事务内写入 ------------------------------------------------------



    # -- 概述调用（工单 21 表达策略 + 工单 04 最终预算） ------------------




    # -- 恢复与等待 ------------------------------------------------------






    # -- 落库 ------------------------------------------------------------
