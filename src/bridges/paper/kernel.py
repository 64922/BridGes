"""``paper.*`` 持久节点配方与执行体（工单 24）。

论文模块从黑盒子图迁移为登记的持久节点：

``paper.parse → paper.plan → paper.search → paper.screen → paper.read →
paper.enrich → paper.evaluate → paper.verify``

- 每个节点是显式持久步骤（产物 + 完成收据 + 质量门），恢复时只重跑未完成
  的节点；输入键包含上游产物哈希，任一上游变化即触发下游重算。
- 硬条件（年份/综述偏好）由代码过滤，来源范围受「仅调用已登记适配器」约束；
  语义相关性由证据匹配与可注入的
  专业角色判断，关键词不是通过条件。
- 方法/实验/局限断言必须有正文依据；未接入全文读取时只交付摘要级结果并
  逐篇标注读取范围。
- 选定论文产物携带可核实身份（arXiv/DOI/年份/链接与内容哈希），供后续模块
  精确引用，不靠自然语言摘要猜。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.kernel.contracts import (
    ArtifactTrust,
    InputDependency,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    NodeSpec,
    QualityGateResult,
    QualityVerdict,
    RecipeDefinition,
    RecoveryPolicy,
)
from bridges.kernel.registry import RecipeRegistry
from bridges.paper.contracts import (
    PaperIdentity,
    PaperQueryPlan,
    PaperReadScope,
    PaperRecommendation,
    PaperRequirementEvidence,
    PaperTermAnalysis,
)
from bridges.paper.parsing import parse_paper_request
from bridges.paper.planning import MIN_TARGET_COUNT, plan_queries
from bridges.paper.ranking import rank_candidates
from bridges.paper.reading import (
    GOAL_CANDIDATES,
    GOAL_COMPARE,
    GOAL_EXPLAIN,
    PaperReading,
    ReadCoordinator,
    supported_claims,
)
from bridges.paper.screening import (
    PaperRelevanceJudge,
    ScreenedCandidate,
    candidate_payload,
    screen_candidates,
)
from bridges.paper.sources import (
    CROSSREF_SOURCE,
    OPENALEX_SOURCE,
    CandidateSearchOutcome,
    EnrichedMetadata,
    EnrichOutcome,
    MetadataEnricher,
    PaperCandidate,
)

#: 配方节点名（进度事件、失败定位与产物身份）。
NODE_PARSE = "paper.parse"
NODE_PLAN = "paper.plan"
NODE_SEARCH = "paper.search"
NODE_SCREEN = "paper.screen"
NODE_READ = "paper.read"
NODE_ENRICH = "paper.enrich"
NODE_EVALUATE = "paper.evaluate"
NODE_VERIFY = "paper.verify"

PAPER_RECIPE_ID = "paper-search"
PAPER_RECIPE_VERSION = "paper-search-recipe-v2"

#: 节点的用户可读中文名（父图失败信息按此标注真实失败位置）。
PAPER_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "理解论文请求",
    NODE_PLAN: "规划论文检索",
    NODE_SEARCH: "检索登记学术来源",
    NODE_SCREEN: "按证据筛选候选",
    NODE_READ: "读取摘要或正文",
    NODE_ENRICH: "核对论文来源",
    NODE_EVALUATE: "整理选择与顺序",
    NODE_VERIFY: "核验身份与断言证据",
}

#: 已登记的确定性能力与版本（代码拒绝未登记能力）。
PAPER_CAPABILITY_VERSIONS: dict[str, str] = {
    "paper.parse_request": "paper-parse-v1",
    "paper.plan_queries": "paper-plan-v1",
    "paper.search_sources": "paper-search-v1",
    "paper.screen_candidates": "paper-screen-v1",
    "paper.read_evidence": "paper-read-v1",
    "paper.enrich_metadata": "paper-enrich-v1",
    "paper.evaluate_selection": "paper-evaluate-v1",
    "paper.verify_selection": "paper-verify-v1",
}

#: 配方的必要门与可选门（登记集合；代码拒绝未登记质量门）。
PAPER_GATES: frozenset[str] = frozenset(
    {
        "paper.identity_present",
        "paper.claims_have_evidence",
        "paper.hard_conditions_hold",
        "paper.independent_review",
    }
)

#: 单轮检索与元数据补充的墙钟预算（秒）。
SEARCH_DEADLINE_SECONDS = 25.0
ENRICH_DEADLINE_SECONDS = 12.0

#: 未取得运行账本时的默认上限（账本存在时以冻结值为准）。
DEFAULT_SCREEN_MAX = 20
DEFAULT_DEEP_READ_MAX = 3

#: 普通查找默认目标篇数（3–5 篇）。
DEFAULT_TARGET_COUNT = 5

#: 阅读目的识别词（比较/解释/普通查找）。
_COMPARE_RE = re.compile(r"比较|对比|区别|差异|compare|versus|vs\.?")
_EXPLAIN_RE = re.compile(r"解释|讲解|讲讲|为什么|原理|如何理解|入门介绍")


def _digest(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


def _candidate(payload: Mapping[str, Any]) -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=str(payload["arxiv_id"]),
        title=str(payload["title"]),
        authors=[str(item) for item in payload.get("authors") or ()],
        published_at=datetime.fromisoformat(str(payload["published_at"])),
        abs_url=str(payload["abs_url"]),
        pdf_url=str(payload.get("pdf_url") or ""),
        abstract=str(payload.get("abstract") or ""),
        primary_category=payload.get("primary_category"),
    )


def _metadata(payload: Mapping[str, Any]) -> EnrichedMetadata:
    return EnrichedMetadata(
        source=str(payload.get("source") or ""),
        matched_title=payload.get("matched_title"),
        doi=payload.get("doi"),
        cited_by_count=payload.get("cited_by_count"),
        venue=payload.get("venue"),
        year=payload.get("year"),
        is_open_access=payload.get("is_open_access"),
        full_text_url=payload.get("full_text_url"),
        error_code=payload.get("error_code"),
        error_message=payload.get("error_message"),
    )


def _record_payload(record: ModuleQueryRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


def _record(payload: Mapping[str, Any]) -> ModuleQueryRecord:
    return ModuleQueryRecord.model_validate(payload)


# ---------------------------------------------------------------------------
# 预算视图：节点领取而非重建预算（工单 09 账本；缺失账本时按无预算模式）
# ---------------------------------------------------------------------------


class PaperBudget:
    """论文外部调用与候选上限的共享运行预算视图。"""

    def __init__(
        self,
        *,
        ledger: Any,
        account_id: str,
        run_id: str,
        work_deadline: datetime,
        screen_max: int = DEFAULT_SCREEN_MAX,
        deep_read_max: int = DEFAULT_DEEP_READ_MAX,
    ) -> None:
        self._ledger = ledger
        self._account_id = account_id
        self._run_id = run_id
        self._work_deadline = work_deadline
        self.screen_max = max(1, screen_max)
        self.deep_read_max = max(0, deep_read_max)

    def remaining_work_ms(self, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        return max(0, int((self._work_deadline - moment).total_seconds() * 1000))

    def deadline_seconds(self, now: datetime | None = None) -> float:
        return max(0.5, self.remaining_work_ms(now) / 1000.0)

    def register_external(self, call_key: str, *, purpose: str) -> bool:
        return bool(
            self._ledger.register_external_call(
                account_id=self._account_id,
                run_id=self._run_id,
                call_key=call_key,
                purpose=purpose,
                now=datetime.now(UTC),
            )
        )

    def release_external(self, call_key: str, *, outcome_code: str) -> None:
        self._ledger.record_external_call_result(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            outcome_code=outcome_code,
            now=datetime.now(UTC),
        )

    def begin_adjustment(self) -> bool:
        return bool(self._ledger.begin_adjustment(
            account_id=self._account_id, run_id=self._run_id,
            reason_code="paper_relevance_insufficient", now=datetime.now(UTC),
        ))

    def end_adjustment(self, outcome_code: str) -> None:
        self._ledger.end_adjustment(
            account_id=self._account_id, run_id=self._run_id,
            outcome_code=outcome_code, now=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# 输入键：上游 content_hash 进入下游键（失效沿依赖传播的唯一机制）
# ---------------------------------------------------------------------------


def _parse_key(inputs: Any) -> str:
    return _digest(
        {
            "content": inputs.user_content,
            "wait": inputs.wait_identity,
            "prior": inputs.prior_digest,
        }
    )


def _plan_key(inputs: Any) -> str:
    return _digest(
        {
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "capability": PAPER_CAPABILITY_VERSIONS["paper.plan_queries"],
        }
    )


def _search_key(inputs: Any) -> str:
    # 这里刻意不含 run_id：同一会话、同一内容与同一冻结日期（见
    # prior_digest）的新运行可以复用真实检索产物；跨日/跨年时解析键变化，
    # 过期来源结果不会复用（恢复失效合同）。
    plans = inputs.artifacts[NODE_PLAN].payload.get("plans") or []
    return _digest(
        {
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "queries": [item.get("query") for item in plans],
            "capability": PAPER_CAPABILITY_VERSIONS["paper.search_sources"],
        }
    )


def _screen_key(inputs: Any) -> str:
    return _digest(
        {
            "search": inputs.artifacts[NODE_SEARCH].content_hash,
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "capability": PAPER_CAPABILITY_VERSIONS["paper.screen_candidates"],
        }
    )


def _read_key(inputs: Any) -> str:
    return _digest(
        {
            "screen": inputs.artifacts[NODE_SCREEN].content_hash,
            "goal": inputs.artifacts[NODE_PARSE].payload.get("reading_goal"),
            "capability": PAPER_CAPABILITY_VERSIONS["paper.read_evidence"],
        }
    )


def _enrich_key(inputs: Any) -> str:
    return _digest(
        {
            "search": inputs.artifacts[NODE_SEARCH].content_hash,
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "capability": PAPER_CAPABILITY_VERSIONS["paper.enrich_metadata"],
        }
    )


def _evaluate_key(inputs: Any) -> str:
    return _digest(
        {
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "screen": inputs.artifacts[NODE_SCREEN].content_hash,
            "read": inputs.artifacts[NODE_READ].content_hash,
            "enrich": inputs.artifacts[NODE_ENRICH].content_hash,
            "capability": PAPER_CAPABILITY_VERSIONS["paper.evaluate_selection"],
        }
    )


def _verify_key(inputs: Any) -> str:
    return _digest(
        {
            "evaluate": inputs.artifacts[NODE_EVALUATE].content_hash,
            "capability": PAPER_CAPABILITY_VERSIONS["paper.verify_selection"],
        }
    )


# ---------------------------------------------------------------------------
# 配方登记
# ---------------------------------------------------------------------------


def build_paper_recipe() -> RecipeDefinition:
    """构造并校验论文配方（必经顺序与依赖只指向前置节点）。"""
    capabilities = PAPER_CAPABILITY_VERSIONS
    return RecipeDefinition(
        recipe_id=PAPER_RECIPE_ID,
        recipe_version=PAPER_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_PARSE,
                capability="paper.parse_request",
                capability_version=capabilities["paper.parse_request"],
                artifact_type="paper.request_analysis",
                input_key=_parse_key,
                recovery=RecoveryPolicy.ASK_INPUT,
                description="解析主题/领域、阅读目标、原话锚点、年份等硬条件与缺口。",
            ),
            NodeSpec(
                name=NODE_PLAN,
                capability="paper.plan_queries",
                capability_version=capabilities["paper.plan_queries"],
                artifact_type="paper.query_plan",
                input_key=_plan_key,
                depends_on=(NODE_PARSE,),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="精确查询与受约束扩展；冻结筛选与深读上限。",
            ),
            NodeSpec(
                name=NODE_SEARCH,
                capability="paper.search_sources",
                capability_version=capabilities["paper.search_sources"],
                artifact_type="paper.candidates",
                input_key=_search_key,
                depends_on=(NODE_PLAN, NODE_PARSE),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="仅调用已登记且可用的来源；学术标识去重并记录真实查询。",
            ),
            NodeSpec(
                name=NODE_SCREEN,
                capability="paper.screen_candidates",
                capability_version=capabilities["paper.screen_candidates"],
                artifact_type="paper.screened_candidates",
                input_key=_screen_key,
                depends_on=(NODE_SEARCH, NODE_PARSE),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="硬条件代码过滤 + 需求到标题/摘要证据的语义匹配。",
            ),
            NodeSpec(
                name=NODE_READ,
                capability="paper.read_evidence",
                capability_version=capabilities["paper.read_evidence"],
                artifact_type="paper.readings",
                input_key=_read_key,
                depends_on=(NODE_SCREEN, NODE_PARSE),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="按结论需要读取摘要/正文片段；未接入或失败时诚实降级。",
            ),
            NodeSpec(
                name=NODE_ENRICH,
                capability="paper.enrich_metadata",
                capability_version=capabilities["paper.enrich_metadata"],
                artifact_type="paper.enrichment",
                input_key=_enrich_key,
                depends_on=(NODE_SEARCH, NODE_PARSE),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="可选补充发表/版本/引用信息；失败只留缺口不阻断。",
            ),
            NodeSpec(
                name=NODE_EVALUATE,
                capability="paper.evaluate_selection",
                capability_version=capabilities["paper.evaluate_selection"],
                artifact_type="paper.selection",
                input_key=_evaluate_key,
                depends_on=(NODE_PARSE, NODE_PLAN, NODE_SCREEN, NODE_READ, NODE_ENRICH),
                required_gates=("paper.identity_present",),
                recovery=RecoveryPolicy.BLOCK,
                description="组织选择与阅读顺序，保存可核实身份；引用量只作辅助。",
            ),
            NodeSpec(
                name=NODE_VERIFY,
                capability="paper.verify_selection",
                capability_version=capabilities["paper.verify_selection"],
                artifact_type="paper.verification",
                input_key=_verify_key,
                depends_on=(NODE_EVALUATE,),
                required_gates=(
                    "paper.claims_have_evidence",
                    "paper.hard_conditions_hold",
                    "paper.independent_review",
                ),
                recovery=RecoveryPolicy.BLOCK,
                description="核对身份、断言证据与未读范围；复杂比较/冲突触发独立复核。",
            ),
        ),
    )


def paper_recipe_registry() -> RecipeRegistry:
    """登记论文能力、质量门与配方；非法定义在装配时即被拒绝。"""
    registry = RecipeRegistry(
        capabilities=PAPER_CAPABILITY_VERSIONS.keys(),
        gates=PAPER_GATES,
    )
    registry.register(build_paper_recipe())
    return registry


# ---------------------------------------------------------------------------
# 质量门处理器
# ---------------------------------------------------------------------------


def identity_present_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要身份缺失阻塞对应结论：每篇选定论文必须有 arXiv ID 或 DOI。"""
    del invocation
    missing = execution.artifact.payload.get("identity_missing") or []
    if missing:
        return QualityGateResult(
            gate="paper.identity_present",
            verdict=QualityVerdict.BLOCKED,
            code="paper_identity_missing",
            message="选定论文缺少可核实身份（arXiv ID/DOI），已阻塞对应结论。",
            detail={"missing": list(missing)},
        )
    return QualityGateResult(gate="paper.identity_present", verdict=QualityVerdict.PASS)


