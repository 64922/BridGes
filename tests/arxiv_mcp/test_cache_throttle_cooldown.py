"""Issue 05：arXiv 结果缓存、请求节流与限流冷却的状态机与投影测试。

覆盖：缓存键归一化与 TTL、仅成功缓存；进程级最小间隔串行调度（fake
clock）与预算不足不发起请求；429/超时冷却状态机（进入/拒绝/到期）；
``httpx.MockTransport`` 纵向（超时→冷却→恢复→成功）调用次数与投影断言。
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from bridges.arxiv_mcp.cache import ArxivResultCache, normalize_query_key
from bridges.arxiv_mcp.client import ArxivMcpClient, ArxivMcpError
from bridges.arxiv_mcp.contracts import ArxivPaper, ArxivSearchStatus
from bridges.arxiv_mcp.guard import ArxivCooldown, ArxivThrottle
from bridges.arxiv_mcp.service import ArxivSearchPlan, ArxivSearchService
from bridges.observability.service import ObservabilityService

ATOM_RESPONSE = """
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <title>Quantum Error Correction with Structured Codes</title>
    <summary>We study structured codes for correcting quantum errors.</summary>
    <published>2024-01-18T12:00:00Z</published>
    <author><name>Ada Lovelace</name></author>
    <link rel="alternate" type="text/html" href="http://arxiv.org/abs/2401.12345v2" />
    <link title="pdf" type="application/pdf" href="http://arxiv.org/pdf/2401.12345v2" />
  </entry>
</feed>
"""


class _FakeClock:
    """可推进的单调时钟；节流/冷却/缓存测试共用同一时间轴。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _advancing_sleeper(clock: _FakeClock):
    def sleep(seconds: float) -> None:
        clock.advance(seconds)

    return sleep


def _paper() -> ArxivPaper:
    return ArxivPaper(
        arxiv_id="2401.12345v2",
        title="Quantum Error Correction with Structured Codes",
        authors=["Ada Lovelace"],
        published_at=datetime(2024, 1, 18, tzinfo=UTC),
        abs_url="https://arxiv.org/abs/2401.12345v2",
        pdf_url="https://arxiv.org/pdf/2401.12345v2",
        abstract="We study structured codes for correcting quantum errors.",
    )


class _RecordingClient:
    """记录每次上游调用时刻的确定性客户端（配合 fake clock）。"""

    def __init__(
        self, papers: list[ArxivPaper] | None = None, clock: _FakeClock | None = None
    ) -> None:
        self.papers = papers if papers is not None else [_paper()]
        self.clock = clock
        self.calls: list[float] = []
        self.queries: list[str] = []

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: object = None,
        deadline: float | None = None,
    ) -> list[ArxivPaper]:
        self.queries.append(query)
        if self.clock is not None:
            self.calls.append(self.clock())
        return self.papers[:max_results]


class _RateLimitClient(_RecordingClient):
    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: object = None,
        deadline: float | None = None,
    ) -> list[ArxivPaper]:
        self.queries.append(query)
        if self.clock is not None:
            self.calls.append(self.clock())
        raise ArxivMcpError(
            "arxiv_rate_limit", "arXiv 请求过于频繁，请稍后重试。", upstream_status="http_429"
        )


class _TimeoutClient(_RecordingClient):
    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: object = None,
        deadline: float | None = None,
    ) -> list[ArxivPaper]:
        self.queries.append(query)
        if self.clock is not None:
            self.calls.append(self.clock())
        raise ArxivMcpError(
            "arxiv_timeout", "arXiv 搜索超时，请重试。", upstream_status="timeout"
        )


def _plan(query: str = "量子 纠错") -> ArxivSearchPlan:
    return ArxivSearchPlan(True, query, "用户明确要求搜索论文")


def _service(
    client: _RecordingClient,
    clock: _FakeClock,
    *,
    cooldown_seconds: float = 20.0,
    min_interval: float = 3.0,
) -> ArxivSearchService:
    return ArxivSearchService(
        client=client,
        cache=ArxivResultCache(clock=clock),
        throttle=ArxivThrottle(
            min_interval=min_interval,
            clock=clock,
            sleeper=_advancing_sleeper(clock),
        ),
        cooldown=ArxivCooldown(cooldown_seconds=cooldown_seconds, clock=clock),
    )


