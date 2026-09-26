"""职业规划模块编排服务（由日常父图在显式派发时调用）。

节点顺序 ``career.parse → career.plan → career.collect → career.filter →
career.analyze → career.advise``。每一步都只写真实发生的事：实际查询词与筛选
条件、真实读到的岗位字段、逐条剔除依据。主样本只收公开可读且岗位与城市都
匹配的岗位，因此读不到页面时如实降级为「未核实链接」，不会把搜索摘要当岗位内容。

本模块不调用模型：正文、分析与建议全部由真实证据渲染与归纳。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeVar

from bridges.career_plan.advising import build_advice
from bridges.career_plan.analyzing import analyze_samples
from bridges.career_plan.collecting import JobPageReader
from bridges.career_plan.contracts import (
    AdjacentJobSuggestion,
    CareerAdviceItem,
    CareerAnalysis,
    CareerCandidateLink,
    CareerPlanProjection,
    CareerPlanStatus,
    CareerQueryPlanItem,
    CareerRequestAnalysis,
    JobSample,
)
from bridges.career_plan.filtering import (
    FilterOutcome,
    JobCandidate,
    filter_candidates,
    site_label,
)
from bridges.career_plan.lexicon import SOURCE_LABELS
from bridges.career_plan.parsing import parse_career_request, pending_payload
from bridges.career_plan.planning import build_plan
from bridges.career_plan.presenting import (
    render_clarification_content,
    render_empty_content,
    render_links_only_content,
    render_result_content,
    render_stopped_content,
)
from bridges.career_plan.searching import CareerSearchHit, CareerSearchPort
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus, ModuleWaitState

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository

NODE_PARSE = "career.parse"
NODE_PLAN = "career.plan"
NODE_COLLECT = "career.collect"
NODE_FILTER = "career.filter"
NODE_ANALYZE = "career.analyze"
NODE_ADVISE = "career.advise"

#: 模块节点中文标签（进度事件与失败定位共用）。
CAREER_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "理解求职目标",
    NODE_PLAN: "制定检索计划",
    NODE_COLLECT: "读取公开岗位",
    NODE_FILTER: "筛选匹配岗位",
    NODE_ANALYZE: "归纳技能与薪资",
    NODE_ADVISE: "给出求职建议",
}

#: 显式模块标识与等待原因（父图与前端都依赖）。
CAREER_MODULE_ID = "career"
WAIT_KIND_CLARIFICATION = "clarification"
WAIT_REASON_CLARIFICATION = "career_clarification"

#: 各阶段的墙钟预算：检索（三条来源查询共用）、逐页读取。
SEARCH_DEADLINE_SECONDS = 30.0
READ_DEADLINE_SECONDS = 20.0

#: 每条来源查询最多读取的岗位页数（每个页面一次真实公开读取）。
READS_PER_SOURCE = 3

#: 记为失败的查询状态（检索成功的空结果不算失败，它有自己的终态）。
FAILED_QUERY_STATUSES: frozenset[ModuleQueryStatus] = frozenset(
    {
        ModuleQueryStatus.ERROR,
        ModuleQueryStatus.TIMEOUT,
        ModuleQueryStatus.CANCELLED,
        ModuleQueryStatus.RATE_LIMITED,
    }
)

#: 证据边界里固定说明（每条都对应真实实现约束）。
BOUNDARY_NOT_MODEL = "本模块不调用模型生成内容，正文、统计与建议都来自实际读到的页面文本。"


class CareerModuleError(Exception):
    """模块内的稳定失败（定位到具体节点）。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class CareerRunOutcome:
    """一轮职业规划分析的收敛结果（父图据此写等待原因与判断终态）。"""

    status: CareerPlanStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)


@dataclass(frozen=True)
class _Collected:
    """检索与读取的真实产出：统一查询记录、待过滤候选与未读到的链接。"""

    records: list[ModuleQueryRecord]
    candidates: list[JobCandidate]
    unread_links: list[CareerSearchHit]


