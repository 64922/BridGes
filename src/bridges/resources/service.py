"""资料子图编排：``resources.parse → search_books / search_videos → rank → present``。

复用 Issue 11 建立的三份合同（证据 / 等待 / 失败与停止），并按本模块的编排
合同落地：

1. **原词优先**：``resources.parse`` 逐字保留本轮专业名词，译名只作扩展；
2. **只问一项**：仅当学习层次确实影响推荐且上下文不足时追问层次，问题随助手
   消息落库（``ModuleWaitState``），下一轮从该处恢复；
3. **两条来源各自记账**：图书书目与视频发现分别产出 ``ModuleQueryRecord``，
   一条来源失败只标注自己的缺口，不用另一条来源或模型记忆冒充；
4. **视频先发现再核对**：公网搜索找到的哔哩哔哩直达页必须经公开接口核对
   元数据通过后才进入清单，核对失败的直接移除；
5. **有界**：图书与视频各有墙钟预算，停止在节点边界生效并把「已停止」写回
   同一条消息。
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
from bridges.resources.contracts import (
    LearningResourcesProjection,
    ResourcesQueryPlan,
    ResourcesStatus,
    ResourcesTermAnalysis,
)
from bridges.resources.parsing import level_label, parse_resources_request, pending_payload
from bridges.resources.planning import plan_resources
from bridges.resources.presenting import (
    render_clarification_content,
    render_empty_content,
    render_mismatch_content,
    render_result_content,
    render_stopped_content,
)
from bridges.resources.ranking import RankOutcome, cover_original_phrase, rank_resources
from bridges.resources.sources import (
    BilibiliVideoDiscoverer,
    BilibiliVideoVerifier,
    BookCandidate,
    OpenAlexBookSource,
    OpenLibraryBookSource,
    VideoVerifyOutcome,
)

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository

#: 本轮子图节点名（进度事件与失败定位使用；父图节点仍是 invoke_subgraph_or_chat）。
#: 与编排合同一致：解析 → 两条检索 → 排序 → 生成，不含独立的计划节点。
NODE_PARSE = "resources.parse"
NODE_SEARCH_BOOKS = "resources.search_books"
NODE_SEARCH_VIDEOS = "resources.search_videos"
NODE_RANK = "resources.rank"
NODE_PRESENT = "resources.present"

#: 节点的用户可读中文名（父图失败信息按此标注真实失败位置）。
RESOURCES_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "理解学习需求",
    NODE_SEARCH_BOOKS: "检索图书书目",
    NODE_SEARCH_VIDEOS: "查找哔哩哔哩视频",
    NODE_RANK: "筛选与排序资料",
    NODE_PRESENT: "整理资料清单",
}

#: 图书检索的墙钟预算（秒）：到点即停止剩余来源并如实标注缺口。
SEARCH_DEADLINE_SECONDS = 20.0

#: 视频核对（逐条查公开元数据）的墙钟预算（秒）。
VERIFY_DEADLINE_SECONDS = 12.0

#: 澄清等待状态的类型标识（等待合同的一部分）。
WAIT_KIND_CLARIFICATION = "clarification"

RESOURCES_MODULE_ID = "resources"

#: 父图运行表的等待原因（可观测的持久化等待状态）。
WAIT_REASON_CLARIFICATION = "resources_clarification"

#: 视为「硬失败」的查询状态：两条来源都硬失败且没有任何候选时如实报失败。
_HARD_FAILURES = frozenset(
    {
        ModuleQueryStatus.ERROR,
        ModuleQueryStatus.TIMEOUT,
        ModuleQueryStatus.RATE_LIMITED,
    }
)


class ResourcesModuleError(Exception):
    """资料子图失败：节点位置、稳定错误码、可操作中文说明与是否可重试。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ResourcesRunOutcome:
    """一轮资料模块的收敛结果（父图据此写等待原因与判断终态）。"""

    status: ResourcesStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)


@dataclass(frozen=True)
class BookSearchOutcomes:
    """两条书目来源的合并结果：候选、按来源的条数与全部统一记录。"""

    candidates: list[BookCandidate]
    source_counts: dict[str, int]
    records: list[ModuleQueryRecord]


