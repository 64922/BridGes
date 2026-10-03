"""学习资料配方的持久节点实现（改进工单 25）。

配方顺序（与 ``docs/workflow/daily-workflows.md`` 的学习资料段落一致）：

    resources.parse → resources.plan
      → (resources.search_books ∥ resources.search_videos)
      → resources.read → resources.match → resources.organize → resources.verify

书与视频两路检索登记在同一并行组（并发上限 2）且互不依赖，共用运行预算；
组内某个节点失败时，另一个已完成节点的产物仍然保留（部分交付由领域层按
产物解释）。读取节点只读取真实页面并记录实际范围；匹配与组织都把证据层次
写进产物；``resources.verify`` 的必需门核对「主线条目全部有证据支持、原始
说法被覆盖、实际读取范围非空」，未通过就不交付。
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from threading import Event
from typing import TYPE_CHECKING, Any

from bridges.contracts.modules import (
    ModuleQueryRecord,
    ModuleQueryStatus,
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
from bridges.resources.contracts import (
    BookReadEvidence,
    LearningResourcesProjection,
    ResourceEvidenceLevel,
    ResourceItem,
    ResourceRole,
    ResourcesQueryPlan,
    ResourcesStatus,
    ResourcesTermAnalysis,
)
from bridges.resources.matching import (
    EvaluatedBook,
    EvaluatedVideo,
    MatchOutcome,
    cover_original_phrase,
    match_resources,
)
from bridges.resources.organizing import OrganizeOutcome, organize_resources
from bridges.resources.parsing import level_label, parse_resources_request
from bridges.resources.planning import plan_resources
from bridges.resources.reading import BookInsightReader
from bridges.resources.sources import (
    BILIBILI_SOURCE,
    BOOK_CATALOG_SOURCE,
    TAVILY_SOURCE,
    BilibiliVideoDiscoverer,
    BilibiliVideoVerifier,
    BookCandidate,
    OpenAlexBookSource,
    OpenLibraryBookSource,
    VideoCandidate,
)

if TYPE_CHECKING:
    from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository
    from bridges.chat.task_materials import ModuleTaskContext

#: 配方节点名（进度事件、失败定位与产物身份）。
NODE_PARSE = "resources.parse"
NODE_PLAN = "resources.plan"
NODE_SEARCH_BOOKS = "resources.search_books"
NODE_SEARCH_VIDEOS = "resources.search_videos"
NODE_READ = "resources.read"
NODE_MATCH = "resources.match"
NODE_ORGANIZE = "resources.organize"
NODE_VERIFY = "resources.verify"

RESOURCES_RECIPE_ID = "learning-resources"
RESOURCES_RECIPE_VERSION = "learning-resources-recipe-v1"

#: 书与视频两路检索的并行组名（配方里连续登记、互不依赖）。
SEARCH_PARALLEL_GROUP = "resources.search"

#: 节点的用户可读中文名（父图失败信息按此标注真实失败位置）。
RESOURCES_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "理解学习需求",
    NODE_PLAN: "制定检索计划",
    NODE_SEARCH_BOOKS: "检索图书书目",
    NODE_SEARCH_VIDEOS: "查找哔哩哔哩视频",
    NODE_READ: "读取目录与简介",
    NODE_MATCH: "核对主题与证据",
    NODE_ORGANIZE: "组织主线与补充",
    NODE_VERIFY: "核验路径证据",
}

#: 已登记的确定性能力与版本（代码拒绝未登记能力）。
RESOURCES_CAPABILITY_VERSIONS: dict[str, str] = {
    "resources.parse_request": "resources-parse-v1",
    "resources.plan_query": "resources-plan-v1",
    "resources.search_books": "resources-search-books-v1",
    "resources.search_videos": "resources-search-videos-v1",
    "resources.read_evidence": "resources-read-v1",
    "resources.match_candidates": "resources-match-v1",
    "resources.organize_items": "resources-organize-v1",
    "resources.verify_delivery": "resources-verify-v1",
}

#: 配方的必要门与可选门（登记集合；代码拒绝未登记质量门）。
RESOURCES_GATES: frozenset[str] = frozenset(
    {
        "resources.path_evidence",
        "resources.counts_and_media",
    }
)

#: 单轮外部调用的墙钟上限（秒）：超时如实失败，绝不无限等待上游。
SEARCH_DEADLINE_SECONDS = 20.0
VERIFY_DEADLINE_SECONDS = 12.0

#: 读取端口缺失或预算用尽时的真实读范围：不是来源没提供，而是本轮没读。
UNREAD_SCOPE = "本轮未读取目录/简介（读取端口未装配或预算已用尽），只按书目元数据判断"

#: 查询/读取的硬失败分类（与 sources 的记录状态一致）。
_HARD_FAILURES = frozenset(
    {
        ModuleQueryStatus.ERROR,
        ModuleQueryStatus.TIMEOUT,
        ModuleQueryStatus.RATE_LIMITED,
    }
)


def _digest(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


def _book_dict(candidate: BookCandidate) -> dict[str, Any]:
    return asdict(candidate)


def _book_from_dict(payload: Mapping[str, Any]) -> BookCandidate:
    return BookCandidate(
        title=str(payload.get("title") or ""),
        creators=[str(name) for name in payload.get("creators") or []],
        year=payload.get("year") if isinstance(payload.get("year"), int) else None,
        publisher=payload.get("publisher"),
        isbn=payload.get("isbn"),
        source=str(payload.get("source") or ""),
        url=str(payload.get("url") or ""),
    )


def _video_dict(candidate: VideoCandidate) -> dict[str, Any]:
    payload = asdict(candidate)
    published = payload.get("published_at")
    payload["published_at"] = published.isoformat() if published is not None else None
    return payload


def _video_from_dict(payload: Mapping[str, Any]) -> VideoCandidate:
    published = payload.get("published_at")
    return VideoCandidate(
        video_id=str(payload.get("video_id") or ""),
        title=str(payload.get("title") or ""),
        uploader=payload.get("uploader"),
        duration_seconds=(
            payload.get("duration_seconds")
            if isinstance(payload.get("duration_seconds"), int)
            else None
        ),
        published_at=(
            datetime.fromisoformat(str(published)) if published else None
        ),
        url=str(payload.get("url") or ""),
        description=str(payload.get("description") or ""),
        view_count=(
            payload.get("view_count") if isinstance(payload.get("view_count"), int) else None
        ),
        like_count=(
            payload.get("like_count") if isinstance(payload.get("like_count"), int) else None
        ),
    )


# ---------------------------------------------------------------------------
# 预算视图：节点领取而非重建预算（工单 09 账本）
# ---------------------------------------------------------------------------


class ResourcesBudget:
    """资料外部调用的共享运行预算视图（缺失账本时按无预算模式运行）。"""

    def __init__(
        self,
        *,
        ledger: RunBudgetLedgerRepository,
        account_id: str,
        run_id: str,
        work_deadline: datetime,
    ) -> None:
        self._ledger = ledger
        self._account_id = account_id
        self._run_id = run_id
        self._work_deadline = work_deadline

    def remaining_work_ms(self, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        return max(0, int((self._work_deadline - moment).total_seconds() * 1000))

    def deadline_seconds(self, now: datetime | None = None) -> float:
        return max(0.5, self.remaining_work_ms(now) / 1000.0)

    def register_external(self, call_key: str, *, purpose: str) -> bool:
        return self._ledger.register_external_call(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            purpose=purpose,
            now=datetime.now(UTC),
        )

    def release_external(self, call_key: str, *, outcome_code: str) -> None:
        self._ledger.record_external_call_result(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            outcome_code=outcome_code,
            now=datetime.now(UTC),
        )


def _budget_skip_record(
    source: str, query: str, *, purpose: str
) -> ModuleQueryRecord:
    return ModuleQueryRecord(
        source=source,
        query=query,
        status=ModuleQueryStatus.ERROR,
        error_code="run_budget_exhausted",
        error_message=f"本轮运行预算已用尽，未发起新的{purpose}请求。",
        retryable=False,
    )


@dataclass(frozen=True)
class BudgetedBookReader:
    """按调用登记/释放共享预算的图书证据读取端口包装。"""

    port: BookInsightReader | None
    budget: ResourcesBudget | None

    def read(
        self,
        candidate_url: str,
        source: str,
        *,
        account_id: str,
        deadline: float | None = None,
    ) -> BookReadEvidence:
        if self.port is None:
            return BookReadEvidence(url=candidate_url, scope=UNREAD_SCOPE)
        if self.budget is None:
            return self.port.read(
                candidate_url, source, account_id=account_id, deadline=deadline
            )
        call_key = f"resources.read:{_digest(candidate_url)[:16]}"
        if not self.budget.register_external(call_key, purpose="resources.read"):
            return BookReadEvidence(
                url=candidate_url,
                scope=UNREAD_SCOPE,
                error="run_budget_exhausted",
            )
        outcome_code = "exception"
        try:
            evidence = self.port.read(
                candidate_url, source, account_id=account_id, deadline=deadline
            )
            outcome_code = evidence.error or "read"
        finally:
            with contextlib.suppress(Exception):  # 释放失败不改变真实结果
                self.budget.release_external(call_key, outcome_code=outcome_code)
        return evidence


# ---------------------------------------------------------------------------
# 质量门（结构化裁决；模型不能自行宣布通过）
# ---------------------------------------------------------------------------


def _projection_items(payload: Mapping[str, Any]) -> list[ResourceItem]:
    return [
        ResourceItem.model_validate(item) for item in payload.get("items", [])
    ]


def _path_evidence_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要门：主线条目全部有证据、原词被覆盖、实际读取范围如实记录。"""
    projection = execution.artifact.payload.get("projection") or {}
    status = str(projection.get("status") or "")
    if status in {ResourcesStatus.ERROR.value, ResourcesStatus.EMPTY.value}:
        return QualityGateResult(gate="resources.path_evidence", verdict=QualityVerdict.PASS)
    items = _projection_items(projection)
    if not items:
        return QualityGateResult(
            gate="resources.path_evidence",
            verdict=QualityVerdict.BLOCKED,
            code="resources_delivery_empty",
            message="成功交付必须至少有一条通过主题门的条目。",
        )
    weak_main = [
        item
        for item in items
        if item.role is ResourceRole.MAIN
        and item.evidence_level is ResourceEvidenceLevel.TITLE
    ]
    if weak_main:
        return QualityGateResult(
            gate="resources.path_evidence",
            verdict=QualityVerdict.BLOCKED,
            code="resources_main_evidence_missing",
            message=(
                "有主线条目只有标题/时长等弱信号，未读取目录或简介，"
                "不能作为主线交付；应降为补充/候选。"
            ),
        )
    if not bool(projection.get("path_verified")) and any(
        item.role is ResourceRole.MAIN for item in items
    ):
        return QualityGateResult(
            gate="resources.path_evidence",
            verdict=QualityVerdict.BLOCKED,
            code="resources_path_unverified",
            message="存在主线条目但路径未确认，本轮不称完整路径已核实。",
        )
    if any(not item.read_scope.strip() for item in items):
        return QualityGateResult(
            gate="resources.path_evidence",
            verdict=QualityVerdict.BLOCKED,
            code="resources_read_scope_missing",
            message="有条款目未记录实际读取范围，无法核对证据来源。",
        )
    analysis = _dependency_analysis(invocation)
    if analysis is not None and not cover_original_phrase(analysis, items):
        return QualityGateResult(
            gate="resources.path_evidence",
            verdict=QualityVerdict.BLOCKED,
            code="resources_topic_mismatch",
            message="推荐结果未覆盖你的原始说法，已停止生成。",
        )
    return QualityGateResult(gate="resources.path_evidence", verdict=QualityVerdict.PASS)