class CareerPlanService:
    """职业规划编排服务。"""

    def __init__(
        self,
        *,
        search: CareerSearchPort,
        reader: JobPageReader,
        search_deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        read_deadline_seconds: float = READ_DEADLINE_SECONDS,
        reads_per_source: int = READS_PER_SOURCE,
    ) -> None:
        self._search = search
        self._reader = reader
        self._search_deadline_seconds = search_deadline_seconds
        self._read_deadline_seconds = read_deadline_seconds
        self._reads_per_source = reads_per_source

    def close(self) -> None:
        closer = getattr(self._reader, "close", None)
        if callable(closer):
            closer()

    # -- 对外入口 --------------------------------------------------------

    def run(
        self,
        *,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        run_context: object | None = None,
        run_model_id: str | None = None,
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
    ) -> CareerRunOutcome:
        """执行一轮职业规划分析；终态全部写回同一条助手消息。"""
        del run_context, run_model_id  # 本模块不调用模型，不存在模型生成的断言。
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise CareerModuleError(
                NODE_PARSE,
                "message_not_found",
                "消息不存在或没有访问权限。",
                retryable=False,
            )
        waiting = self._pending_wait(repo, account_id, conversation_id)
        run = _Run(emit_node)
        analysis = run.node(
            NODE_PARSE,
            lambda: parse_career_request(
                user_message.content,
                pending=None if waiting is None else dict(waiting.context),
            ),
        )
        if analysis.clarification is not None:
            return self._persist_clarification(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=analysis,
            )
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=analysis,
            queries=(),
            stop_event=stop_event,
        )
        if stopped is not None:
            return stopped

        plan = run.node(NODE_PLAN, lambda: build_plan(analysis))
        collected = run.node(
            NODE_COLLECT,
            lambda: self._collect(account_id, plan, stop_event=stop_event),
        )
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=analysis,
            queries=tuple(collected.records),
            stop_event=stop_event,
        )
        if stopped is not None:
            return stopped

        outcome = run.node(
            NODE_FILTER,
            lambda: filter_candidates(
                collected.candidates, analysis, reference=datetime.now(UTC)
            ),
        )
        report = run.node(NODE_ANALYZE, lambda: analyze_samples(outcome.samples))
        advices, adjacent = run.node(
            NODE_ADVISE,
            lambda: build_advice(
                analysis, report, outcome.samples, adjacent_counts=outcome.adjacent_counts
            ),
        )

        projection = _projection(
            analysis=analysis,
            plan=plan,
            collected=collected,
            outcome=outcome,
            report=report,
            advices=advices,
            adjacent=adjacent,
        )
        if projection.status is CareerPlanStatus.ERROR:
            self._persist_failure(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                projection=projection,
            )
            raise CareerModuleError(
                NODE_COLLECT,
                projection.error_code or "career_search_failed",
                projection.error_message or "岗位检索失败，请稍后重试。",
                retryable=projection.retryable,
            )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=_content_for(projection),
        )
        return CareerRunOutcome(
            status=projection.status,
            wait_reason=None,
            queries=list(projection.queries),
        )

    # -- 检索与读取 ------------------------------------------------------

    def _collect(
        self,
        account_id: str,
        plan: tuple[CareerQueryPlanItem, ...],
        *,
        stop_event: threading.Event | None,
    ) -> _Collected:
        """按计划逐来源检索，再逐页公开读取（每页一次，有界去重）。"""
        records: list[ModuleQueryRecord] = []
        candidates: list[JobCandidate] = []
        unread: list[CareerSearchHit] = []
        seen_urls: set[str] = set()
        search_deadline = time.monotonic() + self._search_deadline_seconds
        read_deadline = time.monotonic() + self._read_deadline_seconds
        for item in plan:
            outcome = self._search.search_public(
                account_id,
                query=item.query,
                reason=f"职业规划：{item.source_label}按最小公开查询词检索",
                source=item.source,
                stop_event=stop_event,
                deadline=search_deadline,
            )
            records.append(outcome.record)
            fresh = [hit for hit in outcome.hits if hit.url not in seen_urls]
            for hit in fresh[: self._reads_per_source]:
                seen_urls.add(hit.url)
                if stop_event is not None and stop_event.is_set():
                    break
                read = self._reader.read(
                    hit.url, stop_event=stop_event, deadline=read_deadline
                )
                candidates.append(
                    JobCandidate(
                        url=hit.url,
                        source=item.source,
                        label=hit.title or site_label(hit.url),
                        read=read,
                    )
                )
            for hit in fresh[self._reads_per_source :]:
                seen_urls.add(hit.url)
                unread.append(hit)
            if stop_event is not None and stop_event.is_set():
                break
        return _Collected(records=records, candidates=candidates, unread_links=unread)

    # -- 恢复与等待 ------------------------------------------------------

    def _pending_wait(
        self, repo: ConversationRepository, account_id: str, conversation_id: str
    ) -> ModuleWaitState | None:
        """读取本会话最近一次尚未被后续结果取代的澄清等待状态。"""
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            stored = message.career_plan
            if not isinstance(stored, dict):
                continue
            try:
                projection = CareerPlanProjection.model_validate(stored)
            except ValueError:
                continue
            if projection.pending is not None:
                return projection.pending
            # 失败与停止同样是「本轮结束了」（前端卡片与等待恢复语义一致）：
            # 不再把更早那条澄清当成待续问题，否则用户的下一条消息会被接上
            # 旧请求的城市与阶段。
            if projection.status in {
                CareerPlanStatus.SUCCESS,
                CareerPlanStatus.LINKS_ONLY,
                CareerPlanStatus.EMPTY,
                CareerPlanStatus.ERROR,
                CareerPlanStatus.STOPPED,
            }:
                return None
        return None

    # -- 落库 ------------------------------------------------------------

    def _persist_clarification(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: CareerRequestAnalysis,
    ) -> CareerRunOutcome:
        clarification = analysis.clarification
        assert clarification is not None  # 调用点已判定
        now = datetime.now(UTC)
        projection = CareerPlanProjection(
            status=CareerPlanStatus.CLARIFICATION,
            topic=_topic_of(analysis),
            original_request=analysis.original_request,
            job_terms=list(analysis.job_terms),
            stage=analysis.stage,
            graduation_year=analysis.graduation_year,
            cities=list(analysis.cities),
            constraints=list(analysis.constraints),
            pending=ModuleWaitState(
                module_id=CAREER_MODULE_ID,
                kind=WAIT_KIND_CLARIFICATION,
                question=clarification.question,
                origin_message_id=assistant_message_id,
                context=pending_payload(analysis),
                created_at=now,
            ),
            completed_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=render_clarification_content(analysis),
        )
        return CareerRunOutcome(
            status=projection.status,
            wait_reason=WAIT_REASON_CLARIFICATION,
            queries=[],
        )

    def _persist_stopped(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: CareerRequestAnalysis,
        queries: Sequence[ModuleQueryRecord],
    ) -> CareerRunOutcome:
        now = datetime.now(UTC)
        projection = CareerPlanProjection(
            status=CareerPlanStatus.STOPPED,
            topic=_topic_of(analysis),
            original_request=analysis.original_request,
            job_terms=list(analysis.job_terms),
            stage=analysis.stage,
            graduation_year=analysis.graduation_year,
            cities=list(analysis.cities),
            constraints=list(analysis.constraints),
            queries=list(queries),
            evidence_boundary=["你已停止本轮分析，未继续检索与读取岗位页。"],
            completed_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            projection=projection,
            content=render_stopped_content(projection),
        )
        return CareerRunOutcome(
            status=CareerPlanStatus.STOPPED, wait_reason=None, queries=list(queries)
        )

    def _persist_failure(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        projection: CareerPlanProjection,
    ) -> None:
        """失败先写回同一条消息（失败分类与查询词都留在投影里），再交给父图收敛。"""
        now = datetime.now(UTC)
        repo.update_message_career_plan(
            account_id,
            assistant_message_id,
            projection.model_dump(mode="json"),
            now,
        )
        repo.update_message_content(
            account_id,
            assistant_message_id,
            render_empty_content(projection),
            now,
        )

    def _stopped(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: CareerRequestAnalysis,
        queries: Sequence[ModuleQueryRecord],
        stop_event: threading.Event | None,
    ) -> CareerRunOutcome | None:
        if stop_event is None or not stop_event.is_set():
            return None
        return self._persist_stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=analysis,
            queries=queries,
        )

    def _finalize(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        status: ChatMessageStatus,
        projection: CareerPlanProjection,
        content: str,
    ) -> None:
        now = datetime.now(UTC)
        projection = projection.model_copy(
            update={"completed_at": projection.completed_at or now}
        )
        if content:
            repo.update_message_content(account_id, assistant_message_id, content, now)
        # 局部导入：模块子图与 chat 服务互相引用（父图调用子图、子图复用消息
        # 终态收敛），模块级导入会形成包级循环。
        from bridges.chat.turn import finalize_message

        finalize_message(
            repo,
            account_id,
            assistant_message_id,
            status=status,
            error_code=projection.error_code,
            error_message=projection.error_message,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            lock=None,
            started=time.monotonic(),
            now=now,
            career_plan=projection.model_dump(mode="json"),
        )


