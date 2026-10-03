"""GitHub 项目推荐配方的持久节点实现（改进工单 26）。

配方的必经顺序：

    github.parse → github.plan → github.search → github.read
      → github.match → github.evaluate → github.verify → github.present

每个节点只接收最小任务/版本/运行/节点引用、依赖产物与剩余预算，输出
类型化产物与完成收据：解析出的 idea 与限制、有界检索计划、真实候选与
查询记录、真实取得的仓库证据、逐项需求证据矩阵、排序与覆盖判定、结构化
核验、以及待交付投影（借鉴角度模型调用留在交付 seam，接入 04 预算合同）。

恢复由完成收据驱动：检索失败而证据可用时按输入键回填；限流只保留已完成
检查，未读候选不伪装核实。运行类需求永远停在「未确认」——静态读取不等于
实际运行。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from threading import Event
from typing import TYPE_CHECKING, Any

from bridges.ai.model_quota import RunModelQuota
from bridges.ai.payload_budget import CallMaterialManifest
from bridges.contracts.modules import (
    ModuleQueryRecord,
    ModuleQueryStatus,
)
from bridges.contracts.workflows import RunContextEnvelope
from bridges.github.contracts import (
    GithubIdeaAnalysis,
    GithubRecommendation,
    GithubRepositoryCandidate,
    GithubRepositoryEvidence,
    GithubRequirementInput,
    GithubRequirementKind,
    GithubSearchPlan,
    GithubSupportLevel,
)
from bridges.github.inspecting import GithubRepositoryReader, InspectionOutcome
from bridges.github.lexicon import LICENSE_TERMS, wants_implementation_evidence
from bridges.github.parsing import parse_github_request
from bridges.github.presenting import GithubInsightGenerator, InsightOutcome
from bridges.github.ranking import identity_relevance, match_features, rank_candidates
from bridges.github.searching import (
    FAILED_QUERY_STATUSES,
    MIN_WHOLE_RESULTS,
    GithubSearchPort,
    plan_queries,
)
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

if TYPE_CHECKING:
    from bridges.chat.task_materials import ModuleTaskContext

#: 配方节点名（进度事件、失败定位与产物身份）。
NODE_PARSE = "github.parse"
NODE_PLAN = "github.plan"
NODE_SEARCH = "github.search"
NODE_READ = "github.read"
NODE_MATCH = "github.match"
NODE_EVALUATE = "github.evaluate"
NODE_VERIFY = "github.verify"
NODE_PRESENT = "github.present"

GITHUB_RECIPE_ID = "github-project-recommendation"
GITHUB_RECIPE_VERSION = "github-recipe-v1"

#: 节点的用户可读中文名（父图失败信息按此标注真实失败位置）。
GITHUB_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "理解项目想法",
    NODE_PLAN: "规划检索与核查",
    NODE_SEARCH: "检索公开仓库",
    NODE_READ: "核对仓库证据",
    NODE_MATCH: "逐项匹配必要功能",
    NODE_EVALUATE: "按必要功能排序",
    NODE_VERIFY: "核验矩阵与许可说法",
    NODE_PRESENT: "整理推荐结果",
}

#: 已登记的确定性能力与版本（代码拒绝未登记能力）。
GITHUB_CAPABILITY_VERSIONS: dict[str, str] = {
    "github.parse_request": "github-parse-v1",
    "github.plan_search": "github-plan-v1",
    "github.search_repositories": "github-search-v1",
    "github.read_evidence": "github-read-v1",
    "github.match_requirements": "github-match-v1",
    "github.evaluate_candidates": "github-evaluate-v1",
    "github.verify_matrix": "github-verify-v1",
    "github.present_result": "github-present-v1",
}

#: 配方的必要门与可选门（登记集合；代码拒绝未登记质量门）。
GITHUB_GATES: frozenset[str] = frozenset(
    {
        "github.required_matrix",
        "github.version_sources",
        "github.license_disclosure",
    }
)

#: 各阶段的墙钟预算：检索（允许上游两次查询与收尾）与证据读取。
SEARCH_DEADLINE_SECONDS = 20.0
INSPECT_DEADLINE_SECONDS = 25.0


def _digest(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


def _requires_implementation(analysis: GithubIdeaAnalysis) -> bool:
    """本轮是否要求实现/运行证据（决定读取阶段是否按结论打开实现文件）。"""
    return (
        bool(analysis.constraints.runtime)
        or wants_implementation_evidence(analysis.original_request)
        or any(wants_implementation_evidence(feature) for feature in analysis.features)
    )


# ---------------------------------------------------------------------------
# 质量门（结构化裁决；模型不能自行宣布通过）
# ---------------------------------------------------------------------------


def _required_matrix_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要门：矩阵行必须有支持层次；整体覆盖不得含有未支持/未确认的必要功能。"""
    del invocation
    payload = execution.artifact.payload
    rows = payload.get("matrix") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("kind") != GithubRequirementKind.REQUIRED.value:
            continue
        if not row.get("support_level"):
            return QualityGateResult(
                gate="github.required_matrix",
                verdict=QualityVerdict.BLOCKED,
                code="github_matrix_incomplete",
                message="必要功能矩阵缺少支持层次，本轮不交付覆盖结论。",
            )
    for repo, info in (payload.get("coverage") or {}).items():
        if not isinstance(info, dict):
            continue
        if info.get("whole") and info.get("required_unresolved"):
            return QualityGateResult(
                gate="github.required_matrix",
                verdict=QualityVerdict.BLOCKED,
                code="github_whole_with_gaps",
                message=(
                    f"{repo} 的必要功能仍有未支持/未确认项，不能判为整体适配"
                    "（高比例与 star 不能抵消）。"
                ),
            )
    return QualityGateResult(gate="github.required_matrix", verdict=QualityVerdict.PASS)


