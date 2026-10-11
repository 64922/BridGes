"""论文成功结果与各类终态的交付准备入口。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from bridges.ai.model_quota import RunModelQuota
from bridges.ai.payload_budget import CallMaterialManifest
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord
from bridges.contracts.workflows import RunContextEnvelope
from bridges.kernel.contracts import KernelResult, KernelStatus, NodeArtifact
from bridges.paper.contracts import (
    PaperCandidateProjection,
    PaperCitationEdge,
    PaperIdentity,
    PaperRecommendation,
    PaperSearchProjection,
    PaperSearchStatus,
)
from bridges.paper.delivery_commit import _summarize
from bridges.paper.delivery_states import (
    _failure_projection,
    _prepare_clarification,
    _prepare_empty,
    _prepare_failure,
    _prepare_stopped,
)
from bridges.paper.delivery_types import PaperModuleError, PaperSupersededError, _PreparedDelivery
from bridges.paper.presenting import render_result_content
from bridges.paper.projection import _abstracts, _analysis, _artifact_refs, _notes, _plan, _records
from bridges.paper.ranking import cover_original_phrase
from bridges.paper.recipe import (
    NODE_ENRICH,
    NODE_EVALUATE,
    NODE_PARSE,
    NODE_PLAN,
    NODE_SCREEN,
    NODE_SEARCH,
    NODE_VERIFY,
)

if TYPE_CHECKING:
    from bridges.paper.service import PaperSearchService



def _build_delivery(
    self: PaperSearchService,
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
            return _prepare_success(self,
                result,
                evaluate=evaluate,
                run_context=run_context,
                run_model_id=run_model_id,
                model_quota=model_quota,
                manifest_sink=manifest_sink,
                writing_policy=writing_policy,
            )
        if outcome == "topic_mismatch":
            return _prepare_clarification(self,
                result,
                assistant_message_id=assistant_message_id,
                mismatch=True,
            )
        return _prepare_empty(self, result)
    if result.status is KernelStatus.NEEDS_INPUT:
        screen = result.artifact(NODE_SCREEN)
        return _prepare_clarification(self,
            result,
            assistant_message_id=assistant_message_id,
            mismatch=bool(
                screen is not None and screen.payload.get("topic_mismatch")
            ),
        )
    if result.status in {KernelStatus.STOPPED, KernelStatus.INVALIDATED}:
        return _prepare_stopped(self,
            result, user_message=user_message, records=stopped_records
        )
    return _prepare_failure(self, result, original_request=user_message.content)


def _prepare_success(
    self: PaperSearchService,
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
        projection = _failure_projection(self,
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
    search_records = [
        *_records(result.artifact(NODE_SEARCH)),
        *_records(result.artifact(NODE_SCREEN)),
    ]
    enrich_records = _records(result.artifact(NODE_ENRICH))
    notes = _notes(result)
    abstracts = _abstracts(result.artifact(NODE_SEARCH))
    summary_outcome, policy_version = _summarize(self,
        recommendations,
        abstracts,
        run_context=run_context,
        run_model_id=run_model_id,
        model_quota=model_quota,
        manifest_sink=manifest_sink,
        writing_policy=writing_policy,
    )
    lock = summary_outcome.lock if summary_outcome is not None else None
    if summary_outcome is not None and summary_outcome.summaries:
        # 概述绑定逐字来源证据后才写入交付产物；没有证据的条目已在
        # 生成器内丢弃，不把模型自由文本直接当事实。
        for paper in recommendations:
            paper_key = paper.arxiv_id or paper.candidate_id
            text = summary_outcome.summaries.get(paper_key)
            if text:
                paper.summary_zh = text
                paper.summary_evidence = summary_outcome.evidence.get(paper_key)
    else:
        notes.append(
            (summary_outcome.note if summary_outcome is not None else None)
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
        candidates=[
            PaperCandidateProjection.model_validate(item)
            for item in evaluate.payload.get("candidates") or ()
        ],
        citation_edges=[
            PaperCitationEdge.model_validate(item)
            for item in evaluate.payload.get("citation_edges") or ()
        ],
        research_question=analysis.research_question,
        selected_sources=list(dict.fromkeys(
            item.source
            for item in search_records
            if item.query and item.status.value != "skipped"
        )),
        condition_sources=analysis.condition_sources,
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
