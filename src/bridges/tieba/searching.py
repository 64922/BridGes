"""``tieba.search``：最小公开查询词、允许搜索服务的窄接口与归属判定。

查询词只用原始名词与固定字面量拼装，逐字保留用户说法；域名提示
（``tieba.baidu.com``）必须完整发送——实测把它拆成 word token 后贴吧帖子
不再被召回。外部调用只经允许的搜索服务，每次调用都留下统一查询记录。

归属判定只认「真的读到帖子页面、页面自身声明该吧」这一种确认；搜索摘要只
用于**排除**他吧同名帖（摘要可能混入侧栏推荐，实测同一链接在不同查询下会
给出不同吧名，不能作为确认依据）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.tieba.contracts import (
    TiebaQuestionAnalysis,
    TiebaRejectedCandidate,
    TiebaSearchHit,
)
from bridges.tieba.lexicon import (
    MAX_QUERY_TERMS,
    TARGET_FORUM_NAME,
    TARGET_SCHOOL_ALIASES,
    TIEBA_HOST,
)

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


@dataclass(frozen=True)
class SearchOutcome:
    """一次搜索调用的结果：统一查询记录、全部原始候选与两类判定结果。"""

    record: ModuleQueryRecord
    hits: tuple[TiebaSearchHit, ...]
    candidates: tuple[TiebaSearchHit, ...]
    rejected: tuple[TiebaRejectedCandidate, ...]


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


def plan_queries(analysis: TiebaQuestionAnalysis) -> tuple[str, ...]:
    """生成有界的查询词：先精确词，召回为零时用一条放宽词再试一次。"""
    terms = list(analysis.topic_terms)[:MAX_QUERY_TERMS]
    if not terms:
        return ()
    primary = " ".join([TIEBA_HOST, TARGET_FORUM_NAME, *terms])
    relaxed = " ".join([TARGET_FORUM_NAME, *terms, "贴吧"])
    return (primary,) if relaxed == primary else (primary, relaxed)


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
    kept: list[TiebaSearchHit] = []
    rejected: list[TiebaRejectedCandidate] = []
    seen: set[str] = set()
    for hit in hits:
        thread_id = thread_id_of(hit.url)
        if thread_id is None:
            continue
        url = canonical_url(hit.url)
        if url in seen:
            continue
        seen.add(url)
        hit = hit.model_copy(update={"url": url, "thread_id": thread_id})
        rejection = _rejection_evidence(hit)
        if rejection is None:
            kept.append(hit)
        else:
            rejected.append(
                TiebaRejectedCandidate(url=url, title=hit.title, evidence=rejection)
            )
    return tuple(kept), tuple(rejected)


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
                snippet=result.content or "",
            )
            for result in projection.results
        )
        candidates, rejected = classify_hits(hits)
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
