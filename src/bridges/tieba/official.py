"""``tieba.verify_official``：涉及校规、费用、开放时间与流程时核对学校官方页面。

官方证据只有一种：真的取到了学校官方域名下的页面，并在页面文本里定位到与
原始名词相符的段落。取不到就如实说明未取得官方页面；没有定位到相关段落就
标注未定位，绝不拿吧友说法充当官方规定。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from typing import Protocol

import httpx

from bridges.tieba.contracts import TiebaOfficialCheck, TiebaQuestionAnalysis
from bridges.tieba.http_requests import TiebaHttpRequestError, tracked_get
from bridges.tieba.lexicon import (
    OFFICIAL_DOMAIN_SUFFIX,
    OFFICIAL_TRIGGERS,
    PLACE_EVENT_TERMS,
    relaxed_topic_terms,
)
from bridges.tieba.official_content import notice_body
from bridges.tieba.reading import USER_AGENT

#: 官方检索最多采用的候选页面数（每页一次真实请求）。
OFFICIAL_FETCH_LIMIT = 2

#: 单次官方页面读取的墙钟上限。
OFFICIAL_FETCH_TIMEOUT_SECONDS = 8.0

#: 官方页面响应体上限。
OFFICIAL_MAX_BYTES = 600_000

#: 摘录窗口（命中词前后各取一段）与单条摘录最大长度。
EXCERPT_WINDOW = 120
EXCERPT_MAX_CHARS = 1200

STATUS_VERIFIED = "verified"
STATUS_EXCERPT_NOT_FOUND = "excerpt_not_found"
STATUS_FETCH_FAILED = "fetch_failed"
STATUS_DOMAIN_REJECTED = "domain_rejected"

_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_PAGE_CHROME = re.compile(
    r"<(head|nav|header|footer|aside)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_MAIN_CONTENT = re.compile(
    r"<(article|main)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL
)
_HEADINGS = re.compile(r"<h[1-6]\b[^>]*>.*?</h[1-6]>", re.IGNORECASE | re.DOTALL)
_BLOCK_TAGS = re.compile(
    r"</?(p|div|br|li|tr|td|h[1-6]|section|article|table|ul|ol)\b[^>]*>",
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


class TiebaOfficialReader(Protocol):
    """官方页面读取边界（测试可注入替身）。"""

    def fetch(
        self,
        url: str,
        *,
        terms: tuple[str, ...],
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaOfficialCheck: ...


def official_query(analysis: TiebaQuestionAnalysis) -> str:
    """官方核验的查询词：限定在学校官方域名，带上原始名词与触发词。"""
    return f"site:{OFFICIAL_DOMAIN_SUFFIX} 华东交通大学 {_official_terms(analysis)}".strip()


def official_fallback_query(analysis: TiebaQuestionAnalysis) -> str:
    """备用查询只减少主题条件，始终保留学校官方域名。"""
    terms = _official_terms(analysis).split()
    relaxed = " ".join(relaxed_topic_terms(terms))
    return f"site:{OFFICIAL_DOMAIN_SUFFIX} 华东交通大学 {relaxed} 官方".strip()


def _official_terms(analysis: TiebaQuestionAnalysis) -> str:
    terms = list(analysis.topic_terms)
    for topic in analysis.official_topics:
        if topic not in terms:
            terms.append(topic)
    return " ".join(terms[:4])


def is_official_url(url: str) -> bool:
    """是否为学校官方域名下的页面（二级学院子域也算官方）。"""
    match = re.match(r"^https?://([^/?#]+)", url.strip(), re.IGNORECASE)
    if match is None:
        return False
    host = match.group(1).lower().split(":")[0]
    return host == OFFICIAL_DOMAIN_SUFFIX or host.endswith(f".{OFFICIAL_DOMAIN_SUFFIX}")


class HttpOfficialSiteReader:
    """官方页面读取实现（公开 GET，固定 UA，有界体积与时间）。"""

    uses_per_request_budget = True

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        max_bytes: int = OFFICIAL_MAX_BYTES,
        timeout: float = OFFICIAL_FETCH_TIMEOUT_SECONDS,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        )
        self._max_bytes = max_bytes

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch(
        self,
        url: str,
        *,
        terms: tuple[str, ...],
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaOfficialCheck:
        fetched_at = datetime.now(UTC)
        host = _host_of(url)
        if not is_official_url(url):
            return TiebaOfficialCheck(
                title=url,
                url=url,
                host=host,
                fetched_at=fetched_at,
                status=STATUS_DOMAIN_REJECTED,
                error_code="tieba_official_not_official_domain",
                error_message="该链接不属于学校官方域名，不作为官方依据。",
            )
        if _stopped(stop_event):
            return _failed(
                url, host, fetched_at, "tieba_official_cancelled", "已停止，未读取官方页面。"
            )
        try:
            response = tracked_get(
                self._client,
                url,
                purpose="tieba_official_fetch",
                timeout=OFFICIAL_FETCH_TIMEOUT_SECONDS,
                allowed_url=is_official_url,
                deadline=deadline,
            )
        except TiebaHttpRequestError as exc:
            return _failed(url, host, fetched_at, exc.code, exc.message)
        except httpx.TimeoutException:
            return _failed(
                url,
                host,
                fetched_at,
                "tieba_official_timeout",
                "读取官方页面超时，未取得官方依据。",
            )
        except httpx.HTTPError:
            return _failed(
                url,
                host,
                fetched_at,
                "tieba_official_unavailable",
                "当前无法连接学校官方页面，未取得官方依据。",
            )
        if response.status_code >= 400:
            return _failed(
                url,
                host,
                fetched_at,
                "tieba_official_unavailable",
                f"官方页面返回 HTTP {response.status_code}，未取得官方依据。",
            )
        html = _bounded_text(response, self._max_bytes)
        text = extract_page_text(html)
        title = _clean_title(html) or host
        # 通知标题可用于发现页面，但不能单独充当已定位到正文的依据。
        text = "\n".join(
            line for line in text.splitlines()
            if line not in {title, title.split("-", 1)[0]}
        )
        excerpt, matched = locate_excerpt(text, terms)
        if excerpt is None:
            return TiebaOfficialCheck(
                title=title,
                url=url,
                host=host,
                fetched_at=fetched_at,
                status=STATUS_EXCERPT_NOT_FOUND,
                matched_terms=list(matched),
                error_code="tieba_official_excerpt_not_found",
                error_message="已取得官方页面，但未在该页定位到与问题相关的段落。",
            )
        return TiebaOfficialCheck(
            title=title,
            url=url,
            host=host,
            fetched_at=fetched_at,
            status=STATUS_VERIFIED,
            excerpt=excerpt,
            matched_terms=list(matched),
        )


def extract_page_text(html: str) -> str:
    """把公开页面转成纯文本（去脚本样式与标签，保留段落边界）。"""
    text = _SCRIPT_STYLE.sub(" ", html)
    text = _PAGE_CHROME.sub(" ", text)
    main = _MAIN_CONTENT.search(text)
    if main is not None:
        text = main.group(2)
    text = _HEADINGS.sub(" ", text)
    body = notice_body(text)
    if body is not None:
        text = body
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = unescape(text)
    lines = [_WHITESPACE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def locate_excerpt(
    text: str, terms: tuple[str, ...]
) -> tuple[str | None, tuple[str, ...]]:
    """在页面文本里定位原始名词，返回真实文本窗口与命中的名词。"""
    # 按段落选取实质内容，避免标题首次命中截走正文。
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    anchors = tuple(
        term
        for term in terms
        if term and (term not in OFFICIAL_TRIGGERS or term in PLACE_EVENT_TERMS)
    ) or terms
    matching = [i for i, line in enumerate(paragraphs)
                if any(term and term in line for term in anchors)]
    if not matching:
        return None, ()
    index = max(matching, key=lambda i: (
        sum(term in paragraphs[i] for term in terms), len(paragraphs[i])
    ))
    paragraph = paragraphs[index]
    # “安排如下”是列表引导句，后续日期、时段可能完全不重复检索词。
    # 保留同一正文容器内的后续段落，最多十二段／1200 字，不能只交付引导句。
    if "如下" in paragraph:
        excerpt = "\n".join(paragraphs[index:index + 12])[:EXCERPT_MAX_CHARS]
        return excerpt, tuple(term for term in terms if term and term in excerpt)
    matched = tuple(term for term in terms if term and term in paragraph)
    first = next(paragraph.find(term) for term in anchors if term and term in paragraph)
    start = max(0, first - EXCERPT_WINDOW)
    end = min(len(paragraph), first + EXCERPT_WINDOW)
    excerpt = _WHITESPACE.sub(" ", paragraph[start:end]).strip()
    excerpt = excerpt[:EXCERPT_MAX_CHARS]
    return excerpt, tuple(term for term in matched if term in excerpt)


def _failed(
    url: str, host: str, fetched_at: datetime, code: str, message: str
) -> TiebaOfficialCheck:
    return TiebaOfficialCheck(
        title=url,
        url=url,
        host=host,
        fetched_at=fetched_at,
        status=STATUS_FETCH_FAILED,
        error_code=code,
        error_message=message,
    )


def _host_of(url: str) -> str:
    match = re.match(r"^https?://([^/?#]+)", url.strip(), re.IGNORECASE)
    return match.group(1).lower().split(":")[0] if match is not None else ""


def _clean_title(html: str) -> str:
    match = _TITLE.search(html)
    if match is None:
        return ""
    return _WHITESPACE.sub(" ", unescape(_TAG.sub("", match.group(1)))).strip()


def _bounded_text(response: httpx.Response, max_bytes: int) -> str:
    raw = response.content[:max_bytes]
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _stopped(stop_event: object | None) -> bool:
    checker = getattr(stop_event, "is_set", None)
    return bool(checker()) if callable(checker) else False