def claims_have_evidence_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """方法/实验/局限断言必须有正文依据；只有摘要不得冒充精读。"""
    del invocation
    unsupported = execution.artifact.payload.get("unsupported_claims") or []
    if unsupported:
        return QualityGateResult(
            gate="paper.claims_have_evidence",
            verdict=QualityVerdict.BLOCKED,
            code="paper_claim_without_body_evidence",
            message="存在没有正文依据的方法/实验断言，已阻塞交付。",
            detail={"claims": list(unsupported)},
        )
    return QualityGateResult(gate="paper.claims_have_evidence", verdict=QualityVerdict.PASS)


def hard_conditions_hold_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """年份等硬条件不得静默放宽。"""
    del invocation
    violations = execution.artifact.payload.get("hard_condition_violations") or []
    if violations:
        return QualityGateResult(
            gate="paper.hard_conditions_hold",
            verdict=QualityVerdict.BLOCKED,
            code="paper_hard_condition_violated",
            message="推荐结果违反年份等明确条件，已阻塞交付。",
            detail={"violations": list(violations)},
        )
    return QualityGateResult(gate="paper.hard_conditions_hold", verdict=QualityVerdict.PASS)


def independent_review_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """可选独立复核：记录触发与执行情况，不把它伪装成模型自评。"""
    del invocation
    review = execution.artifact.payload.get("independent_review") or {}
    if review.get("executed") and (review.get("result") or {}).get("passed") is not True:
        return QualityGateResult(
            gate="paper.independent_review",
            verdict=QualityVerdict.BLOCKED,
            code="paper_independent_review_failed",
            message="独立复核未通过或裁决结构无效，已阻塞相关结论。",
        )
    return QualityGateResult(
        gate="paper.independent_review",
        verdict=QualityVerdict.PASS,
        detail={
            "triggered": bool(review.get("triggered")),
            "executed": bool(review.get("executed")),
        },
    )


