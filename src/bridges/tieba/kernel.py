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

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

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
from bridges.tieba import evidence
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaCandidateLink,
    TiebaEvidencePlan,
    TiebaEvidenceRoute,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaQuestionKind,
    TiebaReadResult,
    TiebaRejectedCandidate,
    TiebaResearchProjection,
    TiebaSearchHit,
    TiebaVerification,
)
from bridges.tieba.lexicon import TARGET_FORUM_NAME
from bridges.tieba.official import (
    STATUS_VERIFIED,
    TiebaOfficialReader,
    official_fallback_query,
    official_query,
)
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.reading import TiebaThreadReader
from bridges.tieba.searching import (
    REJECT_NOT_A_THREAD,
    HitDiagnostics,
    SearchOutcome,
    TiebaSearchPort,
    plan_queries,
    query_record,
    thread_id_of,
)

if TYPE_CHECKING:
    from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository
    from bridges.chat.task_materials import ModuleTaskContext

NODE_PARSE = "tieba.parse"
NODE_PLAN = "tieba.plan"
NODE_SEARCH = "tieba.search"
NODE_READ = "tieba.read"
NODE_VERIFY_OFFICIAL = "tieba.verify_official"
NODE_SYNTHESIZE = "tieba.synthesize"
NODE_VERIFY = "tieba.verify"

TIEBA_RECIPE_ID = "tieba-research"
TIEBA_RECIPE_VERSION = "tieba-recipe-v1"

#: 各节点能力版本（产物兼容性与收据键的一部分）。
TIEBA_CAPABILITY_VERSIONS: dict[str, str] = {
    "tieba.parse_request": "tieba-parse-v2",
    "tieba.plan_evidence": "tieba-plan-v2",
    "tieba.verify_official_pages": "tieba-official-v2",
    "tieba.search_threads": "tieba-search-v2",
    "tieba.read_threads": "tieba-read-v2",
    "tieba.synthesize_evidence": "tieba-synthesize-v2",
    "tieba.verify_evidence": "tieba-verify-v2",
}

TIEBA_GATES: tuple[str, ...] = (
    "tieba.attribution_evidence",
    "tieba.read_scope_fidelity",
    "tieba.official_scope",
    "tieba.conflict_disclosure",
)

#: 只查贴吧时官方路径的阻断原因（来源限制生效）。
TIEBA_ONLY_OFFICIAL_BLOCKED = (
    "你明确只查贴吧：本轮遵守来源限制，未访问学校官方页面，"
    "规定相关内容保持未核实。"
)

#: 官方证据缺失或不适用的固定说明（规定类必须出现）。
OFFICIAL_UNVERIFIED_NOTE = "本轮没有取得可确认适用的官方页面，规定相关内容保持未核实。"

#: 自己生成文本里禁止出现的共识式表述（引文保真，不替吧友下结论）。
CONSENSUS_PHRASES: tuple[str, ...] = (
    "普遍认为",
    "普遍反映",
    "大家都说",
    "一致认为",
    "共识是",
)


def _digest(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


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


def _official_key(inputs: Any) -> str:
    return _digest(
        {
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
        }
    )


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
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
        }
    )


def _synthesize_key(inputs: Any) -> str:
    return _digest(
        {
            "read": inputs.artifacts[NODE_READ].content_hash,
            "official": inputs.artifacts[NODE_VERIFY_OFFICIAL].content_hash,
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
        }
    )


def _verify_key(inputs: Any) -> str:
    return _digest(
        {
            "synthesize": inputs.artifacts[NODE_SYNTHESIZE].content_hash,
            "official": inputs.artifacts[NODE_VERIFY_OFFICIAL].content_hash,
            "read": inputs.artifacts[NODE_READ].content_hash,
        }
    )