def _version_sources_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要门：命中的证据必须能定位到版本或取得时间与读取范围。"""
    del invocation
    payload = execution.artifact.payload
    for row in payload.get("matrix") or []:
        if not isinstance(row, dict) or not row.get("matched"):
            continue
        # 命中行（必要/可选/约束）都必须能指回具体来源与读取范围。
        if not row.get("has_sources"):
            return QualityGateResult(
                gate="github.version_sources",
                verdict=QualityVerdict.REPAIRABLE_FAILURE,
                code="github_version_missing",
                message="有命中的需求行缺少来源定位，本轮不交付该行结论。",
            )
    for repo, version in (payload.get("versions") or {}).items():
        if not isinstance(version, dict) or not version.get("present"):
            return QualityGateResult(
                gate="github.version_sources",
                verdict=QualityVerdict.REPAIRABLE_FAILURE,
                code="github_version_missing",
                message=f"{repo} 的证据缺少版本或取得时间定位，本轮不交付确定结论。",
            )
    return QualityGateResult(gate="github.version_sources", verdict=QualityVerdict.PASS)


def _license_disclosure_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要门：许可未知不得被矩阵或正文说成可自由复用。"""
    del invocation
    payload = execution.artifact.payload
    for repo, info in (payload.get("licenses") or {}).items():
        if not isinstance(info, dict):
            continue
        if info.get("supported_claims") and not info.get("detected"):
            return QualityGateResult(
                gate="github.license_disclosure",
                verdict=QualityVerdict.BLOCKED,
                code="github_license_claim_without_evidence",
                message=f"{repo} 在没有许可证据时给出了许可满足结论，本轮不交付。",
            )
        if not info.get("detected") and not info.get("disclosed_unknown"):
            return QualityGateResult(
                gate="github.license_disclosure",
                verdict=QualityVerdict.REPAIRABLE_FAILURE,
                code="github_license_unknown_undisclosed",
                message=f"{repo} 的许可不可得，但交付里没有如实说明保持未知。",
            )
    return QualityGateResult(gate="github.license_disclosure", verdict=QualityVerdict.PASS)


#: 配方质量门处理器（与配方登记的门名一一对应；内核据此执行结构化裁决）。
GITHUB_GATE_HANDLERS: dict[str, Any] = {
    "github.required_matrix": _required_matrix_gate,
    "github.version_sources": _version_sources_gate,
    "github.license_disclosure": _license_disclosure_gate,
}


# ---------------------------------------------------------------------------
# 配方与输入键
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
    return _digest(inputs.artifacts[NODE_PARSE].content_hash)


