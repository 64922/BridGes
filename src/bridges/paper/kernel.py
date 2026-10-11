"""论文节点执行上下文、分发与产物提交。"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.modules import ModuleQueryRecord
from bridges.kernel.contracts import (
    ArtifactTrust,
    InputDependency,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    QualityVerdict,
    RecoveryPolicy,
)
from bridges.paper.artifact_codec import _digest, _payload_read_scope, _source_refs
from bridges.paper.budget import PaperBudget as PaperBudget
from bridges.paper.contracts import PaperRecommendation, PaperTermAnalysis
from bridges.paper.delivery_types import ENRICH_DEADLINE_SECONDS, SEARCH_DEADLINE_SECONDS
from bridges.paper.enrichment import MetadataEnricher
from bridges.paper.flow_context import PaperFlowContext as PaperFlowContext
from bridges.paper.nodes_planning import parse_node, plan_node
from bridges.paper.nodes_reading import enrich_node, read_node
from bridges.paper.nodes_screening import screen_node
from bridges.paper.nodes_search import search_node
from bridges.paper.nodes_selection import evaluate_node, verify_node
from bridges.paper.reading import GOAL_CANDIDATES, GOAL_COMPARE, GOAL_EXPLAIN, ReadCoordinator
from bridges.paper.recipe import DEFAULT_DEEP_READ_MAX as DEFAULT_DEEP_READ_MAX
from bridges.paper.recipe import DEFAULT_SCREEN_MAX as DEFAULT_SCREEN_MAX
from bridges.paper.recipe import NODE_ENRICH as NODE_ENRICH
from bridges.paper.recipe import NODE_EVALUATE as NODE_EVALUATE
from bridges.paper.recipe import NODE_PARSE as NODE_PARSE
from bridges.paper.recipe import NODE_PLAN as NODE_PLAN
from bridges.paper.recipe import NODE_READ as NODE_READ
from bridges.paper.recipe import NODE_SCREEN as NODE_SCREEN
from bridges.paper.recipe import NODE_SEARCH as NODE_SEARCH
from bridges.paper.recipe import NODE_VERIFY as NODE_VERIFY
from bridges.paper.recipe import PAPER_CAPABILITY_VERSIONS as PAPER_CAPABILITY_VERSIONS
from bridges.paper.recipe import PAPER_GATE_HANDLERS as PAPER_GATE_HANDLERS
from bridges.paper.recipe import PAPER_GATES as PAPER_GATES
from bridges.paper.recipe import PAPER_NODE_LABELS as PAPER_NODE_LABELS
from bridges.paper.recipe import PAPER_RECIPE_ID as PAPER_RECIPE_ID
from bridges.paper.recipe import PAPER_RECIPE_VERSION as PAPER_RECIPE_VERSION
from bridges.paper.recipe import build_paper_recipe as build_paper_recipe
from bridges.paper.recipe import claims_have_evidence_gate as claims_have_evidence_gate
from bridges.paper.recipe import hard_conditions_hold_gate as hard_conditions_hold_gate
from bridges.paper.recipe import identity_present_gate as identity_present_gate
from bridges.paper.recipe import independent_review_gate as independent_review_gate
from bridges.paper.recipe import paper_recipe_registry as paper_recipe_registry
from bridges.paper.screening import BatchPaperRelevanceJudge, PaperRelevanceJudge

_EXPLAIN_RE = re.compile(r"解释|讲解|讲讲|为什么|原理|如何理解|入门介绍")


_COMPARE_RE = re.compile(r"比较|对比|区别|差异|compare|versus|vs\.?")


class PaperNodeFlow:
    """论文节点的执行体：复用既有解析/规划/来源/排序，新增证据筛选与读取。"""

    def __init__(
        self,
        *,
        source: Any,
        sources: Mapping[str, Any] | None = None,
        intent_analyzer: Any | None = None,
        enricher: MetadataEnricher | None,
        reader: ReadCoordinator | None = None,
        judge: PaperRelevanceJudge | BatchPaperRelevanceJudge | None = None,
        reviewer: Callable[[list[PaperRecommendation], list[str]], Mapping[str, Any] | None]
        | None = None,
        context: PaperFlowContext | None = None,
        pending_wait: Any = None,
        budget: PaperBudget | None = None,
        stop_event: threading.Event | None = None,
        deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        enrich_deadline_seconds: float = ENRICH_DEADLINE_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sources = dict(sources or {})
        if not self._sources:
            self._sources[str(getattr(source, "source", "arxiv"))] = source
        self._source = source
        self._intent_analyzer = intent_analyzer
        self._query_planner = intent_analyzer if hasattr(intent_analyzer, "plan") else None
        self._enricher = enricher
        self._reader = reader or ReadCoordinator()
        self._judge = judge
        self._reviewer = reviewer
        self._context = context or PaperFlowContext()
        self._pending = pending_wait
        self._budget = budget
        self._stop_event = stop_event
        self._deadline_seconds = deadline_seconds
        self._enrich_deadline_seconds = enrich_deadline_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        #: 真实发生过的外部调用记录；即使停止使产物未能提交，投影里也要
        #: 保留实际查询词（停止合同：已完成的步骤不抹掉）。
        self._search_records: list[ModuleQueryRecord] = []
        self._enrich_records: list[ModuleQueryRecord] = []

    @property
    def external_records(self) -> list[ModuleQueryRecord]:
        return [*self._search_records, *self._enrich_records]

    @property
    def prior_digest(self) -> str:
        reference_time = self._context.reference_time
        return _digest(
            {
                "prior": list(self._context.prior_context),
                "topic_hint": self._context.topic_hint,
                "conditions": [
                    {"kind": getattr(item, "kind", None), "text": getattr(item, "text", "")}
                    for item in self._context.task_conditions
                ],
                "task_scope": self._context.task_scope_used,
                "citation_seed": (
                    self._context.citation_seed.candidate_id
                    if self._context.citation_seed else None
                ),
                "continuation_error": self._context.continuation_error,
                # 冻结日期（运行创建时刻的日期）是恢复失效合同的时间版本：
                # 同一运行跨年仍用冻结日期；同日新运行可复用产物；跨日/跨年
                # 重新解析相对年份并使过期来源结果失效。
                "reference_date": (reference_time.date().isoformat() if reference_time else None),
                "reference_year": reference_time.year if reference_time else None,
            }
        )

    # -- 分发 ------------------------------------------------------------

    def run_node(self, invocation: NodeInvocation) -> NodeExecution:
        try:
            if invocation.spec.name == NODE_PARSE:
                return parse_node(self, invocation)
            if invocation.spec.name == NODE_PLAN:
                return plan_node(self, invocation)
            if invocation.spec.name == NODE_SEARCH:
                return search_node(self, invocation)
            if invocation.spec.name == NODE_SCREEN:
                return screen_node(self, invocation)
            if invocation.spec.name == NODE_READ:
                return read_node(self, invocation)
            if invocation.spec.name == NODE_ENRICH:
                return enrich_node(self, invocation)
            if invocation.spec.name == NODE_EVALUATE:
                return evaluate_node(self, invocation)
            if invocation.spec.name == NODE_VERIFY:
                return verify_node(self, invocation)
            raise KeyError(invocation.spec.name)
        except Exception as error:  # noqa: BLE001 - 未预期异常转结构化失败，不落草稿
            payload: dict[str, Any] = {"error": type(error).__name__}
            artifact = self._artifact(
                invocation,
                payload=payload,
                trust_state=ArtifactTrust.INVALIDATED,
                error={"code": "paper_node_exception", "message": str(error)},
            )
            return NodeExecution(
                artifact=artifact,
                verdict=QualityVerdict.REPAIRABLE_FAILURE,
                status=NodeReceiptStatus.FAILED,
                detail={
                    "code": "paper_node_exception",
                    "message": str(error),
                    "retryable": True,
                },
                stop_recipe=True,
                recovery=RecoveryPolicy.RETRY_NODE,
                failure={
                    "code": "paper_node_exception",
                    "message": str(error),
                    "retryable": True,
                },
            )

    # -- 各节点 ----------------------------------------------------------








    def _comparison_requested(self, invocation: NodeInvocation) -> bool:
        """比较意图来自显式上下文或本轮阅读目标（parse 节点判定）。"""
        if self._context.comparison_requested:
            return True
        parse_payload = invocation.dependencies[NODE_PARSE].payload
        return str(parse_payload.get("reading_goal") or "") == GOAL_COMPARE


    # -- 工具 ------------------------------------------------------------

    def _analysis(self, invocation: NodeInvocation, node: str) -> PaperTermAnalysis:
        return PaperTermAnalysis.model_validate(invocation.dependencies[node].payload["analysis"])

    def _reading_goal(self, content: str) -> str:
        if self._context.reading_goal:
            return self._context.reading_goal
        if self._context.comparison_requested or _COMPARE_RE.search(content):
            return GOAL_COMPARE
        if _EXPLAIN_RE.search(content):
            return GOAL_EXPLAIN
        return GOAL_CANDIDATES

    def _attempt_seconds(self) -> float:
        budget_seconds = (
            self._budget.deadline_seconds() if self._budget is not None else self._deadline_seconds
        )
        return float(min(self._deadline_seconds, budget_seconds))

    def _completed(
        self,
        invocation: NodeInvocation,
        payload: dict[str, Any],
        *,
        requirement_coverage: tuple[dict[str, Any], ...] = (),
        unconfirmed: tuple[str, ...] = (),
    ) -> NodeExecution:
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                payload=payload,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                requirement_coverage=requirement_coverage,
                unconfirmed=unconfirmed,
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _failure(
        self,
        invocation: NodeInvocation,
        *,
        payload: dict[str, Any],
        code: str,
        message: str,
        retryable: bool,
    ) -> NodeExecution:
        artifact = self._artifact(
            invocation,
            payload=payload,
            trust_state=ArtifactTrust.INVALIDATED,
            error={"code": code, "message": message},
        )
        return NodeExecution(
            artifact=artifact,
            verdict=(QualityVerdict.REPAIRABLE_FAILURE if retryable else QualityVerdict.BLOCKED),
            status=NodeReceiptStatus.FAILED,
            detail={"code": code, "message": message, "retryable": retryable},
            stop_recipe=True,
            recovery=RecoveryPolicy.RETRY_NODE if retryable else RecoveryPolicy.BLOCK,
            failure={"code": code, "message": message, "retryable": retryable},
        )

    def _artifact(
        self,
        invocation: NodeInvocation,
        *,
        payload: dict[str, Any],
        trust_state: ArtifactTrust,
        requirement_coverage: tuple[dict[str, Any], ...] = (),
        unconfirmed: tuple[str, ...] = (),
        error: dict[str, Any] | None = None,
    ) -> NodeArtifact:
        dependencies = tuple(
            InputDependency(
                node=node,
                artifact_id=artifact.artifact_id,
                content_hash=artifact.content_hash,
            )
            for node, artifact in invocation.dependencies.items()
        )
        read_scope = _payload_read_scope(payload)
        return NodeArtifact.build(
            account_id=invocation.account_id,
            conversation_id=invocation.conversation_id,
            run_id=invocation.run_id,
            task_id=invocation.inputs.task_id,
            task_version=invocation.inputs.task_version,
            recipe_id=PAPER_RECIPE_ID,
            recipe_version=PAPER_RECIPE_VERSION,
            node=invocation.spec.name,
            artifact_type=invocation.spec.artifact_type,
            capability_version=invocation.spec.capability_version,
            trust_state=trust_state,
            input_key=invocation.spec.input_key(invocation.inputs),
            input_deps=dependencies,
            source_refs=_source_refs(payload),
            read_scope=read_scope,
            requirement_coverage=requirement_coverage,
            unconfirmed=unconfirmed,
            error=error,
            payload=payload,
            now=self._clock(),
        )
