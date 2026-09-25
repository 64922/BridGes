"""论文子图编排：``paper.parse → plan → search → enrich → rank → present``。

本切片是**第一个接到日常父图的显式模块**（V2 Issue 11），因此它同时固化
后续模块复用的三份合同：

1. **证据合同**：每次外部调用产出 ``ModuleQueryRecord``（查询/证据/时间/错误），
   公开检索只发送最小查询词，私有上下文不进外部服务；
2. **等待合同**：缺失或歧义只问一项，提问随助手消息落库（``ModuleWaitState``），
   下一轮从该处恢复读取权威记录与实际回复，不靠内存协程跨请求存活；
3. **失败与停止合同**：查询有超时、有限重试与取消；失败保留查询词与真实分类、
   可重试；停止在节点边界生效并把状态写回同一条消息。

节点进度只对应真实开始/完成的步骤（``paper.parse`` 等），失败定位到具体节点。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeVar

from bridges.contracts.ai import ModelRunLock
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus, ModuleWaitState
from bridges.contracts.workflows import RunContextEnvelope
from bridges.paper.contracts import (
    PaperQueryPlan,
    PaperRecommendation,
    PaperSearchProjection,
    PaperSearchStatus,
    PaperTermAnalysis,
)
from bridges.paper.lexicon import AMBIGUOUS_TERMS
from bridges.paper.parsing import parse_paper_request, pending_payload
from bridges.paper.planning import MIN_TARGET_COUNT, plan_queries
from bridges.paper.presenting import (
    PaperSummaryGenerator,
    render_clarification_content,
    render_empty_content,
    render_mismatch_content,
    render_result_content,
    render_stopped_content,
)
from bridges.paper.ranking import RankOutcome, cover_original_phrase, rank_candidates
from bridges.paper.sources import (
    ArxivPaperSource,
    CandidateSearchOutcome,
    EnrichOutcome,
    MetadataEnricher,
)

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository

#: 本轮子图节点名（进度事件与失败定位使用；父图节点仍是 invoke_subgraph_or_chat）。
NODE_PARSE = "paper.parse"
NODE_PLAN = "paper.plan"
NODE_SEARCH = "paper.search"
NODE_ENRICH = "paper.enrich"
NODE_RANK = "paper.rank"
NODE_PRESENT = "paper.present"

#: 节点的用户可读中文名（父图失败信息按此标注真实失败位置）。
PAPER_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "理解论文请求",
    NODE_PLAN: "规划论文检索",
    NODE_SEARCH: "检索 arXiv",
    NODE_ENRICH: "核对论文来源",
    NODE_RANK: "筛选与排序论文",
    NODE_PRESENT: "整理论文结果",
}

#: 单轮检索的墙钟预算（秒）：超时如实失败，绝不无限等待上游。
SEARCH_DEADLINE_SECONDS = 25.0

#: 元数据补充的墙钟预算（秒）：到点即停止补充并如实标注缺口。
ENRICH_DEADLINE_SECONDS = 12.0

#: 澄清等待状态的类型标识（等待合同的一部分）。
WAIT_KIND_CLARIFICATION = "clarification"

PAPER_MODULE_ID = "paper"

#: 父图运行表的等待原因（可观测的持久化等待状态）。
WAIT_REASON_CLARIFICATION = "paper_clarification"


class PaperModuleError(Exception):
    """论文子图失败：节点位置、稳定错误码、可操作中文说明与是否可重试。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class PaperRunOutcome:
    """一轮论文模块的收敛结果（父图据此写等待原因与判断终态）。"""

    status: PaperSearchStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)


@dataclass(frozen=True)
class SearchAttempts:
    """有界检索的合并结果：采用的那次调用 + 全部调用的统一记录。

    ``plans`` 里第一条是精确词查询，扩展词让召回偏少时用第二条（只有主词）
    再查一次——两次都仍不足时按实际数量如实收敛，不凑篇数、不无限重试。
    """

    outcome: CandidateSearchOutcome
    records: list[ModuleQueryRecord]


