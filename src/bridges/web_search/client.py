"""DuckDuckGo 公网搜索客户端。

客户端只发送查询规划器产出的公开、最小查询词，不读取账户配置、附件或
用户画像。搜索响应和来源页面都受固定大小、协议、目标和重定向上限约束。
"""

from __future__ import annotations

import html
import ipaddress
import re
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime
from html.parser import HTMLParser
from threading import Event
from time import monotonic
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from bridges.web_search.contracts import (
    WebSearchHealth,
    WebSearchHealthStatus,
    WebSearchResult,
)

DUCKDUCKGO_ENDPOINT = "https://html.duckduckgo.com/html/"
DUCKDUCKGO_PROVIDER_VERSION = "duckduckgo-html-v1"
DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_MAX_REDIRECTS = 2
_SOURCE_SUMMARY_MAX_CHARS = 4_000
_PUBLISHED_AT_RE = re.compile(
    r"(?:article:published_time|datePublished|datetime)\s*[\"'=:\s]+"
    r"(20\d{2}-\d{2}-\d{2}(?:[T\s][0-9:+.-]+)?)",
    re.IGNORECASE,
)


class WebSearchError(Exception):
    """可向用户展示的搜索失败。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        permission: bool = False,
        retryable: bool = True,
    ) -> None:
        self.code = code
        self.message = message
        self.permission = permission
        self.retryable = retryable
        super().__init__(message)


class DuckDuckGoClient:
    """DuckDuckGo HTML 网页搜索的同步、可替换客户端。"""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = 8.0,
        max_results: int = DEFAULT_MAX_RESULTS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        fetch_sources: bool = True,
    ) -> None:
        self._client = http_client or httpx.Client(timeout=timeout)
        self._max_results = max(1, min(max_results, DEFAULT_MAX_RESULTS))
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max(0, min(max_redirects, DEFAULT_MAX_REDIRECTS))
        self._should_fetch_sources = fetch_sources

    def search(
        self,
        query: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
        stop_event: Event | None = None,
    ) -> list[WebSearchResult]:
        if not query.strip():
            raise WebSearchError(
                "web_search_request", "公网搜索查询不能为空。", retryable=False
            )
        if _user_cancelled(stop_event):
            raise WebSearchError("web_search_cancelled", "已取消本轮联网搜索。")
        relative_deadline = monotonic() + timeout if timeout is not None else None
        if relative_deadline is not None:
            deadline = (
                relative_deadline
                if deadline is None
                else min(deadline, relative_deadline)
            )
        if deadline is not None and deadline <= monotonic():
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")
        try:
            with self._client.stream(
                "GET",
                DUCKDUCKGO_ENDPOINT,
                params={"q": query},
                follow_redirects=False,
                timeout=_remaining_timeout(deadline),
            ) as response:
                self._validate_response(response)
                body = _read_bounded(response, self._max_response_bytes)
        except httpx.TimeoutException as exc:
            raise WebSearchError(
                "web_search_timeout", "联网搜索超时，请重试。"
            ) from exc
        except httpx.ConnectError as exc:
            code = _connection_error_code(exc)
            message = (
                "无法解析公网搜索地址，请检查 DNS 或网络后重试。"
                if code == "web_search_dns"
                else "当前无法连接公网搜索，请检查网络后重试。"
            )
            raise WebSearchError(code, message) from exc
        except httpx.HTTPError as exc:
            raise WebSearchError(
                "web_search_offline", "当前无法连接公网搜索，请检查网络后重试。"
            ) from exc

        if _user_cancelled(stop_event):
            raise WebSearchError("web_search_cancelled", "已取消本轮联网搜索。")
        if deadline is not None and deadline <= monotonic():
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")

        try:
            results = _parse_results(body, max_results=self._max_results)
        except WebSearchError:
            raise
        except (ValueError, TypeError) as exc:
            raise WebSearchError(
                "web_search_parse", "搜索结果暂时无法解析，请重试。"
            ) from exc
        if not self._should_fetch_sources:
            return results
        return self._fetch_source_pages(
            results, deadline=deadline, stop_event=stop_event
        )

    def health_check(self) -> WebSearchHealth:
        """探测固定提供方；请求不包含任何用户查询。"""
        checked_at = datetime.now(UTC)
        try:
            with self._client.stream(
                "GET",
                DUCKDUCKGO_ENDPOINT,
                params={"q": "bridges-provider-health-check"},
                follow_redirects=False,
            ) as response:
                self._validate_response(response)
                _read_bounded(response, self._max_response_bytes)
        except WebSearchError as exc:
            return _health_error(checked_at, exc.code)
        except httpx.TimeoutException:
            return _health_error(checked_at, "web_search_timeout")
        except httpx.ConnectError as exc:
            return _health_error(checked_at, _connection_error_code(exc))
        except httpx.HTTPError:
            return _health_error(checked_at, "web_search_connect")
        return WebSearchHealth(
            provider="duckduckgo",
            status=WebSearchHealthStatus.READY,
            checked_at=checked_at,
        )

    def _validate_response(self, response: httpx.Response) -> None:
        """统一映射提供方状态，并在消费正文前执行响应上限。"""
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_response_bytes:
                    raise WebSearchError(
                        "web_search_response_too_large",
                        "公网搜索响应过大，已拒绝处理。",
                        retryable=False,
                    )
            except ValueError:
                pass
        if 300 <= response.status_code < 400:
            raise WebSearchError(
                "web_search_redirect",
                "公网搜索发生了不受控重定向，已拒绝处理。",
                retryable=False,
            )
        if response.status_code == 429:
            raise WebSearchError(
                "web_search_rate_limit", "公网搜索请求过于频繁，请稍后重试。"
            )
        if response.status_code in {401, 403}:
            raise WebSearchError(
                "web_search_permission",
                "当前网络未允许访问公网搜索，请检查网络权限后重试。",
                permission=True,
            )
        if response.status_code >= 500:
            raise WebSearchError(
                "web_search_provider",
                "公网搜索提供方暂时不可用，请稍后重试。",
            )
        if response.status_code >= 400:
            raise WebSearchError(
                "web_search_request", "公网搜索请求未完成，请重试。"
            )

    def _fetch_source(
        self,
        result: WebSearchResult,
        *,
        deadline: float | None = None,
        stop_event: Event | None = None,
    ) -> WebSearchResult:
        fetched_at = datetime.now(UTC)
        current_url = result.url
        redirect_count = 0
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    code = (
                        "web_search_cancelled"
                        if _user_cancelled(stop_event)
                        else "web_search_timeout"
                    )
                    message = (
                        "已取消本轮联网搜索。"
                        if code == "web_search_cancelled"
                        else "联网搜索超时，请重试。"
                    )
                    raise WebSearchError(code, message)
                if deadline is not None and deadline <= monotonic():
                    raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")
                unsafe_code = _unsafe_url_code(current_url)
                if unsafe_code is not None:
                    return result.model_copy(
                        update={
                            "verification": "fetch_failed",
                            "fetch_error_code": unsafe_code,
                            "fetched_at": fetched_at,
                            "redirect_count": redirect_count,
                        }
                    )
                with self._client.stream(
                    "GET",
                    current_url,
                    follow_redirects=False,
                    timeout=_remaining_timeout(deadline),
                ) as response:
                    if 300 <= response.status_code < 400:
                        if redirect_count >= self._max_redirects:
                            return result.model_copy(
                                update={
                                    "verification": "fetch_failed",
                                    "fetch_error_code": "web_search_redirect",
                                    "fetched_at": fetched_at,
                                    "redirect_count": redirect_count,
                                }
                            )
                        location = response.headers.get("location")
                        if not location:
                            return result.model_copy(
                                update={
                                    "verification": "fetch_failed",
                                    "fetch_error_code": "web_search_redirect",
                                    "fetched_at": fetched_at,
                                    "redirect_count": redirect_count,
                                }
                            )
                        next_url = urljoin(current_url, location)
                        if _unsafe_url_code(next_url) is not None:
                            return result.model_copy(
                                update={
                                    "verification": "fetch_failed",
                                    "fetch_error_code": "web_search_unsafe_url",
                                    "fetched_at": fetched_at,
                                    "redirect_count": redirect_count + 1,
                                }
                            )
                        current_url = next_url
                        redirect_count += 1
                        continue
                    self._validate_response(response)
                    body = _read_bounded(response, self._max_response_bytes)
                    break
        except WebSearchError as exc:
            return result.model_copy(
                update={
                    "verification": "fetch_failed",
                    "fetch_error_code": exc.code,
                    "fetched_at": fetched_at,
                    "redirect_count": redirect_count,
                }
            )
        except httpx.TimeoutException:
            return result.model_copy(
                update={
                    "verification": "fetch_failed",
                    "fetch_error_code": "web_search_page_timeout",
                    "fetched_at": fetched_at,
                    "redirect_count": redirect_count,
                }
            )
        except httpx.ConnectError as exc:
            return result.model_copy(
                update={
                    "verification": "fetch_failed",
                    "fetch_error_code": (
                        "web_search_page_dns"
                        if _connection_error_code(exc) == "web_search_dns"
                        else "web_search_page_connect"
                    ),
                    "fetched_at": fetched_at,
                    "redirect_count": redirect_count,
                }
            )
        except httpx.HTTPError:
            return result.model_copy(
                update={
                    "verification": "fetch_failed",
                    "fetch_error_code": "web_search_page_fetch",
                    "fetched_at": fetched_at,
                    "redirect_count": redirect_count,
                }
            )

        text = _extract_page_text(body)
        if not text:
            return result.model_copy(
                update={
                    "verification": "summary_only",
                    "fetch_error_code": "web_search_page_empty",
                    "fetched_at": fetched_at,
                    "redirect_count": redirect_count,
                }
            )
        return result.model_copy(
            update={
                "verification": "verified",
                "fetched_at": fetched_at,
                "content_summary": text[:_SOURCE_SUMMARY_MAX_CHARS],
                "published_at": _parse_published_at(body),
                "redirect_count": redirect_count,
            }
        )

    def _fetch_source_pages(
        self,
        results: list[WebSearchResult],
        *,
        deadline: float | None,
        stop_event: Event | None = None,
    ) -> list[WebSearchResult]:
        """用有界线程池并行回抓结果页面，且不等待超出阶段预算的任务。"""

        if not results:
            return []
        executor = ThreadPoolExecutor(
            max_workers=min(len(results), DEFAULT_MAX_RESULTS),
            thread_name_prefix="web-source-fetch",
        )
        futures = {
            executor.submit(
                self._fetch_source,
                result,
                deadline=deadline,
                stop_event=stop_event,
            ): result
            for result in results
        }
        pending = set(futures)
        done: set[object] = set()
        try:
            while pending:
                if stop_event is not None and stop_event.is_set():
                    break
                remaining = (
                    None if deadline is None else deadline - monotonic()
                )
                if remaining is not None and remaining <= 0:
                    break
                completed, _ = wait(
                    pending,
                    timeout=min(0.05, remaining) if remaining is not None else 0.05,
                )
                done.update(completed)
                pending.difference_update(completed)
            fetched: list[WebSearchResult] = []
            for future, result in futures.items():
                if future not in done:
                    fetched.append(
                        result.model_copy(
                            update={
                                "verification": "fetch_failed",
                                "fetch_error_code": "web_search_page_timeout",
                                "fetched_at": datetime.now(UTC),
                            }
                        )
                    )
                    continue
                try:
                    fetched.append(future.result())
                except Exception:  # noqa: BLE001 - 单个来源失败不拖垮整轮
                    fetched.append(
                        result.model_copy(
                            update={
                                "verification": "fetch_failed",
                                "fetch_error_code": "web_search_page_fetch",
                                "fetched_at": datetime.now(UTC),
                            }
                        )
                    )
            return fetched
        finally:
            for future in pending:
                future.cancel()
            if pending:
                completed, _ = wait(pending, timeout=0.5)
                pending.difference_update(completed)
            executor.shutdown(wait=not pending, cancel_futures=True)


def _health_error(checked_at: datetime, code: str) -> WebSearchHealth:
    status = {
        "web_search_dns": WebSearchHealthStatus.DNS_ERROR,
        "web_search_connect": WebSearchHealthStatus.CONNECT_ERROR,
        "web_search_offline": WebSearchHealthStatus.CONNECT_ERROR,
        "web_search_permission": WebSearchHealthStatus.AUTH_ERROR,
        "web_search_rate_limit": WebSearchHealthStatus.RATE_LIMITED,
    }.get(code, WebSearchHealthStatus.UPSTREAM_ERROR)
    return WebSearchHealth(
        provider="duckduckgo",
        status=status,
        checked_at=checked_at,
        error_code=code,
    )


def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    """以流式方式读取响应，超过正文上限时立即中止。"""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise WebSearchError(
                "web_search_response_too_large",
                "公网搜索响应过大，已拒绝处理。",
                retryable=False,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _connection_error_code(exc: httpx.ConnectError) -> str:
    message = str(exc).lower()
    if "offline" in message:
        return "web_search_offline"
    if any(marker in message for marker in ("dns", "name or service", "getaddrinfo", "nodename")):
        return "web_search_dns"
    return "web_search_connect"


def _unsafe_url_code(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "web_search_unsafe_url"
    if parsed.username is not None or parsed.password is not None:
        return "web_search_unsafe_url"
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "web_search_unsafe_url"
    if not hostname or port not in {None, 80, 443}:
        return "web_search_unsafe_url"
    normalized = hostname.lower().rstrip(".")
    if normalized in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
        return "web_search_private_address"
    if normalized.endswith((".local", ".internal", ".home.arpa")):
        return "web_search_private_address"
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return None
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    ):
        return "web_search_private_address"
    return None


class _DuckDuckGoResultParser(HTMLParser):
    """只提取 DDG 结果链接、标题和摘要，不执行页面中的任何内容。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._active_title: list[str] | None = None
        self._active_snippet: list[str] | None = None
        self._active_result_index: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if "result__a" in classes:
            href = attributes.get("href") or ""
            self.results.append({"href": href, "title": "", "snippet": ""})
            self._active_result_index = len(self.results) - 1
            self._active_title = []
        elif "result__snippet" in classes and self.results:
            self._active_result_index = len(self.results) - 1
            self._active_snippet = []

    def handle_data(self, data: str) -> None:
        if self._active_title is not None:
            self._active_title.append(data)
        if self._active_snippet is not None:
            self._active_snippet.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._active_result_index is None:
            return
        result = self.results[self._active_result_index]
        if self._active_title is not None:
            result["title"] = "".join(self._active_title)
            self._active_title = None
        if self._active_snippet is not None:
            result["snippet"] = "".join(self._active_snippet)
            self._active_snippet = None


