"""Issue 03：``WebSearchService._run_queries`` 交接窗口与重试矩阵测试。

覆盖验收标准：
- 清理窗口完成的 future 会被重新消费，而非丢弃真实错误/结果（AC2）；
- 可重试暂态错误（connect/DNS/offline/5xx）只重试一次，成功时调用数为 2，
  连续失败或剩余预算不足时不超过 2（AC4）；
- challenge/429/permission/响应过大/不安全 URL/不可接受重定向/不可重试
  4xx/解析契约错误只调用 1 次（AC5）；
- 固定 200ms 退避可被用户取消打断；剩余预算不足时不再启动第二次（AC6）；
- 每次已启动尝试都保存 provider=tavily、尝试序号、耗时、结果码与
  脱敏状态类别；终态 attempts 与实际 HTTP 调用数一致（AC7）。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from bridges import public_search_budget as search_budget
from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import (
    WebSearchResult,
    WebSearchStatus,
)
from bridges.web_search.service import SearchPlan, WebSearchService
from tests.web_search.test_duckduckgo_service import _FakeSearchClient


def _result(url: str = "https://example.com/source") -> WebSearchResult:
    return WebSearchResult(
        result_id="web-1",
        title="公开来源",
        site="example.com",
        url=url,
        snippet="公开摘要。",
        accessed_at=datetime.now(UTC),
    )


class _GatedClient:
    """由测试闸门控制完成时刻的搜索替身。"""

    def __init__(
        self,
        gate: threading.Event,
        *,
        result: list[WebSearchResult] | None = None,
        error: WebSearchError | None = None,
    ) -> None:
        self._gate = gate
        self._result = result if result is not None else [_result()]
        self._error = error
        self.calls = 0

    def search(self, query: str) -> list[WebSearchResult]:
        self.calls += 1
        if not self._gate.wait(timeout=10):
            raise AssertionError("测试闸门未在超时前放行。")
        if self._error is not None:
            raise self._error
        return self._result


def _service(client: Any, **kwargs: Any) -> WebSearchService:
    return WebSearchService(client=client, **kwargs)


# ---------------------------------------------------------------------------
# _run_queries 交接窗口：清理窗口完成的 future 被再次消费
# ---------------------------------------------------------------------------


def test_run_queries_marks_real_timeout_for_future_past_provider_deadline() -> None:
    """AC2/AC3：清理窗口内完成的 future 被再次消费；越过 provider deadline
    的按真实超时归类（保留尝试计数），而不是丢弃或误标为 stage 超时。"""
    gate = threading.Event()
    client = _GatedClient(gate)
    service = _service(client)

    started = time.monotonic()
    provider_deadline = started + 0.15
    stage_deadline = started + 0.9
    release_at = started + 0.35  # provider 后、stage 前的清理窗口内完成

    def _release() -> None:
        time.sleep(max(0.0, release_at - time.monotonic()))
        gate.set()

    threading.Thread(target=_release, daemon=True, name="test-release").start()
    results, errors, sent, _classifications, _statuses = service._run_queries(  # noqa: SLF001
        ("公开主题",),
        deadline=provider_deadline,
        stage_deadline=stage_deadline,
        stop_event=None,
    )

    assert sent == 1
    assert results == []
    assert len(errors) == 1
    assert isinstance(errors[0], WebSearchError)
    assert errors[0].code == "web_search_timeout"
    assert errors[0].code != "web_search_stage_timeout"
    assert client.calls == 1
    assert time.monotonic() - started < 1.2


def test_run_queries_keeps_real_error_completed_within_provider_deadline() -> None:
    """AC2/AC7：DDG 子 deadline 内完成的真实 WebSearchError 被消费一次且仅一次。"""
    gate = threading.Event()
    gate.set()
    client = _GatedClient(
        gate,
        error=WebSearchError("web_search_connect", "当前无法连接公网搜索。"),
    )
    service = _service(client)

    started = time.monotonic()
    results, errors, sent, _classifications, _statuses = service._run_queries(  # noqa: SLF001
        ("公开主题",),
        deadline=started + 1.0,
        stage_deadline=started + 1.75,
        stop_event=None,
    )

    assert sent == 1
    assert results == []
    assert len(errors) == 1
    assert isinstance(errors[0], WebSearchError)
    assert errors[0].code == "web_search_connect"
    assert client.calls == 1


def test_run_queries_marks_stage_timeout_for_non_cooperative_future() -> None:
    """AC3：直到 stage 硬截止仍无投影的 future 记为 web_search_stage_timeout。"""
    gate = threading.Event()
    client = _GatedClient(gate)
    service = _service(client)

    started = time.monotonic()
    provider_deadline = started + 0.15
    stage_deadline = started + 0.9
    results, errors, sent, _classifications, _statuses = service._run_queries(  # noqa: SLF001
        ("公开主题",),
        deadline=provider_deadline,
        stage_deadline=stage_deadline,
        stop_event=None,
    )
    elapsed = time.monotonic() - started

    assert sent == 1
    assert results == []
    assert len(errors) == 1
    assert errors[0].code == "web_search_stage_timeout"
    assert elapsed < 1.2, f"非合作 future 不得拖垮主流程（实耗 {elapsed:.2f}s）"
    gate.set()


def test_run_queries_user_cancel_skips_submission() -> None:
    stop = threading.Event()
    stop.set()
    client = _GatedClient(threading.Event())
    service = _service(client)

    results, errors, sent, _classifications, _statuses = service._run_queries(  # noqa: SLF001
        ("公开主题",),
        deadline=time.monotonic() + 1.0,
        stage_deadline=time.monotonic() + 1.75,
        stop_event=stop,
    )

    assert sent == 0
    assert results == [] and errors == []
    assert client.calls == 0


# ---------------------------------------------------------------------------
# 重试矩阵：可重试 / 不可重试 / 预算约束
# ---------------------------------------------------------------------------


class _FailingClient:
    """每次调用都抛同一错误；记录调用次数。"""

    def __init__(self, error: WebSearchError) -> None:
        self._error = error
        self.calls = 0

    def search(self, query: str) -> list[WebSearchResult]:
        self.calls += 1
        raise self._error


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("web_search_connect", "连接重置"),
        ("web_search_dns", "DNS 失败"),
        ("web_search_offline", "离线暂态"),
    ],
)
def test_transient_errors_retry_once_then_preserve_last_error(
    code: str, message: str
) -> None:
    client = _FailingClient(WebSearchError(code, message))
    service = _service(client, sleeper=lambda _: None)
    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查"),
    )

    assert projection is not None
    assert projection.status == WebSearchStatus.ERROR
    assert projection.error_code == code
    assert client.calls == 2
    assert projection.attempt_count == 2
    assert [a.attempt_number for a in projection.provider_attempts] == [1, 2]
    assert all(a.provider == "tavily" for a in projection.provider_attempts)
    assert projection.provider_attempts[0].retry_planned is True


def test_5xx_provider_error_is_retried_once() -> None:
    client = _FailingClient(
        WebSearchError(
            "web_search_provider",
            "提供方暂时不可用",
            http_status_category="5xx",
        )
    )
    service = _service(client, sleeper=lambda _: None)
    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查"),
    )

    assert projection is not None
    assert client.calls == 2
    assert projection.attempt_count == 2
    assert projection.http_status_category == "5xx"


def test_provider_error_without_http_category_is_not_retried() -> None:
    """AC4/AC5：无 HTTP 状态类别的 provider 错误不是“明确可重试 5xx”。"""
    client = _FailingClient(
        WebSearchError("web_search_provider", "提供方未知失败"),
    )
    service = _service(client, sleeper=lambda _: None)
    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查"),
    )

    assert projection is not None
    assert client.calls == 1
    assert projection.error_code == "web_search_provider"
    assert projection.attempt_count == 1


def test_final_error_keeps_last_real_attempt_priority() -> None:
    """AC7：两次失败错误不同时，终态按最后一次真实尝试归类。"""
    class _SequenceClient:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> list[WebSearchResult]:
            self.calls += 1
            if self.calls == 1:
                raise WebSearchError("web_search_connect", "连接重置")
            raise WebSearchError("web_search_dns", "DNS 失败")

    client = _SequenceClient()
    service = _service(client, sleeper=lambda _: None)
    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查"),
    )

    assert projection is not None
    assert client.calls == 2
    assert projection.error_code == "web_search_dns"
    assert [a.result_code for a in projection.provider_attempts] == [
        "web_search_connect",
        "web_search_dns",
    ]
    assert projection.provider_attempts[0].retry_planned is True


@pytest.mark.parametrize(
    ("code", "message", "extra"),
    [
        ("web_search_provider_challenge", "人机验证", {"retryable": False}),
        ("web_search_rate_limit", "请求过于频繁", {}),
        ("web_search_permission", "网络权限不足", {"permission": True}),
        ("web_search_response_too_large", "响应过大", {"retryable": False}),
        ("web_search_redirect", "不受控重定向", {"retryable": False}),
        ("web_search_parse", "无法解析结果", {}),
        ("web_search_request", "不可重试 4xx", {"retryable": False}),
    ],
)
def test_non_retryable_errors_call_provider_exactly_once(
    code: str, message: str, extra: dict[str, Any]
) -> None:
    client = _FailingClient(WebSearchError(code, message, **extra))
    service = _service(client, sleeper=lambda _: None)
    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查"),
    )

    assert projection is not None
    assert client.calls == 1, f"{code} 不得立即重试"
    assert projection.error_code == code
    assert projection.attempt_count == 1


def test_timeout_retry_respects_remaining_budget() -> None:
    """AC6：剩余预算不足「200ms 退避 + 最小请求窗口 + 750ms 交接」时不重试。"""
    client = _FailingClient(WebSearchError("web_search_timeout", "超时"))
    service = _service(client, sleeper=lambda _: None)
    plan = SearchPlan(
        True,
        "公开主题",
        "需要事实核查",
        max_retries=1,
    )

    projection = service.search(
        "acct-1",
        plan,
        deadline=time.monotonic() + 0.4,
    )

    assert projection is not None
    assert client.calls == 1, "预算不足时不得启动第二次尝试"
    assert projection.error_code == "web_search_timeout"


def test_timeout_retry_succeeds_when_budget_sufficient() -> None:
    class _TimeoutThenResult:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> list[WebSearchResult]:
            self.calls += 1
            if self.calls == 1:
                raise WebSearchError("web_search_timeout", "超时")
            return [_result()]

    client = _TimeoutThenResult()
    service = _service(client, sleeper=lambda _: None)
    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查"),
    )

    assert projection is not None
    assert projection.status == WebSearchStatus.SUCCESS
    assert client.calls == 2
    assert projection.attempt_count == 2
    assert projection.searched_at is not None
    assert projection.provider_attempts[0].retry_planned is True


def test_200ms_backoff_is_cancellable_by_user() -> None:
    """AC6：固定 200ms 退避能被用户取消打断，不再发起第二次请求。"""
    client = _FailingClient(WebSearchError("web_search_connect", "连接重置"))
    stop = threading.Event()
    service = _service(client, sleeper=lambda _: None)

    def _cancel() -> None:
        time.sleep(0.05)
        stop.set()

    threading.Thread(target=_cancel, daemon=True, name="test-cancel").start()
    started = time.monotonic()
    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查"),
        stop_event=stop,
    )
    elapsed = time.monotonic() - started

    assert projection is not None
    assert projection.status == WebSearchStatus.CANCELLED
    assert projection.error_code == "web_search_cancelled"
    assert projection.can_retry is False
    assert client.calls == 1
    assert elapsed < 0.5, f"退避应被取消打断（实耗 {elapsed:.2f}s）"


def test_backoff_uses_fixed_200ms_from_single_budget_source() -> None:
    """AC1/AC6：退避来自单一预算模块常量，且经可注入 sleeper 生效。"""
    slept: list[float] = []
    client = _FailingClient(WebSearchError("web_search_connect", "连接重置"))

    class _AlwaysFailThenNeverCalled:
        calls = 0

        def search(self, query: str) -> list[WebSearchResult]:
            _AlwaysFailThenNeverCalled.calls += 1
            raise WebSearchError("web_search_connect", "连接重置")

    service = _service(_AlwaysFailThenNeverCalled(), sleeper=slept.append)
    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查"),
    )

    assert projection is not None
    assert _AlwaysFailThenNeverCalled.calls == 2
    assert slept == [search_budget.SEARCH_RETRY_BACKOFF_SECONDS]
    assert slept[0] == pytest.approx(0.2)


def test_attempts_match_actual_http_calls_on_consecutive_failures() -> None:
    """AC7：连续失败时终态 attempts 与实际 HTTP 调用数一致（不出现 0 稀疏记录）。"""
    client = _FailingClient(WebSearchError("web_search_connect", "连接重置"))
    service = _service(client, sleeper=lambda _: None)
    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查"),
    )

    assert projection is not None
    assert projection.attempt_count == client.calls == 2
    assert projection.query_count == 2
    assert len(projection.query_history) == 2
    assert projection.searched_at is not None
    assert len(projection.provider_attempts) == 2
    for attempt in projection.provider_attempts:
        assert attempt.provider == "tavily"
        assert attempt.result_code == "web_search_connect"
        assert attempt.duration_ms >= 0
        assert attempt.query_hash


def test_unexpected_exception_uses_internal_error_code() -> None:
    """AC7：意外内部异常使用独立内部错误码，不冒充 provider 错误。"""
    class _BoomClient:
        calls = 0

        def search(self, query: str) -> list[WebSearchResult]:
            _BoomClient.calls += 1
            raise RuntimeError("内部爆炸")

    service = _service(_BoomClient(), sleeper=lambda _: None)
    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查"),
    )

    assert projection is not None
    assert projection.error_code == "web_search_internal"
    assert projection.can_retry is False
    assert _BoomClient.calls == 1


def test_empty_results_after_rewrite_are_not_rewritten_as_timeout() -> None:
    """AC7：真实空结果（无错误）不得被预算边界改写为稀疏 provider 超时。"""
    class _EmptyClient:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> list[WebSearchResult]:
            self.calls += 1
            return []

    client = _EmptyClient()
    service = _service(client, sleeper=lambda _: None)
    plan = SearchPlan(
        True,
        "公开主题",
        "需要事实核查",
        max_retries=1,
    )

    projection = service.search("acct-1", plan)

    assert projection is not None
    assert projection.status == WebSearchStatus.EMPTY
    assert projection.error_code == "web_search_no_results"
    assert client.calls == 2, "空结果允许一次有界改写"
    assert projection.attempt_count == 2
    assert projection.query_count == 2
    assert all(
        attempt.result_code == "web_search_no_results"
        for attempt in projection.provider_attempts
    )


def test_fake_search_client_still_works_for_service_search() -> None:
    """回归：既有 _FakeSearchClient 替身（不含 deadline 参数）仍可调用。"""
    client = _FakeSearchClient([_result()])
    service = _service(client)
    projection = service.search(
        "acct-1",
        SearchPlan(True, "公开主题", "需要事实核查"),
    )

    assert projection is not None
    assert projection.status == WebSearchStatus.SUCCESS
    assert projection.results[0].url == "https://example.com/source"
    assert projection.attempt_count == 1
    assert projection.provider_attempts[0].provider == "tavily"