def _dependency_analysis(invocation: NodeInvocation) -> ResourcesTermAnalysis | None:
    parse = invocation.dependencies.get(NODE_PARSE)
    if parse is None:
        return None
    payload = parse.payload.get("analysis")
    if not isinstance(payload, dict):
        return None
    return ResourcesTermAnalysis.model_validate(payload)


def _counts_and_media_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """可选门：数量不超过目标、媒介条件未被突破（只记录，不阻断交付）。"""
    del invocation
    projection = execution.artifact.payload.get("projection") or {}
    items = _projection_items(projection)
    books = sum(1 for item in items if item.kind.value == "book")
    videos = sum(1 for item in items if item.kind.value == "video")
    detail: dict[str, Any] = {
        "books": books,
        "videos": videos,
        "target_books": int(projection.get("requested_books") or 0),
        "target_videos": int(projection.get("requested_videos") or 0),
        "media": str(projection.get("media") or ""),
    }
    if books > detail["target_books"] or videos > detail["target_videos"]:
        return QualityGateResult(
            gate="resources.counts_and_media",
            verdict=QualityVerdict.REPAIRABLE_FAILURE,
            code="resources_counts_exceeded",
            message="条目数超过本轮目标数量。",
            detail=detail,
        )
    if (detail["media"] == "books" and videos) or (
        detail["media"] == "videos" and books
    ):
        return QualityGateResult(
            gate="resources.counts_and_media",
            verdict=QualityVerdict.REPAIRABLE_FAILURE,
            code="resources_media_violated",
            message="交付条目与用户明确的媒介条件不一致。",
            detail=detail,
        )
    return QualityGateResult(
        gate="resources.counts_and_media",
        verdict=QualityVerdict.PASS,
        detail=detail,
    )


