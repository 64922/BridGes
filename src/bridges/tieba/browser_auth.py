"""认证探测、官方登录等待与安全验证复验。

认证只由百度贴吧服务端返回的 ``is_login`` 字段确认；会话目录、Cookie、
公开页面 DOM 或 tbs 是否存在都不能单独证明已登录。认证等待与采集可用性
分开判断：登录成功后仍要由实际检索/读取结果确认采集路径可用。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from enum import StrEnum
from threading import Event
from typing import Any
from urllib.parse import urlencode, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as BrowserTimeoutError

AUTH_PROBE_URL = "https://tieba.baidu.com/dc/common/tbs"

TIEBA_HOME_URL = "https://tieba.baidu.com/"

AUTH_WAIT_LIMIT_SECONDS = 600.0

AUTH_PROBE_TIMEOUT_MS = 12_000

AUTH_POLL_INTERVAL_MS = 400

LOGIN_PAGE_TIMEOUT_MS = 18_000

AUTH_REQUIRED = "tieba_browser_auth_required"

AUTH_INTERRUPTED = "tieba_browser_auth_interrupted"

AUTH_STATUS_UNKNOWN = "tieba_browser_auth_status_unknown"

COLLECTION_RESTRICTED = "tieba_browser_collection_restricted"

AUTH_ERROR_CODES = frozenset(
    {
        AUTH_REQUIRED,
        AUTH_INTERRUPTED,
        AUTH_STATUS_UNKNOWN,
        COLLECTION_RESTRICTED,
        "tieba_browser_auth_unverified",
    }
)


class AuthState(StrEnum):
    """专用会话的真实认证状态；未知不能被当作未登录。"""

    AUTHENTICATED = "authenticated"
    ANONYMOUS = "anonymous"
    UNKNOWN = "unknown"


class AuthWaitError(PermissionError):
    """带等待原因的认证错误，供公开投影给出真实恢复方式。"""

    def __init__(self, code: str, *, reason: str | None = None) -> None:
        super().__init__(code)
        self.reason = reason


def login_url(return_url: str) -> str:
    return "https://passport.baidu.com/v2/?" + urlencode(
        {"login": "", "tpl": "tb", "u": return_url}
    )


def request_timeout_ms(closed: Event, deadline: float | None, maximum_ms: int) -> int:
    if closed.is_set():
        raise InterruptedError("tieba_browser_cancelled")
    if deadline is None:
        return maximum_ms
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    if remaining_ms <= 0:
        raise BrowserTimeoutError("贴吧浏览器运行预算已耗尽。")
    return min(maximum_ms, remaining_ms)


def auth_required(page: Page) -> str | None:
    """页面自身是否显示登录或安全验证入口（只用于分类，不证明登录）。"""
    if "百度安全验证" in page.title() or page.locator("#captcha, .pass-sms-wrap").count():
        return "verification"
    if page.locator(".login-guide-placeholder").count():
        return "login"
    return None


def probe_auth(
    page: Page,
    *,
    closed: Event,
    deadline: float | None,
) -> AuthState:
    """用服务端 ``is_login`` 判断认证；网络或结构异常返回未知。"""
    if closed.is_set():
        raise InterruptedError("tieba_browser_cancelled")
    timeout_ms = request_timeout_ms(closed, deadline, AUTH_PROBE_TIMEOUT_MS)
    try:
        response = page.goto(
            AUTH_PROBE_URL,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
    except BrowserTimeoutError:
        return AuthState.UNKNOWN
    except PlaywrightError:
        return AuthState.UNKNOWN
    if response is not None and not getattr(response, "ok", True):
        return AuthState.UNKNOWN
    try:
        payload = json.loads(page.locator("body").inner_text().strip())
    except (PlaywrightError, ValueError, TypeError):
        return AuthState.UNKNOWN
    if not isinstance(payload, dict):
        return AuthState.UNKNOWN
    value = payload.get("is_login")
    if value is True or value == 1:
        return AuthState.AUTHENTICATED
    if value is False or value == 0:
        return AuthState.ANONYMOUS
    return AuthState.UNKNOWN


class AuthSession:
    """一次浏览器动作内的认证状态机；同一等待只打开一个入口。"""

    def __init__(
        self,
        *,
        closed: Event,
        deadline: float | None,
        emit: Callable[[dict[str, Any]], None],
        deadline_changed: Callable[[float | None], None] | None = None,
    ) -> None:
        self._closed = closed
        self._deadline = deadline
        self._emit = emit
        self._deadline_changed = deadline_changed
        self._wait_started: float | None = None
        self._wait_reason: str | None = None
        self._login_waits = 0
        self._verification_waits = 0

    @property
    def deadline(self) -> float | None:
        return self._deadline

    def ensure(self, page: Page) -> Page:
        """动作开始前确认认证；匿名时自动打开官方登录页并等待。"""
        state = probe_auth(page, closed=self._closed, deadline=self._deadline)
        if state is AuthState.AUTHENTICATED:
            return page
        if state is AuthState.UNKNOWN:
            raise AuthWaitError(AUTH_STATUS_UNKNOWN, reason="probe")
        return self._login(page, TIEBA_HOME_URL)

    def recover(self, page: Page, *, reason: str, target_url: str) -> Page:
        """页面上出现登录/验证入口后恢复；已认证时不再打开登录页。"""
        if reason == "verification":
            return self._verify(page, target_url)
        state = probe_auth(page, closed=self._closed, deadline=self._deadline)
        if state is AuthState.AUTHENTICATED:
            raise AuthWaitError(COLLECTION_RESTRICTED, reason="collection")
        if state is AuthState.UNKNOWN:
            raise AuthWaitError(AUTH_STATUS_UNKNOWN, reason="probe")
        return self._login(page, target_url)

    def _login(self, page: Page, return_url: str) -> Page:
        if self._login_waits >= 1:
            raise AuthWaitError(AUTH_INTERRUPTED, reason="login")
        self._login_waits += 1
        self._begin_wait("login")
        outcome = "interrupted"
        until = time.monotonic() + AUTH_WAIT_LIMIT_SECONDS
        try:
            page.goto(
                login_url(return_url),
                wait_until="domcontentloaded",
                timeout=request_timeout_ms(
                    self._closed, self._deadline, LOGIN_PAGE_TIMEOUT_MS
                ),
            )
            while True:
                if self._closed.is_set():
                    raise InterruptedError("tieba_browser_cancelled")
                if page.is_closed():
                    raise AuthWaitError(AUTH_INTERRUPTED, reason="login")
                if time.monotonic() >= until:
                    raise AuthWaitError(AUTH_INTERRUPTED, reason="login")
                if urlparse(page.url).hostname == "tieba.baidu.com":
                    state = probe_auth(
                        page, closed=self._closed, deadline=self._deadline
                    )
                    if state is AuthState.AUTHENTICATED:
                        outcome = "authenticated"
                        return page
                    if state is AuthState.UNKNOWN:
                        outcome = "status_unknown"
                        raise AuthWaitError(AUTH_STATUS_UNKNOWN, reason="probe")
                    page.goto(
                        login_url(return_url),
                        wait_until="domcontentloaded",
                        timeout=request_timeout_ms(
                            self._closed, self._deadline, LOGIN_PAGE_TIMEOUT_MS
                        ),
                    )
                page.wait_for_timeout(AUTH_POLL_INTERVAL_MS)
        except InterruptedError:
            outcome = "cancelled"
            raise
        except AuthWaitError:
            raise
        except PlaywrightError:
            raise AuthWaitError(AUTH_INTERRUPTED, reason="login") from None
        finally:
            self._finish_wait(outcome)

    def _verify(self, page: Page, target_url: str) -> Page:
        if self._verification_waits >= 1:
            raise AuthWaitError(AUTH_INTERRUPTED, reason="verification")
        self._verification_waits += 1
        self._begin_wait("verification")
        outcome = "interrupted"
        until = time.monotonic() + AUTH_WAIT_LIMIT_SECONDS
        try:
            while True:
                if self._closed.is_set():
                    raise InterruptedError("tieba_browser_cancelled")
                if page.is_closed():
                    raise AuthWaitError(AUTH_INTERRUPTED, reason="verification")
                if time.monotonic() >= until:
                    raise AuthWaitError(AUTH_INTERRUPTED, reason="verification")
                if (
                    auth_required(page) is None
                    and urlparse(page.url).hostname == "tieba.baidu.com"
                ):
                    state = probe_auth(
                        page, closed=self._closed, deadline=self._deadline
                    )
                    if state is AuthState.AUTHENTICATED:
                        outcome = "verification_cleared"
                        return page
                    if state is AuthState.ANONYMOUS:
                        outcome = "anonymous"
                        self._finish_wait(outcome)
                        return self._login(page, target_url)
                    outcome = "status_unknown"
                    raise AuthWaitError(AUTH_STATUS_UNKNOWN, reason="probe")
                page.wait_for_timeout(AUTH_POLL_INTERVAL_MS)
        except InterruptedError:
            outcome = "cancelled"
            raise
        except AuthWaitError:
            raise
        except PlaywrightError:
            raise AuthWaitError(AUTH_INTERRUPTED, reason="verification") from None
        finally:
            self._finish_wait(outcome)

    def _begin_wait(self, reason: str) -> None:
        if self._wait_started is not None:
            return
        self._wait_started = time.monotonic()
        self._wait_reason = reason
        self._emit({"auth_wait": "started", "reason": reason})

    def _finish_wait(self, outcome: str) -> None:
        if self._wait_started is None:
            return
        waited = max(0.0, time.monotonic() - self._wait_started)
        if self._deadline is not None:
            self._deadline += waited
            if self._deadline_changed is not None:
                self._deadline_changed(self._deadline)
        self._emit(
            {
                "auth_wait": "finished",
                "reason": self._wait_reason,
                "outcome": outcome,
            }
        )
        self._wait_started = None
        self._wait_reason = None


__all__ = [
    "AUTH_ERROR_CODES",
    "AUTH_INTERRUPTED",
    "AUTH_POLL_INTERVAL_MS",
    "AUTH_PROBE_TIMEOUT_MS",
    "AUTH_PROBE_URL",
    "AUTH_REQUIRED",
    "AUTH_STATUS_UNKNOWN",
    "AUTH_WAIT_LIMIT_SECONDS",
    "AuthSession",
    "AuthState",
    "AuthWaitError",
    "COLLECTION_RESTRICTED",
    "LOGIN_PAGE_TIMEOUT_MS",
    "TIEBA_HOME_URL",
    "auth_required",
    "login_url",
    "probe_auth",
    "request_timeout_ms",
]
