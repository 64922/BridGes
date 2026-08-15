"""Issue 04：arXiv 有界重试、陈旧缓存兜底与 worker 预热的测试。

覆盖：重试预算阈值门控与错误码选择性重试、``attempt_count`` 如实计数、
重试不绕过节流最小间隔；stale 保留窗口/仅失败兜底/账户隔离/开关；
预热开关与失败兜底；``httpx.MockTransport`` 纵向（首次超时→重试→成功、
持续故障→stale 返回→标注）；三个独立回滚开关。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from bridges.arxiv_mcp import limits as arxiv_limits
from bridges.arxiv_mcp.cache import ArxivResultCache
from bridges.arxiv_mcp.client import ArxivMcpClient, ArxivMcpError
from bridges.arxiv_mcp.contracts import ArxivPaper, ArxivSearchStatus
from bridges.arxiv_mcp.guard import ArxivCooldown, ArxivThrottle
from bridges.arxiv_mcp.service import ArxivSearchPlan, ArxivSearchService

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
    """可推进的单调时钟；缓存/节流/冷却测试共用同一时间轴。"""

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


def _plan(query: str = "量子 纠错") -> ArxivSearchPlan:
    return ArxivSearchPlan(True, query, "用户明确要求搜索论文")


class _RecordingClient:
    """记录每次上游调用时刻的确定性客户端（配合 fake clock）。"""

    def __init__(
        self,
        papers: list[ArxivPaper] | None = None,
        clock: _FakeClock | None = None,
        failures: dict[str, int] | None = None,
    ) -> None:
        self.papers = papers if papers is not None else [_paper()]
        self.clock = clock
        self.calls: list[float] = []
        self.queries: list[str] = []
        #: 错误码 → 剩余失败次数；>0 时本次调用抛对应错误并递减。
        self.failures = dict(failures or {})

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
        for code, remaining in self.failures.items():
            if remaining > 0:
                self.failures[code] = remaining - 1
                raise ArxivMcpError(
                    code,
                    {
                        "arxiv_timeout": "arXiv 搜索超时，请重试。",
                        "arxiv_rate_limit": "arXiv 请求过于频繁，请稍后重试。",
                    }.get(code, "arXiv 搜索未完成，请重试。"),
                    upstream_status={
                        "arxiv_timeout": "timeout",
                        "arxiv_rate_limit": "http_429",
                        "arxiv_offline": "network",
                        "arxiv_request": "http_4xx",
                        "arxiv_parse": "parse",
                    }.get(code),
                )
        return self.papers[:max_results]


def _service(
    client: _RecordingClient,
    clock: _FakeClock,
    *,
    cooldown_seconds: float = 20.0,
    min_interval: float = 3.0,
    cache: ArxivResultCache | None = None,
) -> ArxivSearchService:
    return ArxivSearchService(
        client=client,
        cache=cache or ArxivResultCache(clock=clock),
        throttle=ArxivThrottle(
            min_interval=min_interval,
            clock=clock,
            sleeper=_advancing_sleeper(clock),
        ),
        cooldown=ArxivCooldown(cooldown_seconds=cooldown_seconds, clock=clock),
    )


# ---------------------------------------------------------------------------
# 陈旧缓存：TTL 过期后保留、保留窗口、仅失败兜底
# ---------------------------------------------------------------------------


def test_stale_serve_returns_expired_entry_within_retention_window() -> None:
    clock = _FakeClock()
    cache = ArxivResultCache(ttl_seconds=600.0, clock=clock)
    service = _service(_RecordingClient(clock=clock), clock, cache=cache)
    plan = _plan()
    first = service.search("acct-1", plan)
    assert first is not None and first.status == ArxivSearchStatus.SUCCESS

    clock.advance(600.0)  # TTL 恰好到期：正常缓存不再命中
    assert cache.get("acct-1", "量子 纠错", 5, plan.route_version) is None
    # 过期但仍在 24h 保留窗口内：可作 stale 兜底
    stale = cache.get_stale("acct-1", "量子 纠错", 5, plan.route_version)
    assert stale is not None
    assert stale.stale is True
    assert stale.cache_hit is False
    assert stale.status == ArxivSearchStatus.SUCCESS
    assert len(stale.papers) == 1


def test_stale_serve_never_returns_fresh_entry() -> None:
    clock = _FakeClock()
    cache = ArxivResultCache(ttl_seconds=600.0, clock=clock)
    service = _service(_RecordingClient(clock=clock), clock, cache=cache)
    plan = _plan()
    first = service.search("acct-1", plan)

    assert first is not None and first.status == ArxivSearchStatus.SUCCESS
    assert cache.get_stale("acct-1", "量子 纠错", 5, plan.route_version) is None


def test_stale_entry_evicted_after_retention_window() -> None:
    clock = _FakeClock()
    cache = ArxivResultCache(ttl_seconds=600.0, clock=clock)
    service = _service(_RecordingClient(clock=clock), clock, cache=cache)
    plan = _plan()
    first = service.search("acct-1", plan)
    assert first is not None

    clock.advance(600.0 + arxiv_limits.ARXIV_STALE_RETENTION_SECONDS)  # TTL + 保留窗口
    assert cache.get_stale("acct-1", "量子 纠错", 5, plan.route_version) is None
    assert cache.get("acct-1", "量子 纠错", 5, plan.route_version) is None


def test_stale_fallback_is_isolated_per_account() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = _service(client, clock)
    plan = _plan()
    first = service.search("acct-a", plan)
    assert first is not None and first.status == ArxivSearchStatus.SUCCESS

    clock.advance(600.0)
    client.failures["arxiv_timeout"] = 2  # 账户 B 同查询持续失败
    other = service.search("acct-b", _plan())

    assert other is not None and other.status == ArxivSearchStatus.ERROR
    assert other.stale is False
    assert other.error_code == "arxiv_timeout"
    assert len(client.calls) == 3  # 账户 B 不命中账户 A 的 stale 条目


# ---------------------------------------------------------------------------
# 有界重试：预算阈值门控、错误码选择性重试、attempt_count 如实计数
# ---------------------------------------------------------------------------


def test_transient_timeout_retries_once_and_succeeds() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock, failures={"arxiv_timeout": 1})
    service = _service(client, clock)

    result = service.search("acct-1", _plan())

    assert result is not None and result.status == ArxivSearchStatus.SUCCESS
    assert result.attempt_count == 2
    assert result.stale is False
    assert len(client.calls) == 2  # 首次超时 + 自动重发
    assert client.calls[1] - client.calls[0] >= 3.0  # 重试不绕过节流最小间隔
    snapshot = service.metrics_snapshot()
    assert snapshot["arxiv_upstream_calls"] == 2
    assert snapshot["arxiv_retry_attempts"] == 1
    assert snapshot["arxiv_retry_successes"] == 1


def test_retry_skipped_when_remaining_budget_below_threshold() -> None:
    import time as real_time

    clock = _FakeClock()  # 只用于记录调用时刻，预算判定用真实单调时钟
    client = _RecordingClient(clock=clock, failures={"arxiv_timeout": 10})
    service = ArxivSearchService(
        client=client,
        throttle=ArxivThrottle(min_interval=0.0),
        cooldown=ArxivCooldown(cooldown_seconds=0.05),
    )

    result = service.search(
        "acct-1", _plan(), deadline=real_time.monotonic() + 4.0
    )

    assert result is not None and result.status == ArxivSearchStatus.ERROR
    assert result.error_code == "arxiv_timeout"
    assert result.attempt_count == 1
    assert len(client.calls) == 1  # 剩余预算 4s < 阈值 5s：不重发
    assert service.metrics_snapshot()["arxiv_retry_attempts"] == 0


def test_retry_happens_when_remaining_budget_above_threshold() -> None:
    import time as real_time

    clock = _FakeClock()  # 只用于记录调用时刻，预算判定用真实单调时钟
    client = _RecordingClient(clock=clock, failures={"arxiv_timeout": 10})
    service = ArxivSearchService(
        client=client,
        throttle=ArxivThrottle(min_interval=0.0),
        cooldown=ArxivCooldown(cooldown_seconds=0.05),
    )

    result = service.search(
        "acct-1", _plan(), deadline=real_time.monotonic() + 8.0
    )

    assert result is not None and result.status == ArxivSearchStatus.ERROR
    assert result.error_code == "arxiv_timeout"
    assert result.attempt_count == 2  # 剩余预算 ≥ 阈值 5s：重发一次
    assert len(client.calls) == 2
    assert service.metrics_snapshot()["arxiv_retry_attempts"] == 1


@pytest.mark.parametrize(
    ("failure_code", "expected_code"),
    [
        ("arxiv_rate_limit", "arxiv_rate_limit"),
        ("arxiv_request", "arxiv_request"),
        ("arxiv_parse", "arxiv_parse"),
    ],
)
def test_no_retry_on_rate_limit_4xx_or_parse(
    failure_code: str, expected_code: str
) -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock, failures={failure_code: 10})
    service = _service(client, clock)

    result = service.search("acct-1", _plan())

    assert result is not None and result.status == ArxivSearchStatus.ERROR
    assert result.error_code == expected_code
    assert result.attempt_count == 1
    assert len(client.calls) == 1
    assert service.metrics_snapshot()["arxiv_retry_attempts"] == 0


def test_retry_disabled_by_rollback_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arxiv_limits, "ARXIV_RETRY_ENABLED", False)
    clock = _FakeClock()
    client = _RecordingClient(clock=clock, failures={"arxiv_timeout": 10})
    service = _service(client, clock)

    result = service.search("acct-1", _plan())

    assert result is not None and result.status == ArxivSearchStatus.ERROR
    assert result.attempt_count == 1
    assert len(client.calls) == 1  # 开关关闭：回到单发行为


def test_retry_second_attempt_timeout_reports_two_attempts() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock, failures={"arxiv_timeout": 10})
    service = _service(client, clock)

    result = service.search("acct-1", _plan())

    assert result is not None and result.status == ArxivSearchStatus.ERROR
    assert result.error_code == "arxiv_timeout"
    assert result.attempt_count == 2  # 两次真实上游调用都失败
    assert len(client.calls) == 2
    snapshot = service.metrics_snapshot()
    assert snapshot["arxiv_retry_attempts"] == 1
    assert snapshot["arxiv_retry_successes"] == 0


# ---------------------------------------------------------------------------
# stale 兜底：仅失败后返回、标注、attempt_count、冷却语义不变
# ---------------------------------------------------------------------------


def test_stale_serve_after_persistent_timeout_with_annotation() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = _service(client, clock)
    plan = _plan()
    first = service.search("acct-1", plan)
    assert first is not None and first.status == ArxivSearchStatus.SUCCESS

    clock.advance(600.0)  # TTL 到期，条目进入 stale 保留窗口
    client.failures["arxiv_timeout"] = 10
    stale = service.search("acct-1", plan)

    assert stale is not None
    assert stale.status == ArxivSearchStatus.SUCCESS
    assert stale.stale is True
    assert stale.cache_hit is False
    assert stale.attempt_count == 2  # 重试后的真实上游调用数
    assert stale.papers[0].arxiv_id == "2401.12345v2"
    assert service.metrics_snapshot()["arxiv_stale_serves"] == 1
    # 上游失败仍然进入冷却：冷却语义不因 stale 兜底改变
    rejected = service.search("acct-1", plan)
    assert rejected is not None and rejected.status == ArxivSearchStatus.ERROR
    assert rejected.attempt_count == 0
    assert rejected.retry_after_seconds is not None and rejected.retry_after_seconds > 0


def test_stale_serve_after_rate_limit_without_retry() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = _service(client, clock)
    plan = _plan()
    first = service.search("acct-1", plan)
    assert first is not None and first.status == ArxivSearchStatus.SUCCESS

    clock.advance(600.0)
    client.failures["arxiv_rate_limit"] = 10
    stale = service.search("acct-1", plan)

    assert stale is not None and stale.status == ArxivSearchStatus.SUCCESS
    assert stale.stale is True
    assert stale.attempt_count == 1  # 429 不重试，但如实记录 1 次上游调用
    assert len(client.calls) == 2


def test_no_stale_fallback_without_stale_entry() -> None:
    clock = _FakeClock()
    client = _RecordingClient(clock=clock, failures={"arxiv_offline": 10})
    service = _service(client, clock)

    result = service.search("acct-1", _plan())

    assert result is not None and result.status == ArxivSearchStatus.ERROR
    assert result.error_code == "arxiv_offline"
    assert result.stale is False
    assert service.metrics_snapshot()["arxiv_stale_serves"] == 0


def test_stale_fallback_disabled_by_rollback_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arxiv_limits, "ARXIV_STALE_FALLBACK_ENABLED", False)
    clock = _FakeClock()
    client = _RecordingClient(clock=clock)
    service = _service(client, clock)
    plan = _plan()
    first = service.search("acct-1", plan)
    assert first is not None and first.status == ArxivSearchStatus.SUCCESS

    clock.advance(600.0)
    client.failures["arxiv_timeout"] = 10
    result = service.search("acct-1", plan)

    assert result is not None and result.status == ArxivSearchStatus.ERROR
    assert result.error_code == "arxiv_timeout"
    assert result.stale is False


# ---------------------------------------------------------------------------
# httpx.MockTransport 纵向：首次超时→重试→成功；持续故障→stale→标注
# ---------------------------------------------------------------------------


def _mock_transport_client(
    handler: Any,
) -> ArxivMcpClient:
    return ArxivMcpClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_mock_transport_first_timeout_then_retry_then_success() -> None:
    requests: list[httpx.Request] = []
    state = {"timeouts": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if state["timeouts"] > 0:
            state["timeouts"] -= 1
            raise httpx.ReadTimeout("upstream timeout", request=request)
        return httpx.Response(200, text=ATOM_RESPONSE)

    service = ArxivSearchService(
        client=_mock_transport_client(handler),
        throttle=ArxivThrottle(min_interval=0.0),
        cooldown=ArxivCooldown(cooldown_seconds=0.05),
    )
    result = service.search("acct-1", _plan("quantum error correction"))

    assert result is not None and result.status == ArxivSearchStatus.SUCCESS
    assert result.attempt_count == 2
    assert len(requests) == 2  # 首次超时 + 自动重发成功
    assert service.metrics_snapshot()["arxiv_retry_successes"] == 1


def test_mock_transport_persistent_failure_serves_stale_with_annotation() -> None:
    import time as real_time

    requests: list[httpx.Request] = []
    state = {"failures": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if state["failures"] > 0:
            state["failures"] -= 1
            raise httpx.ReadTimeout("upstream timeout", request=request)
        return httpx.Response(200, text=ATOM_RESPONSE)

    service = ArxivSearchService(
        client=_mock_transport_client(handler),
        cache=ArxivResultCache(ttl_seconds=0.05),
        throttle=ArxivThrottle(min_interval=0.0),
        cooldown=ArxivCooldown(cooldown_seconds=0.05),
    )
    plan = _plan("quantum error correction")
    first = service.search("acct-1", plan)
    assert first is not None and first.status == ArxivSearchStatus.SUCCESS
    assert len(requests) == 1

    real_time.sleep(0.1)  # 越过 TTL：条目进入 stale 保留窗口
    state["failures"] = 2  # 上游进入持续故障
    stale = service.search("acct-1", plan)

    assert stale is not None
    assert stale.status == ArxivSearchStatus.SUCCESS
    assert stale.stale is True
    assert stale.attempt_count == 2  # 两次上游调用都失败后兜底
    assert len(requests) == 3
    assert service.metrics_snapshot()["arxiv_stale_serves"] == 1


# ---------------------------------------------------------------------------
# worker 预热：服务层委托、开关与失败兜底
# ---------------------------------------------------------------------------


class _WarmupClient(_RecordingClient):
    def __init__(self) -> None:
        super().__init__(clock=None)
        self.warmup_calls = 0
        self.warmup_result = True
        self.warmup_successes = 0
        self.warmup_failures = 0

    def warmup(self) -> bool:
        self.warmup_calls += 1
        if self.warmup_result:
            self.warmup_successes += 1
        else:
            self.warmup_failures += 1
        return self.warmup_result


def test_service_warmup_delegates_to_process_client() -> None:
    client = _WarmupClient()
    service = ArxivSearchService(client=client)

    assert service.warmup() is True
    assert client.warmup_calls == 1
    assert service.metrics_snapshot()["warmup_successes"] == 1


def test_service_warmup_returns_false_when_switch_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arxiv_limits, "ARXIV_WARMUP_ENABLED", False)
    client = _WarmupClient()
    service = ArxivSearchService(client=client)

    assert service.warmup() is False
    assert client.warmup_calls == 0


def test_service_warmup_skips_client_without_warmup_support() -> None:
    service = ArxivSearchService(client=_RecordingClient(clock=None))

    assert service.warmup() is False


def test_service_warmup_survives_client_failure() -> None:
    client = _WarmupClient()
    client.warmup_result = False
    service = ArxivSearchService(client=client)

    assert service.warmup() is False
    assert client.warmup_calls == 1
    assert service.metrics_snapshot()["warmup_failures"] == 1  # 计数在客户端


class _ExplodingWarmupClient(_RecordingClient):
    def warmup(self) -> bool:
        raise RuntimeError("warmup sidecar failure")


def test_service_warmup_never_blocks_startup_on_exception() -> None:
    service = ArxivSearchService(client=_ExplodingWarmupClient())

    assert service.warmup() is False  # 异常被吞掉，只记日志