#: 配方质量门处理器（与配方登记的门名一一对应；内核据此执行结构化裁决）。
RESOURCES_GATE_HANDLERS: dict[str, Any] = {
    "resources.path_evidence": _path_evidence_gate,
    "resources.counts_and_media": _counts_and_media_gate,
}


# ---------------------------------------------------------------------------
# 配方
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
            "capability": RESOURCES_CAPABILITY_VERSIONS["resources.plan_query"],
        }
    )


def _books_key(inputs: Any) -> str:
    plan = inputs.artifacts[NODE_PLAN].payload["plan"]
    return _digest(
        {
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "query": plan.get("book_query"),
            "limit": plan.get("book_candidate_limit"),
            "capability": RESOURCES_CAPABILITY_VERSIONS["resources.search_books"],
        }
    )


def _videos_key(inputs: Any) -> str:
    plan = inputs.artifacts[NODE_PLAN].payload["plan"]
    return _digest(
        {
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "query": plan.get("video_query"),
            "limit": plan.get("video_candidate_limit"),
            "capability": RESOURCES_CAPABILITY_VERSIONS["resources.search_videos"],
        }
    )


def _read_key(inputs: Any) -> str:
    plan = inputs.artifacts[NODE_PLAN].payload["plan"]
    return _digest(
        {
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "books": inputs.artifacts[NODE_SEARCH_BOOKS].content_hash,
            "videos": inputs.artifacts[NODE_SEARCH_VIDEOS].content_hash,
            "read_books": plan.get("read_limit_books"),
            "read_videos": plan.get("read_limit_videos"),
        }
    )


def _match_key(inputs: Any) -> str:
    return _digest(
        {
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "read": inputs.artifacts[NODE_READ].content_hash,
        }
    )


def _organize_key(inputs: Any) -> str:
    return _digest(
        {
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "match": inputs.artifacts[NODE_MATCH].content_hash,
        }
    )


def _verify_key(inputs: Any) -> str:
    return _digest(
        {
            "organize": inputs.artifacts[NODE_ORGANIZE].content_hash,
            "capability": RESOURCES_CAPABILITY_VERSIONS["resources.verify_delivery"],
        }
    )