def _parse_results(
    body: bytes, *, max_results: int = DEFAULT_MAX_RESULTS
) -> list[WebSearchResult]:
    """解析 DDG HTML 真实链接，拒绝无真实 URL 或危险目标的摘要。"""
    text = body.decode("utf-8", errors="replace")
    if "<html" not in text.lower():
        raise WebSearchError("web_search_parse", "搜索结果暂时无法解析，请重试。")
    parser = _DuckDuckGoResultParser()
    try:
        parser.feed(text)
        parser.close()
    except (ValueError, TypeError) as exc:
        raise WebSearchError("web_search_parse", "搜索结果暂时无法解析，请重试。") from exc

    accessed_at = datetime.now(UTC)
    parsed: list[WebSearchResult] = []
    seen_urls: set[str] = set()
    for item in parser.results:
        url = _resolve_search_result_url(item["href"])
        if (
            not isinstance(url, str)
            or _unsafe_url_code(url) is not None
            or url in seen_urls
        ):
            continue
        title = html.unescape(item["title"]).strip() or "公开网页"
        snippet = html.unescape(item["snippet"]).strip()
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if not host:
            continue
        seen_urls.add(url)
        parsed.append(
            WebSearchResult(
                result_id=f"web-{len(parsed) + 1}",
                title=title.strip() or "公开网页",
                site=host,
                url=url,
                snippet=snippet.strip(),
                accessed_at=accessed_at,
            )
        )
    parsed.sort(key=lambda result: (-_authority_rank(result.site), result.site, result.url))
    return [
        result.model_copy(update={"result_id": f"web-{index}"})
        for index, result in enumerate(parsed[:max_results], 1)
    ]