def _search_key(inputs: Any) -> str:
    return _digest(
        {
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
        }
    )


def _read_key(inputs: Any) -> str:
    return _digest(
        {
            "search": inputs.artifacts[NODE_SEARCH].content_hash,
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
        }
    )


def _match_key(inputs: Any) -> str:
    return _digest(
        {
            "read": inputs.artifacts[NODE_READ].content_hash,
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
        }
    )


def _evaluate_key(inputs: Any) -> str:
    return _digest(inputs.artifacts[NODE_MATCH].content_hash)


def _verify_key(inputs: Any) -> str:
    return _digest(inputs.artifacts[NODE_EVALUATE].content_hash)


def _present_key(inputs: Any) -> str:
    return _digest(
        {
            "verify": inputs.artifacts[NODE_VERIFY].content_hash,
            "evaluate": inputs.artifacts[NODE_EVALUATE].content_hash,
        }
    )


def build_github_recipe() -> RecipeDefinition:
    """构造并校验 GitHub 推荐配方（必经顺序与依赖只指向前置节点）。"""
    return RecipeDefinition(
        recipe_id=GITHUB_RECIPE_ID,
        recipe_version=GITHUB_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_PARSE,
                capability="github.parse_request",
                capability_version=GITHUB_CAPABILITY_VERSIONS["github.parse_request"],
                artifact_type="github.idea_analysis",
                input_key=_parse_key,
                recovery=RecoveryPolicy.ASK_INPUT,
                description="理解项目想法、必要/可选功能与技术/许可/运行限制。",
            ),
            NodeSpec(
                name=NODE_PLAN,
                capability="github.plan_search",
                capability_version=GITHUB_CAPABILITY_VERSIONS["github.plan_search"],
                artifact_type="github.search_plan",
                input_key=_plan_key,
                depends_on=(NODE_PARSE,),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="有界查询词与重点核查要求（整体优先或组件）。",
            ),
            NodeSpec(
                name=NODE_SEARCH,
                capability="github.search_repositories",
                capability_version=GITHUB_CAPABILITY_VERSIONS["github.search_repositories"],
                artifact_type="github.search_results",
                input_key=_search_key,
                depends_on=(NODE_PARSE, NODE_PLAN),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="按计划发出有界检索，记录真实查询词与限流状态。",
            ),
            NodeSpec(
                name=NODE_READ,
                capability="github.read_evidence",
                capability_version=GITHUB_CAPABILITY_VERSIONS["github.read_evidence"],
                artifact_type="github.repository_evidence",
                input_key=_read_key,
                depends_on=(NODE_SEARCH,),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="两遍读取元数据/README/许可/实现文件并记录提交版本。",
            ),
            NodeSpec(
                name=NODE_MATCH,
                capability="github.match_requirements",
                capability_version=GITHUB_CAPABILITY_VERSIONS["github.match_requirements"],
                artifact_type="github.requirement_matrix",
                input_key=_match_key,
                depends_on=(NODE_PARSE, NODE_READ),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="逐项需求给出支持层次、来源定位与缺口。",
            ),
            NodeSpec(
                name=NODE_EVALUATE,
                capability="github.evaluate_candidates",
                capability_version=GITHUB_CAPABILITY_VERSIONS["github.evaluate_candidates"],
                artifact_type="github.ranking",
                input_key=_evaluate_key,
                depends_on=(NODE_PARSE, NODE_SEARCH, NODE_READ, NODE_MATCH),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="必要功能优先排序；可选项与 star 不抵消缺口。",
            ),
            NodeSpec(
                name=NODE_VERIFY,
                capability="github.verify_matrix",
                capability_version=GITHUB_CAPABILITY_VERSIONS["github.verify_matrix"],
                artifact_type="github.verification",
                input_key=_verify_key,
                depends_on=(NODE_PARSE, NODE_READ, NODE_MATCH, NODE_EVALUATE),
                required_gates=(
                    "github.required_matrix",
                    "github.version_sources",
                    "github.license_disclosure",
                ),
                recovery=RecoveryPolicy.BLOCK,
                description="核对矩阵完整性、版本来源与许可说法（结构化裁决）。",
            ),
            NodeSpec(
                name=NODE_PRESENT,
                capability="github.present_result",
                capability_version=GITHUB_CAPABILITY_VERSIONS["github.present_result"],
                artifact_type="github.delivery",
                input_key=_present_key,
                depends_on=(NODE_EVALUATE, NODE_VERIFY),
                recovery=RecoveryPolicy.BLOCK,
                description="借鉴角度（接入 04 预算合同），交付由父图统一提交。",
            ),
        ),
    )