def build_tieba_recipe() -> RecipeDefinition:
    """构造并校验贴吧取证配方（先官方后贴吧，读取后综合与核验）。"""
    return RecipeDefinition(
        recipe_id=TIEBA_RECIPE_ID,
        recipe_version=TIEBA_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_PARSE,
                capability="tieba.parse_request",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.parse_request"],
                artifact_type="tieba.question_analysis",
                input_key=_parse_key,
                recovery=RecoveryPolicy.ASK_INPUT,
                description="识别对象、日期/时效与规定／体验／混合类型。",
            ),
            NodeSpec(
                name=NODE_PLAN,
                capability="tieba.plan_evidence",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.plan_evidence"],
                artifact_type="tieba.evidence_plan",
                input_key=_plan_key,
                depends_on=(NODE_PARSE,),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="登记来源顺序与查询范围（规定类官方优先，体验类只查贴吧）。",
            ),
            NodeSpec(
                name=NODE_VERIFY_OFFICIAL,
                capability="tieba.verify_official_pages",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.verify_official_pages"],
                artifact_type="tieba.official_checks",
                input_key=_official_key,
                depends_on=(NODE_PARSE, NODE_PLAN),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="核对学校官方主体/校区/用途/日期；只查贴吧时跳过。",
            ),
            NodeSpec(
                name=NODE_SEARCH,
                capability="tieba.search_threads",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.search_threads"],
                artifact_type="tieba.search_results",
                input_key=_search_key,
                depends_on=(NODE_PARSE, NODE_PLAN),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="按计划发出有界检索，记录真实查询词与命中构成。",
            ),
            NodeSpec(
                name=NODE_READ,
                capability="tieba.read_threads",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.read_threads"],
                artifact_type="tieba.read_results",
                input_key=_read_key,
                depends_on=(NODE_PARSE, NODE_PLAN, NODE_SEARCH),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="公开读取候选帖，按页面自身吧名确认归属并记录缺失范围。",
            ),
            NodeSpec(
                name=NODE_SYNTHESIZE,
                capability="tieba.synthesize_evidence",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.synthesize_evidence"],
                artifact_type="tieba.synthesis",
                input_key=_synthesize_key,
                depends_on=(
                    NODE_PARSE,
                    NODE_PLAN,
                    NODE_VERIFY_OFFICIAL,
                    NODE_SEARCH,
                    NODE_READ,
                ),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="只用已读楼层归纳经历，核对官方适用性并检测冲突。",
            ),
            NodeSpec(
                name=NODE_VERIFY,
                capability="tieba.verify_evidence",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.verify_evidence"],
                artifact_type="tieba.verification",
                input_key=_verify_key,
                depends_on=(
                    NODE_PARSE,
                    NODE_PLAN,
                    NODE_VERIFY_OFFICIAL,
                    NODE_READ,
                    NODE_SYNTHESIZE,
                ),
                required_gates=TIEBA_GATES,
                recovery=RecoveryPolicy.BLOCK,
                description="结构化核对归属、读取范围、官方范围与冲突披露。",
            ),
        ),
    )


def tieba_recipe_registry() -> RecipeRegistry:
    """登记贴吧能力、质量门与配方；非法定义在装配时即被拒绝。"""
    registry = RecipeRegistry(
        capabilities=TIEBA_CAPABILITY_VERSIONS.keys(),
        gates=TIEBA_GATES,
    )
    registry.register(build_tieba_recipe())
    return registry


# ---------------------------------------------------------------------------
# 共享预算视图（工单 09 账本；缺失账本时按无预算模式运行）
# ---------------------------------------------------------------------------


class TiebaBudget:
    """贴吧外部调用的共享运行预算视图（检索、读取、官方抓取同一条账本）。"""

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


