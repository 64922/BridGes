"""``tieba.verify_official``：涉及校规、费用、开放时间与流程时核对学校官方页面。

官方证据只有一种：真的取到了学校官方域名下的页面，并在页面文本里定位到与
原始名词相符的段落。取不到就如实说明未取得官方页面；没有定位到相关段落就
标注未定位，绝不拿吧友说法充当官方规定。
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from html import unescape
from typing import Protocol

import httpx

from bridges.tieba.contracts import TiebaOfficialCheck, TiebaQuestionAnalysis
from bridges.tieba.lexicon import OFFICIAL_DOMAIN_SUFFIX
from bridges.tieba.reading import USER_AGENT

#: 官方检索最多采用的候选页面数（每页一次真实请求）。
OFFICIAL_FETCH_LIMIT = 2

#: 单次官方页面读取的墙钟上限。
OFFICIAL_FETCH_TIMEOUT_SECONDS = 8.0

#: 官方页面响应体上限。
OFFICIAL_MAX_BYTES = 600_000

#: 摘录窗口（命中词前后各取一段）与单条摘录最大长度。
EXCERPT_WINDOW = 120
EXCERPT_MAX_CHARS = 320

STATUS_VERIFIED = "verified"
STATUS_EXCERPT_NOT_FOUND = "excerpt_not_found"
STATUS_FETCH_FAILED = "fetch_failed"
STATUS_DOMAIN_REJECTED = "domain_rejected"

_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
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
    """官方域名没有命中时的第二查询：只用校名与原始名词再找一次官方页面。"""
    return f"华东交通大学 {_official_terms(analysis)} 官方".strip()


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
        timeout = OFFICIAL_FETCH_TIMEOUT_SECONDS
        if deadline is not None:
            timeout = max(0.5, min(timeout, deadline - time.monotonic()))
        try:
            response = self._client.get(url, timeout=timeout)
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
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = unescape(text)
    lines = [_WHITESPACE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def locate_excerpt(
    text: str, terms: tuple[str, ...]
) -> tuple[str | None, tuple[str, ...]]:
    """在页面文本里定位原始名词，返回真实文本窗口与命中的名词。"""
    matched = tuple(term for term in terms if term and term in text)
    if not matched:
        return None, ()
    first = min(text.find(term) for term in matched)
    start = max(0, first - EXCERPT_WINDOW)
    end = min(len(text), first + EXCERPT_WINDOW)
    excerpt = _WHITESPACE.sub(" ", text[start:end]).strip()
    return excerpt[:EXCERPT_MAX_CHARS], matched


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
