"""论文请求理解与来源查询规划节点。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bridges.kernel.contracts import (
    ArtifactTrust,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    QualityVerdict,
    RecoveryPolicy,
)
from bridges.paper.parsing import parse_paper_request
from bridges.paper.planning import plan_queries
from bridges.paper.recipe import DEFAULT_DEEP_READ_MAX, DEFAULT_SCREEN_MAX, NODE_PARSE

if TYPE_CHECKING:
    from bridges.paper.kernel import PaperNodeFlow



def parse_node(self: PaperNodeFlow, invocation: NodeInvocation) -> NodeExecution:
    if self._context.continuation_error:
        return self._failure(
            invocation, payload={"analysis": None}, code="paper_reference_unavailable",
            message=self._context.continuation_error, retryable=False,
        )
    conditions = list(self._context.task_conditions) if self._context.task_scope_used else None
    now = self._context.reference_time or self._clock()
    if self._intent_analyzer is None:
        analysis = parse_paper_request(
            invocation.inputs.user_content,
            prior_context=list(self._context.prior_context),
            pending=self._pending,
            task_topic_hint=self._context.topic_hint,
            task_conditions=conditions,
            now=now,
        )
    else:
        extra = (
            {"citation_seed": self._context.citation_seed} if self._context.citation_seed else {}
        )
        analysis = self._intent_analyzer.analyze(
            invocation.inputs.user_content,
            prior_context=self._context.prior_context,
            topic_hint=self._context.topic_hint,
            task_conditions=conditions or (),
            pending=self._pending,
            now=now,
            **extra,
        )
        if analysis is None:
            return self._failure(
                invocation,
                payload={
                    "analysis": None,
                    "original_request": invocation.inputs.user_content,
                },
                code="paper_intent_analysis_failed",
                message="未能可靠理解这条论文请求；本轮没有发起外部检索，请重试。",
                retryable=True,
            )
    unavailable = list(analysis.constraints.unsupported_sources)
    unavailable.extend(
        source for source in analysis.constraints.allowed_sources if source not in self._sources
    )
    if unavailable:
        names = "、".join(dict.fromkeys(unavailable))
        return self._failure(
            invocation,
            payload={
                "analysis": analysis.model_dump(mode="json"),
                "original_request": invocation.inputs.user_content,
            },
            code="paper_source_unavailable",
            message=f"请求限定的来源目前不可用：{names}。请改用本轮已接入的来源后重试。",
            retryable=False,
        )
    payload = {
        "analysis": analysis.model_dump(mode="json"),
        "clarification": (
            analysis.clarification.model_dump(mode="json")
            if analysis.clarification is not None
            else None
        ),
        "topic": analysis.original_phrase,
        "domain": analysis.context_key,
        "reading_goal": self._reading_goal(invocation.inputs.user_content),
        "hard_conditions": [
            {"kind": getattr(item, "kind", None), "text": getattr(item, "text", "")}
            for item in self._context.task_conditions
        ],
        "gaps": [analysis.clarification.missing] if analysis.clarification else [],
    }
    if analysis.clarification is not None:
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                payload=payload,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                requirement_coverage=({"requirement": "topic", "status": "needs_input"},),
                unconfirmed=("领域/主题歧义待用户确认",),
            ),
            verdict=QualityVerdict.NEED_INPUT,
            status=NodeReceiptStatus.NEEDS_INPUT,
            detail={"code": "paper_clarification", "question": analysis.clarification.question},
            stop_recipe=True,
            recovery=RecoveryPolicy.ASK_INPUT,
        )
    return self._completed(invocation, payload)


def plan_node(self: PaperNodeFlow, invocation: NodeInvocation) -> NodeExecution:
    analysis = self._analysis(invocation, NODE_PARSE)
    if self._query_planner is None:
        plans = plan_queries(analysis, available_sources=tuple(self._sources))
    else:
        plans = self._query_planner.plan(analysis, available_sources=tuple(self._sources))
        if not plans:
            return self._failure(
                invocation, payload={"plans": []},
                code="paper_query_planning_failed",
                message="未能可靠规划这条论文请求的来源查询；本轮没有发起检索，请重试。",
                retryable=True,
            )
    if not plans:
        return self._failure(
            invocation,
            payload={"plans": [], "target_count": 0},
            code="paper_source_unavailable",
            message="当前来源设置下没有可执行的论文来源。",
            retryable=False,
        )
    plan = plans[0]
    payload = {
        "plans": [item.model_dump(mode="json") for item in plans],
        "target_count": plan.target_count,
        "screen_max": self._budget.screen_max if self._budget else DEFAULT_SCREEN_MAX,
        "read_max": (self._budget.deep_read_max if self._budget else DEFAULT_DEEP_READ_MAX),
        "year_from": analysis.constraints.year_from,
        "year_to": analysis.constraints.year_to,
        "prefer_survey": analysis.constraints.prefer_survey,
        "sort_intent": analysis.constraints.sort_intent.value,
        "arxiv_id": analysis.constraints.arxiv_id,
        "paper_title": analysis.constraints.paper_title,
        "allowed_sources": list(analysis.constraints.allowed_sources),
        "excluded_sources": list(analysis.constraints.excluded_sources),
    }
    return self._completed(invocation, payload)
