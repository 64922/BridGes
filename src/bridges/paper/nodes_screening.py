"""论文证据筛选、一次反馈补搜与引用扩展节点。"""

from __future__ import annotations

import inspect
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.kernel.contracts import (
    ArtifactTrust,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    QualityVerdict,
    RecoveryPolicy,
)
from bridges.paper.artifact_codec import _candidate, _record, _record_payload
from bridges.paper.candidate_codec import candidate_payload
from bridges.paper.identity import merge_candidates
from bridges.paper.planning import DEFAULT_TARGET_COUNT, plan_queries
from bridges.paper.recipe import DEFAULT_SCREEN_MAX, NODE_PARSE, NODE_SEARCH
from bridges.paper.relevance_cache import NodeRelevanceCache
from bridges.paper.screening import screen_candidates
from bridges.paper.source_types import CandidateSearchOutcome

if TYPE_CHECKING:
    from bridges.paper.kernel import PaperNodeFlow



def screen_node(self: PaperNodeFlow, invocation: NodeInvocation) -> NodeExecution:
    analysis = self._analysis(invocation, NODE_PARSE)
    candidates_payload = invocation.dependencies[NODE_SEARCH].payload.get("candidates") or []
    candidates = [_candidate(item) for item in candidates_payload]
    screen_max = self._budget.screen_max if self._budget else DEFAULT_SCREEN_MAX
    cap = max(1, screen_max)
    search_payload = invocation.dependencies[NODE_SEARCH].payload
    search_records = [_record(item) for item in search_payload.get("records") or ()]
    target = int(search_payload.get("target_count") or DEFAULT_TARGET_COUNT)
    judge = (
        NodeRelevanceCache(self._judge)
        if self._judge is not None and hasattr(self._judge, "judge_many") else self._judge
    )
    preliminary = screen_candidates(analysis, candidates[:cap], judge=judge)
    missing = tuple(
        concept.concept
        for concept in analysis.concepts
        if concept.concept
        not in {
            requirement
            for item in preliminary.screened
            for requirement in item.matched_requirements
        }
    )
    adjustment_notes: list[str] = []
    adjustment_records: list[ModuleQueryRecord] = []
    source_order = [
        item.source
        for item in search_records
        if item.status in {ModuleQueryStatus.SUCCESS, ModuleQueryStatus.EMPTY}
        and item.source in self._sources
    ]
    if (
        (len(preliminary.screened) < min(cap, max(20, target)) or missing)
        and not analysis.constraints.arxiv_id
        and not analysis.constraints.paper_title
        and source_order
        and (self._stop_event is None or not self._stop_event.is_set())
    ):
        source_name = "openalex" if "openalex" in source_order else source_order[0]
        if self._query_planner is None:
            followup = plan_queries(
                analysis, available_sources=(source_name,), missing_requirements=missing,
            )
        else:
            followup = self._query_planner.plan(
                analysis, available_sources=(source_name,), feedback={
                    "queries": [item.model_dump(mode="json") for item in search_records],
                    "candidates": [
                        {"candidate_id": item.candidate_id, "title": item.title,
                         "abstract": item.abstract[:1600]} for item in candidates[:cap]
                    ],
                    "filtered": [
                        {"candidate_id": item.candidate.candidate_id, "reason": item.reason}
                        for item in preliminary.excluded
                    ],
                    "semantic_evidence": [item.to_payload() for item in preliminary.screened],
                    "missing_requirements": list(missing),
                    "candidate_goal": min(cap, max(20, target)),
                },
            )
            if not followup:
                adjustment_notes.append("未取得可靠的新补搜查询，保留首轮候选与证据缺口。")
        prior_queries = {item.query.strip().casefold() for item in search_records}
        if followup and followup[0].query.strip().casefold() not in prior_queries:
            plan = followup[0]
            if self._budget is None or self._budget.begin_adjustment():
                key = f"paper.search.adjustment:{source_name}"
                budget = self._budget
                registered = budget is None or budget.register_external(
                    key, purpose="academic_search_adjustment"
                )
                if registered:
                    outcome_code = ModuleQueryStatus.ERROR.value
                    try:
                        source = self._sources[source_name]
                        kwargs: dict[str, Any] = {
                            "max_results": plan.max_results,
                            "stop_event": self._stop_event,
                            "deadline": time.monotonic() + self._attempt_seconds(),
                        }
                        if "year_from" in inspect.signature(source.search).parameters:
                            kwargs["year_from"] = analysis.constraints.year_from
                            kwargs["year_to"] = analysis.constraints.year_to
                        result = source.search(invocation.account_id, plan.query, **kwargs)
                        outcome_code = result.record.status.value
                    except Exception as error:  # noqa: BLE001 - 保留此前来源结果
                        result = CandidateSearchOutcome(
                            plan.query,
                            [],
                            ModuleQueryRecord(
                                source=source_name,
                                query=plan.query,
                                status=ModuleQueryStatus.ERROR,
                                retrieved_at=self._clock(),
                                error_code=f"{source_name}_adjustment_error",
                                error_message="针对缺失概念的补搜失败，保留首轮结果。",
                                retryable=True,
                                detail=type(error).__name__,
                            ),
                        )
                    finally:
                        if budget is not None and registered:
                            budget.release_external(key, outcome_code=outcome_code)
                            budget.end_adjustment(outcome_code)
                    search_records.append(result.record)
                    adjustment_records.append(result.record)
                    self._search_records.append(result.record)
                    candidates = merge_candidates([*candidates, *result.candidates])
                    if result.candidates:
                        adjustment_notes.append(
                            f"针对未覆盖概念“{'、'.join(missing[:3])}”补搜一次，保留了首轮候选。"
                        )
                    else:
                        adjustment_notes.append(
                            "针对缺失概念的补搜未返回新候选，保留首轮结果。"
                        )
                else:
                    if budget is not None:
                        budget.end_adjustment("budget_exhausted")
                    adjustment_notes.append("共享预算不足，未执行缺失概念补搜。")

    before_citation = screen_candidates(analysis, candidates)
    citation_edges: list[dict[str, Any]] = []
    citation_records: list[ModuleQueryRecord] = []
    citation_candidate_ids: set[str] = set()
    citation_source = self._sources.get("openalex")
    openalex_available = any(
        item.source == "openalex"
        and item.status in {ModuleQueryStatus.SUCCESS, ModuleQueryStatus.EMPTY}
        for item in search_records
    )
    if (
        citation_source is not None
        and openalex_available
        and "openalex" not in analysis.constraints.excluded_sources
        and (before_citation.screened or self._context.citation_seed)
        and hasattr(citation_source, "expand_citations")
        and (self._stop_event is None or not self._stop_event.is_set())
    ):
        seed = self._context.citation_seed
        if seed is not None:
            seeds = [seed] if seed.openalex_id else [
                item for item in merge_candidates([seed, *candidates])
                if item.candidate_id == seed.candidate_id and item.openalex_id
            ][:1]
            if not seeds:
                adjustment_notes.append("目标论文尚无可核验的 OpenAlex 身份，未执行引用扩展。")
        else:
            seeds = [item.candidate for item in before_citation.screened[:3]]
        try:
            expanded = citation_source.expand_citations(
                invocation.account_id,
                seeds,
                max_results=8,
                stop_event=self._stop_event,
                deadline=time.monotonic() + self._attempt_seconds(),
                budget=self._budget,
            )
            candidates = merge_candidates([*candidates, *expanded.candidates])
            citation_candidate_ids = {item.candidate_id for item in expanded.candidates}
            citation_edges = [item.model_dump(mode="json") for item in expanded.edges]
            citation_records = list(expanded.records)
            self._search_records.extend(citation_records)
        except Exception:  # noqa: BLE001 - 引用扩展是可选步骤
            adjustment_notes.append("OpenAlex 引用扩展失败，保留已取得候选。")

    reference_seed = self._context.citation_seed
    if reference_seed is not None:
        candidates = [
            item for item in candidates if item.candidate_id != reference_seed.candidate_id
        ]
    final_preliminary = screen_candidates(analysis, candidates)
    priority_ids = [item.candidate.candidate_id for item in final_preliminary.screened]
    prioritized = sorted(
        candidates,
        key=lambda item: (
            0
            if item.candidate_id in priority_ids
            and item.candidate_id not in citation_candidate_ids
            else 1
            if item.candidate_id in citation_candidate_ids
            else 2,
            item.candidate_id,
        ),
    )
    truncated = len(prioritized) > cap
    outcome = screen_candidates(analysis, prioritized[:cap], judge=judge)
    if outcome.topic_mismatch and self._intent_analyzer is not None:
        outcome = replace(
            outcome,
            topic_mismatch=False,
            notes=[
                *outcome.notes,
                "本轮取得的候选没有足够证据支持研究问题；已如实保留为空结果。",
            ],
        )
    retained_ids = {item.candidate.candidate_id for item in outcome.screened}
    citation_edges = [
        edge
        for edge in citation_edges
        if edge["citing_candidate_id"] in retained_ids
        and (edge["cited_candidate_id"] in retained_ids
             or reference_seed is not None
             and edge["cited_candidate_id"] == reference_seed.candidate_id)
    ]
    if truncated:
        outcome.notes.append(f"候选超过本轮初筛上限 {cap} 篇，仅对靠前候选做证据筛选。")
    outcome.notes.extend(adjustment_notes)
    payload = {
        "candidates": [candidate_payload(item) for item in prioritized[:cap]],
        "screened": [item.to_payload() for item in outcome.screened],
        "excluded": [
            {
                "candidate_id": item.candidate.candidate_id,
                "arxiv_id": item.candidate.arxiv_id,
                "title": item.candidate.title,
                "reason": item.reason,
            }
            for item in outcome.excluded
        ],
        "topic_mismatch": outcome.topic_mismatch,
        "hard_condition_blocked": outcome.hard_condition_blocked,
        "notes": list(outcome.notes),
        "judge_used": outcome.judge_used,
        "semantic_rejection_counts": dict(outcome.semantic_rejection_counts),
        "records": [_record_payload(item) for item in (*adjustment_records, *citation_records)],
        "citation_edges": citation_edges,
    }
    if not outcome.screened and any(
        item.reason.startswith("semantic_") for item in outcome.excluded
    ):
        return self._failure(
            invocation, payload=payload, code="paper_semantic_evaluation_unavailable",
            message="本轮候选未完成语义与证据核验，请重试；未交付未经核验的推荐。",
            retryable=True,
        )
    unconfirmed = ("检索结果没有覆盖原始术语的论文",) if outcome.topic_mismatch else ()
    if outcome.topic_mismatch:
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                payload=payload,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                requirement_coverage=(
                    {"requirement": analysis.original_phrase, "status": "mismatch"},
                ),
                unconfirmed=unconfirmed,
            ),
            verdict=QualityVerdict.NEED_INPUT,
            status=NodeReceiptStatus.NEEDS_INPUT,
            detail={
                "code": "paper_topic_mismatch",
                "message": "检索到的候选与原始主题不匹配。",
            },
            stop_recipe=True,
            recovery=RecoveryPolicy.ASK_INPUT,
        )
    return self._completed(invocation, payload)