T = TypeVar("T")


class _Run:
    """节点进度发射器：只对真实开始/完成的节点发 started/completed 与耗时。"""

    def __init__(self, emit_node: Callable[[str, str, int | None], None]) -> None:
        self._emit = emit_node

    def node(self, name: str, body: Callable[[], T]) -> T:
        self._emit(name, "started", None)
        started = time.monotonic()
        result = body()
        self._emit(name, "completed", max(1, int((time.monotonic() - started) * 1000)))
        return result


def _projection(
    *,
    analysis: CareerRequestAnalysis,
    plan: tuple[CareerQueryPlanItem, ...],
    collected: _Collected,
    outcome: FilterOutcome,
    report: CareerAnalysis,
    advices: list[CareerAdviceItem],
    adjacent: list[AdjacentJobSuggestion],
) -> CareerPlanProjection:
    """组装投影并判定终态（终态只由真实证据决定）。"""
    samples = list(outcome.samples)
    links = _candidate_links(collected, outcome)
    status = _status(collected.records, samples, bool(links))
    error = _error_record(collected.records)
    return CareerPlanProjection(
        status=status,
        topic=_topic_of(analysis),
        original_request=analysis.original_request,
        job_terms=list(analysis.job_terms),
        family_title=analysis.family_title,
        stage=analysis.stage,
        graduation_year=analysis.graduation_year,
        cities=list(analysis.cities),
        constraints=list(analysis.constraints),
        plan=list(plan),
        queries=list(collected.records),
        samples=samples,
        candidate_links=links,
        rejected=list(outcome.rejected),
        analysis=report if samples else None,
        advices=list(advices),
        adjacent_suggestions=list(adjacent),
        evidence_boundary=_evidence_boundary(analysis, collected, outcome, samples),
        empty_reason=(
            _empty_reason(analysis)
            if status in {CareerPlanStatus.LINKS_ONLY, CareerPlanStatus.EMPTY}
            else None
        ),
        retryable=bool(error and error.retryable),
        error_code=error.error_code if error is not None else None,
        error_message=error.error_message if error is not None else None,
    )