PAPER_GATE_HANDLERS = {
    "paper.identity_present": identity_present_gate,
    "paper.claims_have_evidence": claims_have_evidence_gate,
    "paper.hard_conditions_hold": hard_conditions_hold_gate,
    "paper.independent_review": independent_review_gate,
}


# ---------------------------------------------------------------------------
# 节点执行体
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaperFlowContext:
    """构造执行体所需的不可变回合上下文（服务层提供）。"""

    prior_context: tuple[str, ...] = ()
    topic_hint: str | None = None
    task_conditions: tuple[Any, ...] = ()
    reading_goal: str | None = None
    comparison_requested: bool = False
    task_scope_used: bool = False
    reference_time: datetime | None = None


class PaperNodeFlow:
    """论文节点的执行体：复用既有解析/规划/来源/排序，新增证据筛选与读取。"""

    def __init__(
        self,
        *,
        source: Any,
        enricher: MetadataEnricher | None,
        reader: ReadCoordinator | None = None,
        judge: PaperRelevanceJudge | None = None,
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
        self._source = source
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
                # 冻结日期（运行创建时刻的日期）是恢复失效合同的时间版本：
                # 同一运行跨年仍用冻结日期；同日新运行可复用产物；跨日/跨年
                # 重新解析相对年份并使过期来源结果失效。
                "reference_date": (
                    reference_time.date().isoformat() if reference_time else None
                ),
                "reference_year": reference_time.year if reference_time else None,
            }
        )

    # -- 分发 ------------------------------------------------------------

    def run_node(self, invocation: NodeInvocation) -> NodeExecution:
        try:
            if invocation.spec.name == NODE_PARSE:
                return self._parse(invocation)
            if invocation.spec.name == NODE_PLAN:
                return self._plan(invocation)
            if invocation.spec.name == NODE_SEARCH:
                return self._search(invocation)
            if invocation.spec.name == NODE_SCREEN:
                return self._screen(invocation)
            if invocation.spec.name == NODE_READ:
                return self._read(invocation)
            if invocation.spec.name == NODE_ENRICH:
                return self._enrich(invocation)
            if invocation.spec.name == NODE_EVALUATE:
                return self._evaluate(invocation)
            if invocation.spec.name == NODE_VERIFY:
                return self._verify(invocation)
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

    def _parse(self, invocation: NodeInvocation) -> NodeExecution:
        conditions = (
            list(self._context.task_conditions) if self._context.task_scope_used else None
        )
        analysis = parse_paper_request(
            invocation.inputs.user_content,
            prior_context=list(self._context.prior_context),
            pending=self._pending,
            task_topic_hint=self._context.topic_hint,
            task_conditions=conditions,
            now=self._context.reference_time or self._clock(),
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
                    requirement_coverage=(
                        {"requirement": "topic", "status": "needs_input"},
                    ),
                    unconfirmed=("领域/主题歧义待用户确认",),
                ),
                verdict=QualityVerdict.NEED_INPUT,
                status=NodeReceiptStatus.NEEDS_INPUT,
                detail={"code": "paper_clarification", "question": analysis.clarification.question},
                stop_recipe=True,
                recovery=RecoveryPolicy.ASK_INPUT,
            )
        return self._completed(invocation, payload)

    def _plan(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation, NODE_PARSE)
        plans = plan_queries(analysis)
        plan = plans[0]
        payload = {
            "plans": [item.model_dump(mode="json") for item in plans],
            "target_count": plan.target_count,
            "screen_max": self._budget.screen_max if self._budget else DEFAULT_SCREEN_MAX,
            "read_max": (
                self._budget.deep_read_max if self._budget else DEFAULT_DEEP_READ_MAX
            ),
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

    def _search(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation, NODE_PARSE)
        plans = [
            PaperQueryPlan.model_validate(item)
            for item in invocation.dependencies[NODE_PLAN].payload["plans"]
        ]
        records: list[ModuleQueryRecord] = []
        attempts: list[dict[str, Any]] = []
        adopted: CandidateSearchOutcome | None = None
        adopted_count = -1
        adopted_index = -1
        adjustment_used = False
        adjustment_outcome: str | None = None
        for index, plan in enumerate(plans):
            if self._stop_event is not None and self._stop_event.is_set():
                break
            if index and self._budget is not None and not self._budget.begin_adjustment():
                break
            call_key = f"paper.search:{index}"
            registered = False
            if self._budget is not None:
                registered = self._budget.register_external(
                    call_key, purpose="academic_search"
                )
                if not registered:
                    if index:
                        adjustment_outcome = "budget_exhausted"
                        self._budget.end_adjustment(adjustment_outcome)
                    break
            if index:
                adjustment_used = True
            outcome_code = "exception"
            try:
                result = self._source.search(
                    invocation.account_id,
                    plan.query,
                    max_results=plan.max_results,
                    stop_event=self._stop_event,
                    deadline=time.monotonic() + self._attempt_seconds(),
                )
                outcome_code = result.record.status.value
            finally:
                if self._budget is not None and registered:
                    self._budget.release_external(call_key, outcome_code=outcome_code)
                if index:
                    adjustment_outcome = outcome_code
                    if self._budget is not None:
                        self._budget.end_adjustment(outcome_code)
            records.append(result.record)
            self._search_records.append(result.record)
            cap = self._budget.screen_max if self._budget else DEFAULT_SCREEN_MAX
            relevant_count = len(screen_candidates(analysis, result.candidates[:cap]).screened)
            attempts.append(
                {
                    "index": index,
                    "query": result.record.query,
                    "status": result.record.status.value,
                    "candidate_count": len(result.candidates),
                    "relevant_count": relevant_count,
                    "adopted": False,
                }
            )
            if adopted is None or relevant_count > adopted_count:
                adopted = result
                adopted_count = relevant_count
                adopted_index = index
            is_last = index + 1 == len(plans)
            if (
                is_last
                or result.record.status not in {ModuleQueryStatus.SUCCESS, ModuleQueryStatus.EMPTY}
                or relevant_count >= MIN_TARGET_COUNT
            ):
                break
        if adopted is None:
            # 首次检索就被预算拒绝：明确报预算不足，不用断言伪装成节点异常。
            return self._failure(
                invocation,
                payload={
                    "candidates": [],
                    "records": [],
                    "query": "",
                    "status": "budget_exhausted",
                    "attempts": list(attempts),
                },
                code="run_budget_exhausted",
                message="本轮运行预算已用尽，未发起新的论文检索。",
                retryable=False,
            )
        if 0 <= adopted_index < len(attempts):
            attempts[adopted_index]["adopted"] = True
        payload = {
            "candidates": [candidate_payload(item) for item in adopted.candidates],
            "records": [_record_payload(item) for item in records],
            "query": adopted.query,
            "status": adopted.record.status.value,
            "attempts": attempts,
            "adjustment": {
                "used": adjustment_used,
                "reason": "paper_relevance_insufficient" if adjustment_used else None,
                "outcome": adjustment_outcome,
            },
        }
        if adopted.record.status in {
            ModuleQueryStatus.ERROR,
            ModuleQueryStatus.TIMEOUT,
            ModuleQueryStatus.RATE_LIMITED,
        }:
            return self._failure(
                invocation,
                payload=payload,
                code=adopted.record.error_code or "paper_search_failed",
                message=adopted.record.error_message or "论文检索失败，请稍后重试。",
                retryable=adopted.record.retryable,
            )
        if adopted.record.status is ModuleQueryStatus.CANCELLED:
            return self._failure(
                invocation,
                payload=payload,
                code="paper_cancelled",
                message="本轮检索已停止。",
                retryable=True,
            )
        return self._completed(invocation, payload)

    def _screen(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation, NODE_PARSE)
        candidates_payload = invocation.dependencies[NODE_SEARCH].payload.get("candidates") or []
        candidates = [_candidate(item) for item in candidates_payload]
        screen_max = self._budget.screen_max if self._budget else DEFAULT_SCREEN_MAX
        cap = max(1, screen_max)
        truncated = len(candidates) > cap
        outcome = screen_candidates(analysis, candidates[:cap], judge=self._judge)
        if truncated:
            outcome.notes.append(
                f"候选超过本轮初筛上限 {cap} 篇，仅对靠前候选做证据筛选。"
            )
        payload = {
            "screened": [item.to_payload() for item in outcome.screened],
            "excluded": [
                {
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
        }
        unconfirmed = (
            ("检索结果没有覆盖原始术语的论文",)
            if outcome.topic_mismatch
            else ()
        )
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

    def _read(self, invocation: NodeInvocation) -> NodeExecution:
        screened = [
            ScreenedCandidate(
                candidate=_candidate(item["candidate"]),
                matched_requirements=tuple(item.get("matched_requirements") or ()),
                evidence=tuple(item.get("evidence") or ()),
                strength=str(item.get("strength") or "weak"),
                unconfirmed=tuple(item.get("unconfirmed") or ()),
            )
            for item in invocation.dependencies[NODE_SCREEN].payload.get("screened") or ()
        ]
        goal = str(
            invocation.dependencies[NODE_PARSE].payload.get("reading_goal")
            or GOAL_CANDIDATES
        )
        outcome = self._reader.read(
            screened,
            goal=goal,
            account_id=invocation.account_id,
            budget=self._budget,
            stop_event=self._stop_event,
        )
        payload = {
            "readings": {
                arxiv_id: {
                    "scope": reading.scope,
                    "sections": [
                        {"name": section.name, "text": section.text, "locator": section.locator}
                        for section in reading.sections
                    ],
                    "notes": list(reading.notes),
                }
                for arxiv_id, reading in outcome.readings.items()
            },
            "notes": list(outcome.notes),
            "deep_read_count": outcome.deep_read_count,
        }
        return self._completed(invocation, payload)

    def _enrich(self, invocation: NodeInvocation) -> NodeExecution:
        candidates = [
            _candidate(item)
            for item in invocation.dependencies[NODE_SEARCH].payload.get("candidates") or []
        ]
        analysis = self._analysis(invocation, NODE_PARSE)
        metadata_sources = _allowed_metadata_sources(analysis)
        if self._enricher is None:
            payload = {
                "metadata": {},
                "records": [],
                "available": False,
                "notes": ["未装配发表信息补充适配器，本轮未核对 DOI/venue/引用量。"],
            }
            return self._completed(invocation, payload)
        if not metadata_sources:
            payload = {
                "metadata": {},
                "records": [],
                "available": False,
                "notes": [
                    "来源条件未允许 Crossref/OpenAlex，本轮未调用这两个补充来源。"
                ],
            }
            return self._completed(invocation, payload)
        try:
            outcome: EnrichOutcome = self._enricher.enrich(
                candidates,
                account_id=invocation.account_id,
                need_publication_info=True,
                deadline=time.monotonic() + min(
                    self._enrich_deadline_seconds,
                    (
                        self._budget.deadline_seconds()
                        if self._budget else self._enrich_deadline_seconds
                    ),
                ),
                budget=self._budget,
                stop_event=self._stop_event,
                sources=metadata_sources,
            )
        except Exception:  # noqa: BLE001 - 可选补充失败只留缺口，不阻断交付
            payload = {
                "metadata": {},
                "records": [],
                "available": False,
                "notes": ["发表信息补充失败，本轮未核对 DOI/venue/引用量。"],
            }
            return self._completed(invocation, payload)
        self._enrich_records.extend(outcome.records)
        notes: list[str] = []
        if len(outcome.metadata) < len(candidates):
            notes.append("部分候选未核对到发表信息，缺失项保持未确认。")
        payload = {
            "metadata": {
                arxiv_id: {
                    "source": item.source,
                    "matched_title": item.matched_title,
                    "doi": item.doi,
                    "cited_by_count": item.cited_by_count,
                    "venue": item.venue,
                    "year": item.year,
                    "is_open_access": item.is_open_access,
                    "full_text_url": item.full_text_url,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                }
                for arxiv_id, item in outcome.metadata.items()
            },
            "records": [_record_payload(item) for item in outcome.records],
            "available": True,
            "notes": notes,
        }
        return self._completed(invocation, payload)

    def _evaluate(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation, NODE_PARSE)
        plan_payload = invocation.dependencies[NODE_PLAN].payload
        plan = PaperQueryPlan.model_validate((plan_payload.get("plans") or [{}])[0])
        screen_payload = invocation.dependencies[NODE_SCREEN].payload
        read_payload = invocation.dependencies[NODE_READ].payload
        enrich_payload = invocation.dependencies[NODE_ENRICH].payload
        screened_payload = screen_payload.get("screened") or []
        metadata = {
            arxiv_id: _metadata(item)
            for arxiv_id, item in (enrich_payload.get("metadata") or {}).items()
        }
        comparison_requested = self._comparison_requested(invocation)
        blocked = screen_payload.get("hard_condition_blocked")
        if not screened_payload:
            outcome = "empty"
            notes = list(screen_payload.get("notes") or ())
            if blocked == "year":
                notes.append(
                    "年份条件内没有主题匹配的真实结果；未放宽年份，保持空结果。"
                )
            elif blocked == "specified_paper":
                notes.append(
                    "没有检索到指定论文；未改用其他论文充当结果。"
                )
            elif blocked == "source":
                notes.append(
                    "来源条件内没有可用候选；未改用未允许的来源。"
                )
            else:
                notes.append("本轮没有取得主题匹配的真实论文，未凑篇数。")
            payload = {
                "recommendations": [],
                "selection": [],
                "identity_missing": [],
                "unsupported_claims": [],
                "hard_condition_violations": [],
                "outcome": outcome,
                "notes": notes,
                "comparison_requested": comparison_requested,
                "conflicts": [],
                "unconfirmed": ["本轮没有可交付的论文结果"],
            }
            return self._completed(invocation, payload)
        outcome = "success"
        candidates = [_candidate(item["candidate"]) for item in screened_payload]
        ranked = rank_candidates(
            analysis,
            plan,
            candidates,
            metadata,
            target_count=int(plan_payload.get("target_count") or DEFAULT_TARGET_COUNT),
            evidence_matches={
                item["candidate"]["arxiv_id"]: list(item["matched_requirements"])
                for item in screened_payload
            },
        )
        evidence_by_id = {
            item["candidate"]["arxiv_id"]: item for item in screened_payload
        }
        readings = {
            arxiv_id: _reading(item)
            for arxiv_id, item in (read_payload.get("readings") or {}).items()
        }
        recommendations: list[PaperRecommendation] = []
        identity_missing: list[str] = []
        unsupported: list[str] = []
        violations: list[str] = []
        for item in ranked.recommendations:
            enriched = metadata.get(item.arxiv_id or "")
            reading = readings.get(item.arxiv_id or "")
            evidence = evidence_by_id.get(item.arxiv_id or "", {})
            candidate = evidence.get("candidate") or {}
            claims = supported_claims(reading)
            item.doi = enriched.doi if enriched is not None else None
            item.venue = enriched.venue if enriched is not None else None
            item.cited_by_count = (
                enriched.cited_by_count if enriched is not None else None
            )
            item.full_text_url = (
                enriched.full_text_url if enriched is not None else None
            )
            item.read_scope = _scope(reading)
            item.full_text_available = item.read_scope is PaperReadScope.FULL_TEXT
            item.source_abstract = str(candidate.get("abstract") or "")
            item.match_evidence = [
                PaperRequirementEvidence(
                    requirement=str(entry.get("requirement") or ""),
                    source=str(entry.get("source") or ""),
                    quote=str(entry.get("quote") or ""),
                )
                for entry in evidence.get("evidence") or ()
            ]
            item.read_evidence = [
                PaperRequirementEvidence(
                    requirement=item.title,
                    source=f"full_text:{section.name}",
                    quote=section.text[:400],
                )
                for section in (reading.sections if reading is not None else ())
                if section.text.strip()
            ]
            item.supported_claims = claims
            if item.read_scope is not PaperReadScope.ABSTRACT and not claims:
                # 宣称读到正文却没有可定位的小节证据：不得冒充精读。
                unsupported.append(f"{item.title}：宣称正文级阅读但未定位到正文依据")
            item.unverified = _unverified(item, claims, reading)
            recommendations.append(item)
            if item.arxiv_id is None and item.doi is None:
                identity_missing.append(item.title)
            if _violates_year(analysis, item):
                violations.append(item.title)
            if _violates_specification(analysis, item):
                violations.append(item.title)
        if ranked.topic_mismatch:
            outcome = "topic_mismatch"
        selection = [
            PaperIdentity(
                order=item.order,
                arxiv_id=item.arxiv_id,
                doi=item.doi,
                title=item.title,
                published_year=item.published_year,
                abs_url=item.abs_url,
                content_hash=NodeArtifact.hash_payload(item.model_dump(mode="json")),
            )
            for item in recommendations
        ]
        conflicts = _conflicts(metadata, recommendations)
        payload = {
            "recommendations": [item.model_dump(mode="json") for item in recommendations],
            "selection": [item.model_dump(mode="json") for item in selection],
            "identity_missing": identity_missing,
            "unsupported_claims": unsupported,
            "hard_condition_violations": violations,
            "outcome": outcome,
            "notes": list(ranked.notes),
            "comparison_requested": comparison_requested,
            "conflicts": conflicts,
            "unconfirmed": [],
        }
        return self._completed(invocation, payload)

    def _comparison_requested(self, invocation: NodeInvocation) -> bool:
        """比较意图来自显式上下文或本轮阅读目标（parse 节点判定）。"""
        if self._context.comparison_requested:
            return True
        parse_payload = invocation.dependencies[NODE_PARSE].payload
        return str(parse_payload.get("reading_goal") or "") == GOAL_COMPARE

    def _verify(self, invocation: NodeInvocation) -> NodeExecution:
        evaluate_payload = invocation.dependencies[NODE_EVALUATE].payload
        recommendations = [
            PaperRecommendation.model_validate(item)
            for item in evaluate_payload.get("recommendations") or []
        ]
        unsupported = list(evaluate_payload.get("unsupported_claims") or [])
        for item in recommendations:
            if item.read_scope is PaperReadScope.ABSTRACT and item.supported_claims:
                # 跨版本防线：旧产物若在未读正文时留下正文断言，仍要阻塞。
                unsupported.append(f"{item.title}：{','.join(item.supported_claims)}")
        unconfirmed: list[str] = []
        for item in recommendations:
            unconfirmed.extend(item.unverified)
        unconfirmed.extend(evaluate_payload.get("unconfirmed") or [])
        triggered = bool(evaluate_payload.get("comparison_requested")) or bool(
            evaluate_payload.get("conflicts")
        )
        review: dict[str, Any] = {"triggered": triggered, "executed": False}
        if triggered and self._reviewer is not None:
            result = self._reviewer(
                recommendations,
                [str(item) for item in (evaluate_payload.get("conflicts") or [])],
            )
            if result is not None:
                # 裁决结构无效（非映射或缺少 passed）按未通过处理，不冒充通过。
                verdict = (
                    dict(result)
                    if isinstance(result, Mapping)
                    else {"passed": False, "reason": "invalid_review_payload"}
                )
                if verdict.get("passed") is not True:
                    verdict.setdefault("reason", "independent_review_not_passed")
                review = {"triggered": True, "executed": True, "result": verdict}
        notes = list(evaluate_payload.get("notes") or ())
        if triggered and not review.get("executed"):
            if self._reviewer is None:
                notes.append(
                    "本轮存在复杂比较或来源冲突，独立复核未装配，"
                    "相关结论按证据直接呈现，未冒充已复核。"
                )
            else:
                notes.append(
                    "本轮存在复杂比较或来源冲突，独立复核未取得有效裁决，"
                    "相关结论按证据直接呈现。"
                )
            unconfirmed.append("独立复核未执行（未装配或裁决无效）")
        passed_checks: list[str] = []
        if not evaluate_payload.get("identity_missing"):
            passed_checks.append("paper.identity_present")
        if not unsupported:
            passed_checks.append("paper.claims_have_evidence")
        if not evaluate_payload.get("hard_condition_violations"):
            passed_checks.append("paper.hard_conditions_hold")
        payload = {
            "passed_checks": passed_checks,
            "unsupported_claims": unsupported,
            "hard_condition_violations": list(
                evaluate_payload.get("hard_condition_violations") or []
            ),
            "unconfirmed": list(dict.fromkeys(unconfirmed)),
            "independent_review": review,
            "notes": notes,
        }
        return self._completed(
            invocation,
            payload,
            requirement_coverage=tuple(
                {"requirement": item.arxiv_id or item.title, "status": item.read_scope.value}
                for item in recommendations
            ),
            unconfirmed=tuple(payload["unconfirmed"]),
        )

    # -- 工具 ------------------------------------------------------------

    def _analysis(self, invocation: NodeInvocation, node: str) -> PaperTermAnalysis:
        return PaperTermAnalysis.model_validate(
            invocation.dependencies[node].payload["analysis"]
        )

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
            verdict=(
                QualityVerdict.REPAIRABLE_FAILURE
                if retryable
                else QualityVerdict.BLOCKED
            ),
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


def _reading(payload: Mapping[str, Any] | None) -> PaperReading | None:
    if not payload:
        return None
    from bridges.paper.reading import PaperSection

    return PaperReading(
        arxiv_id="",
        scope=str(payload.get("scope") or "abstract"),
        sections=tuple(
            PaperSection(
                name=str(item.get("name") or ""),
                text=str(item.get("text") or ""),
                locator=item.get("locator"),
            )
            for item in payload.get("sections") or ()
        ),
        notes=tuple(str(item) for item in payload.get("notes") or ()),
    )


def _scope(reading: PaperReading | None) -> PaperReadScope:
    if reading is None:
        return PaperReadScope.ABSTRACT
    if reading.scope == "full_text":
        return PaperReadScope.FULL_TEXT
    if reading.scope == "partial":
        return PaperReadScope.PARTIAL
    return PaperReadScope.ABSTRACT


def _unverified(
    item: PaperRecommendation,
    claims: list[str],
    reading: PaperReading | None,
) -> list[str]:
    notes = list(item.unverified)
    if _scope(reading) is PaperReadScope.ABSTRACT:
        notes.append("未读全文，方法/实验/局限断言不成立")
    if item.doi is None and item.venue is None:
        notes.append("未核对发表信息")
    if item.cited_by_count is not None:
        notes.append("引用量只作辅助信息，不代表质量或入门性")
    return list(dict.fromkeys(notes))


def _violates_year(analysis: PaperTermAnalysis, item: PaperRecommendation) -> bool:
    if item.published_year is None:
        return False
    year_from = analysis.constraints.year_from
    year_to = analysis.constraints.year_to
    if year_from is not None and item.published_year < year_from:
        return True
    return year_to is not None and item.published_year > year_to


def _violates_specification(
    analysis: PaperTermAnalysis, item: PaperRecommendation
) -> bool:
    """指定论文条件不得被其他论文替代（身份或标题不一致即违约）。"""
    constraints = analysis.constraints
    if constraints.arxiv_id:
        if item.arxiv_id is None:
            return True
        left = item.arxiv_id.split("v")[0].lower()
        right = constraints.arxiv_id.split("v")[0].lower()
        if left != right:
            return True
    title = constraints.paper_title
    if title:
        return " ".join(item.title.lower().split()) != " ".join(title.lower().split())
    return False


def _allowed_metadata_sources(analysis: PaperTermAnalysis) -> list[str]:
    """按来源硬条件计算允许调用的元数据补充来源（空表示不得调用）。"""
    constraints = analysis.constraints
    allowed = {item.lower() for item in constraints.allowed_sources}
    excluded = {item.lower() for item in constraints.excluded_sources}
    return [
        source
        for source in (CROSSREF_SOURCE, OPENALEX_SOURCE)
        if source not in excluded and (not allowed or source in allowed)
    ]


def _conflicts(
    metadata: dict[str, EnrichedMetadata], recommendations: list[PaperRecommendation]
) -> list[str]:
    conflicts: list[str] = []
    for item in recommendations:
        enriched = metadata.get(item.arxiv_id or "")
        if enriched is None or enriched.year is None or item.published_year is None:
            continue
        if abs(enriched.year - item.published_year) > 1:
            conflicts.append(
                f"{item.title}：来源年份 {item.published_year} 与核对年份 {enriched.year} 不一致"
            )
    return conflicts


def _source_refs(payload: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for candidate in payload.get("candidates") or ():
        arxiv_id = candidate.get("arxiv_id")
        if arxiv_id:
            refs.append(str(arxiv_id))
    for item in payload.get("selection") or ():
        if isinstance(item, Mapping) and item.get("arxiv_id"):
            refs.append(str(item["arxiv_id"]))
    return tuple(dict.fromkeys(refs))


def _payload_read_scope(payload: Mapping[str, Any]) -> str:
    if "readings" in payload:
        scopes = {item.get("scope") for item in payload["readings"].values()}
        if scopes == {"abstract"}:
            return "abstract"
        if "full_text" in scopes:
            return "full_text"
        return "partial"
    return "none"


__all__ = [
    "DEFAULT_DEEP_READ_MAX",
    "DEFAULT_SCREEN_MAX",
    "NODE_ENRICH",
    "NODE_EVALUATE",
    "NODE_PARSE",
    "NODE_PLAN",
    "NODE_READ",
    "NODE_SCREEN",
    "NODE_SEARCH",
    "NODE_VERIFY",
    "PAPER_CAPABILITY_VERSIONS",
    "PAPER_GATE_HANDLERS",
    "PAPER_GATES",
    "PAPER_NODE_LABELS",
    "PAPER_RECIPE_ID",
    "PAPER_RECIPE_VERSION",
    "PaperBudget",
    "PaperFlowContext",
    "PaperNodeFlow",
    "build_paper_recipe",
    "paper_recipe_registry",
]
