"""贴吧节点内核：七个持久节点、质量门、恢复续作与共享预算。

节点顺序 ``tieba.parse → tieba.plan → tieba.verify_official → tieba.search
→ tieba.read → tieba.synthesize → tieba.verify``。规定类先核对官方页面再补
贴吧经历；体验类跳过官方核验；混合类两路独立取证、共享同一运行预算。读取
只认「真的读到帖子页面且页面自身声明目标贴吧」这一种归属确认。

质量门由代码而非模型执行：``tieba.verify`` 的产物只有在归属、读取范围、
官方适用范围与冲突披露都通过时才可信；``tieba.verify`` 之前的节点产物都
是证据候选，不是结论。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bridges.kernel.contracts import (
    ArtifactTrust,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    QualityVerdict,
    RecoveryPolicy,
)
from bridges.tieba.budget import (
    BudgetedOfficialReader,
    BudgetedReader,
    BudgetedSearchPort,
    TiebaBudget,
)
from bridges.tieba.evidence import READ_DEADLINE_SECONDS, read_candidates
from bridges.tieba.evidence_official import (
    OFFICIAL_DEADLINE_SECONDS,
    verify_official,
)
from bridges.tieba.evidence_search import (
    SEARCH_DEADLINE_SECONDS,
    search_candidates,
)
from bridges.tieba.evidence_verification import build_evidence_plan
from bridges.tieba.execution_metrics import TiebaExecutionMetrics
from bridges.tieba.lexicon import TARGET_FORUM_NAME
from bridges.tieba.node_flow_resume import resume_payload
from bridges.tieba.node_flow_support import (
    build_artifact,
    failure_execution,
    read_analysis,
    read_plan,
    read_search_attempts,
)
from bridges.tieba.node_synthesis import TiebaSynthesisNodes
from bridges.tieba.official import (
    TiebaOfficialReader,
)
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.reading import TiebaThreadReader
from bridges.tieba.recipe import (
    NODE_PARSE,
    NODE_PLAN,
    NODE_READ,
    NODE_SEARCH,
    NODE_SYNTHESIZE,
    NODE_VERIFY,
    NODE_VERIFY_OFFICIAL,
    TIEBA_ONLY_OFFICIAL_BLOCKED,
    content_digest,
)
from bridges.tieba.searching import (
    TiebaSearchPort,
)
from bridges.tieba.summary import TiebaSummaryGenerator

__all__ = ["TiebaNodeFlow", "TIEBA_ONLY_OFFICIAL_BLOCKED"]

if TYPE_CHECKING:
    from bridges.chat.task_materials import ModuleTaskContext


#: 各节点能力版本（产物兼容性与收据键的一部分）。


#: 只查贴吧时官方路径的阻断原因（来源限制生效）。

#: 官方证据缺失或不适用的固定说明（规定类必须出现）。

#: 自己生成文本里禁止出现的共识式表述（引文保真，不替吧友下结论）。


# ---------------------------------------------------------------------------
# 共享预算视图（工单 09 账本；缺失账本时按无预算模式运行）
# ---------------------------------------------------------------------------


class TiebaNodeFlow:
    """贴吧节点执行体：模型只负责有证据引用的总结，其他结论由代码核验。"""

    def __init__(
        self,
        *,
        search: TiebaSearchPort,
        reader: TiebaThreadReader,
        official_reader: TiebaOfficialReader | None = None,
        clock: Callable[[], datetime] | None = None,
        module_context: ModuleTaskContext | None = None,
        pending_wait: Any = None,
        official_blocked_reason: str | None = None,
        budget: TiebaBudget | None = None,
        stop_event: threading.Event | None = None,
        search_deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        read_deadline_seconds: float = READ_DEADLINE_SECONDS,
        official_deadline_seconds: float = OFFICIAL_DEADLINE_SECONDS,
        repository: Any = None,
        summary_generator: TiebaSummaryGenerator | None = None,
        run_context: Any = None,
        run_model_id: str | None = None,
        model_quota: Any = None,
        run_started_at: datetime | None = None,
        summary_call_count: Callable[[], int] | None = None,
    ) -> None:
        if budget is not None:
            search = BudgetedSearchPort(search, budget, purpose="tieba.search")
            reader = BudgetedReader(reader, budget)
            if official_reader is not None:
                official_reader = BudgetedOfficialReader(official_reader, budget)
        self._budget = budget
        self._metrics = budget.metrics if budget is not None else TiebaExecutionMetrics()
        self._search = search
        self._reader = reader
        self._official_reader = official_reader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._module_context = module_context
        self._pending_wait = pending_wait
        self._official_blocked_reason = official_blocked_reason
        self._stop_event = stop_event
        self._search_deadline_seconds = search_deadline_seconds
        self._read_deadline_seconds = read_deadline_seconds
        self._official_deadline_seconds = official_deadline_seconds
        self._repository = repository
        self._synthesis_nodes = TiebaSynthesisNodes(
            summary_generator=summary_generator,
            budget=budget,
            metrics=self._metrics,
            clock=self._clock,
            run_context=run_context,
            run_model_id=run_model_id,
            model_quota=model_quota,
            run_started_at=run_started_at,
            summary_call_count=summary_call_count,
        )

    @property
    def prior_digest(self) -> str | None:
        if self._module_context is None or not self._module_context.used_task_scope:
            return None
        return content_digest(
            [
                (condition.condition_id, condition.kind, condition.text)
                for condition in self._module_context.effective_conditions
            ]
        )

    def run_node(self, invocation: NodeInvocation) -> NodeExecution:
        handler = {
            NODE_PARSE: self._run_parse,
            NODE_PLAN: self._run_plan,
            NODE_VERIFY_OFFICIAL: self._run_verify_official,
            NODE_SEARCH: self._run_search,
            NODE_READ: self._run_read,
            NODE_SYNTHESIZE: self._synthesis_nodes.run_synthesize,
            NODE_VERIFY: self._synthesis_nodes.run_verify,
        }[invocation.spec.name]
        started = time.monotonic()
        try:
            return handler(invocation)
        except Exception as exc:  # noqa: BLE001 - 未预期异常按可重试失败收敛
            return failure_execution(
                invocation,
                clock=self._clock,
                payload={"error": {"type": exc.__class__.__name__}},
                node=invocation.spec.name,
                verdict=QualityVerdict.REPAIRABLE_FAILURE,
                status=NodeReceiptStatus.FAILED,
                code="tieba_node_error",
                message=f"节点执行出现内部错误（{exc.__class__.__name__}）。",
                retryable=True,
            )
        finally:
            self._metrics.record_stage(
                invocation.spec.name, int((time.monotonic() - started) * 1000)
            )

    def _stage_seconds(self, invocation: NodeInvocation, seconds: float) -> float:
        remaining = seconds
        if invocation.remaining_budget_ms is not None:
            remaining = min(remaining, invocation.remaining_budget_ms / 1000.0)
        if self._budget is not None:
            remaining = min(remaining, self._budget.remaining_work_ms() / 1000.0)
        return max(0.0, remaining)

    def _run_parse(self, invocation: NodeInvocation) -> NodeExecution:
        wait = self._pending_wait
        wait_context = wait.context if wait is not None and isinstance(wait.context, dict) else None
        analysis = parse_tieba_request(
            invocation.inputs.user_content,
            pending=wait_context,
            module_context=self._module_context,
        )
        payload: dict[str, Any] = {"analysis": analysis.model_dump(mode="json")}
        if analysis.clarification is not None:
            return NodeExecution(
                artifact=build_artifact(
                    invocation,
                    clock=self._clock,
                    trust_state=ArtifactTrust.DRAFT,
                    payload=payload,
                    read_scope="问题解析（需要澄清）",
                    unconfirmed=[analysis.clarification.question],
                ),
                verdict=QualityVerdict.NEED_INPUT,
                status=NodeReceiptStatus.NEEDS_INPUT,
                detail={
                    "code": "tieba_clarification",
                    "message": analysis.clarification.question,
                    "retryable": False,
                    "node": NODE_PARSE,
                },
                stop_recipe=True,
                recovery=RecoveryPolicy.ASK_INPUT,
            )
        return NodeExecution(
            artifact=build_artifact(
                invocation,
                clock=self._clock,
                trust_state=ArtifactTrust.DRAFT,
                payload=payload,
                read_scope="问题解析（对象、时间与问题类型）",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_plan(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = read_analysis(invocation)
        plan = build_evidence_plan(analysis, official_blocked_reason=self._official_blocked_reason)
        return NodeExecution(
            artifact=build_artifact(
                invocation,
                clock=self._clock,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={"plan": plan.model_dump(mode="json")},
                read_scope="取证计划（来源顺序与查询范围）",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_verify_official(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = read_analysis(invocation)
        plan = read_plan(invocation)
        if not plan.official_required:
            payload = {
                "checks": [],
                "blocked": False,
                "blocked_reason": None,
                "skipped": True,
                "partial": False,
                "closeout_reason": "skipped",
            }
            return NodeExecution(
                artifact=build_artifact(
                    invocation,
                    clock=self._clock,
                    trust_state=ArtifactTrust.EVIDENCE_BOUND,
                    payload=payload,
                    read_scope="体验类问题：本轮不追加官方核验",
                ),
                verdict=QualityVerdict.PASS,
                status=NodeReceiptStatus.COMPLETED,
            )
        if plan.official_blocked_reason:
            payload = {
                "checks": [],
                "blocked": True,
                "blocked_reason": plan.official_blocked_reason,
                "skipped": False,
                "partial": False,
                "closeout_reason": "blocked",
            }
            return NodeExecution(
                artifact=build_artifact(
                    invocation,
                    clock=self._clock,
                    trust_state=ArtifactTrust.EVIDENCE_BOUND,
                    payload=payload,
                    read_scope="来源限制生效：未访问学校官方页面",
                ),
                verdict=QualityVerdict.PASS,
                status=NodeReceiptStatus.COMPLETED,
            )
        resume = resume_payload(
            self._repository, self._pending_wait, invocation, NODE_VERIFY_OFFICIAL
        )
        outcome = verify_official(
            self._search,
            self._official_reader,
            invocation.account_id,
            analysis,
            stop_event=self._stop_event,
            deadline_seconds=self._stage_seconds(
                invocation, self._official_deadline_seconds
            ),
            resume=resume,
        )
        checks = outcome.checks
        payload = {
            "checks": [check.model_dump(mode="json") for check in checks],
            "blocked": False,
            "blocked_reason": None,
            "skipped": False,
            "partial": outcome.partial,
            "closeout_reason": outcome.closeout_reason,
            "records": [record.model_dump(mode="json") for record in outcome.records],
            "candidates": outcome.candidates,
        }
        return NodeExecution(
            artifact=build_artifact(
                invocation,
                clock=self._clock,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload=payload,
                source_refs=[check.url for check in checks],
                read_scope=f"实际取得 {len(checks)} 个官方页面（按域名与段落定位）",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_search(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = read_analysis(invocation)
        resume = resume_payload(self._repository, self._pending_wait, invocation, NODE_SEARCH)
        attempts = search_candidates(
            self._search,
            invocation.account_id,
            analysis,
            stop_event=self._stop_event,
            deadline_seconds=self._stage_seconds(
                invocation, self._search_deadline_seconds
            ),
            resume=resume,
        )
        payload = {
            "candidates": [hit.model_dump(mode="json") for hit in attempts.hits],
            "rejected": [item.model_dump(mode="json") for item in attempts.rejected],
            "records": [record.model_dump(mode="json") for record in attempts.records],
            "rounds": list(attempts.rounds),
            "raw_hits": attempts.diagnostics.raw_hits,
            "usable": attempts.diagnostics.usable,
            "search_scope": [
                scope.model_dump(mode="json") for scope in attempts.scope
            ],
            "partial": attempts.partial,
            "closeout_reason": attempts.closeout_reason,
        }
        return NodeExecution(
            artifact=build_artifact(
                invocation,
                clock=self._clock,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload=payload,
                source_refs=[hit.url for hit in attempts.hits],
                read_scope=f"实际发出 {len(attempts.records)} 次公开检索",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_read(self, invocation: NodeInvocation) -> NodeExecution:
        attempts = read_search_attempts(invocation)
        resume = resume_payload(self._repository, self._pending_wait, invocation, NODE_READ)
        reads = read_candidates(
            self._reader,
            attempts,
            analysis=read_analysis(invocation),
            stop_event=self._stop_event,
            deadline_seconds=self._stage_seconds(
                invocation, self._read_deadline_seconds
            ),
            resume=resume,
        )
        payload = {
            "confirmed": [post.model_dump(mode="json") for post in reads.confirmed],
            "rejected": [item.model_dump(mode="json") for item in reads.rejected],
            "unconfirmed": [link.model_dump(mode="json") for link in reads.unconfirmed],
            "unreadable": reads.unreadable,
            "not_attempted": reads.not_attempted,
            "unconfirmed_total": reads.unconfirmed_total,
            "attempted_urls": list(reads.attempted_urls),
            "retry_urls": list(reads.retry_urls),
            "partial": reads.partial,
            "first_pass_read_count": reads.first_pass_read_count,
            "deepened_post_count": reads.deepened_post_count,
            "closeout_reason": reads.closeout_reason,
        }
        return NodeExecution(
            artifact=build_artifact(
                invocation,
                clock=self._clock,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload=payload,
                source_refs=[post.url for post in reads.confirmed],
                read_scope=(
                    f"首轮读取 {reads.first_pass_read_count} 个候选页面，"
                    f"深入 {reads.deepened_post_count} 个；"
                    f"确认 {len(reads.confirmed)} 个属于「{TARGET_FORUM_NAME}」"
                ),
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