def _status(
    records: Sequence[ModuleQueryRecord],
    samples: list[JobSample],
    has_candidate_links: bool,
) -> CareerPlanStatus:
    """终态只由真实证据决定：有样本→成功；全是失败→错误；有未核实链接→仅链接。"""
    if samples:
        return CareerPlanStatus.SUCCESS
    if records and all(record.status in FAILED_QUERY_STATUSES for record in records):
        return CareerPlanStatus.ERROR
    if has_candidate_links:
        return CareerPlanStatus.LINKS_ONLY
    return CareerPlanStatus.EMPTY


def _candidate_links(
    collected: _Collected, outcome: FilterOutcome
) -> list[CareerCandidateLink]:
    """未核实的候选链接：读不到页面的、以及超出读取上限没有读的。"""
    links = list(outcome.unconfirmed)
    for hit in collected.unread_links:
        links.append(
            CareerCandidateLink(
                url=hit.url,
                title=hit.title or site_label(hit.url),
                source=hit.source,
                source_label=SOURCE_LABELS.get(hit.source, hit.source),
                note="未核实：超过本轮读取上限，没有读取该岗位页。",
            )
        )
    return links


def _empty_reason(analysis: CareerRequestAnalysis) -> str:
    job = analysis.job_title or (analysis.job_terms[0] if analysis.job_terms else "")
    city_part = f"，城市 {'、'.join(analysis.cities)}" if analysis.cities else ""
    return (
        f"本轮没有取得公开可读且匹配的「{job}」{city_part}岗位样本："
        "上面的检索计划与调用记录是实际发出的查询，未核实的候选链接已逐条列出。"
    )


