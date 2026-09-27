"""贴吧模块编排服务（由日常父图在显式派发时调用）。

节点顺序 ``tieba.parse → tieba.search → tieba.read → tieba.summarize →
tieba.verify_official``。每一步都只写真实发生的事：查询词、候选、实际读到
的页数与楼层、失败分类。确认属于目标贴吧的唯一依据是真的读到了帖子页面，
因此读取被访问限制挡住时结果如实降级为「仅帖链」，不会把搜索摘要当页面内容。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeVar

from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus, ModuleWaitState
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaCandidateLink,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaReadResult,
    TiebaRejectedCandidate,
    TiebaResearchProjection,
    TiebaResearchStatus,
    TiebaSearchHit,
    TiebaTimeFilter,
)
from bridges.tieba.lexicon import HOLIDAY_ARRANGEMENT_TERMS, TARGET_FORUM_NAME
from bridges.tieba.official import (
    OFFICIAL_FETCH_LIMIT,
    TiebaOfficialReader,
    is_official_url,
    official_fallback_query,
    official_query,
)
from bridges.tieba.parsing import parse_tieba_request, pending_payload
from bridges.tieba.presenting import (
    build_sections,
    render_clarification_content,
    render_empty_content,
    render_result_content,
    render_stopped_content,
)
from bridges.tieba.reading import TiebaThreadReader
from bridges.tieba.searching import (
    HitDiagnostics,
    SearchOutcome,
    TiebaSearchPort,
    plan_queries,
)

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository

NODE_PARSE = "tieba.parse"
NODE_SEARCH = "tieba.search"
NODE_READ = "tieba.read"
NODE_SUMMARIZE = "tieba.summarize"
NODE_VERIFY_OFFICIAL = "tieba.verify_official"

#: 模块节点中文标签（进度事件与失败定位共用）。
TIEBA_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "贴吧解析",
    NODE_SEARCH: "贴吧搜索",
    NODE_READ: "贴吧读取",
    NODE_SUMMARIZE: "贴吧归纳",
    NODE_VERIFY_OFFICIAL: "官方核验",
}

#: 显式模块标识与等待原因（父图与前端都依赖）。
TIEBA_MODULE_ID = "tieba"
WAIT_KIND_CLARIFICATION = "clarification"
WAIT_REASON_CLARIFICATION = "tieba_clarification"

#: 各阶段的墙钟预算：搜索（允许搜索服务自身 8s 预算与收尾）、读取、官方核验。
SEARCH_DEADLINE_SECONDS = 25.0
READ_DEADLINE_SECONDS = 12.0
OFFICIAL_DEADLINE_SECONDS = 12.0

#: 单轮最多读取的帖子数（每个帖子一次真实公开读取）。
READ_POSTS_LIMIT = 3

#: 帖链降级最多给出的候选链接数。
CANDIDATE_LINKS_LIMIT = 6

#: 记为失败的查询状态（检索成功的空结果不算失败，它有自己的终态）。
FAILED_QUERY_STATUSES: frozenset[ModuleQueryStatus] = frozenset(
    {
        ModuleQueryStatus.ERROR,
        ModuleQueryStatus.TIMEOUT,
        ModuleQueryStatus.CANCELLED,
        ModuleQueryStatus.RATE_LIMITED,
    }
)

#: 目标贴吧名称由词表单点定义。
FORUM_NAME = TARGET_FORUM_NAME

#: 官方核验触发时的中文说明。
OFFICIAL_TRIGGER_NOTE = (
    "本轮问题涉及校规／费用／开放时间／办事流程／放假安排，已追加学校官方页面核验。"
)

#: 放假安排未指定年份时的说明：不假定年份，也不把历史通知当作本次安排。
#: 排序只用当前年份挑官方页面，年份不会被写进用户问题或官方结论。
HOLIDAY_YEAR_NOTE = (
    "问题没有指定年份：本轮不假定年份，也不把历史通知或旧帖当作本次安排；"
    "官方页面按原始名词与当前年份 {year} 优先挑选（只用于排序），"
    "并按原文摘录与取得时间呈现。"
)

#: 逐轮查询的中文标签；有界计划固定为精确词 + 一条备用放宽词（见 ``plan_queries``）。
QUERY_ROUND_LABELS: tuple[str, str] = ("精确词查询", "备用放宽词查询")

#: 归零终止：这些查询状态下不再跑计划内的后续查询。
STOP_QUERY_STATUSES: frozenset[ModuleQueryStatus] = frozenset(
    {ModuleQueryStatus.CANCELLED}
)

#: 读取失败分类的中文短标签（仅帖链的「页面不可读」原因）。
READ_BLOCK_LABELS: dict[ReadStatus, str] = {
    ReadStatus.ACCESS_RESTRICTED: "访问受限，未绕过",
    ReadStatus.UNRECOGNIZED: "页面结构无法解析",
    ReadStatus.NOT_FOUND: "帖子不存在或已删除",
    ReadStatus.TIMEOUT: "读取超时",
    ReadStatus.ERROR: "读取失败",
    ReadStatus.CANCELLED: "已停止读取",
}


class TiebaModuleError(Exception):
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
class TiebaRunOutcome:
    """一轮贴吧模块的收敛结果（父图据此写等待原因与判断终态）。"""

    status: TiebaResearchStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)


@dataclass(frozen=True)
class _SearchAttempts:
    """有界检索的合并结果：采用的候选、被剔除的候选、查询记录与逐轮诊断。"""

    hits: tuple[TiebaSearchHit, ...]
    rejected: tuple[TiebaRejectedCandidate, ...]
    records: list[ModuleQueryRecord]
    diagnostics: HitDiagnostics = HitDiagnostics()
    rounds: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ReadOutcome:
    """读取阶段的真实产出：确认帖、他吧帖、仅帖链与读取侧计数。"""

    confirmed: list[TiebaPostProjection]
    rejected: list[TiebaRejectedCandidate]
    unconfirmed: list[TiebaCandidateLink]
    #: 读过页面但没取得可确认归属的候选数，与超出读取上限、未尝试读取的候选数。
    unreadable: int = 0
    not_attempted: int = 0
    #: 未取得页面的候选总数（帖链列表按上限截断，计数不截断）。
    unconfirmed_total: int = 0


class TiebaResearchService:
    """贴吧信息搜集编排服务。"""

    def __init__(
        self,
        *,
        search: TiebaSearchPort,
        reader: TiebaThreadReader,
        official_reader: TiebaOfficialReader | None = None,
        search_deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        read_deadline_seconds: float = READ_DEADLINE_SECONDS,
        official_deadline_seconds: float = OFFICIAL_DEADLINE_SECONDS,
    ) -> None:
        self._search = search
        self._reader = reader
        self._official_reader = official_reader
        self._search_deadline_seconds = search_deadline_seconds
        self._read_deadline_seconds = read_deadline_seconds
        self._official_deadline_seconds = official_deadline_seconds

    def close(self) -> None:
        for candidate in (self._reader, self._official_reader):
            closer = getattr(candidate, "close", None)
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
    ) -> TiebaRunOutcome:
        """执行一轮贴吧信息搜集；终态全部写回同一条助手消息。"""
        del run_context, run_model_id  # 本模块不调用模型，不存在模型生成的断言。
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise TiebaModuleError(
                NODE_PARSE,
                "message_not_found",
                "消息不存在或没有访问权限。",
                retryable=False,
            )
        waiting = self._pending_wait(repo, account_id, conversation_id)
        run = _Run(emit_node)
        analysis = run.node(
            NODE_PARSE,
            lambda: parse_tieba_request(
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

        attempts = run.node(
            NODE_SEARCH,
            lambda: self._search_candidates(account_id, analysis, stop_event=stop_event),
        )
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=analysis,
            queries=tuple(attempts.records),
            stop_event=stop_event,
        )
        if stopped is not None:
            return stopped

        reads = run.node(
            NODE_READ,
            lambda: self._read_candidates(attempts, stop_event=stop_event),
        )
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=analysis,
            queries=tuple(attempts.records),
            stop_event=stop_event,
        )
        if stopped is not None:
            return stopped

        time_filter, confirmed, sections = run.node(
            NODE_SUMMARIZE,
            lambda: _summarize(analysis, reads.confirmed),
        )
        official_checks = (
            run.node(
                NODE_VERIFY_OFFICIAL,
                lambda: self._verify_official(account_id, analysis, stop_event=stop_event),
            )
            if analysis.needs_official_check
            else []
        )
        projection = _projection(
            analysis=analysis,
            records=attempts.records,
            confirmed=confirmed,
            rejected=reads.rejected,
            unconfirmed=reads.unconfirmed,
            sections=sections,
            time_filter=time_filter,
            official_checks=official_checks,
            attempts=attempts,
            reads=reads,
        )
        if projection.status is TiebaResearchStatus.ERROR:
            self._persist_failure(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                projection=projection,
            )
            raise TiebaModuleError(
                NODE_SEARCH,
                projection.error_code or "tieba_search_failed",
                projection.error_message or "贴吧检索失败，请稍后重试。",
                retryable=projection.retryable,
            )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=(
                render_result_content(projection)
                if projection.confirmed_posts
                else render_empty_content(projection)
            ),
        )
        return TiebaRunOutcome(
            status=projection.status,
            wait_reason=None,
            queries=list(projection.queries),
        )

    # -- 检索 ------------------------------------------------------------

    def _search_candidates(
        self,
        account_id: str,
        analysis: TiebaQuestionAnalysis,
        *,
        stop_event: threading.Event | None,
    ) -> _SearchAttempts:
        """有界检索：先精确词，没有可用候选时再用一条放宽词。

        「检索成功但没有可用候选」与「请求失败」分开判断：前者按既有计划继续
        下一轮查询（这就是放宽词存在的意义），后者只在可重试时继续；取消、
        永久错误与超出搜索预算都在计划内终止，不扩大查询范围。
        """
        records: list[ModuleQueryRecord] = []
        rejected: list[TiebaRejectedCandidate] = []
        rounds: list[str] = []
        diagnostics = HitDiagnostics()
        deadline = time.monotonic() + self._search_deadline_seconds
        for position, query in enumerate(plan_queries(analysis)):
            if position and _budget_exhausted(stop_event, deadline):
                rounds.append(_skipped_round_note(position, stop_event))
                break
            outcome = self._search.search_public(
                account_id,
                query=query,
                reason=f"{FORUM_NAME}信息搜集：只发送最小公开查询词",
                stop_event=stop_event,
                deadline=deadline,
            )
            records.append(outcome.record)
            rejected.extend(outcome.rejected)
            diagnostics = diagnostics.merged(outcome.diagnostics)
            rounds.append(_round_note(position, outcome))
            if outcome.candidates:
                return _SearchAttempts(
                    hits=outcome.candidates,
                    rejected=tuple(rejected),
                    records=records,
                    diagnostics=diagnostics,
                    rounds=tuple(rounds),
                )
            if not _plan_continues(outcome.record):
                break
        return _SearchAttempts(
            hits=(),
            rejected=tuple(rejected),
            records=records,
            diagnostics=diagnostics,
            rounds=tuple(rounds),
        )

    def _read_candidates(
        self,
        attempts: _SearchAttempts,
        *,
        stop_event: threading.Event | None,
    ) -> _ReadOutcome:
        """读取候选：只有页面自身确认属于目标贴吧的帖子才算确认。"""
        confirmed: list[TiebaPostProjection] = []
        rejected = list(attempts.rejected)
        unconfirmed: list[TiebaCandidateLink] = []
        unreadable = 0
        deadline = time.monotonic() + self._read_deadline_seconds
        for hit in attempts.hits[:READ_POSTS_LIMIT]:
            if stop_event is not None and stop_event.is_set():
                break
            result = self._reader.read(hit.url, stop_event=stop_event, deadline=deadline)
            if result.forum_matches_target:
                confirmed.append(_confirmed_post(hit, result))
                continue
            if result.forum_name and result.status in {ReadStatus.READ, ReadStatus.PARTIAL}:
                rejected.append(
                    TiebaRejectedCandidate(
                        url=result.url,
                        title=result.title or hit.title,
                        evidence=(
                            f"已读取帖子页面，页面声明所属贴吧为「{result.forum_name}」，"
                            "不是目标贴吧"
                        ),
                    )
                )
                continue
            unreadable += 1
            unconfirmed.append(_unconfirmed_link(hit, _read_block_reason(result)))
        # 超出读取上限的候选没有读过页面，因此只能作为帖链给出。
        not_attempted = len(attempts.hits[READ_POSTS_LIMIT:])
        for hit in attempts.hits[READ_POSTS_LIMIT:]:
            unconfirmed.append(_unconfirmed_link(hit, "未读取：超出本轮读取条数上限"))
        return _ReadOutcome(
            confirmed=confirmed,
            rejected=rejected,
            unconfirmed=unconfirmed[:CANDIDATE_LINKS_LIMIT],
            unreadable=unreadable,
            not_attempted=not_attempted,
            unconfirmed_total=len(unconfirmed),
        )

    # -- 官方核验 --------------------------------------------------------

    def _verify_official(
        self,
        account_id: str,
        analysis: TiebaQuestionAnalysis,
        *,
        stop_event: threading.Event | None,
    ) -> list[TiebaOfficialCheck]:
        """官网核验：先按官方域名检索，未命中再用一条去域名限定的查询。"""
        reader = self._official_reader
        if reader is None:
            return []
        deadline = time.monotonic() + self._official_deadline_seconds
        terms = _official_terms(analysis)
        # 问题自带年份时按用户年份挑页面，否则按当前年份（只影响挑选顺序）。
        preferred_year = analysis.time_year or datetime.now(UTC).year
        candidates: list[str] = []
        for query, reason in (
            (official_query(analysis), "校规／费用／开放时间／流程／放假安排核对学校官方页面"),
            (official_fallback_query(analysis), "官方域名未命中，改用校名与主题再找官方页面"),
        ):
            outcome = self._search.search_public(
                account_id,
                query=query,
                reason=reason,
                stop_event=stop_event,
                deadline=deadline,
            )
            candidates = _official_candidates(
                outcome, terms, preferred_year=preferred_year
            )
            if candidates:
                break
        checks: list[TiebaOfficialCheck] = []
        for url in candidates[:OFFICIAL_FETCH_LIMIT]:
            if stop_event is not None and stop_event.is_set():
                break
            checks.append(
                reader.fetch(
                    url,
                    terms=tuple(analysis.topic_terms),
                    stop_event=stop_event,
                    deadline=deadline,
                )
            )
        return checks

    # -- 恢复与等待 ------------------------------------------------------

    def _pending_wait(
        self, repo: ConversationRepository, account_id: str, conversation_id: str
    ) -> ModuleWaitState | None:
        """读取本会话最近一次尚未被后续结果取代的澄清等待状态。"""
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            stored = message.tieba_research
            if not isinstance(stored, dict):
                continue
            try:
                projection = TiebaResearchProjection.model_validate(stored)
            except ValueError:
                continue
            if projection.pending is not None:
                return projection.pending
            if projection.status in {
                TiebaResearchStatus.SUCCESS,
                TiebaResearchStatus.LINKS_ONLY,
                TiebaResearchStatus.EMPTY,
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
        analysis: TiebaQuestionAnalysis,
    ) -> TiebaRunOutcome:
        clarification = analysis.clarification
        assert clarification is not None  # 调用点已判定
        now = datetime.now(UTC)
        projection = TiebaResearchProjection(
            status=TiebaResearchStatus.CLARIFICATION,
            topic=_topic_of(analysis) or FORUM_NAME,
            original_question=analysis.original_question,
            topic_terms=list(analysis.topic_terms),
            place_or_event=list(analysis.place_or_event),
            time_filter=_time_filter_state(analysis, []),
            pending=ModuleWaitState(
                module_id=TIEBA_MODULE_ID,
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
        return TiebaRunOutcome(
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
        analysis: TiebaQuestionAnalysis,
        queries: Sequence[ModuleQueryRecord],
    ) -> TiebaRunOutcome:
        now = datetime.now(UTC)
        projection = TiebaResearchProjection(
            status=TiebaResearchStatus.STOPPED,
            topic=_topic_of(analysis) or FORUM_NAME,
            original_question=analysis.original_question,
            topic_terms=list(analysis.topic_terms),
            place_or_event=list(analysis.place_or_event),
            time_filter=_time_filter_state(analysis, []),
            queries=list(queries),
            evidence_boundary=["你已停止本轮搜集，未继续读取页面。"],
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
        return TiebaRunOutcome(
            status=TiebaResearchStatus.STOPPED, wait_reason=None, queries=list(queries)
        )

    def _persist_failure(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        projection: TiebaResearchProjection,
    ) -> None:
        """失败先写回同一条消息（失败分类与查询词都留在投影里），再交给父图收敛。"""
        now = datetime.now(UTC)
        repo.update_message_tieba_research(
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
        analysis: TiebaQuestionAnalysis,
        queries: Sequence[ModuleQueryRecord],
        stop_event: threading.Event | None,
    ) -> TiebaRunOutcome | None:
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
        projection: TiebaResearchProjection,
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
            tieba_research=projection.model_dump(mode="json"),
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


def _confirmed_post(hit: TiebaSearchHit, result: TiebaReadResult) -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id=result.thread_id,
        url=result.url,
        title=result.title or hit.title,
        affiliation_evidence=f"已读取帖子页面，页面声明所属贴吧为「{result.forum_name}」",
        read_status=result.status,
        pages_read=result.pages_read,
        pages_limit=result.pages_limit,
        total_pages=result.total_pages,
        floor_min=result.floor_min,
        floor_max=result.floor_max,
        replies_obtained=bool(result.replies),
        replies=list(result.replies),
        read_error_code=result.error_code,
        read_error_message=result.error_message,
        retrieved_at=result.retrieved_at,
    )


def _unconfirmed_link(hit: TiebaSearchHit, reason: str) -> TiebaCandidateLink:
    """仅帖链降级：把「为什么只有帖链」的原因写进来源说明，不静默降级。"""
    return TiebaCandidateLink(
        url=hit.url,
        title=hit.title,
        source=f"tavily（{reason}；归属未确认）",
    )


def _read_block_reason(result: TiebaReadResult) -> str:
    """读取失败或页面未声明吧名时的中文短因（页面不可读一类）。"""
    if result.forum_name is None and result.status in {ReadStatus.READ, ReadStatus.PARTIAL}:
        return "页面不可读：页面未声明所属贴吧"
    label = READ_BLOCK_LABELS.get(result.status, result.status.value)
    return f"页面不可读：{label}"


def _budget_exhausted(
    stop_event: threading.Event | None, deadline: float
) -> bool:
    """计划内是否还有继续下一轮查询的余地（停止与预算都在此判定）。"""
    if stop_event is not None and stop_event.is_set():
        return True
    return time.monotonic() >= deadline


def _plan_continues(record: ModuleQueryRecord) -> bool:
    """这一轮查询之后是否继续计划内的下一轮。

    检索本身完成（成功／零结果）但没给出可用候选时继续——这正是备用放宽词
    存在的原因；查询失败只在可重试时继续，取消与永久错误都在此终止。
    """
    if record.status in STOP_QUERY_STATUSES:
        return False
    if record.status in FAILED_QUERY_STATUSES:
        return record.retryable
    return True


def _round_note(position: int, outcome: SearchOutcome) -> str:
    """逐轮诊断：查询词、原始结果数、可用候选数与剔除数（脱敏，只记计数）。"""
    label = QUERY_ROUND_LABELS[position]
    if outcome.record.status in FAILED_QUERY_STATUSES:
        reason = outcome.record.error_message or outcome.record.status.value
        return (
            f"{label}「{outcome.record.query}」未完成（{reason}），"
            f"取得 {len(outcome.hits)} 条原始结果。"
        )
    return (
        f"{label}「{outcome.record.query}」取得 {len(outcome.hits)} 条原始结果："
        f"可用候选 {len(outcome.candidates)} 条，"
        f"剔除 {len(outcome.rejected)} 条（其中非帖子链接 {outcome.diagnostics.not_a_thread} 条、"
        f"他吧证据 {outcome.diagnostics.other_forum} 条）。"
    )


def _skipped_round_note(position: int, stop_event: threading.Event | None) -> str:
    """备用查询没有执行时的原因：停止还是预算耗尽。"""
    label = QUERY_ROUND_LABELS[position]
    if stop_event is not None and stop_event.is_set():
        return f"{label}因你已停止而未执行。"
    return f"{label}因超出本轮检索预算而未执行。"


def _official_terms(analysis: TiebaQuestionAnalysis) -> tuple[str, ...]:
    """官方候选排序用的原始名词（与官方查询词同源，不额外发明词）。"""
    terms = list(analysis.topic_terms)
    terms.extend(topic for topic in analysis.official_topics if topic not in terms)
    return tuple(terms)


def _official_candidates(
    outcome: SearchOutcome, terms: Sequence[str], *, preferred_year: int
) -> list[str]:
    """官方页面按「点题且提到目标年份」优先排序，只作证据挑选顺序。

    真实取证里「放假」类查询的第一条官方命中可能是几年前的活动报道，排序
    把标题同时含原始名词与目标年份的通知类页面提到前面，避免旧帖顶在首位；
    目标年份取用户问题里的年份，问题没写年份时取当前年份。排序结果不影响
    任何结论，年份也不会写进用户问题或官方结论。
    """

    def rank(hit: TiebaSearchHit) -> int:
        title = hit.title or ""
        mentions_topic = any(term and term in title for term in terms)
        if mentions_topic and str(preferred_year) in title:
            return 0
        return 1 if mentions_topic else 2

    candidates = [hit for hit in outcome.hits if is_official_url(hit.url)]
    ordered = sorted(candidates, key=rank)  # 稳定排序：同档保持检索顺序
    return _dedupe([hit.url for hit in ordered])


def _summarize(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> tuple[TiebaTimeFilter, list[TiebaPostProjection], list[str]]:
    """按时间条件收敛已读楼层，并且只从收敛后的真实楼层里分段。"""
    time_filter, filtered = _apply_time_condition(analysis, posts)
    return time_filter, filtered, build_sections(filtered)


def _apply_time_condition(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> tuple[TiebaTimeFilter, list[TiebaPostProjection]]:
    """有时间条件且读到了带时间的楼层时，按条件过滤楼层（如实记录结果）。"""
    year = analysis.time_year
    if not analysis.time_requirement or year is None:
        return _time_filter_state(analysis, posts), posts
    total = sum(len(post.replies) for post in posts)
    filtered: list[TiebaPostProjection] = []
    kept = 0
    for post in posts:
        replies = [
            reply
            for reply in post.replies
            if not reply.posted_at or str(year) in reply.posted_at
        ]
        kept += len(replies)
        filtered.append(
            post.model_copy(
                update={
                    "replies": replies,
                    "replies_obtained": bool(replies),
                    "read_error_message": post.read_error_message
                    if replies
                    else f"时间条件「{analysis.time_requirement}」内没有读到回复。",
                }
            )
        )
    state = TiebaTimeFilter(
        requirement=analysis.time_requirement,
        year=year,
        applied=True,
        note=(
            f"已按时间条件「{analysis.time_requirement}」核对楼层发帖时间："
            f"保留 {kept} 条、剔除 {total - kept} 条。"
        ),
    )
    return state, filtered


def _time_filter_state(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> TiebaTimeFilter:
    """时间条件的执行状态：有没有可核对的时间、能不能真的过滤。"""
    if not analysis.time_requirement:
        return TiebaTimeFilter(
            requirement=None,
            year=None,
            applied=False,
            note="本轮问题没有提出时间条件。",
        )
    has_times = any(reply.posted_at for post in posts for reply in post.replies)
    if not has_times:
        return TiebaTimeFilter(
            requirement=analysis.time_requirement,
            year=analysis.time_year,
            applied=False,
            note=(
                f"保留了你提出的时间条件「{analysis.time_requirement}」；"
                "本轮没有读到带发帖时间的楼层，因此没有按时间过滤。"
            ),
        )
    return TiebaTimeFilter(
        requirement=analysis.time_requirement,
        year=analysis.time_year,
        applied=False,
        note=f"已取得带时间的楼层，可核对你提出的时间条件「{analysis.time_requirement}」。",
    )


def _projection(
    *,
    analysis: TiebaQuestionAnalysis,
    records: Sequence[ModuleQueryRecord],
    confirmed: list[TiebaPostProjection],
    rejected: list[TiebaRejectedCandidate],
    unconfirmed: list[TiebaCandidateLink],
    sections: list[str],
    time_filter: TiebaTimeFilter,
    official_checks: list[TiebaOfficialCheck],
    attempts: _SearchAttempts,
    reads: _ReadOutcome,
) -> TiebaResearchProjection:
    error_record = _first_error(records)
    if confirmed:
        status = TiebaResearchStatus.SUCCESS
    elif unconfirmed:
        status = TiebaResearchStatus.LINKS_ONLY
    elif not records or _only_failures(records):
        status = TiebaResearchStatus.ERROR
    else:
        status = TiebaResearchStatus.EMPTY
    boundary = _evidence_boundary(
        confirmed=confirmed,
        unconfirmed=unconfirmed,
        rejected=rejected,
        attempts=attempts,
        reads=reads,
    )
    if analysis.needs_official_check:
        boundary.append(OFFICIAL_TRIGGER_NOTE)
    if _holiday_without_year(analysis):
        boundary.append(HOLIDAY_YEAR_NOTE.format(year=datetime.now(UTC).year))
    if status is TiebaResearchStatus.LINKS_ONLY:
        boundary.append(
            "候选帖的贴吧归属未能确认：只有真的读到帖子页面才会纳入确认结果，"
            "搜索摘要不足以确认归属。"
        )
    # 失败分类只描述本轮的真实落点：已经读到确认帖子的轮次不整体报失败，早先
    # 一次可重试的查询失败留在查询记录里；没有确认帖子时按可重试如实标注。
    ended_in_error = status is TiebaResearchStatus.ERROR
    retryable = bool(
        error_record is not None
        and error_record.retryable
        and (ended_in_error or not confirmed)
    )
    surface_error = ended_in_error or retryable
    return TiebaResearchProjection(
        status=status,
        topic=_topic_of(analysis) or FORUM_NAME,
        original_question=analysis.original_question,
        topic_terms=list(analysis.topic_terms),
        place_or_event=list(analysis.place_or_event),
        time_filter=time_filter,
        queries=list(records),
        confirmed_posts=confirmed,
        candidate_links=unconfirmed,
        rejected_candidates=rejected,
        official_check_requested=analysis.needs_official_check,
        official_checks=official_checks,
        sections=sections,
        evidence_boundary=boundary,
        empty_reason=(
            _empty_reason(unconfirmed, rejected, attempts.diagnostics)
            if status in {TiebaResearchStatus.LINKS_ONLY, TiebaResearchStatus.EMPTY}
            else None
        ),
        retryable=retryable,
        error_code=(
            error_record.error_code if error_record is not None and surface_error else None
        ),
        error_message=(
            error_record.error_message
            if error_record is not None and surface_error
            else None
        ),
    )


def _holiday_without_year(analysis: TiebaQuestionAnalysis) -> bool:
    """放假安排类问题没有指定年份：必须写明本轮不假定年份。"""
    if analysis.time_year is not None:
        return False
    return bool(set(analysis.topic_terms) & HOLIDAY_ARRANGEMENT_TERMS)


def _empty_reason(
    unconfirmed: list[TiebaCandidateLink],
    rejected: list[TiebaRejectedCandidate],
    diagnostics: HitDiagnostics,
) -> str:
    if unconfirmed:
        return (
            f"没有取得可确认属于「{FORUM_NAME}」的帖子页面，因此只给出候选帖链，"
            "并明确未取得回复内容。"
        )
    if rejected:
        return (
            f"{_hit_composition(diagnostics)}，没有可确认属于「{FORUM_NAME}」的帖子"
            "（已逐个列出剔除依据）。"
        )
    return f"本轮检索没有返回可确认属于「{FORUM_NAME}」的公开帖子。"


def _hit_composition(diagnostics: HitDiagnostics) -> str:
    """原始命中数的构成说明：命中条数不等于可用候选数，也不等于确认帖数。"""
    parts: list[str] = []
    if diagnostics.not_a_thread:
        parts.append(f"非帖子链接 {diagnostics.not_a_thread} 条")
    if diagnostics.other_forum:
        parts.append(f"带其他贴吧证据 {diagnostics.other_forum} 条")
    if diagnostics.duplicate:
        parts.append(f"重复链接 {diagnostics.duplicate} 条")
    if not parts:
        return f"共取得 {diagnostics.raw_hits} 条原始搜索结果"
    return f"共取得 {diagnostics.raw_hits} 条原始搜索结果（{'、'.join(parts)}）"


def _evidence_boundary(
    *,
    confirmed: list[TiebaPostProjection],
    unconfirmed: list[TiebaCandidateLink],
    rejected: list[TiebaRejectedCandidate],
    attempts: _SearchAttempts,
    reads: _ReadOutcome,
) -> list[str]:
    notes = [
        f"只纳入有证据确认属于「{FORUM_NAME}」的帖子；确认依据是真的读到了帖子页面。",
    ]
    notes.extend(attempts.rounds)
    if unconfirmed:
        notes.append(
            f"另有 {reads.unconfirmed_total} 条候选帖没有取得页面（其中页面不可读 "
            f"{reads.unreadable} 条、超出读取上限未读取 {reads.not_attempted} 条），"
            f"因此只给出 {len(unconfirmed)} 条帖链：贴吧归属未确认，也未取得回复内容。"
        )
    if rejected:
        notes.append(
            f"已剔除 {len(rejected)} 条候选（非帖子链接与他吧证据都在剔除依据里逐条留痕）。"
        )
    if confirmed:
        unread = [post for post in confirmed if not post.replies_obtained]
        if unread:
            notes.append(
                f"其中 {len(unread)} 个帖子确认了归属但没有取得回复内容，未做任何内容推断。"
            )
        notes.append("不承诺完整抓取某帖全部回复，也不绕过登录或访问限制。")
    if attempts.diagnostics.raw_hits:
        notes.append(
            f"本轮共取得 {attempts.diagnostics.raw_hits} 条原始搜索结果，"
            f"其中可用候选 {attempts.diagnostics.usable} 条："
            "原始命中数不等于确认帖子数。"
        )
    notes.append("本模块不调用模型生成内容，正文与引文都来自实际取得的页面文本。")
    return notes


def _topic_of(analysis: TiebaQuestionAnalysis) -> str:
    return " ".join(analysis.topic_terms)


def _first_error(records: Sequence[ModuleQueryRecord]) -> ModuleQueryRecord | None:
    for record in records:
        if record.status in FAILED_QUERY_STATUSES:
            return record
    return None


def _only_failures(records: Sequence[ModuleQueryRecord]) -> bool:
    return bool(records) and all(
        record.status in FAILED_QUERY_STATUSES for record in records
    )


def _dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return ordered