class LearningResourcesService:
    """学习资料推荐模块编排服务（由日常父图在显式派发时调用）。"""

    def __init__(
        self,
        *,
        books: Sequence[OpenLibraryBookSource | OpenAlexBookSource] = (),
        discoverer: BilibiliVideoDiscoverer | None = None,
        verifier: BilibiliVideoVerifier | None = None,
        deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        verify_deadline_seconds: float = VERIFY_DEADLINE_SECONDS,
    ) -> None:
        self._books = list(books)
        self._discoverer = discoverer
        self._verifier = verifier
        self._deadline_seconds = deadline_seconds
        self._verify_deadline_seconds = verify_deadline_seconds

    def close(self) -> None:
        for source in self._books:
            source.close()
        if self._verifier is not None:
            self._verifier.close()

    # -- 对外入口 --------------------------------------------------------

    def run(
        self,
        *,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
    ) -> ResourcesRunOutcome:
        """执行一轮资料模块；终态（完成/澄清/空/失败/停止）全部写回同一消息。"""
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise ResourcesModuleError(
                NODE_PARSE, "message_not_found", "消息不存在或没有访问权限。", retryable=False
            )
        pending = self._pending_wait(repo, account_id, conversation_id)
        prior = self._prior_user_messages(repo, account_id, conversation_id, user_message_id)
        run = _Run(emit_node)
        parsed = run.node(
            NODE_PARSE,
            lambda: parse_resources_request(
                user_message.content, prior_context=prior, pending=pending
            ),
        )
        if parsed.clarification is not None:
            return self._persist_clarification(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=parsed,
            )
        plan = plan_resources(parsed)
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=parsed,
            stop_event=stop_event,
        )
        if stopped is not None:
            return stopped
        books = run.node(NODE_SEARCH_BOOKS, lambda: self._search_books(account_id, plan))
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=parsed,
            queries=books.records,
            stop_event=stop_event,
        )
        if stopped is not None:
            return stopped
        videos = run.node(
            NODE_SEARCH_VIDEOS,
            lambda: self._search_videos(
                account_id, plan, stop_event=stop_event
            ),
        )
        all_queries = [*books.records, *videos.records]
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=parsed,
            queries=all_queries,
            stop_event=stop_event,
        )
        if stopped is not None:
            return stopped
        failure = _hard_failure(books, videos)
        if failure is not None:
            self._fail(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                node=failure[0],
                analysis=parsed,
                plan=plan,
                record=failure[1],
                queries=all_queries,
            )
        if not books.candidates and not videos.candidates:
            return self._persist_empty(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=parsed,
                plan=plan,
                queries=all_queries,
                notes=_empty_notes(books, videos),
            )
        ranked = run.node(
            NODE_RANK,
            lambda: rank_resources(
                parsed,
                plan,
                books.candidates,
                videos.candidates,
                book_sources=books.source_counts,
                rejected_videos=videos.rejected,
            ),
        )
        if ranked.topic_mismatch or not ranked.items:
            # 主题不匹配：停止推荐并请用户纠正，不凑数量（同族模块的一致行为）；
            # 这里不建立等待状态——下一句通常是更正后的主题，按全新请求解析更准。
            return self._persist_empty(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=parsed,
                plan=plan,
                queries=all_queries,
                notes=ranked.notes,
                mismatch=True,
            )
        if not cover_original_phrase(parsed, ranked.items):
            self._fail(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                node=NODE_PRESENT,
                analysis=parsed,
                plan=plan,
                queries=all_queries,
                code="resources_topic_mismatch",
                message="推荐结果未覆盖你的原始说法，已停止生成。",
                retryable=False,
            )
        return self._persist_result(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=parsed,
            plan=plan,
            ranked=ranked,
            queries=all_queries,
        )

    # -- 两条检索 --------------------------------------------------------

    def _search_books(self, account_id: str, plan: ResourcesQueryPlan) -> BookSearchOutcomes:
        """按来源顺序检索书目：一条来源失败只标注自己的缺口，不阻断另一条。"""
        candidates: list[BookCandidate] = []
        source_counts: dict[str, int] = {}
        records: list[ModuleQueryRecord] = []
        deadline = time.monotonic() + self._deadline_seconds
        for source in self._books:
            outcome = source.search(
                account_id, plan.book_query, limit=plan.book_candidate_limit, deadline=deadline
            )
            candidates.extend(outcome.candidates)
            source_counts[outcome.record.source] = len(outcome.candidates)
            records.append(outcome.record)
        if not self._books:
            records.append(
                ModuleQueryRecord(
                    source="book_catalog",
                    query=plan.book_query,
                    status=ModuleQueryStatus.SKIPPED,
                    detail="本轮没有装配图书书目来源，未发送任何书目请求。",
                )
            )
        return BookSearchOutcomes(
            candidates=candidates, source_counts=source_counts, records=records
        )

    def _search_videos(
        self,
        account_id: str,
        plan: ResourcesQueryPlan,
        *,
        stop_event: threading.Event | None,
    ) -> VideoVerifyOutcome:
        """先发现公开的哔哩哔哩直达页，再逐条核对真实元数据。"""
        if self._discoverer is None:
            return VideoVerifyOutcome(
                records=[
                    ModuleQueryRecord(
                        source="tavily",
                        query=plan.video_query,
                        status=ModuleQueryStatus.SKIPPED,
                        detail="公网搜索服务未装配，本轮没有发送任何发现请求。",
                    )
                ]
            )
        discovered = self._discoverer.discover(
            account_id,
            plan.video_query,
            limit=plan.video_candidate_limit,
            stop_event=stop_event,
            deadline=time.monotonic() + self._deadline_seconds,
        )
        records = [discovered.record]
        if not discovered.direct_pages:
            return VideoVerifyOutcome(records=records)
        if self._verifier is None:
            records.append(
                ModuleQueryRecord(
                    source="bilibili",
                    query=plan.video_query,
                    status=ModuleQueryStatus.SKIPPED,
                    detail="视频核对客户端未装配，发现的直达页未核对。",
                )
            )
            return VideoVerifyOutcome(records=records)
        verified = self._verifier.verify(
            discovered.direct_pages,
            account_id=account_id,
            deadline=time.monotonic() + self._verify_deadline_seconds,
        )
        return VideoVerifyOutcome(
            candidates=verified.candidates,
            records=[*records, *verified.records],
            rejected=verified.rejected,
        )

    # -- 恢复与等待 ------------------------------------------------------

    def _pending_wait(
        self, repo: ConversationRepository, account_id: str, conversation_id: str
    ) -> ModuleWaitState | None:
        """读取本会话最近一次尚未被后续结果取代的澄清等待状态。"""
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            projection = message.learning_resources
            if not isinstance(projection, dict):
                continue
            try:
                stored = LearningResourcesProjection.model_validate(projection)
            except ValueError:
                continue
            if stored.pending is not None:
                return stored.pending
            if stored.status in {ResourcesStatus.SUCCESS, ResourcesStatus.EMPTY}:
                # 更晚的完成结果已经取代等待状态：不再恢复。
                return None
        return None

    def _prior_user_messages(
        self,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
    ) -> list[str]:
        """已确认的会话前文（最近若干条用户消息），只用于补足学习层次。"""
        prior: list[str] = []
        for message in repo.list_messages(account_id, conversation_id):
            if message.message_id == user_message_id:
                break
            if message.role.value == "user" and message.content.strip():
                prior.append(message.content)
        return prior[-6:]

    # -- 落库 ------------------------------------------------------------

    def _persist_clarification(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: ResourcesTermAnalysis,
    ) -> ResourcesRunOutcome:
        clarification = analysis.clarification
        question = clarification.question if clarification is not None else ""
        now = datetime.now(UTC)
        projection = LearningResourcesProjection(
            status=ResourcesStatus.CLARIFICATION,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            goal=analysis.goal,
            level_basis=analysis.level_basis,
            final_query=analysis.final_query,
            pending=ModuleWaitState(
                module_id=RESOURCES_MODULE_ID,
                kind=WAIT_KIND_CLARIFICATION,
                question=question,
                origin_message_id=assistant_message_id,
                context=pending_payload(
                    analysis, missing=clarification.missing if clarification else "level"
                ),
                created_at=now,
            ),
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=render_clarification_content(analysis) or question,
            now=now,
        )
        return ResourcesRunOutcome(
            status=ResourcesStatus.CLARIFICATION,
            wait_reason=WAIT_REASON_CLARIFICATION,
        )

    def _persist_empty(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: ResourcesTermAnalysis,
        plan: ResourcesQueryPlan,
        queries: Sequence[ModuleQueryRecord],
        notes: Sequence[str],
        mismatch: bool = False,
    ) -> ResourcesRunOutcome:
        now = datetime.now(UTC)
        projection = LearningResourcesProjection(
            status=ResourcesStatus.EMPTY,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            goal=analysis.goal,
            level_label=level_label(analysis.level),
            level_basis=analysis.level_basis,
            queries=list(queries),
            final_query=plan.book_query,
            evidence_notes=list(notes),
            searched_at=now,
        )
        content = (
            render_mismatch_content(analysis, plan, list(notes))
            if mismatch
            else render_empty_content(analysis, plan, list(notes))
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=content,
            now=now,
        )
        return ResourcesRunOutcome(status=ResourcesStatus.EMPTY, queries=list(queries))

    def _persist_result(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: ResourcesTermAnalysis,
        plan: ResourcesQueryPlan,
        ranked: RankOutcome,
        queries: Sequence[ModuleQueryRecord],
    ) -> ResourcesRunOutcome:
        now = datetime.now(UTC)
        projection = LearningResourcesProjection(
            status=ResourcesStatus.SUCCESS,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            goal=analysis.goal,
            level_label=level_label(analysis.level),
            level_basis=analysis.level_basis,
            queries=list(queries),
            final_query=plan.book_query,
            items=list(ranked.items),
            requested_books=plan.target_books,
            requested_videos=plan.target_videos,
            evidence_notes=list(ranked.notes),
            searched_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=render_result_content(analysis, plan, projection),
            now=now,
        )
        return ResourcesRunOutcome(status=ResourcesStatus.SUCCESS, queries=list(queries))

    def _persist_stopped(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: ResourcesTermAnalysis,
        queries: Sequence[ModuleQueryRecord] = (),
    ) -> ResourcesRunOutcome:
        now = datetime.now(UTC)
        projection = LearningResourcesProjection(
            status=ResourcesStatus.STOPPED,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            goal=analysis.goal,
            level_label=level_label(analysis.level),
            level_basis=analysis.level_basis,
            queries=list(queries),
            final_query=analysis.final_query,
            evidence_notes=["用户停止了本轮检索，未生成的步骤不会补做。"],
            searched_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            projection=projection,
            content=render_stopped_content(analysis, None),
            now=now,
        )
        return ResourcesRunOutcome(status=ResourcesStatus.STOPPED, queries=list(queries))

    def _fail(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        node: str,
        analysis: ResourcesTermAnalysis,
        plan: ResourcesQueryPlan,
        record: ModuleQueryRecord | None = None,
        queries: Sequence[ModuleQueryRecord] = (),
        code: str | None = None,
        message: str | None = None,
        retryable: bool = True,
    ) -> None:
        """写回真实失败状态（查询词与分类），再抛出以便父图标注节点位置。"""
        error_code = (
            code
            or (record.error_code if record is not None else None)
            or "resources_search_failed"
        )
        error_message = message or (
            record.error_message
            if record is not None and record.error_message
            else "学习资料检索失败，请稍后重试。"
        )
        retry = record.retryable if record is not None else retryable
        now = datetime.now(UTC)
        projection = LearningResourcesProjection(
            status=ResourcesStatus.ERROR,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            goal=analysis.goal,
            level_label=level_label(analysis.level),
            level_basis=analysis.level_basis,
            queries=list(queries) or ([record] if record is not None else []),
            final_query=plan.book_query,
            searched_at=now,
            error_code=error_code,
            error_message=error_message,
            retryable=retry,
        )
        repo.update_message_learning_resources(
            account_id, assistant_message_id, projection.model_dump(mode="json"), now
        )
        raise ResourcesModuleError(node, error_code, error_message, retryable=retry)

    # -- 内部工具 --------------------------------------------------------

    def _stopped(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: ResourcesTermAnalysis,
        stop_event: threading.Event | None,
        queries: Sequence[ModuleQueryRecord] = (),
    ) -> ResourcesRunOutcome | None:
        """在节点边界检查停止请求：命中即收敛为 stopped（返回结果，不抛异常）。"""
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
        projection: LearningResourcesProjection,
        content: str,
        now: datetime,
    ) -> None:
        # 正文只能在 streaming 期间写入（流式增量接口的守卫），因此先写正文
        # 再收敛终态；终态与资料投影在同一事务内提交（finalize_message）。
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
            started=time.monotonic(),
            now=now,
            learning_resources=projection.model_dump(mode="json"),
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