def build_resources_recipe() -> RecipeDefinition:
    """构造并校验资料配方（必经顺序、并行组与依赖只指向前置节点）。"""
    return RecipeDefinition(
        recipe_id=RESOURCES_RECIPE_ID,
        recipe_version=RESOURCES_RECIPE_VERSION,
        parallel_limit=2,
        nodes=(
            NodeSpec(
                name=NODE_PARSE,
                capability="resources.parse_request",
                capability_version=RESOURCES_CAPABILITY_VERSIONS["resources.parse_request"],
                artifact_type="resources.request_analysis",
                input_key=_parse_key,
                recovery=RecoveryPolicy.ASK_INPUT,
                description="保留原词、识别目的与条件；只在必要时追问学习层次。",
            ),
            NodeSpec(
                name=NODE_PLAN,
                capability="resources.plan_query",
                capability_version=RESOURCES_CAPABILITY_VERSIONS["resources.plan_query"],
                artifact_type="resources.query_plan",
                input_key=_plan_key,
                depends_on=(NODE_PARSE,),
                recovery=RecoveryPolicy.BLOCK,
                description="按目的、媒介与明确数量确定目标与查询词。",
            ),
            NodeSpec(
                name=NODE_SEARCH_BOOKS,
                capability="resources.search_books",
                capability_version=RESOURCES_CAPABILITY_VERSIONS["resources.search_books"],
                artifact_type="resources.book_candidates",
                input_key=_books_key,
                depends_on=(NODE_PLAN,),
                parallel_group=SEARCH_PARALLEL_GROUP,
                recovery=RecoveryPolicy.RETRY_NODE,
                description="书目来源逐个受限检索；一条失败只标注自己的缺口。",
            ),
            NodeSpec(
                name=NODE_SEARCH_VIDEOS,
                capability="resources.search_videos",
                capability_version=RESOURCES_CAPABILITY_VERSIONS["resources.search_videos"],
                artifact_type="resources.video_candidates",
                input_key=_videos_key,
                depends_on=(NODE_PLAN,),
                parallel_group=SEARCH_PARALLEL_GROUP,
                recovery=RecoveryPolicy.RETRY_NODE,
                description="公网搜索发现哔哩哔哩直达页，再逐条核对公开元数据。",
            ),
            NodeSpec(
                name=NODE_READ,
                capability="resources.read_evidence",
                capability_version=RESOURCES_CAPABILITY_VERSIONS["resources.read_evidence"],
                artifact_type="resources.read_evidence",
                input_key=_read_key,
                depends_on=(NODE_PLAN, NODE_SEARCH_BOOKS, NODE_SEARCH_VIDEOS),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="读取书目目录/简介与视频页面并记录实际范围（计入预算）。",
            ),
            NodeSpec(
                name=NODE_MATCH,
                capability="resources.match_candidates",
                capability_version=RESOURCES_CAPABILITY_VERSIONS["resources.match_candidates"],
                artifact_type="resources.match_result",
                input_key=_match_key,
                depends_on=(NODE_PARSE, NODE_PLAN, NODE_READ),
                recovery=RecoveryPolicy.BLOCK,
                description="主题覆盖与先修适配按证据分层，未知保持未知。",
            ),
            NodeSpec(
                name=NODE_ORGANIZE,
                capability="resources.organize_items",
                capability_version=RESOURCES_CAPABILITY_VERSIONS["resources.organize_items"],
                artifact_type="resources.organized_items",
                input_key=_organize_key,
                depends_on=(NODE_PARSE, NODE_PLAN, NODE_READ, NODE_MATCH),
                recovery=RecoveryPolicy.BLOCK,
                description="按目的与证据组织主线/补充；不足如实报差。",
            ),
            NodeSpec(
                name=NODE_VERIFY,
                capability="resources.verify_delivery",
                capability_version=RESOURCES_CAPABILITY_VERSIONS["resources.verify_delivery"],
                artifact_type="resources.delivery",
                input_key=_verify_key,
                depends_on=(NODE_PARSE, NODE_PLAN, NODE_READ, NODE_ORGANIZE),
                required_gates=("resources.path_evidence",),
                optional_gates=("resources.counts_and_media",),
                recovery=RecoveryPolicy.BLOCK,
                description="核验主线证据、原词覆盖与读取范围后形成待交付投影。",
            ),
        ),
    )


def resources_recipe_registry() -> RecipeRegistry:
    """登记资料能力、质量门与配方；非法定义在装配时即被拒绝。"""
    registry = RecipeRegistry(
        capabilities=RESOURCES_CAPABILITY_VERSIONS.keys(),
        gates=RESOURCES_GATES,
    )
    registry.register(build_resources_recipe())
    return registry


# ---------------------------------------------------------------------------
# 节点执行体
# ---------------------------------------------------------------------------