def github_recipe_registry() -> RecipeRegistry:
    """登记 GitHub 能力、质量门与配方；非法定义在装配时即被拒绝。"""
    registry = RecipeRegistry(
        capabilities=GITHUB_CAPABILITY_VERSIONS.keys(),
        gates=GITHUB_GATES,
    )
    registry.register(build_github_recipe())
    return registry


class GithubNodeFlow:
    """GitHub 节点的确定性执行体（模型调用只在交付阶段，严格门控）。"""

    def __init__(
        self,
        *,
        search: GithubSearchPort,
        reader: GithubRepositoryReader,
        clock: Callable[[], datetime] | None = None,
        prior_context: Sequence[Any] = (),
        module_context: ModuleTaskContext | None = None,
        pending_wait: Any = None,
        requirement: GithubRequirementInput | None = None,
        stop_event: Event | None = None,
        search_deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        inspect_deadline_seconds: float = INSPECT_DEADLINE_SECONDS,
        insights: GithubInsightGenerator | None = None,
        run_context: RunContextEnvelope | None = None,
        run_model_id: str | None = None,
        model_quota: RunModelQuota | None = None,
        manifest_sink: Callable[[CallMaterialManifest], None] | None = None,
    ) -> None:
        self._search = search
        self._reader = reader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._prior_context = tuple(prior_context)
        self._module_context = module_context
        self._pending_wait = pending_wait
        self._requirement = requirement
        self._stop_event = stop_event
        self._search_deadline_seconds = search_deadline_seconds
        self._inspect_deadline_seconds = inspect_deadline_seconds
        self._insights = insights
        self._run_context = run_context
        self._run_model_id = run_model_id
        self._model_quota = model_quota
        self._manifest_sink = manifest_sink
        self.last_insight: InsightOutcome = InsightOutcome()

    @property
    def prior_digest(self) -> str | None:
        material: dict[str, Any] = {
            "prior": [
                (
                    getattr(anchor, "kind", None),
                    getattr(anchor, "phrase", None),
                    getattr(anchor, "message_id", None),
                )
                for anchor in self._prior_context
            ],
            "conditions": (
                [
                    (condition.condition_id, condition.kind, condition.text)
                    for condition in self._module_context.effective_conditions
                ]
                if self._module_context is not None
                and self._module_context.used_task_scope
                else []
            ),
            "requirement": (
                self._requirement.model_dump(mode="json")
                if self._requirement is not None
                else None
            ),
        }
        if not any(material.values()):
            return None
        return _digest(material)

    # -- 节点执行体 -------------------------------------------------------

    def run_node(self, invocation: NodeInvocation) -> NodeExecution:
        handler = {
            NODE_PARSE: self._run_parse,
            NODE_PLAN: self._run_plan,
            NODE_SEARCH: self._run_search,
            NODE_READ: self._run_read,
            NODE_MATCH: self._run_match,
            NODE_EVALUATE: self._run_evaluate,
            NODE_VERIFY: self._run_verify,
            NODE_PRESENT: self._run_present,
        }[invocation.spec.name]
        try:
            return handler(invocation)
        except Exception as exc:  # noqa: BLE001 - 未预期异常按可重试失败收敛
            return self._failure_execution(
                invocation,
                payload={"error": {"type": exc.__class__.__name__}},
                node=invocation.spec.name,
                verdict=QualityVerdict.REPAIRABLE_FAILURE,
                status=NodeReceiptStatus.FAILED,
                code="github_node_error",
                message=f"节点执行出现内部错误（{exc.__class__.__name__}）。",
                retryable=True,
            )

    def _run_parse(self, invocation: NodeInvocation) -> NodeExecution:
        conditions = (
            self._module_context.effective_conditions
            if self._module_context is not None and self._module_context.used_task_scope
            else ()
        )
        analysis = parse_github_request(
            invocation.inputs.user_content,
            prior_context=self._prior_context,
            pending=self._pending_wait,
            task_conditions=conditions,
            requirement=self._requirement,
        )
        payload: dict[str, Any] = {"analysis": analysis.model_dump(mode="json")}
        if analysis.clarification is not None:
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
                    trust_state=ArtifactTrust.DRAFT,
                    payload=payload,
                    unconfirmed=[analysis.clarification.question],
                ),
                verdict=QualityVerdict.NEED_INPUT,
                status=NodeReceiptStatus.NEEDS_INPUT,
                detail={
                    "code": "github_clarification",
                    "message": analysis.clarification.question,
                    "retryable": False,
                    "node": NODE_PARSE,
                },
                stop_recipe=True,
                recovery=RecoveryPolicy.ASK_INPUT,
            )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.DRAFT,
                payload=payload,
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_plan(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        implementation_required = _requires_implementation(analysis)
        queries = plan_queries(analysis)
        requirements: list[str] = []
        if implementation_required:
            requirements.append("必要功能需要实现或运行证据：按结论读取入口/实现文件")
        else:
            requirements.append("先读 API 元数据与 README；按 README 结论核对实现路径")
        if analysis.constraints.license:
            requirements.append("用户提出了许可条件：核对许可文件或元数据标注")
        if analysis.constraints.runtime:
            requirements.append("用户要求能跑：静态读取只能标未确认，不得声称已运行")
        if analysis.constraints.excluded:
            requirements.append("用户明确排除的词：在证据文本中核对是否出现")
        plan = GithubSearchPlan(
            strategy="whole_first" if analysis.whole_idea else "component",
            queries=list(queries),
            check_requirements=requirements,
            implementation_required=implementation_required,
            rationale=(
                "先查整体相近项目；召回不足时用要点查询补组件候选。"
                "可选项与 star 不参与必要功能覆盖判定。"
            ),
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={"plan": plan.model_dump(mode="json")},
                read_scope="检索计划（原词查询）",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_search(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        plan = GithubSearchPlan.model_validate(
            self._dep(invocation, NODE_PLAN)["plan"]
        )
        candidates: list[Any] = []
        seen: set[str] = set()
        records: list[ModuleQueryRecord] = []
        reset_at: datetime | None = None
        deadline = self._external_deadline(invocation, self._search_deadline_seconds)
        for index, query in enumerate(plan.queries):
            if index > 0 and len(candidates) >= MIN_WHOLE_RESULTS:
                break
            if self._stop_event is not None and self._stop_event.is_set():
                break
            outcome = self._search.search_repositories(
                invocation.account_id,
                query=query,
                reason="GitHub 项目推荐：只发送最小公开查询词",
                stop_event=self._stop_event,
                deadline=deadline,
            )
            records.append(outcome.record)
            if outcome.reset_at is not None:
                reset_at = outcome.reset_at
            for candidate in outcome.candidates:
                if candidate.full_name in seen:
                    continue
                seen.add(candidate.full_name)
                candidates.append(candidate)
            if not outcome.record.retryable and not outcome.candidates:
                break
            if (
                outcome.record.status in FAILED_QUERY_STATUSES
                and outcome.record.retryable
            ):
                break
        ordered = sorted(
            candidates,
            key=lambda item: -identity_relevance(
                analysis,
                full_name=item.full_name,
                description=item.description,
                topics=list(item.topics),
            ),
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={
                    "candidates": [
                        candidate.model_dump(mode="json") for candidate in ordered
                    ],
                    "queries": [record.model_dump(mode="json") for record in records],
                    "reset_at": reset_at.isoformat() if reset_at is not None else None,
                },
                source_refs=[record.source for record in records],
                read_scope="GitHub 公开仓库检索",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_read(self, invocation: NodeInvocation) -> NodeExecution:
        search_payload = self._dep(invocation, NODE_SEARCH)
        candidates = [
            _candidate_model(item) for item in search_payload.get("candidates", [])
        ]
        if not candidates:
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
                    trust_state=ArtifactTrust.EVIDENCE_BOUND,
                    payload={"evidence": [], "queries": [], "rate_limited": False},
                    read_scope="没有候选可读取",
                ),
                verdict=QualityVerdict.PASS,
                status=NodeReceiptStatus.COMPLETED,
            )
        outcome: InspectionOutcome = self._reader.inspect_candidates(
            invocation.account_id,
            candidates,
            stop_event=self._stop_event,
            deadline=self._external_deadline(invocation, self._inspect_deadline_seconds),
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={
                    "evidence": [
                        item.model_dump(mode="json") for item in outcome.evidence
                    ],
                    "queries": [record.model_dump(mode="json") for record in outcome.records],
                    "rate_limited": outcome.rate_limited,
                    "reset_at": (
                        outcome.reset_at.isoformat()
                        if outcome.reset_at is not None
                        else None
                    ),
                },
                source_refs=[record.source for record in outcome.records],
                read_scope="元数据/README/许可/实现文件（含提交版本）",
                unconfirmed=(
                    ["上游额度限制：部分候选的深入核查未完成"]
                    if outcome.rate_limited
                    else []
                ),
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_match(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        read_payload = self._dep(invocation, NODE_READ)
        matrix: dict[str, list[dict[str, Any]]] = {}
        for raw in read_payload.get("evidence", []):
            evidence = _evidence_model(raw)
            rows = match_features(analysis, evidence)
            matrix[evidence.full_name] = [
                row.model_dump(mode="json") for row in rows
            ]
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={"matrix": matrix},
                read_scope="逐项需求证据矩阵",
                unconfirmed=_matrix_unconfirmed(matrix),
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_evaluate(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        matrix = self._dep(invocation, NODE_MATCH)["matrix"]
        read_payload = self._dep(invocation, NODE_READ)
        search_payload = self._dep(invocation, NODE_SEARCH)
        evidence_by_name = {
            item.full_name: item
            for item in (_evidence_model(raw) for raw in read_payload.get("evidence", []))
        }
        records = [
            ModuleQueryRecord.model_validate(item)
            for item in (
                list(search_payload.get("queries", []))
                + list(read_payload.get("queries", []))
            )
        ]
        inspected = set(evidence_by_name)
        uninspected = []
        for item in search_payload.get("candidates", []):
            candidate = _candidate_model(item)
            if candidate.full_name not in inspected:
                uninspected.append(candidate)
        rate_limited = bool(read_payload.get("rate_limited")) or any(
            record.status is ModuleQueryStatus.RATE_LIMITED for record in records
        )
        ranked = rank_candidates(
            analysis,
            list(evidence_by_name.values()),
            uninspected=uninspected,
            rate_limited=rate_limited,
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={
                    "ranked": {
                        "recommendations": [
                            item.model_dump(mode="json")
                            for item in ranked.recommendations
                        ],
                        "rejected": [
                            item.model_dump(mode="json") for item in ranked.rejected
                        ],
                    },
                    "rate_limited": rate_limited,
                    "reset_at": read_payload.get("reset_at")
                    or search_payload.get("reset_at"),
                    "matrix": matrix,
                },
                read_scope="必要功能优先排序与覆盖判定",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_verify(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        matrix = self._dep(invocation, NODE_MATCH)["matrix"]
        read_payload = self._dep(invocation, NODE_READ)
        evidence_by_name = {
            item.full_name: item
            for item in (_evidence_model(raw) for raw in read_payload.get("evidence", []))
        }
        verification_rows: list[dict[str, Any]] = []
        coverage: dict[str, dict[str, Any]] = {}
        licenses: dict[str, dict[str, Any]] = {}
        versions: dict[str, dict[str, Any]] = {}
        unconfirmed: list[str] = []
        for full_name, rows in matrix.items():
            evidence = evidence_by_name.get(full_name)
            if evidence is None:
                continue
            whole = True
            unresolved: list[str] = []
            for row in rows:
                kind = row.get("kind")
                level = row.get("support_level")
                matched = bool(row.get("matched"))
                sources = row.get("sources") or []
                has_sources = bool(
                    sources
                    and any(
                        (source.get("locator") or source.get("read_range"))
                        for source in sources
                    )
                )
                if not matched and kind in {
                    GithubRequirementKind.REQUIRED.value,
                    GithubRequirementKind.CONSTRAINT.value,
                }:
                    feature = str(row.get("feature") or "")
                    if (
                        kind == GithubRequirementKind.REQUIRED.value
                        and level
                        in {
                            GithubSupportLevel.UNCONFIRMED.value,
                            GithubSupportLevel.UNSUPPORTED.value,
                        }
                    ):
                        unresolved.append(feature)
                    if (
                        kind == GithubRequirementKind.CONSTRAINT.value
                        and feature not in analysis.constraints.excluded
                        and level
                        in {
                            GithubSupportLevel.UNCONFIRMED.value,
                            GithubSupportLevel.UNSUPPORTED.value,
                        }
                    ):
                        unresolved.append(feature)
                    unconfirmed.append(f"{full_name}: {feature}")
                verification_rows.append(
                    {
                        "repo": full_name,
                        "feature": row.get("feature"),
                        "kind": kind,
                        "support_level": level,
                        "matched": matched,
                        "has_sources": has_sources,
                    }
                )
            if unresolved:
                whole = False
            coverage[full_name] = {"whole": whole, "required_unresolved": unresolved}
            version = evidence.version
            # 版本依据缺失时至少保留取得时间（每条证据都有 retrieved_at），
            # 不把「有取得时间」说成「有提交版本」。
            versions[full_name] = {
                "present": evidence.retrieved_at is not None,
                "commit_sha": version.commit_sha if version is not None else None,
                "source": version.source if version is not None else "fetch_time",
            }
            license_rows = [
                row
                for row in rows
                if row.get("kind") == GithubRequirementKind.CONSTRAINT.value
                and row.get("feature") in LICENSE_TERMS
            ]
            supported_claims = [
                row
                for row in license_rows
                if row.get("support_level")
                in {
                    GithubSupportLevel.DOCUMENTED.value,
                    GithubSupportLevel.STATIC_IMPLEMENTATION.value,
                }
            ]
            disclosed_unknown = (
                not evidence.license.detected and not supported_claims
            )
            licenses[full_name] = {
                "detected": evidence.license.detected,
                "file_read": evidence.license.file_read,
                "supported_claims": bool(supported_claims),
                "disclosed_unknown": disclosed_unknown,
            }
        summary = (
            f"核验完成：{len(evidence_by_name)} 个仓库的矩阵、版本与许可说法；"
            f"未确认项 {len(unconfirmed)} 条。"
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.QUALIFIED,
                payload={
                    "matrix": verification_rows,
                    "coverage": coverage,
                    "licenses": licenses,
                    "versions": versions,
                    "unconfirmed": unconfirmed,
                    "summary": summary,
                },
                read_scope="矩阵完整性、版本来源与许可说法核验",
                unconfirmed=unconfirmed,
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_present(self, invocation: NodeInvocation) -> NodeExecution:
        ranked = self._dep(invocation, NODE_EVALUATE)["ranked"]
        recommendations = [
            GithubRecommendation.model_validate(item)
            for item in ranked.get("recommendations", [])
        ]
        insight = self._generate_insights(recommendations)
        self.last_insight = insight
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.QUALIFIED,
                payload={
                    "insights": dict(insight.insights),
                    "note": insight.note,
                    "model_called": insight.lock is not None,
                },
                read_scope="借鉴角度（模型在证据范围内的归纳）",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _generate_insights(
        self, recommendations: Sequence[GithubRecommendation]
    ) -> InsightOutcome:
        if self._insights is None or not recommendations:
            return InsightOutcome()
        if self._run_context is None:
            return InsightOutcome(
                note="借鉴角度未生成（本轮没有可用的运行上下文），只给证据本身。"
            )
        result = self._insights.generate(
            self._run_context,
            list(recommendations),
            model_id=self._run_model_id,
            model_quota=self._model_quota,
        )
        if result.manifest is not None and self._manifest_sink is not None:
            self._manifest_sink(result.manifest)
        return result

    # -- 内部工具 ---------------------------------------------------------

    def _analysis(self, invocation: NodeInvocation) -> GithubIdeaAnalysis:
        return GithubIdeaAnalysis.model_validate(
            self._dep(invocation, NODE_PARSE)["analysis"]
        )

    def _dep(self, invocation: NodeInvocation, node: str) -> dict[str, Any]:
        return dict(invocation.dependencies[node].payload)

    def _artifact(
        self,
        invocation: NodeInvocation,
        *,
        trust_state: ArtifactTrust,
        payload: dict[str, Any],
        source_refs: Sequence[str] = (),
        read_scope: str = "",
        requirement_coverage: Sequence[Mapping[str, Any]] = (),
        unconfirmed: Sequence[str] = (),
        error: dict[str, Any] | None = None,
    ) -> NodeArtifact:
        inputs = invocation.inputs
        dependencies = tuple(
            InputDependency(
                node=name,
                artifact_id=artifact.artifact_id,
                content_hash=artifact.content_hash,
            )
            for name, artifact in invocation.dependencies.items()
        )
        return NodeArtifact.build(
            account_id=inputs.account_id,
            conversation_id=inputs.conversation_id,
            run_id=inputs.run_id,
            task_id=inputs.task_id,
            task_version=inputs.task_version,
            recipe_id=GITHUB_RECIPE_ID,
            recipe_version=GITHUB_RECIPE_VERSION,
            node=invocation.spec.name,
            artifact_type=invocation.spec.artifact_type,
            capability_version=invocation.spec.capability_version,
            trust_state=trust_state,
            input_key=invocation.spec.input_key(inputs),
            input_deps=dependencies,
            source_refs=tuple(source_refs),
            read_scope=read_scope,
            requirement_coverage=tuple(dict(item) for item in requirement_coverage),
            unconfirmed=tuple(unconfirmed),
            error=error,
            payload=payload,
            now=self._clock(),
        )

    def _failure_execution(
        self,
        invocation: NodeInvocation,
        *,
        payload: dict[str, Any],
        node: str,
        verdict: QualityVerdict,
        status: NodeReceiptStatus,
        code: str,
        message: str,
        retryable: bool,
        stopped: bool = False,
    ) -> NodeExecution:
        artifact = self._artifact(
            invocation,
            trust_state=ArtifactTrust.INVALIDATED,
            payload=payload,
            error={"code": code, "message": message, "retryable": retryable},
        )
        return NodeExecution(
            artifact=artifact,
            verdict=verdict,
            status=status,
            detail={
                "code": code,
                "message": message,
                "retryable": retryable,
                "node": node,
                "stopped": stopped,
            },
            stop_recipe=True,
            recovery=invocation.spec.recovery,
        )

    def _external_deadline(self, invocation: NodeInvocation, seconds: float) -> float:
        """本地截止与运行预算取较小者：预算用尽时不再空等上游。"""
        remaining = seconds
        if invocation.remaining_budget_ms is not None:
            remaining = min(remaining, invocation.remaining_budget_ms / 1000.0)
        return time.monotonic() + max(0.5, remaining)


def _matrix_unconfirmed(matrix: Mapping[str, list[dict[str, Any]]]) -> list[str]:
    notes: list[str] = []
    for full_name, rows in matrix.items():
        for row in rows:
            if row.get("support_level") in {
                GithubSupportLevel.UNCONFIRMED.value,
                GithubSupportLevel.UNSUPPORTED.value,
            }:
                notes.append(f"{full_name}: {row.get('feature')}")
    return notes


def _candidate_model(raw: Any) -> GithubRepositoryCandidate:
    return GithubRepositoryCandidate.model_validate(raw)


def _evidence_model(raw: Any) -> GithubRepositoryEvidence:
    return GithubRepositoryEvidence.model_validate(raw)


__all__ = [
    "GITHUB_CAPABILITY_VERSIONS",
    "GITHUB_GATE_HANDLERS",
    "GITHUB_GATES",
    "GITHUB_NODE_LABELS",
    "GITHUB_RECIPE_ID",
    "GITHUB_RECIPE_VERSION",
    "INSPECT_DEADLINE_SECONDS",
    "NODE_EVALUATE",
    "NODE_MATCH",
    "NODE_PARSE",
    "NODE_PLAN",
    "NODE_PRESENT",
    "NODE_READ",
    "NODE_SEARCH",
    "NODE_VERIFY",
    "SEARCH_DEADLINE_SECONDS",
    "GithubNodeFlow",
    "build_github_recipe",
    "github_recipe_registry",
]