# ---------------------------------------------------------------------------
# 缓存键归一化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Transformer", "transformer"),
        ("  graph   neural networks  ", "graph neural networks"),
        ("量子 纠错", "纠错 量子"),
        ("transformer 量子 纠错", "量子 纠错 transformer"),
        (
            'title:"Attention Is All You Need" transformer',
            'transformer title:"Attention Is All You Need"',
        ),
        ("  graph   neural   networks  ", "graph  neural   networks"),
    ],
)
def test_cache_key_normalizes_case_whitespace_and_word_order(
    left: str, right: str
) -> None:
    assert normalize_query_key(left) == normalize_query_key(right)


def test_cache_key_keeps_quoted_phrase_atomic() -> None:
    phrase_a = 'ti:"A B"'
    phrase_b = 'ti:"B A"'
    assert normalize_query_key(phrase_a) != normalize_query_key(phrase_b)


# ---------------------------------------------------------------------------
# 结果缓存：TTL、仅成功缓存、cache_hit 标记
# ---------------------------------------------------------------------------


def test_repeated_normalized_query_hits_cache_with_zero_upstream_calls() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = _service(client, clock)

    first = service.search("acct-1", _plan("量子 纠错"))
    second = service.search("acct-1", _plan("纠错 量子"))

    assert first is not None and first.status == ArxivSearchStatus.SUCCESS
    assert first.cache_hit is False
    assert first.attempt_count == 1
    assert second is not None and second.status == ArxivSearchStatus.SUCCESS
    assert second.cache_hit is True
    assert second.attempt_count == 0
    assert len(client.calls) == 1  # 第二次命中缓存，上游调用数为 0
    assert client.queries == ["量子 纠错"]


def test_cache_entry_expires_after_ttl_and_next_search_reaches_upstream() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = _service(client, clock)

    first = service.search("acct-1", _plan())
    assert first is not None and first.status == ArxivSearchStatus.SUCCESS
    clock.advance(600.0)  # TTL 恰好到期
    second = service.search("acct-1", _plan())

    assert second is not None and second.status == ArxivSearchStatus.SUCCESS
    assert second.cache_hit is False
    assert len(client.calls) == 2


def test_failed_and_empty_results_are_never_cached() -> None:
    clock = _FakeClock()
    rate_client = _RateLimitClient(clock=clock)
    rate_service = _service(rate_client, clock)
    rejected = rate_service.search("acct-1", _plan())
    assert rejected is not None and rejected.status == ArxivSearchStatus.ERROR
    clock.advance(100.0)  # 冷却到期、缓存若有则仍在 TTL 内
    retried = rate_service.search("acct-1", _plan())
    assert retried is not None and retried.status == ArxivSearchStatus.ERROR
    assert len(rate_client.calls) == 2  # 失败结果未入缓存，再次真实发起

    empty_clock = _FakeClock()
    empty_client = _RecordingClient(papers=[], clock=empty_clock)
    empty_service = _service(empty_client, empty_clock)
    empty = empty_service.search("acct-1", _plan())
    assert empty is not None and empty.status == ArxivSearchStatus.EMPTY
    empty_again = empty_service.search("acct-1", _plan())
    assert empty_again is not None and empty_again.status == ArxivSearchStatus.EMPTY
    assert len(empty_client.calls) == 2  # 空结果不缓存


def test_cache_does_not_cross_max_results_or_route_version() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = _service(client, clock)

    service.search("acct-1", ArxivSearchPlan(True, "量子 纠错", "原因", max_results=3))
    other = service.search(
        "acct-1", ArxivSearchPlan(True, "量子 纠错", "原因", max_results=5)
    )

    assert other is not None and other.cache_hit is False
    assert len(client.calls) == 2


def test_cache_is_isolated_per_account() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = _service(client, clock)

    first = service.search("acct-a", _plan())
    assert first is not None and first.status == ArxivSearchStatus.SUCCESS
    other_account = service.search("acct-b", _plan())

    assert other_account is not None and other_account.cache_hit is False
    assert len(client.calls) == 2  # 账户 B 的同一查询不命中账户 A 的缓存
    same_account = service.search("acct-a", _plan("量子 纠错"))
    assert same_account is not None and same_account.cache_hit is True
    assert len(client.calls) == 2


# ---------------------------------------------------------------------------
# 节流：进程级最小间隔与预算不足
# ---------------------------------------------------------------------------


