"""Issue 13 来源适配：图书书目、视频发现与视频元数据核对。

全部用可控的假 HTTP 客户端（无外网），覆盖：

- Open Library 书目解析（标题/作者/年份/出版社/ISBN 优先级/书目页链接）；
- OpenAlex 图书记录解析（标题/年份/作者/落地页）；
- 超时、限流、不可达与解析失败都产出稳定的分类码，不抛出上游细节；
- 哔哩哔哩直达页的识别（只认 /video/BV… 与 /video/av…，短链与站外一律丢弃）；
- 视频元数据核对（标题/作者/发布时间/时长/简介），不可见时丢弃并记账；
- 每次外发请求都留下账户归属的脱敏披露审计，且不含查询正文。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from bridges.contracts.modules import ModuleQueryStatus
from bridges.contracts.observability import AuditAction
from bridges.resources.sources import (
    BILIBILI_SOURCE,
    OPENALEX_SOURCE,
    OPENLIBRARY_SOURCE,
    BilibiliVideoDiscoverer,
    BilibiliVideoVerifier,
    OpenAlexBookSource,
    OpenLibraryBookSource,
)

NOW = datetime(2026, 9, 25, tzinfo=UTC)


class _RecordingClient:
    """假 httpx 客户端：按 URL 返回预设响应，并记录实际发送的查询参数。"""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.closed = False

    def get(self, url: str, *, params: dict[str, str], **kwargs: Any) -> httpx.Response:
        del kwargs
        self.calls.append((url, dict(params)))
        outcome = self.responses.get(url)
        if outcome is None:
            return httpx.Response(
                404, json={"error": "not configured"}, request=httpx.Request("GET", url)
            )
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            return httpx.Response(outcome, json={}, request=httpx.Request("GET", url))
        if isinstance(outcome, str):
            return httpx.Response(
                200, content=outcome.encode("utf-8"), request=httpx.Request("GET", url)
            )
        return httpx.Response(200, json=outcome, request=httpx.Request("GET", url))

    def close(self) -> None:
        self.closed = True


class _FakeObservability:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


def _openlibrary_payload() -> dict[str, Any]:
    return {
        "numFound": 2,
        "docs": [
            {
                "key": "/works/OL26329500W",
                "title": "深度学习：核心原理与案例分析",
                "author_name": ["李明"],
                "first_publish_year": 2019,
                "publisher": ["人民邮电出版社"],
                "isbn": ["7115512345", "9787115512340"],
            },
            {
                "key": "/works/OL999W",
                "title": "No Authors Here",
                "first_publish_year": 2001,
            },
        ],
    }


# ---------------------------------------------------------------------------
# resources.search_books（Open Library）
# ---------------------------------------------------------------------------


def test_open_library_source_parses_bibliography_and_prefers_isbn13() -> None:
    """书目解析：标题、作者、年份、出版社、可点开的书目页，ISBN 取 13 位。"""
    client = _RecordingClient({"https://openlibrary.org/search.json": _openlibrary_payload()})
    source = OpenLibraryBookSource(client=client)  # type: ignore[arg-type]
    outcome = source.search("acct-1", "深度学习", limit=8)

    assert outcome.record.status is ModuleQueryStatus.SUCCESS
    assert outcome.record.evidence_count == 2
    first = outcome.candidates[0]
    assert first.title == "深度学习：核心原理与案例分析"
    assert first.creators == ["李明"]
    assert first.year == 2019
    assert first.publisher == "人民邮电出版社"
    assert first.isbn == "9787115512340"
    assert first.url == "https://openlibrary.org/works/OL26329500W"
    assert first.source == OPENLIBRARY_SOURCE
    # 第二条没有作者/出版社/ISBN：如实留空，不补造。
    assert outcome.candidates[1].creators == []
    assert outcome.candidates[1].publisher is None
    assert outcome.candidates[1].isbn is None
    # 外部只收到最小查询词与字段选择。
    assert client.calls == [
        (
            "https://openlibrary.org/search.json",
            {
                "q": "深度学习",
                "limit": "8",
                "fields": "key,title,author_name,first_publish_year,publisher,isbn",
            },
        )
    ]


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (httpx.TimeoutException("slow"), ModuleQueryStatus.TIMEOUT, "openlibrary_timeout"),
        (httpx.ConnectError("down"), ModuleQueryStatus.ERROR, "openlibrary_offline"),
        (429, ModuleQueryStatus.RATE_LIMITED, "openlibrary_rate_limit"),
        (500, ModuleQueryStatus.ERROR, "openlibrary_http_500"),
        ("not json", ModuleQueryStatus.ERROR, "openlibrary_parse"),
    ],
)
def test_open_library_failures_are_classified_without_leaking_details(
    failure: Any, expected_status: ModuleQueryStatus, expected_code: str
) -> None:
    """超时/不可达/限流/HTTP 错误/解析失败都产出稳定分类码与可操作中文说明。"""
    client = _RecordingClient({"https://openlibrary.org/search.json": failure})
    source = OpenLibraryBookSource(client=client)  # type: ignore[arg-type]
    outcome = source.search("acct-1", "深度学习", limit=8)

    assert outcome.candidates == []
    assert outcome.record.status is expected_status
    assert outcome.record.error_code == expected_code
    assert outcome.record.error_message is not None
    assert outcome.record.evidence_count == 0
    # 可重试＝瞬时类失败（超时/不可达/限流）；HTTP 4xx 与解析失败不重试。
    assert outcome.record.retryable is expected_code.endswith(
        ("_timeout", "_offline", "_rate_limit")
    )


def test_sources_without_client_report_unavailable_not_silently_empty() -> None:
    """未装配客户端时如实报「未查询」，而不是假装查过但没有结果。"""
    outcome = OpenLibraryBookSource(client=None).search("acct-1", "深度学习", limit=8)
    assert outcome.record.status is ModuleQueryStatus.ERROR
    assert outcome.record.error_code == "openlibrary_unavailable"


# ---------------------------------------------------------------------------
# resources.search_books（OpenAlex）
# ---------------------------------------------------------------------------


def test_openalex_source_parses_book_records() -> None:
    """OpenAlex 图书记录：标题、年份、作者与落地页（用于补充英文书目）。"""
    payload = {
        "meta": {"count": 1},
        "results": [
            {
                "id": "https://openalex.org/W2557283755",
                "title": "Deep Learning",
                "publication_year": 2016,
                "authorships": [{"author": {"display_name": "Ian Goodfellow"}}],
                "primary_location": {
                    "landing_page_url": "https://dl.acm.org/citation.cfm?id=3086952",
                    "source": {"display_name": "MIT Press"},
                },
                "ids": {"openalex": "https://openalex.org/W2557283755"},
            },
            {"title": ""},
        ],
    }
    client = _RecordingClient({"https://api.openalex.org/works": payload})
    source = OpenAlexBookSource(client=client)  # type: ignore[arg-type]
    outcome = source.search("acct-1", "deep learning", limit=8)

    assert outcome.record.source == OPENALEX_SOURCE
    assert len(outcome.candidates) == 1
    candidate = outcome.candidates[0]
    assert candidate.title == "Deep Learning"
    assert candidate.year == 2016
    assert candidate.creators == ["Ian Goodfellow"]
    assert candidate.publisher == "MIT Press"
    assert candidate.url == "https://dl.acm.org/citation.cfm?id=3086952"
    assert candidate.isbn is None
    assert client.calls[0][1]["filter"] == "type:book"


# ---------------------------------------------------------------------------
# resources.search_videos：发现
# ---------------------------------------------------------------------------


class _FakeWebSearch:
    """假公网搜索服务：返回预设结果，并记录收到的查询计划。"""

    def __init__(self, urls: list[str], **kwargs: Any) -> None:
        self._projection = _projection(urls, **kwargs)
        self.plans: list[Any] = []

    def search(self, account_id: str, plan: Any, **kwargs: Any) -> Any:
        del account_id, kwargs
        self.plans.append(plan)
        return self._projection


def _projection(urls: list[str], **overrides: Any) -> Any:
    from bridges.web_search.contracts import (
        WebSearchProjection,
        WebSearchResult,
        WebSearchStatus,
    )

    results = [
        WebSearchResult(
            result_id=f"r{index}",
            title=f"结果 {index}",
            site="哔哩哔哩",
            url=url,
            accessed_at=NOW,
        )
        for index, url in enumerate(urls, start=1)
    ]
    payload: dict[str, Any] = {
        "status": WebSearchStatus.SUCCESS,
        "trigger_reason": "V2 学习资料推荐：发现哔哩哔哩公开讲解视频",
        "query_summary": "机器学习 零基础 入门 教程",
        "results": results,
        "searched_at": NOW,
    }
    payload.update(overrides)
    return WebSearchProjection(**payload)


def test_video_discovery_keeps_only_bilibili_direct_pages() -> None:
    """发现只在返回结果里挑确属哔哩哔哩视频直达页的链接，其余丢弃且保序去重。"""
    fake = _FakeWebSearch(
        [
            "https://www.bilibili.com/video/BV1pu411o7BE?p=1&t=10",
            "https://www.zhihu.com/question/123",
            "https://www.bilibili.com/video/av12345",
            "https://www.bilibili.com/bangumi/play/ep123",
            "https://b23.tv/Sh0rt",
            "https://www.bilibili.com/video/BV1pu411o7BE",
            "https://www.bilibili.com/video/BV1pu411o7BE?spm_id_from=333",
        ]
    )
    discoverer = BilibiliVideoDiscoverer(fake)  # type: ignore[arg-type]
    outcome = discoverer.discover("acct-1", "机器学习 教程", limit=8)

    assert outcome.direct_pages == [
        "https://www.bilibili.com/video/BV1pu411o7BE?p=1&t=10",
        "https://www.bilibili.com/video/av12345",
    ]
    assert outcome.record.status is ModuleQueryStatus.SUCCESS
    assert outcome.record.evidence_count == 2
    # 只发一条最小查询，且不携带任何私有上下文。
    assert len(fake.plans) == 1
    assert fake.plans[0].queries == ("机器学习 教程",)
    assert fake.plans[0].max_queries == 1


def test_video_discovery_without_service_reports_skip() -> None:
    """未装配公网搜索时如实报「未发送任何发现请求」，不假装发现了空结果。"""
    outcome = BilibiliVideoDiscoverer(None).discover("acct-1", "机器学习 教程", limit=8)
    assert outcome.direct_pages == []
    assert outcome.record.status is ModuleQueryStatus.SKIPPED
    assert outcome.record.detail is not None


def test_video_discovery_reports_credential_gap_as_error() -> None:
    """未配置搜索凭据时把上游错误分类如实带出（用户据此去配置）。"""
    from bridges.web_search.contracts import WebSearchStatus

    fake = _FakeWebSearch(
        [],
        status=WebSearchStatus.ERROR,
        error_code="web_search_credentials",
        error_message="未配置搜索凭据：请先运行 BridGes start 配置 Tavily API Key。",
        can_retry=True,
    )
    outcome = BilibiliVideoDiscoverer(fake).discover("acct-1", "机器学习 教程", limit=8)  # type: ignore[arg-type]
    assert outcome.record.status is ModuleQueryStatus.ERROR
    assert outcome.record.error_code == "web_search_credentials"
    assert outcome.record.retryable is True


# ---------------------------------------------------------------------------
# resources.search_videos：核对
# ---------------------------------------------------------------------------


def _view_payload(
    *,
    bvid: str = "BV1pu411o7BE",
    title: str = "Transformer论文逐段精读【论文精读】",
    uploader: str = "跟李沐学AI",
    duration: int = 5225,
    pubdate: int = 1635381398,
    desc: str = "更多请见：https://github.com/mli/paper-reading",
    view: int | None = 1815690,
    like: int | None = 45780,
) -> dict[str, Any]:
    stat: dict[str, Any] = {}
    if view is not None:
        stat["view"] = view
    if like is not None:
        stat["like"] = like
    return {
        "code": 0,
        "data": {
            "bvid": bvid,
            "title": title,
            "owner": {"name": uploader},
            "duration": duration,
            "pubdate": pubdate,
            "desc": desc,
            "stat": stat,
        },
    }


def test_video_verifier_reads_public_metadata_and_rewrites_canonical_link() -> None:
    """核对通过后使用公开接口的元数据，并用核对到的 id 拼出规范直达链接。"""
    client = _RecordingClient(
        {"https://api.bilibili.com/x/web-interface/view": _view_payload()}
    )
    verifier = BilibiliVideoVerifier(client=client)  # type: ignore[arg-type]
    outcome = verifier.verify(
        ["https://www.bilibili.com/video/BV1pu411o7BE?spm_id_from=333.999"],
        account_id="acct-1",
    )

    assert outcome.rejected == 0
    candidate = outcome.candidates[0]
    assert candidate.video_id == "BV1pu411o7BE"
    assert candidate.title == "Transformer论文逐段精读【论文精读】"
    assert candidate.uploader == "跟李沐学AI"
    assert candidate.duration_seconds == 5225
    assert candidate.published_at == datetime.fromtimestamp(1635381398, tz=UTC)
    assert candidate.url == "https://www.bilibili.com/video/BV1pu411o7BE"
    assert candidate.description.startswith("更多请见")
    # 公开计数同属可取得的元数据，如实带出（只作弱证据）。
    assert candidate.view_count == 1815690
    assert candidate.like_count == 45780
    assert client.calls[0][1] == {"bvid": "BV1pu411o7BE"}


def test_video_verifier_keeps_missing_counters_as_none() -> None:
    """接口没给公开计数时不臆造数字，字段保持 None。"""
    client = _RecordingClient(
        {
            "https://api.bilibili.com/x/web-interface/view": _view_payload(
                view=None, like=None
            )
        }
    )
    verifier = BilibiliVideoVerifier(client=client)  # type: ignore[arg-type]
    outcome = verifier.verify(
        ["https://www.bilibili.com/video/BV1pu411o7BE"], account_id="acct-1"
    )
    assert outcome.candidates[0].view_count is None
    assert outcome.candidates[0].like_count is None


def test_video_verifier_drops_unavailable_video_and_records_why() -> None:
    """视频不可见（code≠0）时丢弃并记账，不用搜索结果标题补位。"""
    client = _RecordingClient(
        {
            "https://api.bilibili.com/x/web-interface/view": {
                "code": -404,
                "message": "啥都木有",
            }
        }
    )
    verifier = BilibiliVideoVerifier(client=client)  # type: ignore[arg-type]
    outcome = verifier.verify(
        ["https://www.bilibili.com/video/BV1pu411o7BE"], account_id="acct-1"
    )
    assert outcome.candidates == []
    assert outcome.rejected == 1
    assert outcome.records[0].status is ModuleQueryStatus.ERROR
    assert outcome.records[0].error_code == "bilibili_video_unavailable_-404"
    assert outcome.records[0].error_message is not None
    assert "不可见" in outcome.records[0].error_message


def test_video_verifier_uses_aid_for_av_pages() -> None:
    """av 号直达页按 aid 参数核对（顺带覆盖两种 id 形态）。"""
    client = _RecordingClient(
        {"https://api.bilibili.com/x/web-interface/view": _view_payload(bvid="BV1xx411c7mD")}
    )
    verifier = BilibiliVideoVerifier(client=client)  # type: ignore[arg-type]
    verifier.verify(["https://www.bilibili.com/video/av12345"], account_id="acct-1")
    assert client.calls[0][1] == {"aid": "12345"}


def test_video_verifier_is_bounded_by_lookup_limit() -> None:
    """核对有上限：超出部分不再发请求，并追加一条如实的跳过记录。"""
    client = _RecordingClient(
        {"https://api.bilibili.com/x/web-interface/view": _view_payload()}
    )
    verifier = BilibiliVideoVerifier(client=client, max_lookups=2)  # type: ignore[arg-type]
    pages = [f"https://www.bilibili.com/video/BV1pu411o7B{index}" for index in "ABCD"]
    outcome = verifier.verify(pages, account_id="acct-1")

    assert len(client.calls) == 2
    assert len(outcome.candidates) == 2
    assert outcome.records[-1].status is ModuleQueryStatus.SKIPPED
    assert outcome.records[-1].detail is not None


# ---------------------------------------------------------------------------
# 云端披露审计
# ---------------------------------------------------------------------------


def test_metadata_lookups_are_audited_without_query_text() -> None:
    """每次外发请求都写账户归属的脱敏审计：来源、查询指纹与长度，不含正文。"""
    observability = _FakeObservability()
    client = _RecordingClient(
        {
            "https://openlibrary.org/search.json": _openlibrary_payload(),
            "https://api.bilibili.com/x/web-interface/view": _view_payload(),
        }
    )
    OpenLibraryBookSource(
        client=client, observability=observability  # type: ignore[arg-type]
    ).search("acct-7", "深度学习", limit=8)
    BilibiliVideoVerifier(
        client=client, observability=observability  # type: ignore[arg-type]
    ).verify(["https://www.bilibili.com/video/BV1pu411o7BE"], account_id="acct-7")

    assert len(observability.events) == 2
    book_event, video_event = observability.events
    assert book_event["actor_account_id"] == "acct-7"
    assert book_event["action"] is AuditAction.LEARNING_RESOURCE_LOOKUP
    assert book_event["details"]["provider"] == OPENLIBRARY_SOURCE
    assert book_event["details"]["query_length"] == len("深度学习")
    assert book_event["details"]["data_categories"] == ["public_query_terms"]
    assert "深度学习" not in str(book_event["details"])
    assert video_event["details"]["provider"] == BILIBILI_SOURCE
    assert video_event["details"]["terminal"] == "matched"
    assert BILIBILI_SOURCE in video_event["reason"] or "视频" in video_event["reason"]


def test_audit_result_reflects_retryable_failures() -> None:
    """超时/限流走可重试分类，不可达走阻断分类（审计结果与真实原因一致）。"""
    observability = _FakeObservability()
    client = _RecordingClient(
        {"https://openlibrary.org/search.json": httpx.TimeoutException("slow")}
    )
    OpenLibraryBookSource(
        client=client, observability=observability  # type: ignore[arg-type]
    ).search("acct-7", "深度学习", limit=8)
    from bridges.contracts.observability import AuditResult

    assert observability.events[0]["result"] is AuditResult.RETRYABLE_FAIL
    assert observability.events[0]["details"]["terminal"] == "openlibrary_timeout"
