"""专用会话的账户边界、检索范围与真实读取范围回归。"""

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from bridges.contracts.modules import ModuleQueryStatus
from bridges.runtime.lock import DataDirectoryLock
from bridges.tieba.browser import BrowserTiebaSearch, browser_terms, read_projection
from bridges.tieba.browser_runtime import TiebaBrowserError, TiebaBrowserRuntime, profile_path
from bridges.tieba.searching import SearchOutcome, query_record


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


def test_browser_failure_is_kept_when_falling_back_to_scoped_index() -> None:
    class Unavailable:
        def request(self, *args: Any, **kwargs: Any) -> Any:
            raise TiebaBrowserError("tieba_browser_setup_required", "请登录")

    class Public:
        def search_public(self, account_id: str, *, query: str, **kwargs: Any) -> SearchOutcome:
            assert account_id == "alice" and query.startswith("site:tieba.baidu.com/p/")
            return SearchOutcome(
                query_record(query=query, status=ModuleQueryStatus.EMPTY, evidence_count=0),
                (),
                (),
                (),
            )

    search = BrowserTiebaSearch(Unavailable(), Public())  # type: ignore[arg-type]
    outcome = search.search_public(
        "alice", query="site:tieba.baidu.com/p/ 华东交通大学吧 国庆", reason="测试"
    )
    assert outcome.prior_records[0].source == "tieba_browser"
    assert outcome.prior_records[0].error_code == "tieba_browser_setup_required"


def test_auth_interruption_is_not_hidden_by_public_search_fallback() -> None:
    class NeedsLogin:
        def request(self, *args: Any, **kwargs: Any) -> Any:
            raise TiebaBrowserError(
                "tieba_browser_auth_interrupted", "登录未完成", excluded_wait_ms=4200
            )

    class MustNotFallback:
        def search_public(self, *args: Any, **kwargs: Any) -> SearchOutcome:
            raise AssertionError("认证中断不能回退成普通公开索引成功")

    result = BrowserTiebaSearch(NeedsLogin(), MustNotFallback()).search_public(  # type: ignore[arg-type]
        "alice", query="site:tieba.baidu.com/p/ 华东交通大学吧 宿舍", reason="测试"
    )
    assert result.record.error_code == "tieba_browser_auth_interrupted"
    assert result.excluded_wait_ms == 4200


def test_official_query_never_uses_the_tieba_login_session() -> None:
    class Unused:
        def request(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("官方页面不能读取贴吧账户会话")

    class Public:
        def search_public(self, account_id: str, *, query: str, **kwargs: Any) -> SearchOutcome:
            assert query.startswith("site:ecjtu.edu.cn")
            return SearchOutcome(
                query_record(query=query, status=ModuleQueryStatus.EMPTY, evidence_count=0),
                (),
                (),
                (),
            )

    result = BrowserTiebaSearch(Unused(), Public()).search_public(  # type: ignore[arg-type]
        "alice", query="site:ecjtu.edu.cn 国庆", reason="测试"
    )
    assert result.record.source == "tavily"
