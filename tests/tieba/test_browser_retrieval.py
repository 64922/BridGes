"""专用浏览器检索、实际读取范围和工作进程生命周期回归。"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from bridges.runtime.lock import DataDirectoryLock
from bridges.tieba.browser import BrowserTiebaThreadReader, browser_terms, read_projection
from bridges.tieba.browser_runtime import (
    ACCOUNT_DELETION_MARKER,
    TiebaBrowserError,
    TiebaBrowserRuntime,
    _BrowserWorker,
    fence_account_profile,
    profile_path,
)
from bridges.tieba.browser_worker import wait_for_thread_page_or_auth


def test_profiles_are_account_isolated_and_paths_cannot_escape(tmp_path: Path) -> None:
    first = profile_path(tmp_path, "../alice")
    assert first.parent == tmp_path / "tieba-browser"
    assert first != profile_path(tmp_path, "bob")
    assert first == profile_path(tmp_path, "../alice")


def test_native_search_uses_topic_words_and_fixed_forum_scope() -> None:
    assert browser_terms("site:tieba.baidu.com/p/ 华东交通大学吧 国庆节 假期") == "国庆 假期"
    with pytest.raises(ValueError):
        browser_terms("site:douyin.com 华东交通大学 国庆")


def test_rendered_replies_are_partial_and_never_invent_floors() -> None:
    result = read_projection(
        "https://tieba.baidu.com/p/11081457230",
        {
            "forum": "华东交通大学吧",
            "title": "假来～",
            "original": "怎么国庆只剩两天了",
            "posted_at": "10-06",
            "replies": [{"content": "我不要上早八", "posted_at": "10-06"}],
        },
    )
    assert result.forum_matches_target
    assert result.status.value == "partial" and result.total_pages is None
    assert all(reply.floor is None for reply in result.replies)
    assert result.replies[0].is_original_poster
    assert "未穷尽" in (result.error_message or "")
    assert result.pages_read == result.pages_limit == 1
    assert result.scroll_rounds == 0 and result.scroll_rounds_limit == 0
    assert result.replies[1].posted_at == "10-06"


def test_read_projection_preserves_reply_identity_and_nested_parent() -> None:
    result = read_projection(
        "https://tieba.baidu.com/p/123",
        {
            "forum": "华东交通大学吧",
            "original": "主题正文",
            "loaded_reply_count": 2,
            "total_replies": 20,
            "replies": [
                {
                    "content": "楼层回复",
                    "reply_id": "floor-1",
                    "floor": 3,
                    "posted_at": "2026-09-01",
                },
                {
                    "content": "楼中楼补充",
                    "reply_id": "sub-2",
                    "parent_reply_id": "floor-1",
                    "floor": 3,
                    "posted_at": "2026-09-02",
                    "nested": True,
                },
            ],
        },
    )
    assert result.status.value == "partial"
    assert result.total_replies == 20
    assert result.replies[2].reply_id == "sub-2"
    assert result.replies[2].parent_reply_id == "floor-1"


def test_empty_thread_is_the_only_browser_read_marked_complete() -> None:
    result = read_projection(
        "https://tieba.baidu.com/p/123",
        {
            "forum": "华东交通大学吧",
            "loaded_reply_count": 0,
            "total_replies": 0,
            "scroll_rounds": 3,
            "scroll_rounds_limit": 8,
            "replies": [],
        },
    )
    assert result.status.value == "read"
    assert (result.scroll_rounds, result.scroll_rounds_limit) == (3, 8)


def test_structured_read_keeps_author_identity_and_unread_pages() -> None:
    result = read_projection(
        "https://tieba.baidu.com/p/123",
        {
            "forum": "华东交通大学吧",
            "title": "宿舍条件",
            "original": "北区宿舍热水怎么样？",
            "author": "楼主甲",
            "origin_reply_id": "100",
            "original_floor": 1,
            "posted_at": "2026-10-16 15:21",
            "loaded_reply_count": 2,
            "total_replies": 9,
            "total_pages": 2,
            "pages_read": 1,
            "pages_limit": 1,
            "replies": [
                {
                    "content": "晚上热水有时不稳定。",
                    "reply_id": "101",
                    "floor": 2,
                    "posted_at": "2026-10-16 16:00",
                    "author": "吧友乙",
                },
                {
                    "content": "暑假刚修过。",
                    "reply_id": "102",
                    "parent_reply_id": "101",
                    "floor": 2,
                    "posted_at": "2026-10-16 16:10",
                    "author": None,
                    "nested": True,
                },
            ],
            "source": "structured",
        },
    )

    assert result.status.value == "partial"
    assert result.replies[0].author == "楼主甲"
    assert result.replies[0].floor == 1
    assert result.replies[0].reply_id == "100"
    assert result.replies[1].author == "吧友乙"
    assert result.replies[2].author is None
    assert result.total_pages == 2
    assert "共 2 页" in (result.error_message or "")
    assert "9 条" in (result.error_message or "")


def test_explicit_invalid_response_is_unrecognized() -> None:
    result = read_projection(
        "https://tieba.baidu.com/p/123",
        {"response_valid": False, "forum": "华东交通大学吧", "title": "宿舍条件"},
    )

    assert result.status.value == "unrecognized"
    assert result.error_code == "tieba_read_unrecognized"
    assert result.replies == []


def test_forum_sidebar_cannot_confirm_a_different_thread() -> None:
    result = read_projection(
        "https://tieba.baidu.com/p/123",
        {
            "forum": "其他大学吧",
            "title": "国庆假期",
            "original": "讨论",
            "replies": [],
        },
    )
    assert not result.forum_matches_target


def test_profile_busy_is_reported_without_launching_a_second_browser(tmp_path: Path) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    profile = profile_path(tmp_path, "alice")
    profile.mkdir(parents=True)
    with DataDirectoryLock(profile), pytest.raises(TiebaBrowserError) as failure:
        runtime.request("alice", {"action": "search", "terms": "国庆"})
    assert failure.value.code == "tieba_browser_busy"
    runtime.close()


def test_runtime_reuses_worker_context_between_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        def poll(self) -> None:
            return None

    runtime = TiebaBrowserRuntime(tmp_path)
    spawned: list[Path] = []
    workers: list[_BrowserWorker] = []
    sent: list[dict[str, Any]] = []
    disposed: list[object] = []

    def spawn(profile: Path) -> _BrowserWorker:
        spawned.append(profile)
        worker = _BrowserWorker(Process(), queue.Queue())  # type: ignore[arg-type]
        workers.append(worker)
        return worker

    monkeypatch.setattr(runtime, "_spawn", spawn)
    monkeypatch.setattr(runtime, "_send", lambda current, payload: sent.append(payload))
    monkeypatch.setattr(runtime, "_receive", lambda *_args: {"ok": True})
    monkeypatch.setattr(runtime, "_dispose", lambda current: disposed.append(current))

    assert runtime.request("alice", {"action": "search", "terms": "宿舍"}) == {"ok": True}
    assert runtime.request("alice", {"action": "read", "url": "https://tieba.baidu.com/p/1"}) == {
        "ok": True
    }
    assert runtime.request("bob", {"action": "search", "terms": "宿舍"}) == {"ok": True}
    runtime.close()

    assert len(spawned) == 2
    assert spawned[0] != spawned[1]
    assert [payload["action"] for payload in sent] == ["search", "read", "search"]
    assert disposed == workers


def test_runtime_disposes_evicted_worker_outside_global_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        def poll(self) -> None:
            return None

    runtime = TiebaBrowserRuntime(tmp_path)
    runtime._MAX_WORKERS = 1  # noqa: SLF001 - 缩小容量以覆盖 LRU 驱逐
    monkeypatch.setattr(
        runtime,
        "_spawn",
        lambda _profile: _BrowserWorker(Process(), queue.Queue()),
    )
    monkeypatch.setattr(runtime, "_send", lambda *_args: None)
    monkeypatch.setattr(runtime, "_receive", lambda *_args: {"ok": True})
    lock_available_during_dispose: list[bool] = []

    def dispose(_worker: _BrowserWorker) -> None:
        acquired: list[bool] = []

        def probe_lock() -> None:
            available = runtime._lock.acquire(timeout=0.5)  # noqa: SLF001
            acquired.append(available)
            if available:
                runtime._lock.release()  # noqa: SLF001

        probe = threading.Thread(target=probe_lock)
        probe.start()
        probe.join(timeout=1)
        lock_available_during_dispose.append(bool(acquired and acquired[0]))

    monkeypatch.setattr(runtime, "_dispose", dispose)
    runtime.request("alice", {"action": "search"})
    runtime.request("bob", {"action": "search"})
    runtime.close()

    assert lock_available_during_dispose == [True, True]


def test_runtime_checks_deadline_after_starting_worker_before_sending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        def poll(self) -> None:
            return None

    runtime = TiebaBrowserRuntime(tmp_path)
    worker = _BrowserWorker(Process(), queue.Queue())
    sent: list[dict[str, Any]] = []

    def spawn(_profile: Path) -> _BrowserWorker:
        time.sleep(0.06)
        return worker

    monkeypatch.setattr(runtime, "_spawn", spawn)
    monkeypatch.setattr(runtime, "_send", lambda _worker, payload: sent.append(payload))
    monkeypatch.setattr(runtime, "_dispose", lambda _worker: None)

    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request(
            "alice",
            {"action": "search"},
            deadline=time.monotonic() + 0.02,
        )
    assert failure.value.code == "tieba_browser_timeout"
    assert sent == []


def test_lock_wait_reports_deadline_expiry_as_timeout(tmp_path: Path) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    lock = threading.Lock()
    lock.acquire()
    runtime._account_locks["alice"] = lock
    try:
        with pytest.raises(TiebaBrowserError) as failure:
            runtime.request(
                "alice",
                {"action": "search"},
                deadline=time.monotonic() + 0.01,
            )
        assert failure.value.code == "tieba_browser_timeout"
    finally:
        lock.release()


def test_remove_account_stops_worker_and_deletes_only_its_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        def poll(self) -> None:
            return None

    runtime = TiebaBrowserRuntime(tmp_path)
    profile = profile_path(tmp_path, "alice")
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Cookies").write_text("account session", encoding="utf-8")
    other_profile = profile_path(tmp_path, "bob")
    other_profile.mkdir(parents=True)
    worker = _BrowserWorker(Process(), queue.Queue())  # type: ignore[arg-type]
    runtime._workers["alice"] = worker
    runtime._last_used["alice"] = time.monotonic()
    disposed: list[_BrowserWorker] = []
    monkeypatch.setattr(runtime, "_dispose", disposed.append)

    runtime.remove_account("alice")

    assert disposed == [worker]
    assert profile.exists()
    assert {entry.name for entry in profile.iterdir()} == {
        ".bridges.lock",
        ACCOUNT_DELETION_MARKER,
    }
    assert other_profile.exists()


def test_account_deletion_fence_blocks_request_waiting_for_account_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    held_lock = threading.Lock()
    held_lock.acquire()
    attempted_lock = threading.Event()

    class ObservedLock:
        def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
            attempted_lock.set()
            return held_lock.acquire(blocking, timeout)

        def release(self) -> None:
            held_lock.release()

    runtime._account_locks["alice"] = ObservedLock()  # type: ignore[assignment]
    spawned: list[Path] = []
    monkeypatch.setattr(runtime, "_spawn", lambda profile: spawned.append(profile))
    failures: list[TiebaBrowserError] = []

    def request() -> None:
        try:
            runtime.request("alice", {"action": "search"})
        except TiebaBrowserError as error:
            failures.append(error)

    waiter = threading.Thread(target=request)
    waiter.start()
    assert attempted_lock.wait(timeout=1)
    fence_account_profile(tmp_path, "alice")
    held_lock.release()
    waiter.join(timeout=2)

    assert not waiter.is_alive()
    assert [failure.code for failure in failures] == ["tieba_browser_account_deleting"]
    assert spawned == []


def test_response_invalid_maps_to_unrecognized_read() -> None:
    class Runtime:
        def request(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise TiebaBrowserError(
                "tieba_browser_response_invalid", "页面响应结构校验失败"
            )

    result = BrowserTiebaThreadReader(Runtime(), "alice").read(
        "https://tieba.baidu.com/p/123"
    )
    assert result.status.value == "unrecognized"
    assert result.error_code == "tieba_browser_response_invalid"


def test_thread_page_wait_accepts_auth_marker_without_waiting_for_post_body() -> None:
    class Locator:
        def __init__(self, count: int) -> None:
            self._count = count

        def count(self) -> int:
            return self._count

    class LoginPage:
        waited_for_state = False

        def wait_for_function(self, predicate: str, *, timeout: int) -> None:
            assert timeout == 9000
            assert ".login-guide-placeholder" in predicate
            self.waited_for_state = True

        def title(self) -> str:
            assert self.waited_for_state
            return "贴吧登录"

        def locator(self, selector: str) -> Locator:
            assert self.waited_for_state
            return Locator(1 if selector == ".login-guide-placeholder" else 0)

    assert wait_for_thread_page_or_auth(LoginPage(), threading.Event()) == "login"


def test_expired_or_cancelled_work_does_not_launch_chrome(tmp_path: Path) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    profile_path(tmp_path, "alice").mkdir(parents=True)
    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request(
            "alice", {"action": "search", "terms": "国庆"}, deadline=time.monotonic() - 1
        )
    assert failure.value.code == "tieba_browser_timeout"
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request("alice", {"action": "search", "terms": "国庆"}, stop_event=stopped)
    assert failure.value.code == "tieba_browser_cancelled"
