"""Tieba-side HTTP request accounting and bounded same-source redirects."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from bridges.public_search_budget import report_request_outcome, track_request

MAX_SOURCE_REDIRECTS = 3


class TiebaHttpRequestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def tracked_get(
    client: httpx.Client,
    url: str,
    *,
    purpose: str,
    timeout: float,
    allowed_url: Callable[[str], bool],
    params: Mapping[str, str | int] | None = None,
    deadline: float | None = None,
    max_bytes: int | None = None,
) -> httpx.Response:
    """Register each HTTP GET, checking the run guard before every redirect hop."""
    if not _is_allowed_url(url, allowed_url):
        raise TiebaHttpRequestError(
            "tieba_external_domain_rejected", "请求链接不属于允许的来源域名。"
        )

    current_url = url
    current_params = params
    for redirect_count in range(MAX_SOURCE_REDIRECTS + 1):
        request_timeout = timeout
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise httpx.TimeoutException("request deadline exceeded")
            request_timeout = min(timeout, remaining)
        with track_request(purpose) as denial:
            if denial is not None:
                message = (
                    "本轮运行预算已用尽，未发起新的外部请求。"
                    if denial == "run_budget_exhausted"
                    else "账户正在删除，未发起新的外部请求。"
                    if denial == "tieba_browser_account_deleting"
                    else "任务已停止或发生变化，未发起新的外部请求。"
                )
                raise TiebaHttpRequestError(denial, message)
            try:
                with client.stream(
                    "GET",
                    current_url,
                    params=current_params,
                    timeout=request_timeout,
                    follow_redirects=False,
                ) as streamed:
                    if deadline is not None and monotonic() >= deadline:
                        raise httpx.TimeoutException(
                            "request deadline exceeded", request=streamed.request
                        )
                    body = b""
                    if not streamed.is_redirect:
                        body = _read_stream(streamed, deadline=deadline, max_bytes=max_bytes)
                    response = httpx.Response(
                        streamed.status_code,
                        headers=streamed.headers,
                        content=body,
                        request=streamed.request,
                    )
            except httpx.RemoteProtocolError as exc:
                if "Invalid URL in location header:" not in str(exc):
                    raise
                raise TiebaHttpRequestError(
                    "tieba_external_redirect_rejected",
                    "页面跳转地址无效，未继续访问。",
                ) from exc
            if deadline is not None and monotonic() >= deadline:
                raise httpx.TimeoutException(
                    "request deadline exceeded", request=response.request
                )
            report_request_outcome(f"http_{response.status_code}")
        current_params = None
        if not response.is_redirect:
            return response

        location = response.headers.get("location")
        if not location:
            return response
        try:
            target = urljoin(str(response.url), location)
        except ValueError as exc:
            raise TiebaHttpRequestError(
                "tieba_external_redirect_rejected",
                "页面跳转地址无效，未继续访问。",
            ) from exc
        if not _is_allowed_url(target, allowed_url):
            raise TiebaHttpRequestError(
                "tieba_external_redirect_rejected",
                "页面跳转到未允许的域名，未继续访问。",
            )
        if redirect_count >= MAX_SOURCE_REDIRECTS:
            raise TiebaHttpRequestError(
                "tieba_external_redirect_limit", "页面跳转次数超过上限，未继续访问。"
            )
        current_url = _preserve_query_params(target, params)

    raise TiebaHttpRequestError(
        "tieba_external_redirect_limit", "页面跳转次数超过上限，未继续访问。"
    )


def _read_stream(
    response: httpx.Response,
    *,
    deadline: float | None,
    max_bytes: int | None,
) -> bytes:
    limit = max(0, int(max_bytes)) if max_bytes is not None else None
    if limit == 0:
        return b""
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        if deadline is not None and monotonic() >= deadline:
            raise httpx.TimeoutException(
                "request deadline exceeded", request=response.request
            )
        if not chunk:
            continue
        if limit is not None:
            chunk = chunk[: max(0, limit - size)]
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if limit is not None and size >= limit:
            break
    return b"".join(chunks)


def _is_allowed_url(url: str, allowed_url: Callable[[str], bool]) -> bool:
    try:
        return allowed_url(url)
    except ValueError:
        return False


def _preserve_query_params(
    url: str, params: Mapping[str, str | int] | None
) -> str:
    if not params:
        return url
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    existing_keys = {key for key, _ in query}
    query.extend(
        (key, str(value)) for key, value in params.items() if key not in existing_keys
    )
    return urlunsplit(parts._replace(query=urlencode(query)))
