"""认证状态机：匿名不靠公开 DOM 判定，登录后复验认证并自动续接。"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from playwright.sync_api import Error as PlaywrightError

from bridges.tieba.browser_auth import (
    AUTH_PROBE_URL,
    AuthSession,
    AuthState,
    probe_auth,
)
from bridges.tieba.browser_dom import SEARCH_DOM, THREAD_DOM
from bridges.tieba.browser_worker import _error_payload, search
from bridges.tieba.delivery import _auth_wait_question


class _Response:
    ok = True
    status = 200


class _Locator:
    def __init__(self, owner: _Page, selector: str) -> None:
        self._owner = owner
        self._selector = selector

    def count(self) -> int:
        if self._selector == ".login-guide-placeholder":
            return 1 if self._owner.login_guide else 0
        if self._selector == "#captcha, .pass-sms-wrap":
            return 1 if self._owner.captcha else 0
        if self._selector == ".main-content .forum-name, .tbm-post-thread, .tbm-floor-thread":
            return 1 if self._owner.public_content else 0
        return 0

    def inner_text(self) -> str:
        return self._owner.body_text

    def first(self) -> _Locator:
        return self


class _Page:
    def __init__(self, site: _Site) -> None:
        self.site = site
        self.url = "about:blank"
        self.body_text = ""
        self.title_text = "百度贴吧"
        self.closed = False
        self.login_guide = False
        self.captcha = False
        self.public_content = False
        self.on_login_page = False

    def goto(self, url: str, **_: Any) -> _Response:
        return self.site.goto(self, url)

    def is_closed(self) -> bool:
        return self.closed

    def title(self) -> str:
        return self.title_text

    def locator(self, selector: str) -> _Locator:
        return _Locator(self, selector)

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.site.wait(self, milliseconds)

    def wait_for_function(self, *_: Any, **__: Any) -> None:
        return None

    def evaluate(self, script: str, arg: Any = None) -> Any:
        return self.site.evaluate(self, script, arg)


class _Site:
    def __init__(
        self,
        *,
        is_login: int = 0,
        public_hits: int = 1,
        probe_raises: bool = False,
        login_succeeds: bool = True,
        close_on_login_wait: bool = False,
        login_guide_on_search: bool = False,
        captcha_first_search: bool = False,
    ) -> None:
        self.is_login = is_login
        self.public_hits = public_hits
        self.probe_raises = probe_raises
        self.login_succeeds = login_succeeds
        self.close_on_login_wait = close_on_login_wait
        self.login_guide_on_search = login_guide_on_search
        self.captcha_first_search = captcha_first_search
        self.login_pages = 0
        self.probe_states: list[int] = []
        self.search_visits = 0
        self.page = _Page(self)

    def goto(self, page: _Page, url: str) -> _Response:
        if url == AUTH_PROBE_URL:
            if self.probe_raises:
                raise PlaywrightError("probe network failure")
            page.url = url
            page.body_text = json.dumps({"tbs": "test-tbs", "is_login": self.is_login})
            page.public_content = False
            self.probe_states.append(self.is_login)
            return _Response()
        if url.startswith("https://passport.baidu.com/v2/?"):
            self.login_pages += 1
            page.url = "https://passport.baidu.com/v2/?login=&tpl=tb"
            page.body_text = "百度登录"
            page.on_login_page = True
            page.public_content = False
            return _Response()
        if "/f/search/res" in url:
            self.search_visits += 1
            page.url = url
            page.body_text = "搜索结果"
            page.public_content = True
            page.login_guide = self.login_guide_on_search
            page.captcha = self.captcha_first_search and self.search_visits == 1
            page.title_text = "百度安全验证" if page.captcha else "搜索-百度贴吧"
            return _Response()
        if "/p/" in url:
            page.url = url
            page.body_text = "帖子正文"
            page.public_content = True
            page.login_guide = False
            page.captcha = False
            page.title_text = "宿舍讨论 - 百度贴吧"
            return _Response()
        raise AssertionError(f"unexpected url: {url}")

    def wait(self, page: _Page, _milliseconds: int) -> None:
        if page.on_login_page:
            if self.close_on_login_wait:
                page.closed = True
                return
            if self.login_succeeds:
                self.is_login = 1
                page.on_login_page = False
                page.url = "https://tieba.baidu.com/"
        if page.captcha:
            page.captcha = False
            page.title_text = "搜索-百度贴吧"

    def evaluate(self, page: _Page, script: str, arg: Any = None) -> Any:
        if script == SEARCH_DOM:
            if arg == "1":
                return [
                    {
                        "url": f"https://tieba.baidu.com/p/{index}",
                        "title": f"宿舍讨论 {index}",
                        "snippet": "",
                    }
                    for index in range(self.public_hits)
                ]
            return []
        if script == THREAD_DOM:
            return {
                "forum": "华东交通大学吧",
                "title": "宿舍讨论",
                "original": "宿舍热水稳定吗",
                "replies": [{"content": "晚上比较稳定", "floor": 2}],
            }
        return None


def _session(site: _Site) -> tuple[AuthSession, _Page, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    auth = AuthSession(
        closed=threading.Event(),
        deadline=time.monotonic() + 60,
        emit=events.append,
    )
    return auth, site.page, events


def test_public_search_dom_does_not_prove_login_and_opens_one_login() -> None:
    site = _Site(is_login=0, public_hits=20)
    auth, page, events = _session(site)

    page = auth.ensure(page)
    hits = search(page, "宿舍", threading.Event(), auth.deadline, auth)

    assert len(hits) == 20
    assert site.login_pages == 1
    assert site.probe_states == [0, 1]
    assert [(item["auth_wait"], item["reason"]) for item in events] == [
        ("started", "login"),
        ("finished", "login"),
    ]
    assert events[-1]["outcome"] == "authenticated"


def test_authenticated_session_skips_login_and_collects() -> None:
    site = _Site(is_login=1, public_hits=3)
    auth, page, events = _session(site)

    page = auth.ensure(page)
    hits = search(page, "宿舍", threading.Event(), auth.deadline, auth)

    assert len(hits) == 3
    assert site.login_pages == 0
    assert events == []


def test_auth_probe_unknown_is_not_treated_as_anonymous() -> None:
    site = _Site(is_login=0, probe_raises=True)
    auth, page, events = _session(site)

    with pytest.raises(PermissionError) as failure:
        auth.ensure(page)

    assert str(failure.value) == "tieba_browser_auth_status_unknown"
    assert site.login_pages == 0
    assert events == []
    payload = _error_payload(failure.value)
    assert payload["error"] == "tieba_browser_auth_status_unknown"
    assert "没有打开登录页" in payload["message"]


def test_closed_login_window_keeps_task_with_paired_interruption_event() -> None:
    site = _Site(is_login=0, close_on_login_wait=True)
    auth, page, events = _session(site)

    with pytest.raises(PermissionError) as failure:
        auth.ensure(page)

    assert str(failure.value) == "tieba_browser_auth_interrupted"
    assert site.login_pages == 1
    assert [item["auth_wait"] for item in events] == ["started", "finished"]
    assert events[-1]["outcome"] == "interrupted"


def test_second_login_request_in_the_same_action_does_not_open_again() -> None:
    site = _Site(is_login=0, login_guide_on_search=True)
    auth, page, events = _session(site)
    page = auth.ensure(page)
    site.is_login = 0

    with pytest.raises(PermissionError) as failure:
        search(page, "宿舍", threading.Event(), auth.deadline, auth)

    assert str(failure.value) == "tieba_browser_auth_interrupted"
    assert site.login_pages == 1
    assert [item["auth_wait"] for item in events] == ["started", "finished"]
    assert events[-1]["outcome"] == "authenticated"


def test_authenticated_verification_opens_stays_and_resumes_collection() -> None:
    site = _Site(is_login=1, captcha_first_search=True)
    auth, page, events = _session(site)

    page = auth.ensure(page)
    hits = search(page, "宿舍", threading.Event(), auth.deadline, auth)

    assert len(hits) == 1
    assert site.login_pages == 0
    assert site.search_visits == 3
    assert [(item["auth_wait"], item["reason"]) for item in events] == [
        ("started", "verification"),
        ("finished", "verification"),
    ]
    assert events[-1]["outcome"] == "verification_cleared"


def test_probe_auth_reads_is_login_field_only() -> None:
    site = _Site(is_login=0)
    page = site.page
    assert probe_auth(page, closed=threading.Event(), deadline=None) is AuthState.ANONYMOUS
    site.is_login = 1
    assert probe_auth(page, closed=threading.Event(), deadline=None) is AuthState.AUTHENTICATED


def test_probe_auth_rejects_malformed_payload() -> None:
    site = _Site(is_login=0)
    page = site.page

    class Malformed(_Page):
        def goto(self, url: str, **_: Any) -> _Response:
            page.url = url
            page.body_text = "not-json"
            return _Response()

    malformed = Malformed(site)
    assert probe_auth(malformed, closed=threading.Event(), deadline=None) is AuthState.UNKNOWN


def test_auth_wait_notifies_worker_deadline_extension() -> None:
    site = _Site(is_login=0)
    initial = time.monotonic() + 30
    updates: list[float | None] = []
    auth = AuthSession(
        closed=threading.Event(),
        deadline=initial,
        emit=lambda _value: None,
        deadline_changed=updates.append,
    )

    auth.ensure(site.page)

    assert updates
    assert updates[-1] is not None and updates[-1] >= initial


def test_auth_wait_question_keeps_verification_reason_and_recovery() -> None:
    message = (
        "贴吧安全验证等待已中断；任务已保留，请在聊天中说“继续”重试。"
    )
    projection = SimpleNamespace(
        queries=[
            SimpleNamespace(
                error_code="tieba_browser_auth_interrupted",
                error_message=message,
            )
        ],
        confirmed_posts=[],
        candidate_links=[],
    )

    question = _auth_wait_question(projection)  # type: ignore[arg-type]

    assert "安全验证" in question
    assert "继续" in question
