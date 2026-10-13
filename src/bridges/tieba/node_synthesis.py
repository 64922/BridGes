"""贴吧综合与最终证据核验节点。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from bridges.kernel.contracts import (
    ArtifactTrust,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    QualityVerdict,
    RecoveryPolicy,
)
from bridges.tieba.budget import TiebaBudget
from bridges.tieba.contracts import (
    TiebaEvidencePlan,
    TiebaOfficialCheck,
    TiebaQuestionAnalysis,
    TiebaResearchProjection,
)
from bridges.tieba.evidence_official import (
    detect_conflicts,
)
from bridges.tieba.evidence_projection import build_projection
from bridges.tieba.evidence_time import summarize
from bridges.tieba.evidence_verification import run_verification
from bridges.tieba.node_flow_support import (
    build_artifact,
    dependency_payload,
    read_analysis,
    read_checks,
    read_outcome,
    read_plan,
    read_search_attempts,
)
from bridges.tieba.official import (
    STATUS_VERIFIED,
)
from bridges.tieba.recipe import (
    NODE_SYNTHESIZE,
    NODE_VERIFY,
    OFFICIAL_UNVERIFIED_NOTE,
)
from bridges.tieba.summary import TiebaSummaryGenerator


def _official_unverified_note(
    analysis: TiebaQuestionAnalysis,
    plan: TiebaEvidencePlan,
    checks: Sequence[TiebaOfficialCheck],
) -> str | None:
    """规定相关内容没有可用且适用的官方证据时的固定说明。"""
    if not analysis.needs_official_check:
        return None
    if plan.official_blocked_reason:
        return "来源限制生效：规定相关内容未核实（未访问学校官方页面）。"
    applicable = [
        check
        for check in checks
        if check.status == STATUS_VERIFIED
        and check.applicability is not None
        and check.applicability.applicable
    ]
    if applicable:
        return None
    return OFFICIAL_UNVERIFIED_NOTE


class TiebaSynthesisNodes:
    """构造综合投影并运行代码级证据质量核验。"""

    def __init__(
        self,
        *,
        summary_generator: TiebaSummaryGenerator | None,
        budget: TiebaBudget | None,
        clock: Callable[[], datetime],
        run_context: Any,
        run_model_id: str | None,
        model_quota: Any,
    ) -> None:
        self._summary_generator = summary_generator
        self._budget = budget
        self._clock = clock
        self._run_context = run_context
        self._run_model_id = run_model_id
        self._model_quota = model_quota

    def run_synthesize(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = read_analysis(invocation)
        plan = read_plan(invocation)
        checks = read_checks(invocation)
        attempts = read_search_attempts(invocation)
        reads = read_outcome(invocation)
        time_filter, confirmed, sections = summarize(analysis, reads.confirmed)
        conflicts = detect_conflicts(analysis, checks, reads.confirmed)
        projection = build_projection(
            analysis=analysis,
            records=attempts.records,
            confirmed=confirmed,
            rejected=reads.rejected,
            unconfirmed=reads.unconfirmed,
            sections=sections,
            time_filter=time_filter,
            official_checks=list(checks),
            conflicts=conflicts,
            attempts=attempts,
            reads=reads,
            official_blocked_reason=plan.official_blocked_reason,
            plan_rationale=plan.rationale,
            source_priority=list(plan.source_priority),
            parallel_evidence=plan.parallel_evidence,
        )
        note = _official_unverified_note(analysis, plan, checks)
        if note is not None:
            projection = projection.model_copy(update={"official_unverified_note": note})
        if self._summary_generator is not None:
            summary = self._summary_generator.generate(
                self._run_context,
                confirmed,
                topic=analysis.original_question,
                time_requirement=analysis.time_requirement,
                model_id=self._run_model_id,
                model_quota=self._model_quota,
                budget=self._budget.model_budget if self._budget is not None else None,
            )
            projection = projection.model_copy(
                update={
                    "summary": summary.summary,
                    "summary_claims": summary.claims,
                    "summary_citations": summary.citations,
                    "summary_note": summary.note,
                }
            )
        else:
            projection = projection.model_copy(
                update={"summary_note": "总结未生成：当前运行没有可用的模型网关。"}
            )
        return NodeExecution(
            artifact=build_artifact(
                invocation,
                clock=self._clock,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={"projection": projection.model_dump(mode="json")},
                source_refs=[post.url for post in projection.confirmed_posts],
                read_scope="归纳：只使用实际读到的楼层与官方原文章节",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def run_verify(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = read_analysis(invocation)
        plan = read_plan(invocation)
        checks = read_checks(invocation)
        reads = read_outcome(invocation)
        projection = TiebaResearchProjection.model_validate(
            dependency_payload(invocation, NODE_SYNTHESIZE)["projection"]
        )
        verification = run_verification(analysis, plan, projection, checks, reads.confirmed)
        if not (
            verification.attribution_consistent
            and verification.read_scope_consistent
            and verification.unread_claims_absent
            and verification.consensus_claims_absent
            and verification.official_scope_consistent
            and verification.conflicts_disclosed
        ):
            return NodeExecution(
                artifact=build_artifact(
                    invocation,
                    clock=self._clock,
                    trust_state=ArtifactTrust.INVALIDATED,
                    payload={"projection": projection.model_dump(mode="json")},
                    error={
                        "code": "tieba_verification_failed",
                        "message": verification.summary,
                    },
                ),
                verdict=QualityVerdict.BLOCKED,
                status=NodeReceiptStatus.FAILED,
                detail={
                    "code": "tieba_verification_failed",
                    "message": verification.summary,
                    "retryable": False,
                    "node": NODE_VERIFY,
                },
                stop_recipe=True,
                recovery=RecoveryPolicy.BLOCK,
            )
        verified = projection.model_copy(update={"verification": verification})
        return NodeExecution(
            artifact=build_artifact(
                invocation,
                clock=self._clock,
                trust_state=ArtifactTrust.QUALIFIED,
                payload={
                    "projection": verified.model_dump(mode="json"),
                    "verification": verification.model_dump(mode="json"),
                },
                source_refs=[post.url for post in verified.confirmed_posts],
                read_scope="核验：归属、读取范围、官方适用范围与冲突披露",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )
