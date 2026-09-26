"""GitHub 项目推荐编排服务（由日常父图在显式派发时调用）。

节点顺序 ``github.parse → github.search → github.inspect → github.rank →
github.present``。每一步都只写真实发生的事：实际查询词、真正取到的 API 元数据、
README 自述、许可文件与**实际读取到的实现文件**、以及如实的限流与失败分类。

三条核心约束（``docs/v2/workflows.md`` 第 7 节）：

1. 解析保留完整 idea 的核心场景与必要功能，优先找整体相似的项目；整体项目不足
   时才用要点查询补组件候选，并逐条记下它覆盖了哪一部分；
2. README 只是项目自述——没有读取实现文件就不对内部架构作断言；没有读到许可
   文件就不声称代码可自由复用；
3. 上游限流、文件缺失或匹配不足时展示实际结果与局限，并保留可重试状态。
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
from bridges.contracts.workflows import RunContextEnvelope
from bridges.github.contracts import (
    GithubContextSource,
    GithubEvidenceKind,
    GithubIdeaAnalysis,
    GithubProjectsProjection,
    GithubProjectStatus,
    GithubRateLimitState,
    GithubRecommendation,
    GithubRejectedRepository,
    GithubRepositoryCandidate,
)
from bridges.github.inspecting import GithubRepositoryReader, InspectionOutcome
from bridges.github.parsing import parse_github_request, pending_payload
from bridges.github.presenting import (
    GithubInsightGenerator,
    InsightOutcome,
    render_clarification_content,
    render_empty_content,
    render_result_content,
    render_stopped_content,
)
from bridges.github.ranking import (
    RankOutcome,
    identity_relevance,
    rank_candidates,
)
from bridges.github.searching import (
    MIN_WHOLE_RESULTS,
    GithubSearchPort,
    plan_queries,
)

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository

NODE_PARSE = "github.parse"
NODE_SEARCH = "github.search"
NODE_INSPECT = "github.inspect"
NODE_RANK = "github.rank"
NODE_PRESENT = "github.present"

#: 模块节点中文标签（进度事件与失败定位共用）。
GITHUB_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "理解项目想法",
    NODE_SEARCH: "检索公开仓库",
    NODE_INSPECT: "核对仓库证据",
    NODE_RANK: "按功能匹配排序",
    NODE_PRESENT: "整理推荐结果",
}

#: 显式模块标识与等待原因（父图与前端都依赖）。
GITHUB_MODULE_ID = "github"
WAIT_KIND_CLARIFICATION = "clarification"
WAIT_REASON_CLARIFICATION = "github_clarification"

#: 各阶段的墙钟预算：检索（允许上游两次查询与收尾）与证据读取。
SEARCH_DEADLINE_SECONDS = 20.0
INSPECT_DEADLINE_SECONDS = 25.0

#: 记为失败的查询状态（检索成功的空结果不算失败，它有自己的终态）。
FAILED_QUERY_STATUSES: frozenset[ModuleQueryStatus] = frozenset(
    {
        ModuleQueryStatus.ERROR,
        ModuleQueryStatus.TIMEOUT,
        ModuleQueryStatus.CANCELLED,
        ModuleQueryStatus.RATE_LIMITED,
    }
)

#: 前文回看的用户消息条数上限（够用即止，不把整段历史塞进解析）。
PRIOR_MESSAGES_LOOKBACK = 8


class GithubModuleError(Exception):
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
class GithubRunOutcome:
    """一轮 GitHub 项目推荐的收敛结果（父图据此写等待原因与判断终态）。"""

    status: GithubProjectStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)


@dataclass(frozen=True)
class _SearchAttempts:
    """有界检索的合并结果：候选取自哪条查询，全部调用都留下记录。"""

    candidates: list[GithubRepositoryCandidate]
    records: list[ModuleQueryRecord]


class GithubProjectsService:
    """GitHub 项目推荐编排服务。"""

    def __init__(
        self,
        *,
        search: GithubSearchPort,
        reader: GithubRepositoryReader,
        insights: GithubInsightGenerator | None = None,
        search_deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        inspect_deadline_seconds: float = INSPECT_DEADLINE_SECONDS,
    ) -> None:
        self._search = search
        self._reader = reader
        self._insights = insights
        self._search_deadline_seconds = search_deadline_seconds
        self._inspect_deadline_seconds = inspect_deadline_seconds

    # -- 对外入口 --------------------------------------------------------

    def run(
        self,
        *,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        run_context: RunContextEnvelope | None = None,
        run_model_id: str | None = None,
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
    ) -> GithubRunOutcome:
        """执行一轮 GitHub 项目推荐；终态全部写回同一条助手消息。"""
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise GithubModuleError(
                NODE_PARSE,
                "message_not_found",
                "消息不存在或没有访问权限。",
                retryable=False,
            )
        waiting = self._pending_wait(repo, account_id, conversation_id)
        prior = self._prior_context(repo, account_id, conversation_id, user_message_id)
        run = _Run(emit_node)
        analysis = run.node(
            NODE_PARSE,
            lambda: parse_github_request(
                user_message.content,
                prior_context=prior,
                pending=waiting,
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

        inspection = run.node(
            NODE_INSPECT,
            lambda: self._inspect(account_id, attempts, stop_event=stop_event),
        )
        inspected = [item.full_name for item in inspection.evidence]
        remaining = [
            candidate
            for candidate in attempts.candidates
            if candidate.full_name not in set(inspected)
        ]
        stopped = self._stopped(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            analysis=analysis,
            queries=tuple(attempts.records) + tuple(inspection.records),
            stop_event=stop_event,
        )
        if stopped is not None:
            return stopped

        ranked = run.node(
            NODE_RANK,
            lambda: rank_candidates(
                analysis, inspection.evidence, uninspected=remaining
            ),
        )
        insights = run.node(
            NODE_PRESENT,
            lambda: self._generate_insights(
                ranked, run_context=run_context, run_model_id=run_model_id
            ),
        )
        projection = _projection(
            analysis=analysis,
            records=list(attempts.records) + list(inspection.records),
            ranked=ranked,
            rate_limited=inspection.rate_limited or _has_rate_limit(attempts.records),
            insights=insights,
            insights_note=insights.note,
        )
        records = list(projection.queries)
        if projection.status is GithubProjectStatus.ERROR:
            self._persist_failure(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                projection=projection,
            )
            raise GithubModuleError(
                NODE_SEARCH,
                projection.error_code or "github_search_failed",
                projection.error_message or "GitHub 检索失败，请稍后重试。",
                retryable=projection.retryable,
            )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            insights=insights,
            content=(
                render_result_content(projection)
                if projection.recommendations
                else render_empty_content(projection)
            ),
        )
        return GithubRunOutcome(
            status=projection.status,
            wait_reason=None,
            queries=records,
        )

    # -- 检索 ------------------------------------------------------------

    def _search_candidates(
        self,
        account_id: str,
        analysis: GithubIdeaAnalysis,
        *,
        stop_event: threading.Event | None,
    ) -> _SearchAttempts:
        """有界检索：先整体查询，召回不足时逐条用要点查询补组件候选。"""
        candidates: list[GithubRepositoryCandidate] = []
        seen: set[str] = set()
        records: list[ModuleQueryRecord] = []
        deadline = time.monotonic() + self._search_deadline_seconds
        for index, query in enumerate(plan_queries(analysis)):
            if index > 0 and len(candidates) >= MIN_WHOLE_RESULTS:
                break
            outcome = self._search.search_repositories(
                account_id,
                query=query,
                reason="GitHub 项目推荐：只发送最小公开查询词",
                stop_event=stop_event,
                deadline=deadline,
            )
            records.append(outcome.record)
            for candidate in outcome.candidates:
                if candidate.full_name in seen:
                    continue
                seen.add(candidate.full_name)
                candidates.append(candidate)
            if not outcome.record.retryable and not outcome.candidates:
                break
            if outcome.record.status in FAILED_QUERY_STATUSES and outcome.record.retryable:
                break
        return _SearchAttempts(
            candidates=_by_identity_relevance(analysis, candidates), records=records
        )

    # -- 证据读取 --------------------------------------------------------

    def _inspect(
        self,
        account_id: str,
        attempts: _SearchAttempts,
        *,
        stop_event: threading.Event | None,
    ) -> InspectionOutcome:
        if not attempts.candidates:
            return InspectionOutcome()
        deadline = time.monotonic() + self._inspect_deadline_seconds
        return self._reader.inspect_candidates(
            account_id,
            attempts.candidates,
            stop_event=stop_event,
            deadline=deadline,
        )

    # -- 借鉴角度（可选，严格门控） --------------------------------------

    def _generate_insights(
        self,
        ranked: RankOutcome,
        *,
        run_context: RunContextEnvelope | None,
        run_model_id: str | None,
    ) -> InsightOutcome:
        if self._insights is None or not ranked.recommendations:
            return InsightOutcome()
        if run_context is None:
            return InsightOutcome(
                note="借鉴角度未生成（本轮没有可用的运行上下文），只给证据本身。"
            )
        return self._insights.generate(
            run_context, ranked.recommendations, model_id=run_model_id
        )

    # -- 恢复与等待 ------------------------------------------------------

    def _pending_wait(
        self, repo: ConversationRepository, account_id: str, conversation_id: str
    ) -> ModuleWaitState | None:
        """读取本会话最近一次尚未被后续结果取代的澄清等待状态。"""
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            stored = message.github_projects
            if not isinstance(stored, dict):
                continue
            try:
                projection = GithubProjectsProjection.model_validate(stored)
            except ValueError:
                continue
            if projection.pending is not None:
                return projection.pending
            if projection.status in {
                GithubProjectStatus.SUCCESS,
                GithubProjectStatus.METADATA_ONLY,
                GithubProjectStatus.EMPTY,
            }:
                return None
        return None

    def _prior_context(
        self,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
    ) -> list[GithubContextSource]:
        """已确认的会话前文里的可追溯原词（供「找实现它的项目」指向）。

        只看**模块投影**里的原词，按时间顺序合并：上一轮论文搜索的原始术语，
        以及上一轮 GitHub 请求的场景。每条都带回承载它的助手消息 ID，用户
        看到的「前文依据」因此能追溯到具体那条会话记录；普通聊天消息不作为
        依据，避免把无关的一句话当成检索主题。
        """
        anchors: list[GithubContextSource] = []
        for message in repo.list_messages(account_id, conversation_id):
            if message.message_id == user_message_id:
                break
            anchors.extend(_paper_anchors(message))
            anchors.extend(_github_anchors(message))
        return anchors[-PRIOR_MESSAGES_LOOKBACK:]

    # -- 落库 ------------------------------------------------------------

    def _persist_clarification(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: GithubIdeaAnalysis,
    ) -> GithubRunOutcome:
        clarification = analysis.clarification
        assert clarification is not None  # 调用点已判定
        now = datetime.now(UTC)
        projection = GithubProjectsProjection(
            status=GithubProjectStatus.CLARIFICATION,
            scenario=analysis.scenario,
            original_request=analysis.original_request,
            features=list(analysis.features),
            tech_terms=list(analysis.tech_terms),
            whole_idea=analysis.whole_idea,
            component_terms=list(analysis.component_terms),
            context_source=analysis.context_source,
            rate_limit=GithubRateLimitState(),
            pending=ModuleWaitState(
                module_id=GITHUB_MODULE_ID,
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
            insights=InsightOutcome(),
            content=render_clarification_content(projection),
        )
        return GithubRunOutcome(
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
        analysis: GithubIdeaAnalysis,
        queries: Sequence[ModuleQueryRecord],
    ) -> GithubRunOutcome:
        now = datetime.now(UTC)
        projection = GithubProjectsProjection(
            status=GithubProjectStatus.STOPPED,
            scenario=analysis.scenario,
            original_request=analysis.original_request,
            features=list(analysis.features),
            tech_terms=list(analysis.tech_terms),
            whole_idea=analysis.whole_idea,
            component_terms=list(analysis.component_terms),
            context_source=analysis.context_source,
            queries=list(queries),
            rate_limit=_rate_limit_state(queries, limited=False),
            evidence_boundary=["你已停止本轮推荐，未继续读取仓库证据。"],
            completed_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            projection=projection,
            insights=InsightOutcome(),
            content=render_stopped_content(projection),
        )
        return GithubRunOutcome(
            status=GithubProjectStatus.STOPPED, wait_reason=None, queries=list(queries)
        )

    def _persist_failure(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        projection: GithubProjectsProjection,
    ) -> None:
        """失败先写回同一条消息（失败分类与查询词都留在投影里），再交给父图收敛。"""
        now = datetime.now(UTC)
        repo.update_message_github_projects(
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
        analysis: GithubIdeaAnalysis,
        queries: Sequence[ModuleQueryRecord],
        stop_event: threading.Event | None,
    ) -> GithubRunOutcome | None:
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
        projection: GithubProjectsProjection,
        insights: InsightOutcome,
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
            run_lock_id=insights.lock.lock_id if insights.lock is not None else None,
            lock=insights.lock,
            started=time.monotonic(),
            now=now,
            github_projects=projection.model_dump(mode="json"),
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
    analysis: GithubIdeaAnalysis,
    records: Sequence[ModuleQueryRecord],
    ranked: RankOutcome,
    rate_limited: bool,
    insights: InsightOutcome,
    insights_note: str | None,
) -> GithubProjectsProjection:
    error_record = _first_error(records)
    recommendations = [
        item.model_copy(update={"insight_zh": insights.insights.get(item.full_name)})
        for item in ranked.recommendations
    ]
    if recommendations:
        # 全部推荐都只到元数据一级：如实降级为「只取得元数据」，并保留可重试。
        metadata_only = all(
            GithubEvidenceKind.README not in item.evidence_kinds
            and GithubEvidenceKind.IMPLEMENTATION not in item.evidence_kinds
            for item in recommendations
        )
        status = (
            GithubProjectStatus.METADATA_ONLY if metadata_only else GithubProjectStatus.SUCCESS
        )
    elif not records or _only_failures(records):
        status = GithubProjectStatus.ERROR
    else:
        status = GithubProjectStatus.EMPTY
    boundary = _evidence_boundary(
        analysis=analysis,
        recommendations=recommendations,
        rejected=ranked.rejected,
        rate_limited=rate_limited or status is GithubProjectStatus.METADATA_ONLY,
        insights_note=insights_note,
        has_insights=bool(insights.insights),
    )
    return GithubProjectsProjection(
        status=status,
        scenario=analysis.scenario,
        original_request=analysis.original_request,
        features=list(analysis.features),
        tech_terms=list(analysis.tech_terms),
        whole_idea=analysis.whole_idea,
        component_terms=list(analysis.component_terms),
        context_source=analysis.context_source,
        queries=list(records),
        recommendations=recommendations,
        rejected=list(ranked.rejected),
        rate_limit=_rate_limit_state(
            records,
            limited=rate_limited or bool(status is GithubProjectStatus.METADATA_ONLY),
        ),
        evidence_boundary=boundary,
        empty_reason=(
            _failure_reason(error_record)
            if status is GithubProjectStatus.ERROR and error_record is not None
            else (_empty_reason(ranked) if status is GithubProjectStatus.EMPTY else None)
        ),
        retryable=bool(error_record and error_record.retryable)
        or rate_limited
        or status is GithubProjectStatus.METADATA_ONLY,
        error_code=error_record.error_code if error_record is not None else None,
        error_message=error_record.error_message if error_record is not None else None,
    )


def _failure_reason(record: ModuleQueryRecord) -> str:
    """失败时正文要写清「哪一步没成、上游怎么说的、能不能重试」。"""
    message = record.error_message or "上游没有给出原因。"
    code = f"（错误码：{record.error_code}）" if record.error_code else ""
    tail = "稍后可以重试。" if record.retryable else "本轮不重试。"
    return f"本轮检索未完成：{message}{code}{tail}"


def _empty_reason(ranked: RankOutcome) -> str:
    if ranked.rejected:
        return (
            "本轮没有取得能对上你 idea 要点的仓库证据，因此没有推荐；"
            "未纳入的候选与理由逐条列在下面。"
        )
    return (
        "本轮检索没有返回可用的公开仓库，我不会用记忆补造条目。"
        "可以补充功能描述或换个说法后重试。"
    )


def _evidence_boundary(
    *,
    analysis: GithubIdeaAnalysis,
    recommendations: Sequence[GithubRecommendation],
    rejected: Sequence[GithubRejectedRepository],
    rate_limited: bool,
    insights_note: str | None,
    has_insights: bool,
) -> list[str]:
    notes = [
        "只纳入真实取得证据的公开仓库：API 元数据来自 GitHub 接口，"
        "README 是项目自述，实现文件证据来自实际读取到的路径与内容。",
        "README 只说明项目自称的能力；没有读取实现文件时不对内部架构作断言，"
        "没有读到许可文件时不声称代码可自由复用。",
    ]
    if analysis.context_source is not None:
        notes.append(
            f"本轮 idea 取自{analysis.context_source.label}的原始词"
            f"「{analysis.context_source.phrase}」，检索词与它逐字一致。"
        )
    if not analysis.whole_idea:
        notes.append("你本轮要的是组件，推荐一律按组件项目呈现，不代表完整产品。")
    if rejected:
        notes.append(f"另有 {len(rejected)} 个候选没有纳入推荐，理由已逐条列出。")
    if rate_limited:
        notes.append(
            "本轮撞上 GitHub 接口额度限制，缺失的 README／文件证据已在各仓库下如实标出；"
            "稍后重试可补齐。"
        )
    if not recommendations:
        notes.append("本轮没有可展示的推荐，正文只保留实际查询词与真实原因。")
    if insights_note:
        notes.append(insights_note)
    if has_insights:
        notes.append(
            "「借鉴角度」是模型在下列证据范围内的归纳，不是仓库原文；"
            "判定依据以功能匹配与已读文件为准。"
        )
    notes.append("star 数只作辅助参考，不作为功能匹配或代码质量的依据。")
    return notes


def _rate_limit_state(
    records: Sequence[ModuleQueryRecord], *, limited: bool
) -> GithubRateLimitState:
    if not limited:
        return GithubRateLimitState(note="本轮未撞上 GitHub 接口额度限制。")
    for record in records:
        if record.status is ModuleQueryStatus.RATE_LIMITED:
            return GithubRateLimitState(
                limited=True,
                note=record.error_message or "GitHub 接口额度已用尽，稍后可重试。",
            )
    return GithubRateLimitState(limited=True, note="本轮撞上 GitHub 接口额度限制，稍后可重试。")


def _first_error(records: Sequence[ModuleQueryRecord]) -> ModuleQueryRecord | None:
    for record in records:
        if record.status in FAILED_QUERY_STATUSES:
            return record
    return None


def _only_failures(records: Sequence[ModuleQueryRecord]) -> bool:
    return bool(records) and all(
        record.status in FAILED_QUERY_STATUSES for record in records
    )


def _has_rate_limit(records: Sequence[ModuleQueryRecord]) -> bool:
    return any(record.status is ModuleQueryStatus.RATE_LIMITED for record in records)


def _paper_anchors(message: object) -> list[GithubContextSource]:
    """上一轮论文搜索的原始术语（可追溯的前文原词，AC5 指向对象之一）。"""
    stored = getattr(message, "paper_search", None)
    if not isinstance(stored, dict):
        return []
    phrase = str(stored.get("original_phrase") or "").strip()
    if not phrase:
        return []
    return [
        GithubContextSource(
            kind="paper_search",
            label="上一轮论文搜索",
            phrase=phrase,
            message_id=_message_id(message),
        )
    ]


def _github_anchors(message: object) -> list[GithubContextSource]:
    """上一轮 GitHub 请求的场景（同会话连续使用时的指向对象）。"""
    stored = getattr(message, "github_projects", None)
    if not isinstance(stored, dict):
        return []
    phrase = str(stored.get("scenario") or "").strip()
    if not phrase:
        return []
    return [
        GithubContextSource(
            kind="github_projects",
            label="上一轮 GitHub 项目推荐",
            phrase=phrase,
            message_id=_message_id(message),
        )
    ]


def _message_id(message: object) -> str | None:
    value = getattr(message, "message_id", None)
    return str(value) if value else None


def _by_identity_relevance(
    analysis: GithubIdeaAnalysis, candidates: list[GithubRepositoryCandidate]
) -> list[GithubRepositoryCandidate]:
    """按「名称／简介／话题与想法的词汇重合度」排序后再读证据。

    读取仓库要花上游核心额度，额度有限，所以先把身份信息里有重合的候选排前面
    （稳定排序，同分保持检索顺序）；不重合的候选仍在名单里，只是排在后面，最后
    是否推荐仍由证据决定。
    """
    return sorted(
        candidates,
        key=lambda candidate: -identity_relevance(
            analysis,
            full_name=candidate.full_name,
            description=candidate.description,
            topics=candidate.topics,
        ),
    )


__all__ = [
    "FAILED_QUERY_STATUSES",
    "GITHUB_MODULE_ID",
    "GITHUB_NODE_LABELS",
    "GithubModuleError",
    "GithubProjectsService",
    "GithubRunOutcome",
    "NODE_INSPECT",
    "NODE_PARSE",
    "NODE_PRESENT",
    "NODE_RANK",
    "NODE_SEARCH",
    "WAIT_REASON_CLARIFICATION",
]