def test_consecutive_upstream_requests_are_spaced_at_least_min_interval() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = _service(client, clock)

    service.search("acct-1", _plan("量子 纠错"))
    service.search("acct-1", _plan("Transformer"))
    service.search("acct-1", _plan("图神经网络"))

    assert len(client.calls) == 3
    for earlier, later in zip(client.calls, client.calls[1:], strict=False):
        assert later - earlier >= 3.0


def test_throttle_wait_counts_against_stage_budget_and_skips_request() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = _service(client, clock)

    service.search("acct-1", _plan("量子 纠错"))
    assert len(client.calls) == 1
    # 距上次请求仅 1 秒，而最小间隔 3 秒：剩余预算 1 秒不足，不发起请求。
    clock.advance(1.0)
    result = service.search(
        "acct-1", _plan("Transformer"), deadline=clock.value + 1.0
    )

    assert result is not None
    assert result.status == ArxivSearchStatus.ERROR
    assert result.error_code == "arxiv_timeout"
    assert result.attempt_count == 0
    assert len(client.calls) == 1  # 未发起上游请求


def test_concurrent_requests_serialize_through_the_same_window() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = _service(client, clock)
    import threading

    results: list[object] = []
    threads = [
        threading.Thread(
            target=lambda index=i: results.append(
                service.search("acct-1", _plan(f"主题 {index}"))
            )
        )
        for i in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(client.calls) == 4
    for earlier, later in zip(client.calls, client.calls[1:], strict=False):
        assert later - earlier >= 3.0


# ---------------------------------------------------------------------------
# 冷却状态机：进入 / 拒绝 / 到期
# ---------------------------------------------------------------------------


def test_rate_limit_enters_cooldown_and_rejects_with_retry_after_seconds() -> None:
    clock = _FakeClock()
    client = _RateLimitClient(clock=clock)
    service = _service(client, clock)

    first = service.search("acct-1", _plan())
    assert first is not None and first.status == ArxivSearchStatus.ERROR
    assert first.error_code == "arxiv_rate_limit"
    assert first.attempt_count == 1
    assert len(client.calls) == 1

    # 冷却期内手动重试：不打上游，返回准确错误与剩余等待秒数。
    clock.advance(3.0)
    rejected = service.search("acct-1", _plan())
    assert rejected is not None and rejected.status == ArxivSearchStatus.ERROR
    assert rejected.error_code == "arxiv_rate_limit"
    assert rejected.upstream_status == "http_429"
    assert rejected.can_retry is True
    assert rejected.attempt_count == 0
    assert rejected.retry_after_seconds is not None and 0 < rejected.retry_after_seconds <= 20
    assert len(client.calls) == 1  # 冷却期拒绝不发起上游请求

    clock.advance(30.0)
    recovered = service.search("acct-1", _plan())
    assert recovered is not None and recovered.status == ArxivSearchStatus.ERROR
    assert len(client.calls) == 2  # 冷却到期后真实发起


def test_timeout_enters_cooldown_and_expiry_restores_real_request() -> None:
    clock = _FakeClock()
    client = _TimeoutClient(clock=clock)
    service = _service(client, clock)

    first = service.search("acct-1", _plan())
    assert first is not None and first.error_code == "arxiv_timeout"
    assert first.upstream_status == "timeout"

    clock.advance(1.0)
    rejected = service.search("acct-1", _plan())
    assert rejected is not None
    assert rejected.error_code == "arxiv_timeout"
    assert rejected.retry_after_seconds is not None and rejected.retry_after_seconds > 0
    assert len(client.calls) == 1

    clock.advance(30.0)
    recovered = service.search("acct-1", _plan())
    assert recovered is not None and recovered.error_code == "arxiv_timeout"
    assert len(client.calls) == 2


def test_cooldown_rejection_reports_exact_remaining_seconds() -> None:
    clock = _FakeClock()
    cooldown = ArxivCooldown(cooldown_seconds=20.0, clock=clock)
    cooldown.activate("arxiv_rate_limit", "arXiv 请求过于频繁，请稍后重试。", "http_429")
    clock.advance(5.0)

    rejection = cooldown.reject()

    assert rejection is not None
    assert rejection.code == "arxiv_rate_limit"
    assert rejection.message == "arXiv 请求过于频繁，请稍后重试。"
    assert rejection.upstream_status == "http_429"
    assert rejection.retry_after_seconds == 15  # 剩余 15 秒整

    clock.advance(0.5)
    assert cooldown.reject() is not None
    assert cooldown.remaining_seconds() == 15  # 剩余 14.5 秒向上取整

    clock.advance(15.0)
    assert cooldown.reject() is None  # 到期后不再拒绝


# ---------------------------------------------------------------------------
# 审计与指标
# ---------------------------------------------------------------------------


def test_audit_records_cache_hit_attempts_throttle_and_cooldown_rejection() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    observations = ObservabilityService()
    service = _service(client, clock)
    service._observability = observations  # noqa: SLF001 - 测试注入观测接口

    service.search("acct-1", _plan())
    service.search("acct-1", _plan("纠错 量子"))  # 缓存命中
    events = observations.list_audit_events(account_id="acct-1")

    assert len(events) == 2
    assert events[0].details["cache_hit"] is False
    assert events[0].details["attempt_count"] == 1
    assert events[0].details["throttle_wait_ms"] == 0
    assert events[0].details["cooldown_rejection"] is False
    assert events[1].details["cache_hit"] is True
    assert events[1].details["attempt_count"] == 0


def test_metrics_snapshot_counts_upstream_calls_hits_and_rejections() -> None:
    clock = _FakeClock()
    rate_client = _RateLimitClient(clock=clock)
    service = _service(rate_client, clock)

    service.search("acct-1", _plan())
    service.search("acct-1", _plan())  # 冷却拒绝
    clock.advance(30.0)
    service.search("acct-1", _plan())  # 到期后真实发起，再次 429 并延长冷却
    snapshot = service.metrics_snapshot()

    assert snapshot["arxiv_upstream_calls"] == 2
    assert snapshot["cache_hits"] == 0
    assert snapshot["cache_misses"] == 3
    assert snapshot["cache_hit_ratio"] == 0.0
    assert snapshot["cooldown_rejections"] == 1


def test_independent_rollback_switches_restore_old_behavior() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = ArxivSearchService(
        client=client,
        cache=ArxivResultCache(clock=clock, enabled=False),
        throttle=ArxivThrottle(
            min_interval=3.0,
            enabled=False,
            clock=clock,
            sleeper=_advancing_sleeper(clock),
        ),
        cooldown=ArxivCooldown(clock=clock, enabled=False),
    )

    service.search("acct-1", _plan())
    service.search("acct-1", _plan("量子 纠错"))  # 缓存关闭：真实发起
    service.search("acct-1", _plan("Transformer"))  # 节流关闭：无等待

    assert len(client.calls) == 3
    assert all(call == 1000.0 for call in client.calls)


# ---------------------------------------------------------------------------
# httpx.MockTransport 纵向：超时 → 冷却 → 恢复 → 成功
# ---------------------------------------------------------------------------


def test_mock_transport_timeout_then_cooldown_then_recovery_to_success() -> None:
    requests: list[httpx.Request] = []
    state = {"timeouts": 2}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if state["timeouts"] > 0:
            state["timeouts"] -= 1
            raise httpx.ReadTimeout("upstream timeout", request=request)
        return httpx.Response(200, text=ATOM_RESPONSE)

    client = ArxivMcpClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    service = ArxivSearchService(
        client=client,
        throttle=ArxivThrottle(min_interval=0.0),  # 纵向测试关闭节流等待
        cooldown=ArxivCooldown(cooldown_seconds=0.05),
    )
    import time

    plan = _plan("quantum error correction")
    first = service.search("acct-1", plan)
    assert first is not None and first.error_code == "arxiv_timeout"
    assert first.attempt_count == 1

    rejected = service.search("acct-1", plan)
    assert rejected is not None
    assert rejected.error_code == "arxiv_timeout"
    assert rejected.attempt_count == 0
    assert rejected.retry_after_seconds is not None and rejected.retry_after_seconds > 0

    time.sleep(0.1)  # 冷却到期
    second = service.search("acct-1", plan)
    assert second is not None and second.error_code == "arxiv_timeout"
    assert second.attempt_count == 1

    time.sleep(0.1)  # 第二次冷却到期
    recovered = service.search("acct-1", plan)
    assert recovered is not None
    assert recovered.status == ArxivSearchStatus.SUCCESS
    assert recovered.attempt_count == 1
    assert recovered.cache_hit is False

    cached = service.search("acct-1", plan)  # 成功后命中缓存
    assert cached is not None and cached.cache_hit is True and cached.attempt_count == 0
    assert len(requests) == 3  # 2 次超时 + 1 次成功；冷却拒绝与缓存命中不打上游