class PaperSearchService:
    """论文模块编排服务（由日常父图在显式派发时调用）。"""

    def __init__(
        self,
        *,
        source: ArxivPaperSource,
        enricher: MetadataEnricher | None = None,
        summarizer: PaperSummaryGenerator | None = None,
        deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        enrich_deadline_seconds: float = ENRICH_DEADLINE_SECONDS,
    ) -> None:
        self._source = source
        self._enricher = enricher
        self._summarizer = summarizer
        self._deadline_seconds = deadline_seconds
        self._enrich_deadline_seconds = enrich_deadline_seconds

    def close(self) -> None:
        if self._enricher is not None:
            self._enricher.close()

    # -- 对外入口 --------------------------------------------------------

    def run(
        self,
        *,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        run_context: RunContextEnvelope,
        run_model_id: str | None,
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
    ) -> PaperRunOutcome:
        """执行一轮论文模块；终态（完成/澄清/空/失败/停止）全部写回同一消息。"""
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise PaperModuleError(
                NODE_PARSE, "message_not_found", "消息不存在或没有访问权限。", retryable=False
            )
        pending = self._pending_wait(repo, account_id, conversation_id)
        prior = self._prior_user_messages(repo, account_id, conversation_id, user_message_id)
        analysis = _Run(emit_node)
        parsed = analysis.node(
            NODE_PARSE,
            lambda: parse_paper_request(
                user_message.content, prior_context=prior, pending=pending
            ),
        )
        if parsed.clarification is not None:
            return self._persist_clarification(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=parsed,
                question=parsed.clarification.question,
            )
        plans = analysis.node(NODE_PLAN, lambda: plan_queries(parsed))
        plan = plans[0]
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=parsed,
            plan=plan,
            stop_event=stop_event,
        )
        if stopped is not None:
            return stopped
        search = analysis.node(
            NODE_SEARCH,
            lambda: self._search(account_id, plans, stop_event=stop_event),
        )
        outcome = search.outcome
        # 停止优先于其他终态：用户已请求停止时如实显示已停止（查询记录仍保留），
        # 绝不把停止写成失败或"没有结果"。
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=parsed,
            plan=plan,
            stop_event=stop_event,
            queries=search.records,
        )
        if stopped is not None:
            return stopped
        status = outcome.record.status
        if status is ModuleQueryStatus.CANCELLED:
            return self._persist_stopped(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=parsed,
                plan=plan,
                queries=search.records,
            )
        if status in {
            ModuleQueryStatus.ERROR,
            ModuleQueryStatus.TIMEOUT,
            ModuleQueryStatus.RATE_LIMITED,
        }:
            self._fail(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                node=NODE_SEARCH,
                analysis=parsed,
                plan=plan,
                record=outcome.record,
                queries=search.records,
            )
        if not outcome.candidates:
            return self._persist_empty(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=parsed,
                plan=plan,
                queries=search.records,
            )

        enrich = analysis.node(
            NODE_ENRICH,
            lambda: self._enrich(
                outcome,
                account_id=account_id,
                deadline_seconds=self._enrich_deadline_seconds,
            ),
        )
        ranked = analysis.node(
            NODE_RANK,
            lambda: rank_candidates(parsed, plan, outcome.candidates, enrich.metadata),
        )
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=parsed,
            plan=plan,
            stop_event=stop_event,
            queries=[*search.records, *enrich.records],
        )
        if stopped is not None:
            return stopped
        if ranked.topic_mismatch:
            # 主题不匹配：停止推荐并请用户澄清，不凑满篇数（等待合同同澄清）。
            return self._persist_clarification(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=parsed,
                question=render_mismatch_content(parsed, plan, ranked.notes),
                plan=plan,
                queries=[*search.records, *enrich.records],
                notes=ranked.notes,
            )

        summary_map, summary_note, summary_lock = self._summarize(
            ranked.recommendations,
            outcome,
            run_context=run_context,
            run_model_id=run_model_id,
        )
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=parsed,
            plan=plan,
            stop_event=stop_event,
            queries=[*search.records, *enrich.records],
        )
        if stopped is not None:
            return stopped
        return self._persist_result(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=parsed,
            plan=plan,
            outcome=outcome,
            queries=search.records,
            enrich=enrich,
            ranked=ranked,
            summary_map=summary_map,
            summary_note=summary_note,
            lock=summary_lock,
        )

    # -- 各节点的最小实现 ------------------------------------------------

    def _search(
        self,
        account_id: str,
        plans: Sequence[PaperQueryPlan],
        *,
        stop_event: threading.Event | None,
    ) -> SearchAttempts:
        """有界检索：先按精确词查询，候选偏少时才用主词放宽一次。

        只有第一次调用成功且候选少于目标下限时才发起第二次；失败/取消不重试
        （错误分类已经真实记录）。两次调用都留在查询记录里，采用候选更多的那次。
        """
        records: list[ModuleQueryRecord] = []
        adopted: CandidateSearchOutcome | None = None
        for index, plan in enumerate(plans):
            result = self._source.search(
                account_id,
                plan.query,
                max_results=plan.max_results,
                stop_event=stop_event,
                deadline=time.monotonic() + self._deadline_seconds,
            )
            records.append(result.record)
            if adopted is None or len(result.candidates) > len(adopted.candidates):
                adopted = result
            is_last = index + 1 == len(plans)
            if (
                is_last
                or result.record.status is not ModuleQueryStatus.SUCCESS
                or len(result.candidates) >= MIN_TARGET_COUNT
            ):
                break
        assert adopted is not None  # plans 至少一条（paper.plan 保证）
        return SearchAttempts(outcome=adopted, records=records)

    def _enrich(
        self,
        outcome: CandidateSearchOutcome,
        *,
        account_id: str,
        deadline_seconds: float,
    ) -> EnrichOutcome:
        if self._enricher is None:
            return EnrichOutcome()
        return self._enricher.enrich(
            outcome.candidates,
            account_id=account_id,
            need_publication_info=True,
            deadline=time.monotonic() + deadline_seconds,
        )

    def _summarize(
        self,
        recommendations: list[PaperRecommendation],
        outcome: CandidateSearchOutcome,
        *,
        run_context: RunContextEnvelope,
        run_model_id: str | None,
    ) -> tuple[dict[str, str] | None, str | None, ModelRunLock | None]:
        if self._summarizer is None:
            return None, None, None
        abstracts = {item.arxiv_id: item.abstract for item in outcome.candidates}
        result = self._summarizer.generate(
            run_context,
            recommendations,
            abstracts=abstracts,
            model_id=run_model_id,
        )
        return result.summaries or None, result.note, result.lock

    # -- 恢复与等待 ------------------------------------------------------

    def _pending_wait(
        self, repo: ConversationRepository, account_id: str, conversation_id: str
    ) -> ModuleWaitState | None:
        """读取本会话最近一次尚未被后续结果取代的澄清等待状态。"""
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            projection = message.paper_search
            if not isinstance(projection, dict):
                continue
            try:
                stored = PaperSearchProjection.model_validate(projection)
            except ValueError:
                continue
            if stored.pending is not None:
                return stored.pending
            if stored.status in {PaperSearchStatus.SUCCESS, PaperSearchStatus.EMPTY}:
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
        """已确认的会话前文（最近若干条用户消息），只用于语境消歧。"""
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
        analysis: PaperTermAnalysis,
        question: str,
        plan: PaperQueryPlan | None = None,
        queries: Sequence[ModuleQueryRecord] = (),
        notes: Sequence[str] = (),
    ) -> PaperRunOutcome:
        now = datetime.now(UTC)
        projection = PaperSearchProjection(
            status=PaperSearchStatus.CLARIFICATION,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            context_label=analysis.context_label,
            queries=list(queries),
            final_query=plan.query if plan is not None else analysis.final_query,
            evidence_notes=list(notes),
            pending=ModuleWaitState(
                module_id=PAPER_MODULE_ID,
                kind=WAIT_KIND_CLARIFICATION,
                question=question,
                origin_message_id=assistant_message_id,
                context=_clarification_payload(analysis),
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
        return PaperRunOutcome(
            status=PaperSearchStatus.CLARIFICATION,
            wait_reason=WAIT_REASON_CLARIFICATION,
        )

    def _persist_empty(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: PaperTermAnalysis,
        plan: PaperQueryPlan,
        queries: Sequence[ModuleQueryRecord],
    ) -> PaperRunOutcome:
        now = datetime.now(UTC)
        notes = ["arXiv 对本主题没有返回结果；稀疏领域可换英文术语或放宽年份后重试。"]
        projection = PaperSearchProjection(
            status=PaperSearchStatus.EMPTY,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            context_label=analysis.context_label,
            queries=list(queries),
            final_query=plan.query,
            evidence_notes=notes,
            searched_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=render_empty_content(analysis, plan, notes),
            now=now,
        )
        return PaperRunOutcome(status=PaperSearchStatus.EMPTY, queries=list(queries))

    def _persist_result(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: PaperTermAnalysis,
        plan: PaperQueryPlan,
        outcome: CandidateSearchOutcome,
        queries: Sequence[ModuleQueryRecord],
        enrich: EnrichOutcome,
        ranked: RankOutcome,
        summary_map: dict[str, str] | None,
        summary_note: str | None,
        lock: ModelRunLock | None,
    ) -> PaperRunOutcome:
        recommendations = list(ranked.recommendations)
        if not cover_original_phrase(analysis, recommendations):
            self._fail(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                node=NODE_PRESENT,
                analysis=analysis,
                plan=plan,
                queries=[*queries, *enrich.records],
                code="paper_topic_mismatch",
                message="推荐结果未覆盖你的原始术语，已停止生成。",
                retryable=False,
            )
        notes = list(ranked.notes)
        if summary_map:
            for paper in recommendations:
                text = summary_map.get(paper.arxiv_id or "")
                if text:
                    paper.summary_zh = text
        else:
            notes.append(
                summary_note
                or "未生成中文概述，本轮只依据来源返回的标题、摘要与元数据给出理由。"
            )
        now = datetime.now(UTC)
        projection = PaperSearchProjection(
            status=PaperSearchStatus.SUCCESS,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            context_label=analysis.context_label,
            queries=[*queries, *enrich.records],
            final_query=plan.query,
            papers=recommendations,
            requested_count=plan.target_count,
            evidence_notes=notes,
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
            lock=lock,
        )
        return PaperRunOutcome(
            status=PaperSearchStatus.SUCCESS,
            queries=[*queries, *enrich.records],
        )

    def _persist_stopped(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: PaperTermAnalysis,
        plan: PaperQueryPlan,
        queries: Sequence[ModuleQueryRecord] = (),
    ) -> PaperRunOutcome:
        now = datetime.now(UTC)
        projection = PaperSearchProjection(
            status=PaperSearchStatus.STOPPED,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            final_query=plan.query,
            queries=list(queries),
            evidence_notes=["用户停止了本轮检索，未生成的步骤不会补做。"],
            searched_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            projection=projection,
            content=render_stopped_content(analysis, plan),
            now=now,
        )
        return PaperRunOutcome(status=PaperSearchStatus.STOPPED, queries=list(queries))

    def _fail(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        node: str,
        analysis: PaperTermAnalysis,
        plan: PaperQueryPlan | None,
        record: ModuleQueryRecord | None = None,
        queries: Sequence[ModuleQueryRecord] = (),
        code: str | None = None,
        message: str | None = None,
        retryable: bool = True,
    ) -> None:
        """写回真实失败状态（查询词与分类），再抛出以便父图标注节点位置。"""
        error_code = (
            code or (record.error_code if record is not None else None) or "paper_search_failed"
        )
        error_message = message or (
            record.error_message
            if record is not None and record.error_message
            else "论文检索失败，请稍后重试。"
        )
        retry = record.retryable if record is not None else retryable
        now = datetime.now(UTC)
        projection = PaperSearchProjection(
            status=PaperSearchStatus.ERROR,
            original_phrase=analysis.original_phrase,
            normalized_term=analysis.normalized_term,
            expansions=list(analysis.expansions),
            confidence=analysis.confidence,
            context_label=analysis.context_label,
            final_query=plan.query if plan is not None else analysis.final_query,
            queries=list(queries) or ([record] if record is not None else []),
            searched_at=now,
            error_code=error_code,
            error_message=error_message,
            retryable=retry,
        )
        repo.update_message_paper_search(
            account_id, assistant_message_id, projection.model_dump(mode="json"), now
        )
        raise PaperModuleError(node, error_code, error_message, retryable=retry)

    # -- 内部工具 --------------------------------------------------------

    def _stopped(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: PaperTermAnalysis,
        plan: PaperQueryPlan,
        stop_event: threading.Event | None,
        queries: Sequence[ModuleQueryRecord] = (),
    ) -> PaperRunOutcome | None:
        """在节点边界检查停止请求：命中即收敛为 stopped（返回结果，不抛异常）。"""
        if stop_event is None or not stop_event.is_set():
            return None
        return self._persist_stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=analysis,
            plan=plan,
            queries=queries,
        )

    def _finalize(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        status: ChatMessageStatus,
        projection: PaperSearchProjection,
        content: str,
        now: datetime,
        lock: ModelRunLock | None = None,
    ) -> None:
        # 正文只能在 streaming 期间写入（流式增量接口的守卫），因此先写正文
        # 再收敛终态；终态与论文投影在同一事务内提交（finalize_message）。
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
            run_lock_id=lock.lock_id if lock is not None else None,
            lock=lock,
            started=time.monotonic(),
            now=now,
            paper_search=projection.model_dump(mode="json"),
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


def _clarification_payload(analysis: PaperTermAnalysis) -> dict[str, object]:
    return pending_payload(analysis, ambiguous_term=_ambiguous_key(analysis))


def _ambiguous_key(analysis: PaperTermAnalysis) -> str | None:
    """恢复澄清时按歧义术语的候选语境判定（术语键取自原始短语）。"""
    lowered = analysis.original_phrase.lower()
    for term in AMBIGUOUS_TERMS:
        if any(alias.lower() in lowered for alias in term.aliases):
            return term.term
    return None