def _resolve_search_result_url(value: str) -> str:
    """把 DDG 的跳转链接还原为结果真实 URL。"""

    raw = html.unescape(value).strip()
    if raw.startswith("/"):
        raw = urljoin("https://duckduckgo.com", raw)
    parsed = urlparse(raw)
    hostname = parsed.hostname or ""
    if (
        (hostname == "duckduckgo.com" or hostname.endswith(".duckduckgo.com"))
        and parsed.path == "/l/"
    ):
        encoded = parse_qs(parsed.query).get("uddg", [""])[0]
        if encoded:
            return unquote(encoded)
    return raw


def _authority_rank(site: str) -> int:
    normalized = site.lower().removeprefix("www.")
    if normalized.endswith(".gov") or normalized.endswith(".gov.cn"):
        return 4
    if normalized.endswith(".edu") or normalized.endswith(".edu.cn"):
        return 3
    if normalized in {"who.int", "cdc.gov", "nature.com", "science.org"}:
        return 3
    return 1


def _extract_page_text(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _parse_published_at(body: bytes) -> datetime | None:
    text = body.decode("utf-8", errors="replace")
    match = _PUBLISHED_AT_RE.search(text)
    if match is None:
        return None
    value = match.group(1).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _remaining_timeout(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.001, deadline - monotonic())


def _user_cancelled(stop_event: Event | None) -> bool:
    """区分用户取消与来源截止信号。"""
    if stop_event is None:
        return False
    marker = getattr(stop_event, "user_is_set", None)
    return bool(marker()) if callable(marker) else stop_event.is_set()
