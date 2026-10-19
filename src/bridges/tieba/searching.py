"""``tieba.search``：最小公开查询词、允许搜索服务的窄接口与归属判定。

查询词只用原始名词与固定来源拼装；主查询与备用查询均限定贴吧帖子域名。
外部调用只经允许的搜索服务，每次调用都留下统一查询记录。

归属判定只认「真的读到帖子页面、页面自身声明该吧」这一种确认；搜索摘要只
用于**排除**他吧同名帖（摘要可能混入侧栏推荐，实测同一链接在不同查询下会
给出不同吧名，不能作为确认依据）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.tieba.contracts import (
    TiebaQueryRound,
    TiebaQuestionAnalysis,
    TiebaRejectedCandidate,
    TiebaSearchHit,
    TiebaSearchScope,
)
from bridges.tieba.lexicon import (
    MAX_QUERY_TERMS,
    TARGET_FORUM_NAME,
    TARGET_SCHOOL_ALIASES,
    TIEBA_HOST,
    relaxed_topic_terms,
)
from bridges.tieba.search_merge import merge_search_hit, with_discovery
from bridges.web_search.scope import query_scopes, url_in_scopes

#: 候选帖链接形态（含移动端与桌面端主机）。
_THREAD_URL = re.compile(
    r"^https?://(?:(?:c|www)\.)?tieba\.baidu\.com/p/(?P<tid>\d+)", re.IGNORECASE
)

#: 「吧名 + 关注数 + 贴子数」＝页面自身吧头形态；出现他吧吧头即排除。
_FORUM_HEADER = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9]{1,20}吧)[\s・.]{0,3}关注\s*[\d.]+\s*w?\s*贴子"
)

#: 「标题就是一个吧名」的形态：整条标题只有吧名本身。
_FORUM_ONLY_TITLE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9·]{1,12}吧$")

#: 标题两侧常见的装饰字符（书名号、括号、破折号与空白）。
_TITLE_EDGE = " \t【】[]（）()《》<>\u2014-"

#: 帖子链接的规范形态（去掉移动端装饰参数，便于用户点击与去重）。
CANONICAL_THREAD_URL = "https://tieba.baidu.com/p/{thread_id}"

#: 归属剔除原因（稳定文案，测试与前端都依赖）。
REJECT_OTHER_FORUM_HEADER = "搜索摘要里的页面吧头指向其他贴吧"
REJECT_OTHER_FORUM_TITLE = "搜索结果标题就是其他贴吧的名称"
REJECT_NOT_A_THREAD = "搜索结果不是帖子页面链接"


@dataclass(frozen=True)
class HitDiagnostics:
    """候选判定的脱敏计数：原始命中数不等于可读候选数，更不等于确认帖数。"""

    raw_hits: int = 0
    not_a_thread: int = 0
    other_forum: int = 0
    duplicate: int = 0
    usable: int = 0

    def merged(self, other: HitDiagnostics) -> HitDiagnostics:
        """多轮检索的计数合并（逐轮说明由调用方另外记录）。"""
        return HitDiagnostics(
            raw_hits=self.raw_hits + other.raw_hits,
            not_a_thread=self.not_a_thread + other.not_a_thread,
            other_forum=self.other_forum + other.other_forum,
            duplicate=self.duplicate + other.duplicate,
            usable=self.usable + other.usable,
        )


@dataclass(frozen=True)
class SearchOutcome:
    """一次搜索调用的结果：统一查询记录、全部原始候选与两类判定结果。"""

    record: ModuleQueryRecord
    hits: tuple[TiebaSearchHit, ...]
    candidates: tuple[TiebaSearchHit, ...]
    rejected: tuple[TiebaRejectedCandidate, ...]
    diagnostics: HitDiagnostics = HitDiagnostics()
    prior_records: tuple[ModuleQueryRecord, ...] = ()
    scope: tuple[TiebaSearchScope, ...] = ()
    excluded_wait_ms: int = 0


class TiebaSearchPort(Protocol):
    """允许的搜索服务的窄接口（由 ``WebSearchService`` 适配）。"""

    def search_public(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome: ...


#: 查询计划里只起装饰作用的词：去重时与来源前缀一起忽略。
_DECORATIVE_QUERY_WORDS: frozenset[str] = frozenset({"贴吧", "吧", "吧里", "帖子", "贴子"})

#: 计划查询的固定来源前缀（与浏览器参数校验共用）。
QUERY_SCOPE_PREFIX = ("site:tieba.baidu.com/p/", TARGET_FORUM_NAME)

PRIMARY_QUERY_GOAL = "原词查询"
SYNONYM_QUERY_GOAL = "常用说法查询"
RELATED_QUERY_GOAL = "相关说法查询"
RELAXED_QUERY_GOAL = "备用放宽词查询"


def execution_terms(query: str) -> str:
    """返回真正用于吧内检索的最小词：去掉来源前缀与装饰词。

    这是查询计划的去重键，也是浏览器实际发送的 ``terms``。公共索引路径仍会
    发送带固定来源的完整 ``query``；两条路径共享同一组对比词。
    """
    words = query.split()
    if tuple(words[:2]) != QUERY_SCOPE_PREFIX:
        return " ".join(words)[:80]
    expressions = [
        "国庆" if word == "国庆节" else word
        for word in words[2 : MAX_QUERY_TERMS + 2]
        if word not in _DECORATIVE_QUERY_WORDS
    ]
    return " ".join(expressions)[:80]


def plan_query_rounds(analysis: TiebaQuestionAnalysis) -> tuple[TiebaQueryRound, ...]:
    """生成有界的原词、常用说法与放宽查询，并按真实执行词去重。

    「宿舍」与「宿舍 贴吧」在吧内是同一句话，只执行一次；备用放宽词只有在
    真正改变检索表达时才进入计划，零结果后的调整因此不会被装饰词骗过。
    """
    terms = list(analysis.topic_terms)[:MAX_QUERY_TERMS]
    if not terms:
        return ()
    scope = [f"site:{TIEBA_HOST}/p/", TARGET_FORUM_NAME]
    rounds: list[TiebaQueryRound] = []
    seen: set[str] = set()

    def add(goal: str, query_terms: list[str]) -> None:
        query = " ".join([*scope, *query_terms])
        expression = execution_terms(query)
        if not expression or expression in seen:
            return
        seen.add(expression)
        rounds.append(TiebaQueryRound(goal=goal, query=query, execution_terms=expression))

    add(PRIMARY_QUERY_GOAL, terms)
    for index, alternative in enumerate(_topic_alternatives(terms)):
        add(SYNONYM_QUERY_GOAL if index == 0 else RELATED_QUERY_GOAL, alternative)
    # 最后才放宽泛化词；地点、事件和明确的原始主题条件仍保留。
    relaxed_terms = [
        "国庆" if term == "国庆节" else term for term in relaxed_topic_terms(terms)
    ]
    add(RELAXED_QUERY_GOAL, [*relaxed_terms, "贴吧"])
    return tuple(rounds[:4])


def plan_queries(analysis: TiebaQuestionAnalysis) -> tuple[str, ...]:
    """生成有界的原词、常用说法与放宽查询；每条仍固定在目标贴吧。"""
    return tuple(item.query for item in plan_query_rounds(analysis))


def _topic_alternatives(terms: list[str]) -> list[list[str]]:
    """仅扩展有明确常用说法的校园话题，不改动用户的其他检索条件。"""
    variants: dict[str, tuple[str, ...]] = {
        "宿舍": ("寝室",),
        "宿舍条件": ("寝室条件", "住宿条件"),
        "寝室": ("宿舍",),
        "寝室条件": ("宿舍条件", "住宿条件"),
        "食堂": ("餐厅", "饭堂"),
        "转专业": ("转专业流程",),
        "就业": ("找工作", "毕业去向"),
    }
    result: list[list[str]] = []
    for term in terms:
        for alternative in variants.get(term, ()):
            result.append([alternative if item == term else item for item in terms])
    return result


def canonical_url(url: str) -> str:
    """把移动端链接归一到桌面端帖子链接（不同形态指向同一帖）。"""
    match = _THREAD_URL.match(url.strip())
    if match is None:
        return url.strip()
    return CANONICAL_THREAD_URL.format(thread_id=match.group("tid"))


def thread_id_of(url: str) -> str | None:
    match = _THREAD_URL.match(url.strip())
    return match.group("tid") if match is not None else None


def classify_hits(
    hits: tuple[TiebaSearchHit, ...],
) -> tuple[tuple[TiebaSearchHit, ...], tuple[TiebaRejectedCandidate, ...]]:
    """按可得证据分组：可保留的候选与被他吧证据排除的候选。"""
    kept, rejected, _ = classify_hits_with_diagnostics(hits)
    return kept, rejected


def merge_search_hits(hits: tuple[TiebaSearchHit, ...]) -> tuple[TiebaSearchHit, ...]:
    """按规范帖子链接合并候选，保留首次出现顺序。"""
    merged: dict[str, TiebaSearchHit] = {}
    order: list[str] = []
    for hit in hits:
        thread_id = thread_id_of(hit.url)
        url = canonical_url(hit.url) if thread_id is not None else hit.url.strip()
        normalized = hit.model_copy(update={"url": url, "thread_id": thread_id or hit.thread_id})
        if url in merged:
            merged[url] = merge_search_hit(merged[url], normalized)
        else:
            merged[url] = normalized
            order.append(url)
    return tuple(merged[url] for url in order)


def classify_hits_with_diagnostics(
    hits: tuple[TiebaSearchHit, ...],
) -> tuple[
    tuple[TiebaSearchHit, ...],
    tuple[TiebaRejectedCandidate, ...],
    HitDiagnostics,
]:
    """同上，并给出脱敏计数：非帖子链接、他吧证据、重复与可用候选各多少条。

    不是帖子页面的命中（用户主页、专题页、官网页面）也留下剔除记录：空态
    必须能解释「搜索结果为什么没有可用帖子」，不能静默丢弃。同一帖子的
    重复命中若已保留，则把后来的发现路径合并进去，而不是直接丢弃。
    """
    kept: list[TiebaSearchHit] = []
    kept_by_url: dict[str, TiebaSearchHit] = {}
    rejected: list[TiebaRejectedCandidate] = []
    seen: set[str] = set()
    not_a_thread = 0
    other_forum = 0
    duplicate = 0
    for hit in hits:
        thread_id = thread_id_of(hit.url)
        if thread_id is None:
            not_a_thread += 1
            rejected.append(
                TiebaRejectedCandidate(
                    url=hit.url.strip(),
                    title=hit.title,
                    evidence=REJECT_NOT_A_THREAD,
                )
            )
            continue
        url = canonical_url(hit.url)
        normalized = with_discovery(
            hit.model_copy(update={"url": url, "thread_id": thread_id})
        )
        if url in kept_by_url:
            duplicate += 1
            kept_by_url[url] = merge_search_hit(kept_by_url[url], normalized)
            continue
        if url in seen:
            duplicate += 1
            continue
        seen.add(url)
        rejection = _rejection_evidence(normalized)
        if rejection is None:
            kept.append(normalized)
            kept_by_url[url] = normalized
        else:
            other_forum += 1
            rejected.append(
                TiebaRejectedCandidate(url=url, title=normalized.title, evidence=rejection)
            )
    kept = [kept_by_url.get(hit.url, hit) for hit in kept]
    diagnostics = HitDiagnostics(
        raw_hits=len(hits),
        not_a_thread=not_a_thread,
        other_forum=other_forum,
        duplicate=duplicate,
        usable=len(kept),
    )
    return tuple(kept), tuple(rejected), diagnostics


def _rejection_evidence(hit: TiebaSearchHit) -> str | None:
    """他吧同名帖的排除依据；没有任何他吧证据时返回 None。

    只有明确指向他吧的证据才剔除：误剔除会让真实候选连同帖链一起消失，
    误保留最多多读一次页面或降级成「归属未确认」的帖链，代价更小。因此
    标题／吧头里只要出现本校写法（含简称），就不当作他吧证据。
    """
    title = hit.title.strip().strip(_TITLE_EDGE)
    if (
        _FORUM_ONLY_TITLE.match(title)
        and title != TARGET_FORUM_NAME
        and not _mentions_target(title)
    ):
        return REJECT_OTHER_FORUM_TITLE
    for match in _FORUM_HEADER.finditer(hit.snippet):
        name = match.group("name")
        if name != TARGET_FORUM_NAME and not _mentions_target(name):
            return f"{REJECT_OTHER_FORUM_HEADER}（{name}）"
    return None


def _mentions_target(text: str) -> bool:
    """文本里是否出现本校写法（摘要是拼接文本，吧名可能被前文粘住）。"""
    return any(alias in text for alias in TARGET_SCHOOL_ALIASES)


def query_record(
    *,
    query: str,
    status: ModuleQueryStatus,
    evidence_count: int,
    retrieved_at: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    retryable: bool = False,
    detail: str | None = None,
    source: str = "tavily",
) -> ModuleQueryRecord:
    """统一查询记录（沿用 V2 Issue 11 建立的证据合同）。"""
    return ModuleQueryRecord(
        source=source,
        query=query,
        status=status,
        evidence_count=evidence_count,
        retrieved_at=retrieved_at or datetime.now(UTC),
        error_code=error_code,
        error_message=error_message,
        retryable=retryable,
        detail=detail,
    )


class WebSearchServiceAdapter:
    """把允许的搜索服务（唯一通用公网提供方）适配成贴吧检索边界。

    查询词由本模块构造（只含原始名词与固定字面量），经同一服务走审计、
    缓存与预算路径；搜索服务标注的页面抓取失败不算检索失败——贴吧的帖子
    页面本来就常被访问限制挡住，正文由 ``tieba.read`` 自己按真实结果判断。
    """

    uses_per_request_budget = True

    def __init__(self, service: object, *, max_results: int = 6) -> None:
        self._service = service
        self._max_results = max_results

    def search_public(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome:
        from bridges.web_search.service import SearchPlan  # noqa: PLC0415

        plan = SearchPlan(
            should_search=True,
            query=query,
            reason=reason,
            queries=(query,),
            max_results=self._max_results,
            # 来源约束改变后，不复用旧版无域名约束的公网缓存。
            query_hash=hashlib.sha256(f"tieba-source-scope-v1:{query}".encode()).hexdigest(),
        )
        projection = self._service.search(  # type: ignore[attr-defined]
            account_id, plan, stop_event=stop_event, deadline=deadline
        )
        retrieved_at = datetime.now(UTC)
        if projection is None:
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.ERROR,
                    evidence_count=0,
                    retrieved_at=retrieved_at,
                    error_code="tieba_search_failed",
                    error_message="贴吧检索没有形成结果，请稍后重试。",
                    retryable=True,
                ),
                hits=(),
                candidates=(),
                rejected=(),
            )
        hits = tuple(
            TiebaSearchHit(
                url=result.url,
                title=result.title,
                snippet=result.snippet,
            )
            for result in projection.results
            if url_in_scopes(result.url, query_scopes(query))
        )
        candidates, rejected, diagnostics = classify_hits_with_diagnostics(hits)
        return SearchOutcome(
            record=query_record(
                query=query,
                status=_record_status(projection.status, has_hits=bool(hits)),
                evidence_count=len(hits),
                retrieved_at=projection.searched_at or retrieved_at,
                error_code=projection.error_code,
                error_message=projection.error_message,
                retryable=projection.can_retry,
                detail=_record_detail(projection.status),
            ),
            hits=hits,
            candidates=candidates,
            rejected=rejected,
            diagnostics=diagnostics,
        )


def _record_status(status: object, *, has_hits: bool) -> ModuleQueryStatus:
    """把搜索服务状态映射成统一查询记录状态（页面抓取失败不算检索失败）。"""
    name = getattr(status, "value", str(status))
    if name == "cancelled":
        return ModuleQueryStatus.CANCELLED
    if name == "empty":
        return ModuleQueryStatus.EMPTY
    if name in {
        "success",
        "partial",
        "source_conflict",
        "fetch_error",
        "evidence_insufficient",
    }:
        # 搜索服务本身返回了结果，只是结果页正文可能没抓到；页面正文由读取
        # 阶段按自己的真实结果判断，所以这里仍记为一次成功调用。
        return ModuleQueryStatus.SUCCESS if has_hits else ModuleQueryStatus.EMPTY
    return ModuleQueryStatus.ERROR


def _record_detail(status: object) -> str | None:
    name = getattr(status, "value", str(status))
    if name == "fetch_error":
        return "搜索服务无法抓取结果页面正文，只返回了标题与链接。"
    if name == "evidence_insufficient":
        return "搜索服务只返回了搜索摘要，没有可核实的页面正文。"
    if name == "source_conflict":
        return "搜索服务标注多个公开来源相互冲突。"
    if name == "partial":
        return "搜索服务只取到了部分结果页面。"
    return None