class ResourcesNodeFlow:
    """资料节点的确定性执行体（不调用模型，不自主任意委派）。"""

    def __init__(
        self,
        *,
        books: Sequence[OpenLibraryBookSource | OpenAlexBookSource] = (),
        discoverer: BilibiliVideoDiscoverer | None = None,
        verifier: BilibiliVideoVerifier | None = None,
        reader: BookInsightReader | None = None,
        clock: Callable[[], datetime] | None = None,
        prior_context: Sequence[str] = (),
        module_context: ModuleTaskContext | None = None,
        pending_wait: Any = None,
        budget: ResourcesBudget | None = None,
        stop_event: Event | None = None,
        deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        verify_deadline_seconds: float = VERIFY_DEADLINE_SECONDS,
    ) -> None:
        self._books = list(books)
        self._discoverer = discoverer
        self._verifier = verifier
        self._reader = reader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._prior_context = tuple(prior_context)
        self._module_context = module_context
        self._pending_wait = pending_wait
        self._budget = budget
        self._stop_event = stop_event
        self._deadline_seconds = deadline_seconds
        self._verify_deadline_seconds = verify_deadline_seconds

    @property
    def prior_digest(self) -> str | None:
        if self._module_context is not None and self._module_context.used_task_scope:
            return _digest(
                [
                    (condition.condition_id, condition.kind, condition.text)
                    for condition in self._module_context.effective_conditions
                ]
            )
        return _digest(list(self._prior_context)) if self._prior_context else None

    # -- 节点执行体 -------------------------------------------------------

    def run_node(self, invocation: NodeInvocation) -> NodeExecution:
        handler = {
            NODE_PARSE: self._run_parse,
            NODE_PLAN: self._run_plan,
            NODE_SEARCH_BOOKS: self._run_search_books,
            NODE_SEARCH_VIDEOS: self._run_search_videos,
            NODE_READ: self._run_read,
            NODE_MATCH: self._run_match,
            NODE_ORGANIZE: self._run_organize,
            NODE_VERIFY: self._run_verify,
        }[invocation.spec.name]
        try:
            return handler(invocation)
        except Exception as exc:  # noqa: BLE001 - 未预期异常按可重试失败收敛
            message = f"节点执行出现内部错误（{exc.__class__.__name__}）。"
            return self._failure_execution(
                invocation,
                payload={"error": {"type": exc.__class__.__name__}},
                node=invocation.spec.name,
                verdict=QualityVerdict.REPAIRABLE_FAILURE,
                status=NodeReceiptStatus.FAILED,
                code="resources_node_error",
                message=message,
                retryable=True,
            )

    def _run_parse(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = parse_resources_request(
            invocation.inputs.user_content,
            prior_context=self._prior_context,
            pending=self._pending_wait,
            module_context=self._module_context,
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
                    "code": "resources_clarification",
                    "message": analysis.clarification.question,
                    "retryable": False,
                    "node": NODE_PARSE,
                    "missing": analysis.clarification.missing,
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
            detail={"confidence": analysis.confidence},
        )

    def _run_plan(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = ResourcesTermAnalysis.model_validate(
            self._dep(invocation, NODE_PARSE)["analysis"]
        )
        plan = plan_resources(analysis)
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={"plan": plan.model_dump(mode="json")},
                read_scope="解析结果的条件推导（无外部读取）",
                requirement_coverage=[
                    {"requirement": "目标数量与媒介", "covered": True},
                    {"requirement": "原词进入两条查询", "covered": True},
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_search_books(self, invocation: NodeInvocation) -> NodeExecution:
        plan = self._plan(invocation)
        deadline = self._external_deadline(self._deadline_seconds)
        candidates: list[BookCandidate] = []
        source_counts: dict[str, int] = {}
        records: list[ModuleQueryRecord] = []
        skipped_for_media = plan.target_books == 0
        if skipped_for_media:
            # 用户明确的媒介条件：只要视频就不检索图书，也不消耗预算。
            records.append(
                ModuleQueryRecord(
                    source=BOOK_CATALOG_SOURCE,
                    query=plan.book_query,
                    status=ModuleQueryStatus.SKIPPED,
                    detail="你明确只要视频，本轮未检索图书书目。",
                )
            )
        for index, source in enumerate(self._books):
            if skipped_for_media:
                break
            name = str(getattr(source, "source", f"book_source_{index}"))
            call_key = f"resources.books:{name}:{_digest(plan.book_query)[:16]}"
            if self._budget is not None and not self._budget.register_external(
                call_key, purpose="resources.search_books"
            ):
                records.append(
                    _budget_skip_record(name, plan.book_query, purpose="书目检索")
                )
                source_counts[name] = 0
                continue
            outcome_code = "exception"
            try:
                outcome = source.search(
                    invocation.account_id,
                    plan.book_query,
                    limit=plan.book_candidate_limit,
                    deadline=deadline,
                )
                outcome_code = outcome.record.status.value
            finally:
                if self._budget is not None:
                    with contextlib.suppress(Exception):
                        self._budget.release_external(
                            call_key, outcome_code=outcome_code
                        )
            candidates.extend(outcome.candidates)
            source_counts[outcome.record.source] = len(outcome.candidates)
            records.append(outcome.record)
        if not self._books and not skipped_for_media:
            records.append(
                ModuleQueryRecord(
                    source=BOOK_CATALOG_SOURCE,
                    query=plan.book_query,
                    status=ModuleQueryStatus.SKIPPED,
                    detail="本轮没有装配图书书目来源，未发送任何书目请求。",
                )
            )
        payload: dict[str, Any] = {
            "query": plan.book_query,
            "candidates": [_book_dict(candidate) for candidate in candidates],
            "source_counts": source_counts,
            "records": [record.model_dump(mode="json") for record in records],
        }
        # 有硬失败时本轮结果仍可读（兄弟节点与部分交付要用），但标记失效：
        # 不进入按输入键的复用，用户重试时重跑失败来源。
        hard_failed = any(record.status in _HARD_FAILURES for record in records)
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=(
                    ArtifactTrust.INVALIDATED
                    if hard_failed
                    else ArtifactTrust.EVIDENCE_BOUND
                    if candidates
                    else ArtifactTrust.DRAFT
                ),
                payload=payload,
                source_refs=[record.source for record in records],
                read_scope="Open Library / OpenAlex 公开书目检索",
                requirement_coverage=[
                    {
                        "requirement": f"图书候选 {plan.target_books} 本",
                        "covered": bool(candidates),
                    }
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={"candidate_count": len(candidates)},
        )

    def _run_search_videos(self, invocation: NodeInvocation) -> NodeExecution:
        plan = self._plan(invocation)
        deadline = self._external_deadline(self._deadline_seconds)
        records: list[ModuleQueryRecord] = []
        candidates: list[VideoCandidate] = []
        rejected = 0
        pages: list[str] = []
        if plan.target_videos == 0:
            # 用户明确的媒介条件：只要图书就不检索视频，也不消耗预算。
            records.append(
                ModuleQueryRecord(
                    source=TAVILY_SOURCE,
                    query=plan.video_query,
                    status=ModuleQueryStatus.SKIPPED,
                    detail="你明确只要图书，本轮未检索视频。",
                )
            )
        elif self._discoverer is None:
            records.append(
                ModuleQueryRecord(
                    source=TAVILY_SOURCE,
                    query=plan.video_query,
                    status=ModuleQueryStatus.SKIPPED,
                    detail="公网搜索服务未装配，本轮没有发送任何发现请求。",
                )
            )
        else:
            call_key = f"resources.videos:{_digest(plan.video_query)[:16]}"
            if self._budget is not None and not self._budget.register_external(
                call_key, purpose="resources.search_videos"
            ):
                records.append(
                    _budget_skip_record(TAVILY_SOURCE, plan.video_query, purpose="视频发现")
                )
                pages = []
            else:
                outcome_code = "exception"
                try:
                    discovered = self._discoverer.discover(
                        invocation.account_id,
                        plan.video_query,
                        limit=plan.video_candidate_limit,
                        stop_event=self._stop_event,
                        deadline=deadline,
                    )
                    outcome_code = discovered.record.status.value
                finally:
                    if self._budget is not None:
                        with contextlib.suppress(Exception):
                            self._budget.release_external(
                                call_key, outcome_code=outcome_code
                            )
                records.append(discovered.record)
                pages = discovered.direct_pages
            if pages and self._verifier is None:
                records.append(
                    ModuleQueryRecord(
                        source=BILIBILI_SOURCE,
                        query=plan.video_query,
                        status=ModuleQueryStatus.SKIPPED,
                        detail="视频核对客户端未装配，发现的直达页未核对。",
                    )
                )
            elif pages:
                assert self._verifier is not None
                verify_key = f"resources.verify_videos:{_digest(pages[0])[:16]}"
                skip = self._budget is not None and not self._budget.register_external(
                    verify_key, purpose="resources.search_videos"
                )
                if skip:
                    records.append(
                        _budget_skip_record(
                            BILIBILI_SOURCE, plan.video_query, purpose="视频核对"
                        )
                    )
                else:
                    verify_code = "exception"
                    try:
                        verified = self._verifier.verify(
                            pages,
                            account_id=invocation.account_id,
                            deadline=time.monotonic() + self._verify_deadline_seconds,
                        )
                        verify_code = "matched"
                    finally:
                        if self._budget is not None:
                            with contextlib.suppress(Exception):
                                self._budget.release_external(
                                    verify_key, outcome_code=verify_code
                                )
                    candidates = verified.candidates
                    records.extend(verified.records)
                    rejected = verified.rejected
        payload: dict[str, Any] = {
            "query": plan.video_query,
            "candidates": [_video_dict(candidate) for candidate in candidates],
            "records": [record.model_dump(mode="json") for record in records],
            "rejected": rejected,
            "direct_pages": len(pages),
        }
        hard_failed = any(record.status in _HARD_FAILURES for record in records)
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=(
                    ArtifactTrust.INVALIDATED
                    if hard_failed
                    else ArtifactTrust.EVIDENCE_BOUND
                    if candidates
                    else ArtifactTrust.DRAFT
                ),
                payload=payload,
                source_refs=[record.source for record in records],
                read_scope="哔哩哔哩公开视频页核对（标题、作者、时间、时长、简介、公开计数）",
                requirement_coverage=[
                    {
                        "requirement": f"视频候选 {plan.target_videos} 条",
                        "covered": bool(candidates),
                    }
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={"candidate_count": len(candidates), "rejected": rejected},
        )

    def _run_read(self, invocation: NodeInvocation) -> NodeExecution:
        plan = self._plan(invocation)
        books_payload = self._dep(invocation, NODE_SEARCH_BOOKS)
        videos_payload = self._dep(invocation, NODE_SEARCH_VIDEOS)
        reader = BudgetedBookReader(port=self._reader, budget=self._budget)
        deadline = self._external_deadline(self._deadline_seconds)
        book_candidates = list(books_payload.get("candidates", []))
        evidence: dict[str, Any] = {}
        notes: list[str] = []
        for candidate in book_candidates[: plan.read_limit_books]:
            url = str(candidate.get("url") or "")
            if not url:
                continue
            read = reader.read(
                url,
                str(candidate.get("source") or ""),
                account_id=invocation.account_id,
                deadline=deadline,
            )
            evidence[url] = read.model_dump(mode="json")
            if read.error:
                notes.append(f"一条书目未能读取目录/简介（{read.error}），只按书目元数据判断。")
        if len(book_candidates) > plan.read_limit_books:
            notes.append(
                f"图书候选 {len(book_candidates)} 本，本轮按读取上限实际深读 "
                f"{plan.read_limit_books} 本。"
            )
        video_candidates = list(videos_payload.get("candidates", []))
        video_reads: dict[str, str] = {}
        for candidate in video_candidates[: plan.read_limit_videos]:
            video_id = str(candidate.get("video_id") or "")
            if not video_id:
                continue
            has_description = bool(str(candidate.get("description") or "").strip())
            video_reads[video_id] = (
                "哔哩哔哩公开视频页：标题、作者、发布时间、时长、简介（未观看视频）"
                if has_description
                else "哔哩哔哩公开视频页：标题、作者、发布时间、时长（该页未提供简介）"
            )
        if len(video_candidates) > plan.read_limit_videos:
            notes.append(
                f"视频候选 {len(video_candidates)} 条，本轮按读取上限实际核对 "
                f"{plan.read_limit_videos} 条页面。"
            )
        payload: dict[str, Any] = {
            "book_evidence": evidence,
            "video_reads": video_reads,
            "book_candidates": list(books_payload.get("candidates", [])),
            "video_candidates": list(videos_payload.get("candidates", [])),
            "book_records": list(books_payload.get("records", [])),
            "video_records": list(videos_payload.get("records", [])),
            "source_counts": dict(books_payload.get("source_counts", {})),
            "rejected_videos": int(videos_payload.get("rejected") or 0),
            "notes": notes,
        }
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=(
                    ArtifactTrust.EVIDENCE_BOUND if evidence else ArtifactTrust.DRAFT
                ),
                payload=payload,
                source_refs=["openlibrary"] if evidence else [],
                read_scope="图书目录/简介与视频页面（逐条记录实际范围）",
                requirement_coverage=[
                    {
                        "requirement": "记录实际读取范围",
                        "covered": bool(evidence or video_reads),
                    }
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={"books_read": len(evidence), "videos_read": len(video_reads)},
        )

    def _run_match(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = ResourcesTermAnalysis.model_validate(
            self._dep(invocation, NODE_PARSE)["analysis"]
        )
        read = self._dep(invocation, NODE_READ)
        books = [
            _book_from_dict(item) for item in read.get("book_candidates", [])
        ]
        videos = [
            _video_from_dict(item) for item in read.get("video_candidates", [])
        ]
        evidence = {
            url: BookReadEvidence.model_validate(payload)
            for url, payload in (read.get("book_evidence") or {}).items()
        }
        outcome = match_resources(
            analysis,
            books,
            videos,
            book_evidence=evidence,
            book_sources=dict(read.get("source_counts") or {}),
            rejected_videos=int(read.get("rejected_videos") or 0),
        )
        payload = self._match_payload(outcome)
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=(
                    ArtifactTrust.QUALIFIED
                    if outcome.books or outcome.videos
                    else ArtifactTrust.DRAFT
                ),
                payload=payload,
                source_refs=["openlibrary", "openalex", "bilibili"],
                read_scope="主题覆盖与先修适配的证据核对",
                requirement_coverage=[
                    {
                        "requirement": "证据分层（目录/简介/标题）",
                        "covered": bool(outcome.books or outcome.videos),
                    }
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={
                "books": len(outcome.books),
                "videos": len(outcome.videos),
                "topic_mismatch": outcome.topic_mismatch,
            },
        )

    def _run_organize(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = ResourcesTermAnalysis.model_validate(
            self._dep(invocation, NODE_PARSE)["analysis"]
        )
        plan = self._plan(invocation)
        read = self._dep(invocation, NODE_READ)
        match = self._dep(invocation, NODE_MATCH)
        outcome = organize_resources(
            analysis,
            plan,
            self._match_outcome(match),
            book_records=list(read.get("book_records", [])),
            video_records=list(read.get("video_records", [])),
        )
        payload = self._organize_payload(outcome)
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=(
                    ArtifactTrust.QUALIFIED
                    if outcome.path_verified
                    else ArtifactTrust.EVIDENCE_BOUND
                ),
                payload=payload,
                source_refs=["openlibrary", "openalex", "bilibili"],
                read_scope="按目标与证据组织主线与补充",
                requirement_coverage=[
                    {
                        "requirement": "主线/补充角色",
                        "covered": bool(outcome.items),
                    },
                    {
                        "requirement": "路径证据确认",
                        "covered": outcome.path_verified,
                    },
                ],
                unconfirmed=(
                    []
                    if outcome.path_verified
                    else ["主线证据未确认，只列补充/候选"]
                ),
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={"items": len(outcome.items), "path_verified": outcome.path_verified},
        )

    def _run_verify(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = ResourcesTermAnalysis.model_validate(
            self._dep(invocation, NODE_PARSE)["analysis"]
        )
        plan = self._plan(invocation)
        read = self._dep(invocation, NODE_READ)
        organized = self._dep(invocation, NODE_ORGANIZE)
        items = [ResourceItem.model_validate(item) for item in organized.get("items", [])]
        failure = organized.get("failure")
        topic_mismatch = bool(organized.get("topic_mismatch"))
        queries = [
            ModuleQueryRecord.model_validate(item)
            for item in [*read.get("book_records", []), *read.get("video_records", [])]
        ]
        status = ResourcesStatus.SUCCESS
        if failure is not None:
            status = ResourcesStatus.ERROR
        elif topic_mismatch or not items:
            status = ResourcesStatus.EMPTY
        now = self._clock()
        projection = LearningResourcesProjection(
            status=status,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            goal=analysis.goal,
            goal_kind=analysis.goal_kind,
            media=analysis.media,
            assumptions=list(analysis.assumptions),
            needs_practice_project=analysis.needs_practice_project,
            level_label=level_label(analysis.level),
            level_basis=analysis.level_basis,
            queries=queries,
            final_query=plan.book_query,
            items=items,
            requested_books=plan.target_books,
            requested_videos=plan.target_videos,
            path_verified=bool(organized.get("path_verified")),
            parallel_limit=plan.parallel_limit,
            evidence_notes=list(organized.get("notes", [])),
            pending=None,
            searched_at=now,
            error_code=(failure or {}).get("code") if failure else None,
            error_message=(failure or {}).get("message") if failure else None,
            retryable=bool((failure or {}).get("retryable", False)) if failure else False,
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=(
                    ArtifactTrust.QUALIFIED
                    if projection.status is ResourcesStatus.SUCCESS
                    and projection.path_verified
                    else ArtifactTrust.EVIDENCE_BOUND
                ),
                payload={"projection": projection.model_dump(mode="json")},
                source_refs=[record.source for record in queries],
                read_scope="待交付的学习资料投影",
                requirement_coverage=[
                    {"requirement": "待交付产物", "covered": True},
                    {
                        "requirement": "原词覆盖",
                        "covered": projection.status
                        in {ResourcesStatus.ERROR, ResourcesStatus.EMPTY}
                        or cover_original_phrase(analysis, items),
                    },
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    # -- 反序列化内部工具 -------------------------------------------------

    def _match_payload(self, outcome: MatchOutcome) -> dict[str, Any]:
        return {
            "books": [
                {
                    "candidate": _book_dict(book.candidate),
                    "stage": book.stage,
                    "score": book.score,
                    "covered": book.covered,
                    "evidence_level": book.evidence_level.value,
                    "evidence": (
                        book.evidence.model_dump(mode="json")
                        if book.evidence is not None
                        else None
                    ),
                    "match_basis": book.match_basis,
                    "read_scope": book.read_scope,
                }
                for book in outcome.books
            ],
            "videos": [
                {
                    "candidate": _video_dict(video.candidate),
                    "stage": video.stage,
                    "score": video.score,
                    "covered": video.covered,
                    "evidence_level": video.evidence_level.value,
                    "match_basis": video.match_basis,
                    "read_scope": video.read_scope,
                }
                for video in outcome.videos
            ],
            "notes": list(outcome.notes),
            "topic_mismatch": outcome.topic_mismatch,
        }

    def _match_outcome(self, payload: Mapping[str, Any]) -> MatchOutcome:
        return MatchOutcome(
            books=[
                EvaluatedBook(
                    candidate=_book_from_dict(item.get("candidate") or {}),
                    stage=str(item.get("stage") or ""),
                    score=int(item.get("score") or 0),
                    covered=bool(item.get("covered")),
                    evidence_level=ResourceEvidenceLevel(
                        str(item.get("evidence_level") or "title")
                    ),
                    evidence=(
                        BookReadEvidence.model_validate(item["evidence"])
                        if item.get("evidence")
                        else None
                    ),
                    match_basis=str(item.get("match_basis") or ""),
                    read_scope=str(item.get("read_scope") or ""),
                )
                for item in payload.get("books", [])
            ],
            videos=[
                EvaluatedVideo(
                    candidate=_video_from_dict(item.get("candidate") or {}),
                    stage=str(item.get("stage") or ""),
                    score=int(item.get("score") or 0),
                    covered=bool(item.get("covered")),
                    evidence_level=ResourceEvidenceLevel(
                        str(item.get("evidence_level") or "title")
                    ),
                    match_basis=str(item.get("match_basis") or ""),
                    read_scope=str(item.get("read_scope") or ""),
                )
                for item in payload.get("videos", [])
            ],
            notes=[str(note) for note in payload.get("notes", [])],
            topic_mismatch=bool(payload.get("topic_mismatch")),
        )

    def _organize_payload(self, outcome: OrganizeOutcome) -> dict[str, Any]:
        return {
            "items": [
                item.model_dump(mode="json") for item in outcome.items
            ],
            "notes": list(outcome.notes),
            "path_verified": outcome.path_verified,
            "topic_mismatch": outcome.topic_mismatch,
            "failure": outcome.failure,
        }

    # -- 内部工具 ---------------------------------------------------------

    def _dep(self, invocation: NodeInvocation, node: str) -> dict[str, Any]:
        return dict(invocation.dependencies[node].payload)

    def _plan(self, invocation: NodeInvocation) -> ResourcesQueryPlan:
        return ResourcesQueryPlan.model_validate(
            self._dep(invocation, NODE_PLAN)["plan"]
        )

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
            recipe_id=RESOURCES_RECIPE_ID,
            recipe_version=RESOURCES_RECIPE_VERSION,
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

    def _external_deadline(self, fallback_seconds: float) -> float:
        remaining = fallback_seconds
        if self._budget is not None:
            remaining = min(remaining, self._budget.deadline_seconds())
        return time.monotonic() + remaining


__all__ = [
    "NODE_MATCH",
    "NODE_ORGANIZE",
    "NODE_PARSE",
    "NODE_PLAN",
    "NODE_READ",
    "NODE_SEARCH_BOOKS",
    "NODE_SEARCH_VIDEOS",
    "NODE_VERIFY",
    "RESOURCES_CAPABILITY_VERSIONS",
    "RESOURCES_GATE_HANDLERS",
    "RESOURCES_NODE_LABELS",
    "RESOURCES_RECIPE_ID",
    "RESOURCES_RECIPE_VERSION",
    "SEARCH_DEADLINE_SECONDS",
    "SEARCH_PARALLEL_GROUP",
    "UNREAD_SCOPE",
    "VERIFY_DEADLINE_SECONDS",
    "BudgetedBookReader",
    "ResourcesBudget",
    "ResourcesNodeFlow",
    "build_resources_recipe",
    "resources_recipe_registry",
]
