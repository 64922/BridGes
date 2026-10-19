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
from bridges.tieba.browser_structured import BROWSER_PROTOCOL_VERSION
from bridges.tieba.budget import TiebaBudget
from bridges.tieba.contracts import (
    TiebaEvidencePlan,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaResearchProjection,
    TiebaSearchScope,
    TiebaTimeFilter,
)
from bridges.tieba.evidence import ReadOutcome
from bridges.tieba.evidence_official import (
    detect_conflicts,
)
from bridges.tieba.evidence_projection import _search_scope_notes, build_projection
from bridges.tieba.evidence_search import SearchAttempts
from bridges.tieba.evidence_time import summarize
from bridges.tieba.evidence_verification import run_verification
from bridges.tieba.execution_metrics import TiebaExecutionMetrics
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
    NODE_VERIFY_OFFICIAL,
    OFFICIAL_UNVERIFIED_NOTE,
    TIEBA_CAPABILITY_VERSIONS,
    TIEBA_RECIPE_VERSION,
)
from bridges.tieba.run_metadata import (
    TiebaBrowserCleanup,
    TiebaRequestBreakdown,
    TiebaRunMetadata,
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


def _run_metadata(
    invocation: NodeInvocation,
    *,
    attempts: SearchAttempts,
    reads: ReadOutcome,
    checks: Sequence[TiebaOfficialCheck],
    confirmed: Sequence[TiebaPostProjection],
    time_filter: TiebaTimeFilter,
    started_at: datetime,
    finished_at: datetime,
    model_calls: int,
    official_closeout_reason: str = "completed",
    stage_timings_ms: dict[str, int] | None = None,
    remaining_work_ms: int | None = None,
    request_breakdown: Sequence[TiebaRequestBreakdown] = (),
    browser_cleanup: TiebaBrowserCleanup | None = None,
) -> TiebaRunMetadata:
    search_count = len(attempts.records)
    scope = getattr(attempts, "scope", ())
    read_count = len(reads.attempted_urls)
    diagnostics = getattr(attempts, "diagnostics", None)
    official_count = len(checks)
    elapsed_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    scope_reasons: dict[str, int] = {}
    for post in confirmed:
        for reason, count in post.scope_excluded_reasons.items():
            scope_reasons[reason] = scope_reasons.get(reason, 0) + count
    exclusion_reasons = dict(time_filter.exclusion_reasons)
    for reason, count in scope_reasons.items():
        exclusion_reasons[reason] = exclusion_reasons.get(reason, 0) + count
    return TiebaRunMetadata(
        run_id=invocation.run_id,
        recipe_version=TIEBA_RECIPE_VERSION,
        component_versions={
            "browser_protocol": BROWSER_PROTOCOL_VERSION,
            **TIEBA_CAPABILITY_VERSIONS,
        },
        started_at=started_at,
        completed_at=finished_at,
        elapsed_ms=elapsed_ms,
        request_count=search_count + read_count + official_count,
        search_request_count=search_count,
        search_page_count=sum(item.pages_read for item in scope),
        read_request_count=read_count,
        official_request_count=official_count,
        model_call_count=model_calls,
        candidate_count=getattr(diagnostics, "raw_hits", 0),
        usable_candidate_count=getattr(diagnostics, "usable", 0),
        read_post_count=read_count,
        confirmed_post_count=len(confirmed),
        read_reply_count=sum(len(post.replies) for post in confirmed),
        qualified_evidence_count=time_filter.eligible_count,
        context_only_evidence_count=time_filter.context_only_count,
        excluded_evidence_count=(
            time_filter.excluded_count + sum(scope_reasons.values())
        ),
        exclusion_reasons=exclusion_reasons,
        source_urls=[post.url for post in confirmed],
        unread_scope=_unread_scope(confirmed, scope),
        first_pass_post_count=int(
            getattr(reads, "first_pass_read_count", 0) or 0
        ),
        deepened_post_count=int(getattr(reads, "deepened_post_count", 0) or 0),
        physical_request_count=sum(item.count for item in request_breakdown),
        request_breakdown=list(request_breakdown),
        stage_timings_ms=dict(stage_timings_ms or {}),
        remaining_work_ms=remaining_work_ms,
        closeout_reasons={
            "search": getattr(attempts, "closeout_reason", "completed"),
            "read": getattr(reads, "closeout_reason", "completed"),
            "official": official_closeout_reason,
        },
        browser_cleanup=browser_cleanup,
    )


def _unread_scope(
    confirmed: Sequence[TiebaPostProjection],
    scope: Sequence[TiebaSearchScope] = (),
) -> list[str]:
    notes = _search_scope_notes(scope)
    if not confirmed:
        notes.append("本轮没有确认任何帖子，未读范围覆盖全部候选帖链。")
        return notes
    for post in confirmed:
        pages_complete = post.pages_complete or (
            post.total_pages is not None and post.pages_read >= post.total_pages
        )
        if not pages_complete:
            declared = (
                f"，页面声明共 {post.total_pages} 页"
                if post.total_pages is not None
                else ""
            )
            span = (
                f"第 1–{post.pages_read} 页"
                if post.pages_read > 1
                else f"第 {post.pages_read} 页"
            )
            detail = (
                f"；{post.pagination_error_message}"
                if post.pagination_error_message
                else ""
            )
            unread = f"只读取{span}{declared}{detail}。"
            if post.page_evidence:
                success = "、".join(str(item.page) for item in post.page_evidence)
                notes.append(f"{post.url}：已成功读取第 {success} 页；{unread}")
            else:
                notes.append(f"{post.url}：{unread}")
        if post.nested_incomplete_count:
            detail = (
                f"；{post.nested_error_message}"
                if post.nested_error_message
                else ""
            )
            notes.append(
                f"{post.url}：有 {post.nested_incomplete_count} 个父楼层的楼中楼"
                f"未确认读完{detail}。"
            )
        if post.total_replies is not None:
            notes.append(
                f"{post.url}：页面声明 {post.total_replies} 条回复，"
                f"本轮记录 {len(post.replies)} 条楼层（含楼中楼）。"
            )
        if not post.replies_obtained:
            notes.append(f"{post.url}：未取得回复内容。")
    return notes


class TiebaSynthesisNodes:
    """构造综合投影并运行代码级证据质量核验。"""

    def __init__(
        self,
        *,
        summary_generator: TiebaSummaryGenerator | None,
        budget: TiebaBudget | None,
        metrics: TiebaExecutionMetrics,
        clock: Callable[[], datetime],
        run_context: Any,
        run_model_id: str | None,
        model_quota: Any,
        run_started_at: datetime | None = None,
        summary_call_count: Callable[[], int] | None = None,
    ) -> None:
        self._summary_generator = summary_generator
        self._budget = budget
        self._metrics = metrics
        self._clock = clock
        self._run_context = run_context
        self._run_model_id = run_model_id
        self._model_quota = model_quota
        self._run_started_at = run_started_at
        self._summary_call_count = summary_call_count

    def run_synthesize(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = read_analysis(invocation)
        plan = read_plan(invocation)
        checks = read_checks(invocation)
        attempts = read_search_attempts(invocation)
        reads = read_outcome(invocation)
        official_payload = dependency_payload(invocation, NODE_VERIFY_OFFICIAL)
        official_closeout = str(
            official_payload.get("closeout_reason") or "completed"
        )
        time_filter, confirmed, summary_posts, sections = summarize(
            analysis, reads.confirmed
        )
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
            query_rounds=plan.query_rounds,
            source_priority=list(plan.source_priority),
            parallel_evidence=plan.parallel_evidence,
        )
        note = _official_unverified_note(analysis, plan, checks)
        if note is not None:
            projection = projection.model_copy(update={"official_unverified_note": note})
        if self._summary_generator is not None:
            summary = self._summary_generator.generate(
                self._run_context,
                summary_posts,
                topic=analysis.original_question,
                time_requirement=analysis.time_requirement,
                time_filter=time_filter,
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
        finished_at = self._clock()
        projection = projection.model_copy(
            update={
                "run_metadata": _run_metadata(
                    invocation,
                    attempts=attempts,
                    reads=reads,
                    checks=checks,
                    confirmed=reads.confirmed,
                    time_filter=time_filter,
                    started_at=self._run_started_at or finished_at,
                    finished_at=finished_at,
                    model_calls=(
                        self._summary_call_count() if self._summary_call_count else 0
                    ),
                    official_closeout_reason=official_closeout,
                    stage_timings_ms=self._metrics.stage_timings_ms(),
                    remaining_work_ms=(
                        self._budget.remaining_work_ms()
                        if self._budget is not None
                        else None
                    ),
                    request_breakdown=(
                        self._budget.request_breakdown()
                        if self._budget is not None
                        else ()
                    ),
                    browser_cleanup=(
                        self._budget.browser_cleanup_summary()
                        if self._budget is not None
                        else None
                    ),
                )
            }
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
