"""受限外部来源适配器：图书书目 + 哔哩哔哩视频发现与核对。

统一记录合同（``bridges.contracts.modules``）在每次调用后都产出
:class:`~bridges.contracts.modules.ModuleQueryRecord`：查询词、真实取得的
证据条数、取得时间与错误分类。**外部检索只发送最小查询词**——账户、会话、
画像与附件材料一律不出现在查询里。

来源分工（对应 ``docs/v2/workflows.md`` 第 4 节）：

- ``resources.search_books``：Open Library 图书书目（书目、ISBN、出版社、
  可点开的书目页）为主来源，OpenAlex 图书记录作有限补充（补上主来源没有的
  英文书目）；两者都按来源分别记账，绝不把一处的元数据标成另一处的。
- ``resources.search_videos``：先用公网搜索（Tavily，复用既有受限服务与披露
  审计）发现公开的哔哩哔哩视频直达页，再对每个直达页核对**真实可取得的**
  标题、作者、发布时间、时长与简介；核对不通过的链接一律丢弃，绝不凭搜索
  结果标题臆造视频内容。

每次外发请求都留下账户归属的脱敏披露审计（``AuditAction.LEARNING_RESOURCE_LOOKUP``，
只记来源、查询指纹、长度、状态与耗时，不记正文）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from threading import Event
from typing import Any
from urllib.parse import urlsplit

import httpx

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.observability.service import ObservabilityService
from bridges.web_search.contracts import WebSearchProjection, WebSearchStatus
from bridges.web_search.service import SearchPlan, WebSearchService

OPENLIBRARY_SOURCE = "openlibrary"
OPENALEX_SOURCE = "openalex"
#: 视频发现用的公网搜索来源（复用既有受限 web_search 服务，来源名与之一致）。
TAVILY_SOURCE = "tavily"
BILIBILI_SOURCE = "bilibili"
#: 未装配任何书目来源时的占位来源名（如实标注「本轮没有发送书目请求」）。
BOOK_CATALOG_SOURCE = "book_catalog"

OPENLIBRARY_ENDPOINT = "https://openlibrary.org/search.json"
OPENALEX_ENDPOINT = "https://api.openalex.org/works"
BILIBILI_VIEW_ENDPOINT = "https://api.bilibili.com/x/web-interface/view"

#: 书目来源的单次请求超时（秒）与逐轮候选上限（有限尝试）。
BOOK_TIMEOUT_SECONDS = 8.0
BOOK_SEARCH_LIMIT = 8

#: 视频核对：单次请求超时与逐轮核对上限（每个直达页只查一次）。
VIDEO_TIMEOUT_SECONDS = 8.0
VIDEO_VERIFY_LIMIT = 8

#: 视频发现（公网搜索）逐轮结果上限。
VIDEO_DISCOVERY_LIMIT = 8

#: 哔哩哔哩直达页的规范形式（核对通过后用 verifier 取到的 id 重新拼）。
BILIBILI_WATCH_TEMPLATE = "https://www.bilibili.com/video/{video_id}"

#: 可接受的哔哩哔哩视频路径（只认 /video/BV… 与 /video/av…，不跟随短链）。
_BILIBILI_HOSTS = frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com"})
_BILIBILI_PATH = re.compile(r"^/video/(?P<video_id>BV[0-9A-Za-z]{10}|av\d{1,12})/?$")


@dataclass(frozen=True)
class BookCandidate:
    """书目来源返回的真实图书元数据（未做任何改写）。"""

    title: str
    creators: list[str]
    year: int | None
    publisher: str | None
    isbn: str | None
    source: str
    url: str


@dataclass(frozen=True)
class BookSearchOutcome:
    """一次图书检索的结果与统一记录。"""

    query: str
    candidates: list[BookCandidate]
    record: ModuleQueryRecord


@dataclass(frozen=True)
class VideoCandidate:
    """经核对通过的一条哔哩哔哩视频（元数据全部来自公开接口）。"""

    video_id: str
    title: str
    uploader: str | None
    duration_seconds: int | None
    published_at: datetime | None
    url: str
    #: 公开简介：属于核对过、可取得的元数据（用作「已核对简介」的依据），
    #: 但不参与主题门——清单里的每一条都要凭标题就能看出与本轮主题的关系。
    description: str
    #: 公开计数（平台统计，只作弱证据；接口未给为 None）。
    view_count: int | None = None
    like_count: int | None = None


@dataclass(frozen=True)
class VideoDiscoveryOutcome:
    """一次视频发现的结果：公网搜索命中的哔哩哔哩直达页与统一记录。"""

    query: str
    direct_pages: list[str]
    record: ModuleQueryRecord


@dataclass(frozen=True)
class VideoVerifyOutcome:
    """一轮视频核对的结果、记录与逐条真实元数据。"""

    candidates: list[VideoCandidate] = field(default_factory=list)
    records: list[ModuleQueryRecord] = field(default_factory=list)
    rejected: int = 0


class OpenLibraryBookSource:
    """Open Library 图书书目：书目、ISBN 与出版社的主来源。"""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        observability: ObservabilityService | None = None,
        timeout: float = BOOK_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._observability = observability
        self._timeout = timeout

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def search(
        self,
        account_id: str,
        query: str,
        *,
        limit: int,
        deadline: float | None = None,
    ) -> BookSearchOutcome:
        params = {
            "q": query,
            "limit": str(max(1, min(limit, BOOK_SEARCH_LIMIT))),
            "fields": "key,title,author_name,first_publish_year,publisher,isbn",
        }
        started = time.monotonic()
        payload, failure = self._get_json(
            OPENLIBRARY_ENDPOINT, params, OPENLIBRARY_SOURCE, deadline
        )
        self._audit(
            account_id=account_id,
            source=OPENLIBRARY_SOURCE,
            query=query,
            failure=failure,
            started=started,
        )
        if failure is not None:
            return BookSearchOutcome(
                query=query,
                candidates=[],
                record=_failure_record(OPENLIBRARY_SOURCE, query, failure),
            )
        candidates = [
            candidate
            for candidate in (
                _openlibrary_candidate(doc) for doc in (payload or {}).get("docs") or []
            )
            if candidate is not None
        ]
        return BookSearchOutcome(
            query=query,
            candidates=candidates,
            record=ModuleQueryRecord(
                source=OPENLIBRARY_SOURCE,
                query=query,
                status=ModuleQueryStatus.SUCCESS if candidates else ModuleQueryStatus.EMPTY,
                evidence_count=len(candidates),
                retrieved_at=datetime.now(UTC),
            ),
        )

    # -- 内部实现 --------------------------------------------------------

    def _get_json(
        self, endpoint: str, params: dict[str, str], source: str, deadline: float | None
    ) -> tuple[dict[str, Any] | None, str | None]:
        return _get_json(
            self._client,
            endpoint,
            params,
            source,
            timeout=self._timeout,
            deadline=deadline,
        )

    def _audit(
        self,
        *,
        account_id: str,
        source: str,
        query: str,
        failure: str | None,
        started: float,
    ) -> None:
        log_metadata_lookup(
            self._observability,
            account_id=account_id,
            source=source,
            query=query,
            terminal=failure or "queried",
            started=started,
        )


class OpenAlexBookSource:
    """OpenAlex 图书记录：补上书目来源没有的英文书目（有限补充，失败只报缺口）。"""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        observability: ObservabilityService | None = None,
        timeout: float = BOOK_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._observability = observability
        self._timeout = timeout

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def search(
        self,
        account_id: str,
        query: str,
        *,
        limit: int,
        deadline: float | None = None,
    ) -> BookSearchOutcome:
        params = {
            "search": query,
            "filter": "type:book",
            "per-page": str(max(1, min(limit, BOOK_SEARCH_LIMIT))),
            "select": "id,title,publication_year,authorships,primary_location,ids",
        }
        started = time.monotonic()
        payload, failure = self._get_json(OPENALEX_ENDPOINT, params, OPENALEX_SOURCE, deadline)
        self._audit(
            account_id=account_id,
            source=OPENALEX_SOURCE,
            query=query,
            failure=failure,
            started=started,
        )
        if failure is not None:
            return BookSearchOutcome(
                query=query, candidates=[], record=_failure_record(OPENALEX_SOURCE, query, failure)
            )
        candidates = [
            candidate
            for candidate in (
                _openalex_candidate(item) for item in (payload or {}).get("results") or []
            )
            if candidate is not None
        ]
        return BookSearchOutcome(
            query=query,
            candidates=candidates,
            record=ModuleQueryRecord(
                source=OPENALEX_SOURCE,
                query=query,
                status=ModuleQueryStatus.SUCCESS if candidates else ModuleQueryStatus.EMPTY,
                evidence_count=len(candidates),
                retrieved_at=datetime.now(UTC),
            ),
        )

    def _get_json(
        self, endpoint: str, params: dict[str, str], source: str, deadline: float | None
    ) -> tuple[dict[str, Any] | None, str | None]:
        return _get_json(
            self._client,
            endpoint,
            params,
            source,
            timeout=self._timeout,
            deadline=deadline,
        )

    def _audit(
        self,
        *,
        account_id: str,
        source: str,
        query: str,
        failure: str | None,
        started: float,
    ) -> None:
        log_metadata_lookup(
            self._observability,
            account_id=account_id,
            source=source,
            query=query,
            terminal=failure or "queried",
            started=started,
        )


class BilibiliVideoDiscoverer:
    """用公网搜索发现公开的哔哩哔哩视频直达页（只发现，不解释内容）。

    Tavily 的调用复用既有受限公网搜索服务（固定端点、预算、缓存、冷却与
    披露审计都已存在）；这里只做一件确定性的事：把返回结果里**确属**
    ``bilibili.com/video/BV…`` 或 ``/video/av…`` 的直达页挑出来，其余丢弃。
    """

    def __init__(self, service: WebSearchService | None) -> None:
        self._service = service

    def discover(
        self,
        account_id: str,
        query: str,
        *,
        limit: int,
        stop_event: Event | None = None,
        deadline: float | None = None,
    ) -> VideoDiscoveryOutcome:
        if self._service is None:
            return VideoDiscoveryOutcome(
                query=query,
                direct_pages=[],
                record=ModuleQueryRecord(
                    source=TAVILY_SOURCE,
                    query=query,
                    status=ModuleQueryStatus.SKIPPED,
                    detail="公网搜索服务未装配，本轮没有发送任何发现请求。",
                ),
            )
        plan = SearchPlan(
            should_search=True,
            query=query,
            reason="V2 学习资料推荐：发现哔哩哔哩公开讲解视频",
            queries=(query,),
            max_queries=1,
            max_results=max(1, min(limit, VIDEO_DISCOVERY_LIMIT)),
        )
        projection = self._service.search(
            account_id, plan, stop_event=stop_event, deadline=deadline
        )
        if projection is None:
            return VideoDiscoveryOutcome(
                query=query,
                direct_pages=[],
                record=ModuleQueryRecord(
                    source=TAVILY_SOURCE,
                    query=query,
                    status=ModuleQueryStatus.SKIPPED,
                    detail="本轮没有可发送的发现查询。",
                ),
            )
        pages = _bilibili_direct_pages(result.url for result in projection.results)
        return VideoDiscoveryOutcome(
            query=projection.query_summary or query,
            direct_pages=pages,
            record=_discovery_record(query, projection, len(pages)),
        )


class BilibiliVideoVerifier:
    """逐条核对哔哩哔哩视频可取得的元数据（标题、作者、时间、时长、简介）。

    核对只读取公开接口返回的字段；接口失败或视频不可见时**丢弃该条**并记账，
    不会用搜索结果的标题或摘要冒充视频元数据。用户可见的链路一律用核对通过
    的 id 重新拼——发现链路给出的链接只用于取 id。
    """

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        observability: ObservabilityService | None = None,
        timeout: float = VIDEO_TIMEOUT_SECONDS,
        max_lookups: int = VIDEO_VERIFY_LIMIT,
    ) -> None:
        self._client = client
        self._observability = observability
        self._timeout = timeout
        self._max_lookups = max_lookups

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def verify(
        self,
        pages: list[str],
        *,
        account_id: str,
        deadline: float | None = None,
    ) -> VideoVerifyOutcome:
        candidates: list[VideoCandidate] = []
        records: list[ModuleQueryRecord] = []
        rejected = 0
        truncated = False
        for index, page in enumerate(pages):
            if index >= self._max_lookups:
                truncated = True
                break
            if deadline is not None and time.monotonic() >= deadline:
                truncated = True
                break
            video_id = _video_id_from_url(page)
            if video_id is None:
                rejected += 1
                continue
            started = time.monotonic()
            candidate, failure = self._lookup(video_id, deadline=deadline)
            log_metadata_lookup(
                self._observability,
                account_id=account_id,
                source=BILIBILI_SOURCE,
                query=video_id,
                terminal=failure or "matched",
                started=started,
            )
            records.append(_verify_record(video_id, candidate, failure))
            if candidate is None:
                rejected += 1
                continue
            candidates.append(candidate)
        if truncated:
            records.append(
                ModuleQueryRecord(
                    source=f"{BILIBILI_SOURCE}_verify",
                    query="（本轮核对未逐条完成）",
                    status=ModuleQueryStatus.SKIPPED,
                    detail="视频核对达到本轮上限或时间预算，剩余直达页未核对。",
                )
            )
        return VideoVerifyOutcome(candidates=candidates, records=records, rejected=rejected)

    def _lookup(
        self, video_id: str, *, deadline: float | None
    ) -> tuple[VideoCandidate | None, str | None]:
        if self._client is None:
            return None, "bilibili_unavailable"
        if deadline is not None and time.monotonic() >= deadline:
            return None, "bilibili_deadline"
        params = {"bvid": video_id} if video_id.startswith("BV") else {"aid": video_id[2:]}
        try:
            response = self._client.get(
                BILIBILI_VIEW_ENDPOINT,
                params=params,
                timeout=self._timeout,
                headers={
                    "User-Agent": "BridGes/1.0 (public video metadata lookup)",
                    "Referer": BILIBILI_WATCH_TEMPLATE.format(video_id=video_id),
                },
            )
        except httpx.TimeoutException:
            return None, "bilibili_timeout"
        except httpx.HTTPError:
            return None, "bilibili_offline"
        if response.status_code == 429:
            return None, "bilibili_rate_limit"
        if response.status_code >= 400:
            return None, f"bilibili_http_{response.status_code}"
        try:
            payload = response.json()
        except ValueError:
            return None, "bilibili_parse"
        if not isinstance(payload, dict):
            return None, "bilibili_parse"
        if payload.get("code") != 0:
            # 视频不存在/已删除/不可见：如实丢弃，不拿搜索结果标题补位。
            return None, f"bilibili_video_unavailable_{_as_int(payload.get('code')) or 'unknown'}"
        return _bilibili_candidate(payload.get("data")), None


# ---------------------------------------------------------------------------
# 记录与审计
# ---------------------------------------------------------------------------


def log_metadata_lookup(
    observability: ObservabilityService | None,
    *,
    account_id: str,
    source: str,
    query: str,
    terminal: str,
    started: float,
) -> None:
    """记录一次外发元数据请求（账户归属 + 数据类别 + 查询指纹，不含正文）。"""
    if observability is None:
        return
    observability.log_audit(
        actor_account_id=account_id,
        action=AuditAction.LEARNING_RESOURCE_LOOKUP,
        result=_audit_result(terminal),
        reason="学习资料推荐：公开书目／视频元数据核对",
        details={
            "data_categories": ["public_query_terms"],
            "provider": source,
            "query_fingerprint": _query_fingerprint(query),
            "query_length": len(query),
            "terminal": terminal,
            "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        },
    )


def _get_json(
    client: httpx.Client | None,
    endpoint: str,
    params: dict[str, str],
    source: str,
    *,
    timeout: float,
    deadline: float | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """一次有界 GET + JSON 解析；失败返回稳定的错误码（绝不抛出上游细节）。"""
    if client is None:
        return None, f"{source}_unavailable"
    if deadline is not None and time.monotonic() >= deadline:
        return None, f"{source}_deadline"
    try:
        response = client.get(
            endpoint,
            params=params,
            timeout=timeout,
            headers={"User-Agent": "BridGes/1.0 (learning resource metadata lookup)"},
        )
    except httpx.TimeoutException:
        return None, f"{source}_timeout"
    except httpx.HTTPError:
        return None, f"{source}_offline"
    if response.status_code == 429:
        return None, f"{source}_rate_limit"
    if response.status_code >= 400:
        return None, f"{source}_http_{response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return None, f"{source}_parse"
    if not isinstance(payload, dict):
        return None, f"{source}_parse"
    return payload, None


def _failure_record(source: str, query: str, failure: str) -> ModuleQueryRecord:
    return ModuleQueryRecord(
        source=source,
        query=query,
        status=_status_for_failure(failure),
        evidence_count=0,
        retrieved_at=datetime.now(UTC),
        error_code=failure,
        error_message=_failure_message(source, failure),
        retryable=failure.endswith(("_timeout", "_offline", "_rate_limit", "_deadline")),
    )


def _status_for_failure(failure: str) -> ModuleQueryStatus:
    if failure.endswith("_timeout"):
        return ModuleQueryStatus.TIMEOUT
    if failure.endswith("_rate_limit"):
        return ModuleQueryStatus.RATE_LIMITED
    if failure.endswith("_deadline"):
        return ModuleQueryStatus.SKIPPED
    return ModuleQueryStatus.ERROR


def _failure_message(source: str, failure: str) -> str:
    label = {OPENLIBRARY_SOURCE: "Open Library", OPENALEX_SOURCE: "OpenAlex"}.get(source, source)
    if failure.endswith("_timeout"):
        return f"{label} 超时，本轮未取得该来源的书目。"
    if failure.endswith("_offline"):
        return f"{label} 不可达，本轮未取得该来源的书目。"
    if failure.endswith("_rate_limit"):
        return f"{label} 限流，本轮未取得该来源的书目。"
    if failure.endswith("_deadline"):
        return f"{label} 不在本轮时间预算内，未查询。"
    if failure.endswith("_unavailable"):
        return f"{label} 客户端未装配，本轮未查询。"
    return f"{label} 返回错误，本轮未取得该来源的书目。"


def _discovery_record(
    query: str, projection: WebSearchProjection, page_count: int
) -> ModuleQueryRecord:
    status = _discovery_status(projection, page_count)
    return ModuleQueryRecord(
        source=TAVILY_SOURCE,
        query=projection.query_summary or query,
        status=status,
        evidence_count=page_count,
        retrieved_at=projection.searched_at or datetime.now(UTC),
        error_code=projection.error_code,
        error_message=projection.error_message,
        retryable=projection.can_retry,
        detail=_discovery_detail(projection),
    )


def _discovery_status(
    projection: WebSearchProjection, page_count: int
) -> ModuleQueryStatus:
    if projection.status is WebSearchStatus.CANCELLED:
        return ModuleQueryStatus.CANCELLED
    if projection.status in {WebSearchStatus.ERROR, WebSearchStatus.PERMISSION}:
        return ModuleQueryStatus.ERROR
    if projection.error_code == "web_search_timeout":
        return ModuleQueryStatus.TIMEOUT
    if projection.error_code and "rate" in projection.error_code:
        return ModuleQueryStatus.RATE_LIMITED
    if projection.error_code:
        return ModuleQueryStatus.ERROR
    return ModuleQueryStatus.SUCCESS if page_count else ModuleQueryStatus.EMPTY


def _discovery_detail(projection: WebSearchProjection) -> str | None:
    notes: list[str] = []
    if projection.cache_hit:
        notes.append("命中未过期的搜索结果缓存")
    if projection.attempt_count:
        notes.append(f"上游请求 {projection.attempt_count} 次")
    if projection.cooldown_until is not None:
        notes.append("上游处于冷却期")
    return "；".join(notes) if notes else None


def _verify_record(
    video_id: str, candidate: VideoCandidate | None, failure: str | None
) -> ModuleQueryRecord:
    return ModuleQueryRecord(
        source=BILIBILI_SOURCE,
        query=video_id,
        status=(
            ModuleQueryStatus.SUCCESS
            if candidate is not None
            else _status_for_failure(failure or "bilibili_error")
        ),
        evidence_count=1 if candidate is not None else 0,
        retrieved_at=datetime.now(UTC),
        error_code=failure,
        error_message=_verify_message(failure) if failure is not None else None,
        retryable=(
            failure is not None
            and failure.endswith(("_timeout", "_offline", "_rate_limit"))
        ),
    )


def _verify_message(failure: str) -> str:
    if failure.endswith("_timeout"):
        return "哔哩哔哩元数据接口超时，该视频未核对。"
    if failure.endswith("_offline"):
        return "哔哩哔哩元数据接口不可达，该视频未核对。"
    if failure.endswith("_rate_limit"):
        return "哔哩哔哩元数据接口限流，该视频未核对。"
    if failure.startswith("bilibili_video_unavailable"):
        return "该视频当前不可见（不存在或已下架），已从清单移除。"
    return "该视频的元数据未取得，已从清单移除。"


def _audit_result(terminal: str) -> AuditResult:
    if terminal in {"queried", "matched"}:
        return AuditResult.SUCCESS
    if terminal.endswith(("_timeout", "_offline", "_rate_limit", "_deadline")):
        return AuditResult.RETRYABLE_FAIL
    if terminal.endswith("_unavailable"):
        return AuditResult.BLOCKED
    return AuditResult.DEGRADED


# ---------------------------------------------------------------------------
# 来源响应解析
# ---------------------------------------------------------------------------


def _openlibrary_candidate(doc: Any) -> BookCandidate | None:
    if not isinstance(doc, dict):
        return None
    title = str(doc.get("title") or "").strip()
    key = str(doc.get("key") or "").strip()
    if not title or not key.startswith("/"):
        return None
    authors = [str(name) for name in (doc.get("author_name") or []) if str(name).strip()]
    publishers = [str(name) for name in (doc.get("publisher") or []) if str(name).strip()]
    return BookCandidate(
        title=title,
        creators=authors[:6],
        year=_as_int(doc.get("first_publish_year")),
        publisher=publishers[0] if publishers else None,
        isbn=_preferred_isbn(doc.get("isbn")),
        source=OPENLIBRARY_SOURCE,
        url=f"https://openlibrary.org{key}",
    )


def _openalex_candidate(item: Any) -> BookCandidate | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    location = item.get("primary_location") or {}
    source = (location.get("source") or {}) if isinstance(location, dict) else {}
    landing = (
        str(location.get("landing_page_url") or "").strip()
        if isinstance(location, dict)
        else ""
    )
    openalex_id = str(item.get("id") or "").strip()
    url = landing or openalex_id
    if not url:
        return None
    authors = [
        str((authorship.get("author") or {}).get("display_name") or "").strip()
        for authorship in item.get("authorships") or []
        if isinstance(authorship, dict)
    ]
    return BookCandidate(
        title=title,
        creators=[name for name in authors if name][:6],
        year=_as_int(item.get("publication_year")),
        publisher=str(source.get("display_name") or "").strip() or None,
        isbn=_preferred_isbn((item.get("ids") or {}).get("isbn")),
        source=OPENALEX_SOURCE,
        url=url,
    )


def _bilibili_candidate(data: Any) -> VideoCandidate | None:
    if not isinstance(data, dict):
        return None
    video_id = str(data.get("bvid") or "").strip()
    title = str(data.get("title") or "").strip()
    if not video_id or not title:
        return None
    owner = data.get("owner") or {}
    uploader = str(owner.get("name") or "").strip() if isinstance(owner, dict) else ""
    stat = data.get("stat") if isinstance(data.get("stat"), dict) else {}
    return VideoCandidate(
        video_id=video_id,
        title=title,
        uploader=uploader or None,
        duration_seconds=_as_int(data.get("duration")),
        published_at=_from_unix(data.get("pubdate")),
        url=BILIBILI_WATCH_TEMPLATE.format(video_id=video_id),
        description=str(data.get("desc") or "").strip(),
        # 公开计数（播放/点赞）同属可取得的元数据；只作弱证据，不当作质量结论。
        view_count=_as_int((stat or {}).get("view")),
        like_count=_as_int((stat or {}).get("like")),
    )


def _bilibili_direct_pages(urls: Any) -> list[str]:
    """从搜索结果里挑出确属哔哩哔哩视频直达页的链接（其余丢弃）。

    按**视频 id** 去重：同一条视频常被搜索服务返回多个带跟踪参数的链接，
    保留首次出现的那个链接及其参数，输出顺序与搜索排名一致。
    """
    pages: list[str] = []
    seen: set[str] = set()
    for url in urls:
        text = str(url or "").strip()
        if not text:
            continue
        video_id = _video_id_from_url(text)
        if video_id is None or video_id in seen:
            continue
        seen.add(video_id)
        pages.append(text)
    return pages


def _video_id_from_url(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"}:
        return None
    if parts.hostname is None or parts.hostname not in _BILIBILI_HOSTS:
        return None
    match = _BILIBILI_PATH.match(parts.path)
    if match is None:
        return None
    return match.group("video_id")


def _preferred_isbn(value: Any) -> str | None:
    """优先取 13 位 ISBN，其次 10 位；来源未给可读 ISBN 时返回 None。"""
    raw = value if isinstance(value, list) else ([value] if value else [])
    candidates = [re.sub(r"[^0-9Xx]", "", str(item)) for item in raw]
    for candidate in candidates:
        if len(candidate) == 13:
            return candidate
    for candidate in candidates:
        if len(candidate) == 10:
            return candidate
    return None


def _query_fingerprint(query: str) -> str:
    return sha256(" ".join(query.split()).encode("utf-8")).hexdigest()[:16]


def _from_unix(value: Any) -> datetime | None:
    stamp = _as_int(value)
    if stamp is None or stamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(stamp, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None