class _CallKeys:
    """外部调用的唯一键计数器（并发占位的领取与释放按同一键配对）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counter = 0

    def next(self, prefix: str) -> str:
        with self._lock:
            self._counter += 1
            return f"{prefix}:{self._counter}"


class _BudgetedSearchPort:
    """共享预算门控的搜索端口：预算耗尽时返回失败记录，不发起调用。"""

    def __init__(
        self, port: TiebaSearchPort, budget: TiebaBudget, *, purpose: str
    ) -> None:
        self._port = port
        self._budget = budget
        self._purpose = purpose
        self._keys = _CallKeys()

    def search_public(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome:
        key = self._keys.next(self._purpose)
        if not self._budget.register_external(key, purpose=self._purpose):
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.ERROR,
                    evidence_count=0,
                    error_code="run_budget_exhausted",
                    error_message="本轮运行预算已用尽，未发起新的检索请求。",
                    retryable=False,
                ),
                hits=(),
                candidates=(),
                rejected=(),
                diagnostics=HitDiagnostics(),
            )
        try:
            return self._port.search_public(
                account_id,
                query=query,
                reason=reason,
                stop_event=stop_event,
                deadline=deadline,
            )
        finally:
            self._budget.release_external(key, outcome_code="completed")


class _BudgetedReader:
    """共享预算门控的帖子读取：预算耗尽时如实返回未读取。"""

    def __init__(self, reader: TiebaThreadReader, budget: TiebaBudget) -> None:
        self._reader = reader
        self._budget = budget
        self._keys = _CallKeys()

    def read(
        self,
        url: str,
        *,
        pages_limit: int | None = None,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaReadResult:
        key = self._keys.next("tieba.read")
        if not self._budget.register_external(key, purpose="tieba_read"):
            return TiebaReadResult(
                url=url,
                thread_id=thread_id_of(url),
                status=ReadStatus.ERROR,
                error_code="run_budget_exhausted",
                error_message="本轮运行预算已用尽，未读取该帖子页面。",
                retrieved_at=datetime.now(UTC),
            )
        try:
            if pages_limit is None:
                return self._reader.read(url, stop_event=stop_event, deadline=deadline)
            return self._reader.read(
                url,
                pages_limit=pages_limit,
                stop_event=stop_event,
                deadline=deadline,
            )
        finally:
            self._budget.release_external(key, outcome_code="completed")


class _BudgetedOfficialReader:
    """共享预算门控的官方页面读取：预算耗尽时如实返回未取得。"""

    def __init__(self, reader: TiebaOfficialReader, budget: TiebaBudget) -> None:
        self._reader = reader
        self._budget = budget
        self._keys = _CallKeys()

    def fetch(
        self,
        url: str,
        *,
        terms: tuple[str, ...],
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaOfficialCheck:
        key = self._keys.next("tieba.official")
        if not self._budget.register_external(key, purpose="tieba_official"):
            return TiebaOfficialCheck(
                title=url,
                url=url,
                host=urlsplit(url).hostname or "",
                fetched_at=datetime.now(UTC),
                status="fetch_failed",
                error_code="run_budget_exhausted",
                error_message="本轮运行预算已用尽，未读取官方页面。",
            )
        try:
            return self._reader.fetch(
                url, terms=terms, stop_event=stop_event, deadline=deadline
            )
        finally:
            self._budget.release_external(key, outcome_code="completed")


def build_evidence_plan(
    analysis: TiebaQuestionAnalysis, *, official_blocked_reason: str | None
) -> TiebaEvidencePlan:
    """按问题类型安排来源顺序：规定官方优先，体验只查贴吧，混合两路独立。"""
    if analysis.question_kind is TiebaQuestionKind.EXPERIENCE:
        return TiebaEvidencePlan(
            question_kind=analysis.question_kind,
            routes=[TiebaEvidenceRoute.TIEBA],
            source_priority=[TiebaEvidenceRoute.TIEBA],
            official_required=False,
            official_blocked_reason=None,
            check_requirements=["以目标贴吧的真实帖子与回复呈现实际经历与不同看法。"],
            tieba_queries=list(plan_queries(analysis)),
            official_queries=[],
            rationale="体验类问题：以目标贴吧实际帖子与回复为主，只使用真实读到的楼层。",
        )
    blocked = official_blocked_reason
    if analysis.question_kind is TiebaQuestionKind.POLICY:
        rationale = (
            "规定类问题：先核对与任务相关的学校官方主体/校区/用途/日期，"
            "再由贴吧帖子与回复提供实际办理经历；两路独立、共享同一运行预算。"
        )
    else:
        rationale = (
            "混合问题：官方页面核对规定，贴吧帖子与回复给实际经历；"
            "两路独立取证、共享同一运行预算，冲突按时间与适用范围分列。"
        )
    requirements = ["核对官方主体、点名校区、用途与日期（版本）是否适用。"]
    if analysis.campus_terms:
        requirements.append(
            f"官方页面需点名校区「{analysis.campus_terms[0]}」或标注未确认。"
        )
    if analysis.time_requirement:
        requirements.append(f"核对时间条件「{analysis.time_requirement}」的时效。")
    requirements.append("贴吧路径提供真实办理经历与不同看法，不替代官方规定。")
    return TiebaEvidencePlan(
        question_kind=analysis.question_kind,
        routes=[TiebaEvidenceRoute.OFFICIAL, TiebaEvidenceRoute.TIEBA],
        source_priority=[TiebaEvidenceRoute.OFFICIAL, TiebaEvidenceRoute.TIEBA],
        official_required=not blocked,
        official_blocked_reason=blocked,
        check_requirements=requirements,
        tieba_queries=list(plan_queries(analysis)),
        official_queries=(
            [] if blocked else [official_query(analysis), official_fallback_query(analysis)]
        ),
        rationale=rationale,
    )


def run_verification(
    analysis: TiebaQuestionAnalysis,
    plan: TiebaEvidencePlan,
    projection: TiebaResearchProjection,
    checks: Sequence[TiebaOfficialCheck],
    posts: Sequence[TiebaPostProjection],
) -> TiebaVerification:
    """结构化核验：归属、读取范围、未读声明、官方范围与冲突披露。"""
    read_by_url = {post.url: post for post in posts}
    attribution = all(
        post.url in read_by_url
        and TARGET_FORUM_NAME in post.affiliation_evidence
        and post.affiliation_evidence.startswith("已读取帖子页面")
        for post in projection.confirmed_posts
    )
    read_scope = _read_scope_consistent(projection.confirmed_posts, posts)
    unread_absent = _quotes_traceable(projection.sections, posts)
    consensus_absent = not _has_consensus_phrases(projection)
    official_scope = _official_scope_consistent(analysis, plan, projection, checks)
    recomputed = evidence.detect_conflicts(analysis, checks, posts)
    projected_keys = {
        (item.official_url, tuple(item.topic_terms)) for item in projection.conflicts
    }
    recomputed_keys = {
        (item.official_url, tuple(item.topic_terms)) for item in recomputed
    }
    conflicts_disclosed = projected_keys == recomputed_keys
    unconfirmed: list[str] = []
    if not attribution:
        unconfirmed.append("存在与读取页面不一致的帖归属引用。")
    if not read_scope:
        unconfirmed.append("存在超出实际读取范围的楼层引用。")
    if not unread_absent:
        unconfirmed.append("存在无法回溯到已读回复的摘录。")
    if not official_scope:
        unconfirmed.append("官方说法超出已取得且适用的页面范围。")
    if not conflicts_disclosed:
        unconfirmed.append("官方与帖子的冲突没有如实分列。")
    passed = (
        attribution
        and read_scope
        and unread_absent
        and consensus_absent
        and official_scope
        and conflicts_disclosed
    )
    return TiebaVerification(
        attribution_consistent=attribution,
        read_scope_consistent=read_scope,
        unread_claims_absent=unread_absent,
        consensus_claims_absent=consensus_absent,
        official_scope_consistent=official_scope,
        conflicts_disclosed=conflicts_disclosed,
        summary=(
            "核验通过：归属、读取范围、官方适用范围与冲突披露都与真实产物一致。"
            if passed
            else "核验未通过：" + "；".join(unconfirmed)
        ),
        unconfirmed=unconfirmed,
    )


def _read_scope_consistent(
    projected: Sequence[TiebaPostProjection], posts: Sequence[TiebaPostProjection]
) -> bool:
    read_by_url = {post.url: post for post in posts}
    for item in projected:
        source = read_by_url.get(item.url)
        if source is None:
            return False
        if item.pages_read > source.pages_read or item.pages_limit != source.pages_limit:
            return False
        if item.floor_min != source.floor_min or item.floor_max != source.floor_max:
            return False
        source_replies = {(reply.floor, reply.content) for reply in source.replies}
        for reply in item.replies:
            if (reply.floor, reply.content) not in source_replies:
                return False
    return True


def _quotes_traceable(
    sections: Sequence[str], posts: Sequence[TiebaPostProjection]
) -> bool:
    """每个分段里的引文都必须能回溯到真实读到的回复原文。"""
    contents = [
        reply.content.strip()
        for post in posts
        for reply in post.replies
        if reply.content
    ]
    if not sections:
        return True
    for section in sections:
        body = section.split("：", 1)[-1]
        for chunk in body.split("；"):
            quote = chunk.split("（", 1)[0].strip()
            if quote.endswith("…"):
                quote = quote[:-1]
            if not quote:
                continue
            if not any(content.startswith(quote) for content in contents):
                return False
    return True


def _has_consensus_phrases(projection: TiebaResearchProjection) -> bool:
    """检查我们自己生成的文字：引文之外的模板不得出现共识式表述。"""
    generated = [
        projection.plan_rationale or "",
        projection.empty_reason or "",
        projection.official_unverified_note or "",
        projection.official_blocked_reason or "",
        *(item.note for item in projection.conflicts),
        *(item.time_basis for item in projection.conflicts),
        *(item.scope_basis for item in projection.conflicts),
    ]
    return any(phrase in text for phrase in CONSENSUS_PHRASES for text in generated)


def _official_scope_consistent(
    analysis: TiebaQuestionAnalysis,
    plan: TiebaEvidencePlan,
    projection: TiebaResearchProjection,
    checks: Sequence[TiebaOfficialCheck],
) -> bool:
    if not analysis.needs_official_check:
        return not projection.official_checks
    if plan.official_blocked_reason:
        return (
            projection.official_blocked_reason == plan.official_blocked_reason
            and not projection.official_checks
        )
    for check in checks:
        if check.status == STATUS_VERIFIED and (
            not check.excerpt or not check.matched_terms or check.applicability is None
        ):
            return False
    applicable = [
        check
        for check in checks
        if check.status == STATUS_VERIFIED
        and check.applicability is not None
        and check.applicability.applicable
    ]
    return bool(applicable or projection.official_unverified_note)


class TiebaNodeFlow:
    """贴吧节点的确定性执行体（不调用模型，全部基于真实外部结果）。"""

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
        search_deadline_seconds: float = evidence.SEARCH_DEADLINE_SECONDS,
        read_deadline_seconds: float = evidence.READ_DEADLINE_SECONDS,
        official_deadline_seconds: float = evidence.OFFICIAL_DEADLINE_SECONDS,
        repository: Any = None,
    ) -> None:
        if budget is not None:
            search = _BudgetedSearchPort(search, budget, purpose="tieba.search")
            reader = _BudgetedReader(reader, budget)
            if official_reader is not None:
                official_reader = _BudgetedOfficialReader(official_reader, budget)
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

    @property
    def prior_digest(self) -> str | None:
        if self._module_context is None or not self._module_context.used_task_scope:
            return None
        return _digest(
            [
                (condition.condition_id, condition.kind, condition.text)
                for condition in self._module_context.effective_conditions
            ]
        )

    # -- 节点执行体 -------------------------------------------------------

    def run_node(self, invocation: NodeInvocation) -> NodeExecution:
        handler = {
            NODE_PARSE: self._run_parse,
            NODE_PLAN: self._run_plan,
            NODE_VERIFY_OFFICIAL: self._run_verify_official,
            NODE_SEARCH: self._run_search,
            NODE_READ: self._run_read,
            NODE_SYNTHESIZE: self._run_synthesize,
            NODE_VERIFY: self._run_verify,
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
                code="tieba_node_error",
                message=f"节点执行出现内部错误（{exc.__class__.__name__}）。",
                retryable=True,
            )

    def _run_parse(self, invocation: NodeInvocation) -> NodeExecution:
        wait = self._pending_wait
        wait_context = (
            wait.context
            if wait is not None and isinstance(wait.context, dict)
            else None
        )
        analysis = parse_tieba_request(
            invocation.inputs.user_content,
            pending=wait_context,
            module_context=self._module_context,
        )
        payload: dict[str, Any] = {"analysis": analysis.model_dump(mode="json")}
        if analysis.clarification is not None:
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
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
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.DRAFT,
                payload=payload,
                read_scope="问题解析（对象、时间与问题类型）",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_plan(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        plan = build_evidence_plan(
            analysis, official_blocked_reason=self._official_blocked_reason
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={"plan": plan.model_dump(mode="json")},
                read_scope="取证计划（来源顺序与查询范围）",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_verify_official(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        plan = self._plan(invocation)
        if not plan.official_required:
            payload = {
                "checks": [],
                "blocked": False,
                "blocked_reason": None,
                "skipped": True,
                "partial": False,
            }
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
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
            }
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
                    trust_state=ArtifactTrust.EVIDENCE_BOUND,
                    payload=payload,
                    read_scope="来源限制生效：未访问学校官方页面",
                ),
                verdict=QualityVerdict.PASS,
                status=NodeReceiptStatus.COMPLETED,
            )
        resume = self._resume_payload(invocation, NODE_VERIFY_OFFICIAL)
        checks = evidence.verify_official(
            self._search,
            self._official_reader,
            invocation.account_id,
            analysis,
            stop_event=self._stop_event,
            deadline_seconds=self._remaining_seconds(
                invocation, self._official_deadline_seconds
            ),
            resume=resume,
        )
        partial = bool(self._stop_event is not None and self._stop_event.is_set())
        payload = {
            "checks": [check.model_dump(mode="json") for check in checks],
            "blocked": False,
            "blocked_reason": None,
            "skipped": False,
            "partial": partial,
        }
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload=payload,
                source_refs=[check.url for check in checks],
                read_scope=f"实际取得 {len(checks)} 个官方页面（按域名与段落定位）",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_search(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        resume = self._resume_payload(invocation, NODE_SEARCH)
        attempts = evidence.search_candidates(
            self._search,
            invocation.account_id,
            analysis,
            stop_event=self._stop_event,
            deadline_seconds=self._remaining_seconds(
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
            "partial": attempts.partial,
        }
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload=payload,
                source_refs=[hit.url for hit in attempts.hits],
                read_scope=f"实际发出 {len(attempts.records)} 次公开检索",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_read(self, invocation: NodeInvocation) -> NodeExecution:
        attempts = self._search_attempts(invocation)
        resume = self._resume_payload(invocation, NODE_READ)
        reads = evidence.read_candidates(
            self._reader,
            attempts,
            stop_event=self._stop_event,
            deadline_seconds=self._remaining_seconds(
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
            "partial": reads.partial,
        }
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload=payload,
                source_refs=[post.url for post in reads.confirmed],
                read_scope=(
                    f"实际读取 {len(reads.attempted_urls)} 个候选页面；"
                    f"确认 {len(reads.confirmed)} 个属于「{TARGET_FORUM_NAME}」"
                ),
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_synthesize(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        plan = self._plan(invocation)
        checks = self._checks(invocation)
        attempts = self._search_attempts(invocation)
        reads = self._reads(invocation)
        time_filter, confirmed, sections = evidence.summarize(
            analysis, reads.confirmed
        )
        conflicts = evidence.detect_conflicts(analysis, checks, reads.confirmed)
        projection = evidence.build_projection(
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
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={"projection": projection.model_dump(mode="json")},
                source_refs=[post.url for post in projection.confirmed_posts],
                read_scope="归纳：只使用实际读到的楼层与官方原文章节",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_verify(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        plan = self._plan(invocation)
        checks = self._checks(invocation)
        reads = self._reads(invocation)
        projection = TiebaResearchProjection.model_validate(
            self._dep(invocation, NODE_SYNTHESIZE)["projection"]
        )
        verification = run_verification(
            analysis, plan, projection, checks, reads.confirmed
        )
        if not (
            verification.attribution_consistent
            and verification.read_scope_consistent
            and verification.unread_claims_absent
            and verification.consensus_claims_absent
            and verification.official_scope_consistent
            and verification.conflicts_disclosed
        ):
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
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
            artifact=self._artifact(
                invocation,
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

    # -- 内部工具 ---------------------------------------------------------

    def _analysis(self, invocation: NodeInvocation) -> TiebaQuestionAnalysis:
        return TiebaQuestionAnalysis.model_validate(
            self._dep(invocation, NODE_PARSE)["analysis"]
        )

    def _plan(self, invocation: NodeInvocation) -> TiebaEvidencePlan:
        return TiebaEvidencePlan.model_validate(
            self._dep(invocation, NODE_PLAN)["plan"]
        )

    def _checks(self, invocation: NodeInvocation) -> list[TiebaOfficialCheck]:
        checks: list[TiebaOfficialCheck] = []
        for raw in self._dep(invocation, NODE_VERIFY_OFFICIAL).get("checks", []):
            try:
                checks.append(TiebaOfficialCheck.model_validate(raw))
            except ValueError:
                continue
        return checks

    def _search_attempts(self, invocation: NodeInvocation) -> evidence.SearchAttempts:
        payload = self._dep(invocation, NODE_SEARCH)
        hits: list[TiebaSearchHit] = []
        for raw in payload.get("candidates", []):
            try:
                hits.append(TiebaSearchHit.model_validate(raw))
            except ValueError:
                continue
        rejected: list[TiebaRejectedCandidate] = []
        for raw in payload.get("rejected", []):
            try:
                rejected.append(TiebaRejectedCandidate.model_validate(raw))
            except ValueError:
                continue
        records: list[ModuleQueryRecord] = []
        for raw in payload.get("records", []):
            try:
                records.append(ModuleQueryRecord.model_validate(raw))
            except ValueError:
                continue
        # 产物里只记原始命中数与可用候选数；剔除构成按唯一剔除记录重建，
        # 保证跨节点恢复后「原始命中构成」与剔除记录条目仍能对上。
        not_a_thread = sum(
            1 for item in rejected if item.evidence == REJECT_NOT_A_THREAD
        )
        other_forum = len(rejected) - not_a_thread
        raw_hits = int(payload.get("raw_hits") or 0)
        usable = int(payload.get("usable") or len(hits))
        diagnostics = HitDiagnostics(
            raw_hits=raw_hits,
            not_a_thread=not_a_thread,
            other_forum=other_forum,
            duplicate=max(0, raw_hits - not_a_thread - other_forum - usable),
            usable=usable,
        )
        return evidence.SearchAttempts(
            hits=tuple(hits),
            rejected=tuple(rejected),
            records=records,
            diagnostics=diagnostics,
            rounds=tuple(str(note) for note in payload.get("rounds", [])),
            partial=bool(payload.get("partial")),
        )

    def _reads(self, invocation: NodeInvocation) -> evidence.ReadOutcome:
        payload = self._dep(invocation, NODE_READ)
        confirmed: list[TiebaPostProjection] = []
        for raw in payload.get("confirmed", []):
            try:
                confirmed.append(TiebaPostProjection.model_validate(raw))
            except ValueError:
                continue
        rejected: list[TiebaRejectedCandidate] = []
        for raw in payload.get("rejected", []):
            try:
                rejected.append(TiebaRejectedCandidate.model_validate(raw))
            except ValueError:
                continue
        unconfirmed: list[TiebaCandidateLink] = []
        for raw in payload.get("unconfirmed", []):
            try:
                unconfirmed.append(TiebaCandidateLink.model_validate(raw))
            except ValueError:
                continue
        return evidence.ReadOutcome(
            confirmed=confirmed,
            rejected=rejected,
            unconfirmed=unconfirmed,
            unreadable=int(payload.get("unreadable") or 0),
            not_attempted=int(payload.get("not_attempted") or 0),
            unconfirmed_total=int(payload.get("unconfirmed_total") or len(unconfirmed)),
            attempted_urls=tuple(str(url) for url in payload.get("attempted_urls", [])),
            partial=bool(payload.get("partial")),
        )

    def _dep(self, invocation: NodeInvocation, node: str) -> dict[str, Any]:
        return dict(invocation.dependencies[node].payload)

    def _resume_payload(
        self, invocation: NodeInvocation, node: str
    ) -> dict[str, Any] | None:
        """按同一输入键读取上一轮的部分产物，供续作未完成检索/读取。

        只回填与当前能力版本一致、且确实是部分完成的产物；完整产物会正常
        按收据/产物复用，不会走到这里。
        """
        if self._repository is None:
            return None
        input_key = invocation.spec.input_key(invocation.inputs)
        artifact = self._repository.find_artifact(
            invocation.account_id, invocation.conversation_id, node, input_key
        )
        if (
            artifact is None
            or artifact.capability_version != invocation.spec.capability_version
        ):
            return None
        if artifact.payload.get("partial") is not True:
            return None
        return dict(artifact.payload)

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
            recipe_id=TIEBA_RECIPE_ID,
            recipe_version=TIEBA_RECIPE_VERSION,
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
            },
            stop_recipe=True,
            recovery=invocation.spec.recovery,
        )

    def _remaining_seconds(self, invocation: NodeInvocation, seconds: float) -> float:
        """本地截止与运行预算取较小者：预算用尽时不再空等上游。"""
        remaining = seconds
        if invocation.remaining_budget_ms is not None:
            remaining = min(remaining, invocation.remaining_budget_ms / 1000.0)
        return max(0.0, remaining)


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


# ---------------------------------------------------------------------------
# 质量门：代码复核核验产物与依赖产物，模型不能自行宣布通过
# ---------------------------------------------------------------------------


def _gate_result(
    gate: str, passed: bool, *, code: str, message: str
) -> QualityGateResult:
    return QualityGateResult(
        gate=gate,
        verdict=QualityVerdict.PASS if passed else QualityVerdict.BLOCKED,
        code="" if passed else code,
        message="" if passed else message,
    )


def _recomputed_verification(invocation: NodeInvocation) -> TiebaVerification:
    analysis = TiebaQuestionAnalysis.model_validate(
        dict(invocation.dependencies[NODE_PARSE].payload)["analysis"]
    )
    plan = TiebaEvidencePlan.model_validate(
        dict(invocation.dependencies[NODE_PLAN].payload)["plan"]
    )
    checks: list[TiebaOfficialCheck] = []
    for raw in dict(invocation.dependencies[NODE_VERIFY_OFFICIAL].payload).get(
        "checks", []
    ):
        try:
            checks.append(TiebaOfficialCheck.model_validate(raw))
        except ValueError:
            continue
    posts: list[TiebaPostProjection] = []
    for raw in dict(invocation.dependencies[NODE_READ].payload).get("confirmed", []):
        try:
            posts.append(TiebaPostProjection.model_validate(raw))
        except ValueError:
            continue
    projection = TiebaResearchProjection.model_validate(
        dict(invocation.dependencies[NODE_SYNTHESIZE].payload)["projection"]
    )
    return run_verification(analysis, plan, projection, checks, posts)


def _gate_attribution(
    invocation: NodeInvocation, _execution: NodeExecution
) -> QualityGateResult:
    verification = _recomputed_verification(invocation)
    return _gate_result(
        "tieba.attribution_evidence",
        verification.attribution_consistent,
        code="tieba_attribution_inconsistent",
        message="确认帖的归属依据与真实读取页面不一致。",
    )


def _gate_read_scope(
    invocation: NodeInvocation, _execution: NodeExecution
) -> QualityGateResult:
    verification = _recomputed_verification(invocation)
    passed = (
        verification.read_scope_consistent
        and verification.unread_claims_absent
        and verification.consensus_claims_absent
    )
    return _gate_result(
        "tieba.read_scope_fidelity",
        passed,
        code="tieba_read_scope_inconsistent",
        message="引用超出实际读取范围，或存在无法回溯的摘录/共识表述。",
    )


def _gate_official_scope(
    invocation: NodeInvocation, _execution: NodeExecution
) -> QualityGateResult:
    verification = _recomputed_verification(invocation)
    return _gate_result(
        "tieba.official_scope",
        verification.official_scope_consistent,
        code="tieba_official_scope_inconsistent",
        message="官方说法超出已取得且适用的页面范围（域名不自动放行）。",
    )


def _gate_conflict_disclosure(
    invocation: NodeInvocation, _execution: NodeExecution
) -> QualityGateResult:
    verification = _recomputed_verification(invocation)
    return _gate_result(
        "tieba.conflict_disclosure",
        verification.conflicts_disclosed,
        code="tieba_conflict_not_disclosed",
        message="官方与帖子的冲突没有按时间/范围如实分列。",
    )


TIEBA_GATE_HANDLERS: dict[str, Any] = {
    "tieba.attribution_evidence": _gate_attribution,
    "tieba.read_scope_fidelity": _gate_read_scope,
    "tieba.official_scope": _gate_official_scope,
    "tieba.conflict_disclosure": _gate_conflict_disclosure,
}


__all__ = [
    "NODE_PARSE",
    "NODE_PLAN",
    "NODE_SEARCH",
    "NODE_READ",
    "NODE_VERIFY_OFFICIAL",
    "NODE_SYNTHESIZE",
    "NODE_VERIFY",
    "OFFICIAL_UNVERIFIED_NOTE",
    "TIEBA_CAPABILITY_VERSIONS",
    "TIEBA_GATES",
    "TIEBA_GATE_HANDLERS",
    "TIEBA_ONLY_OFFICIAL_BLOCKED",
    "TIEBA_RECIPE_ID",
    "TIEBA_RECIPE_VERSION",
    "TiebaBudget",
    "TiebaNodeFlow",
    "build_evidence_plan",
    "build_tieba_recipe",
    "run_verification",
    "tieba_recipe_registry",
]