def _hard_failure(
    books: BookSearchOutcomes, videos: VideoVerifyOutcome
) -> tuple[str, ModuleQueryRecord] | None:
    """两条检索都没有可用答复、且至少一处硬失败时返回（失败节点，第一条硬失败记录）。

    「可用答复」指某条来源真的跑完并如实给出了结果（成功或确实没有）。只要存在
    这样一条记录，整轮就不判失败——失败的那条只作为证据缺口呈现，避免把「部分
    来源缺数据」说成整轮失败。
    """
    if books.candidates or videos.candidates:
        return None
    records = [*books.records, *videos.records]
    if not records or any(
        record.status in {ModuleQueryStatus.SUCCESS, ModuleQueryStatus.EMPTY}
        for record in records
    ):
        return None
    for record in books.records:
        if record.status in _HARD_FAILURES:
            return NODE_SEARCH_BOOKS, record
    for record in videos.records:
        if record.status in _HARD_FAILURES:
            return NODE_SEARCH_VIDEOS, record
    return None


def _empty_notes(books: BookSearchOutcomes, videos: VideoVerifyOutcome) -> list[str]:
    notes: list[str] = []
    if not books.candidates:
        notes.append("图书书目来源没有返回与本主题相符的书目。")
    if not videos.candidates:
        notes.append("没有可以核对的哔哩哔哩视频直达页（或核对全部未通过）。")
    notes.extend(
        f"{record.source} 本轮状态：{record.status.value}。"
        + (
            f"（{record.error_message}）"
            if record.error_message
            else (f"（{record.detail}）" if record.detail else "")
        )
        for record in [*books.records, *videos.records]
        if record.status is not ModuleQueryStatus.SUCCESS
    )
    return notes