def _evidence_boundary(
    analysis: CareerRequestAnalysis,
    collected: _Collected,
    outcome: FilterOutcome,
    samples: list[JobSample],
) -> list[str]:
    """证据边界：只写本轮真实的取舍与缺口。"""
    notes = [
        "只把公开可读、岗位名命中目标岗位（或真正同义名）、城市可核对的岗位纳入主样本；"
        "搜索摘要不构成岗位样本。",
    ]
    if outcome.unconfirmed:
        notes.append(
            f"另有 {len(outcome.unconfirmed)} 个候选岗位页没有取得内容，只给出链接并标注"
            "未核实：它们的岗位名、城市与薪资都未核实，未纳入任何统计。"
        )
    if collected.unread_links:
        notes.append(
            f"本轮每条来源最多读取 {READS_PER_SOURCE} 个岗位页，"
            f"另有 {len(collected.unread_links)} 个候选没有读取。"
        )
    if analysis.adjacent_jobs:
        notes.append(
            "相邻岗位（"
            + "、".join(analysis.adjacent_jobs[:3])
            + "）单列建议，不并入技能与薪资统计。"
        )
    if analysis.experience_hint:
        notes.append(
            f"你提到的经验要求「{analysis.experience_hint}」本轮没有做经验过滤："
            "样本里的经验要求逐条展示，由你自己核对。"
        )
    if not samples:
        notes.append("本轮没有可用岗位样本，因此没有给出技能、薪资或市场层面的结论。")
    notes.append(BOUNDARY_NOT_MODEL)
    return notes


def _content_for(projection: CareerPlanProjection) -> str:
    if projection.samples:
        return render_result_content(projection)
    if projection.status is CareerPlanStatus.LINKS_ONLY:
        return render_links_only_content(projection)
    return render_empty_content(projection)


def _topic_of(analysis: CareerRequestAnalysis) -> str:
    job = analysis.job_title or " ".join(analysis.job_terms) or "未确定岗位"
    parts = [job]
    if analysis.cities:
        parts.append("、".join(analysis.cities))
    if analysis.stage:
        parts.append(analysis.stage)
    return " · ".join(parts)


def _error_record(records: Sequence[ModuleQueryRecord]) -> ModuleQueryRecord | None:
    for record in records:
        if record.status in FAILED_QUERY_STATUSES:
            return record
    return None


__all__ = [
    "CAREER_MODULE_ID",
    "CAREER_NODE_LABELS",
    "CareerModuleError",
    "CareerPlanService",
    "CareerRunOutcome",
    "NODE_ADVISE",
    "NODE_ANALYZE",
    "NODE_COLLECT",
    "NODE_FILTER",
    "NODE_PARSE",
    "NODE_PLAN",
]
