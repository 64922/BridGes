"""贴吧浏览器异常与来源降级边界。"""

from __future__ import annotations

import time
from typing import Any

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.browser import BrowserTiebaSearch
from bridges.tieba.browser_runtime import (
    TiebaBrowserError,
)
from bridges.tieba.searching import SearchOutcome, query_record


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


def test_unverified_auth_state_does_not_fall_back_or_request_manual_continue() -> None:
    class Unverified:
        def request(self, *args: Any, **kwargs: Any) -> Any:
            raise TiebaBrowserError(
                "tieba_browser_auth_unverified",
                "贴吧页面可读，但无法独立确认专用会话的认证状态；本轮未继续采集。",
            )

    class MustNotFallback:
        def search_public(self, *args: Any, **kwargs: Any) -> SearchOutcome:
            raise AssertionError("认证状态未知时不能回退成普通公开搜索成功")

    result = BrowserTiebaSearch(Unverified(), MustNotFallback()).search_public(  # type: ignore[arg-type]
        "alice", query="site:tieba.baidu.com/p/ 华东交通大学吧 宿舍", reason="测试"
    )

    assert result.record.error_code == "tieba_browser_auth_unverified"
    assert result.record.retryable is False


def test_work_time_exhaustion_is_terminal_and_never_uses_public_fallback() -> None:
    class Exhausted:
        def request(self, *args: Any, **kwargs: Any) -> Any:
            raise TiebaBrowserError(
                "tieba_browser_work_time_exhausted",
                "本轮剩余贴吧工作预算已耗尽，未发起后续页面请求。",
            )

    class MustNotFallback:
        def search_public(self, *args: Any, **kwargs: Any) -> SearchOutcome:
            raise AssertionError("工作预算耗尽不能回退成普通公开索引成功")

    result = BrowserTiebaSearch(Exhausted(), MustNotFallback()).search_public(  # type: ignore[arg-type]
        "alice", query="site:tieba.baidu.com/p/ 华东交通大学吧 宿舍", reason="测试"
    )
    assert result.record.status is ModuleQueryStatus.ERROR
    assert result.record.error_code == "tieba_browser_work_time_exhausted"
    assert result.record.retryable is False


def test_queue_full_is_terminal_and_never_uses_public_fallback() -> None:
    class QueueFull:
        def request(self, *args: Any, **kwargs: Any) -> Any:
            raise TiebaBrowserError(
                "tieba_browser_request_queue_full",
                "同时等待的贴吧页面请求过多，已停止继续加载。",
            )

    class MustNotFallback:
        def search_public(self, *args: Any, **kwargs: Any) -> SearchOutcome:
            raise AssertionError("等待队列已满不能回退成普通公开索引成功")

    result = BrowserTiebaSearch(QueueFull(), MustNotFallback()).search_public(  # type: ignore[arg-type]
        "alice", query="site:tieba.baidu.com/p/ 华东交通大学吧 宿舍", reason="测试"
    )
    assert result.record.status is ModuleQueryStatus.ERROR
    assert result.record.error_code == "tieba_browser_request_queue_full"
    assert result.record.retryable is False


def test_browser_search_forwards_run_deadline_to_worker() -> None:
    deadline = time.monotonic() + 10

    class Runtime:
        payload: dict[str, Any] = {}

        def request(
            self,
            _account_id: str,
            payload: dict[str, Any],
            **kwargs: Any,
        ) -> dict[str, Any]:
            assert kwargs["deadline"] == deadline
            self.payload = payload
            return {"hits": []}

    class MustNotFallback:
        def search_public(self, *args: Any, **kwargs: Any) -> SearchOutcome:
            raise AssertionError("贴吧查询应走浏览器 worker")

    runtime = Runtime()
    outcome = BrowserTiebaSearch(runtime, MustNotFallback()).search_public(  # type: ignore[arg-type]
        "alice",
        query="site:tieba.baidu.com/p/ 华东交通大学吧 宿舍",
        reason="测试",
        deadline=deadline,
    )

    assert runtime.payload["deadline_monotonic"] == deadline
    assert outcome.record.status is ModuleQueryStatus.SUCCESS


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
