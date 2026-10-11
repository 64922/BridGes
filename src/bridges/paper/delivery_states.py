"""论文澄清、空结果、停止与错误投影准备。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState
from bridges.kernel.contracts import KernelResult
from bridges.paper.contracts import PaperQueryPlan, PaperSearchProjection, PaperSearchStatus
from bridges.paper.delivery_types import (
    PAPER_MODULE_ID,
    WAIT_KIND_CLARIFICATION,
    PaperModuleError,
    _PreparedDelivery,
)
from bridges.paper.parsing import parse_paper_request
from bridges.paper.planning import plan_queries
from bridges.paper.presenting import (
    render_clarification_content,
    render_empty_content,
    render_mismatch_content,
    render_stopped_content,
)
from bridges.paper.projection import (
    _analysis,
    _artifact_refs,
    _clarification_payload,
    _notes,
    _plan,
    _records,
)
from bridges.paper.recipe import NODE_ENRICH, NODE_PARSE, NODE_PLAN, NODE_SCREEN, NODE_SEARCH

if TYPE_CHECKING:
    from bridges.paper.service import PaperSearchService



def _prepare_empty(self: PaperSearchService, result: KernelResult) -> _PreparedDelivery:
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
            *_records(result.artifact(NODE_SCREEN)),
            *_records(result.artifact(NODE_ENRICH)),
        ],
        final_query=plan.query,
        research_question=analysis.research_question,
        selected_sources=list(dict.fromkeys(
            item.source for item in _records(result.artifact(NODE_SEARCH))
            if item.query and item.status.value != "skipped"
        )),
        condition_sources=analysis.condition_sources,
        requested_count=plan.target_count,
        artifacts=_artifact_refs(result),
        evidence_notes=notes,
        searched_at=now,
    )
    return _PreparedDelivery(
        projection=projection,
        content=render_empty_content(
            analysis, plan, notes, sources=projection.selected_sources
        ),
        message_status=ChatMessageStatus.DONE,
    )


def _prepare_clarification(
    self: PaperSearchService,
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
        condition_sources=analysis.condition_sources,
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
    self: PaperSearchService,
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
        condition_sources=analysis.condition_sources,
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


def _prepare_failure(
    self: PaperSearchService, result: KernelResult, *, original_request: str = ""
) -> _PreparedDelivery:
    failure = result.failure
    code = failure.code if failure is not None else "paper_search_failed"
    message = (
        failure.message if failure is not None else "论文检索失败，请稍后重试。"
    )
    retryable = failure.retryable if failure is not None else True
    node = failure.node if failure is not None else NODE_SEARCH
    projection = _failure_projection(self,
        result,
        code=code,
        message=message,
        retryable=retryable,
        original_request=original_request,
    )
    return _PreparedDelivery(
        projection=projection,
        content="",
        message_status=ChatMessageStatus.DONE,
        failure=PaperModuleError(node, code, message, retryable=retryable),
    )


def _failure_projection(
    self: PaperSearchService,
    result: KernelResult,
    *,
    code: str,
    message: str,
    retryable: bool,
    original_request: str = "",
) -> PaperSearchProjection:
    parse_artifact = result.artifact(NODE_PARSE)
    analysis = _analysis(parse_artifact)
    plan_artifact = result.artifact(NODE_PLAN)
    plan = _plan(plan_artifact, analysis) if plan_artifact is not None else None
    records = [
        *_records(result.artifact(NODE_SEARCH)),
        *_records(result.artifact(NODE_SCREEN)),
        *_records(result.artifact(NODE_ENRICH)),
    ]
    return PaperSearchProjection(
        status=PaperSearchStatus.ERROR,
        original_phrase=analysis.original_phrase or original_request,
        normalized_term=analysis.normalized_term,
        expansions=list(analysis.expansions),
        confidence=analysis.confidence,
        context_label=analysis.context_label,
        final_query=plan.query if plan is not None else "",
        queries=records,
        condition_sources=analysis.condition_sources,
        artifacts=_artifact_refs(result),
        searched_at=self._clock(),
        error_code=code,
        error_message=message,
        retryable=retryable,
    )
