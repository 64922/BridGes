"""Issue 04：DDG 健康快照监视器的 fake-clock 确定性测试。

覆盖：首次刷新、缓存命中、过期、并发合并、连续失败、旧成功过期、
恢复与探测超时；以及聊天降级提示的接入。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import (
    WebSearchHealth,
    WebSearchHealthStatus,
    WebSearchStatus,
)
from bridges.web_search.health_monitor import (
    PENDING_ERROR_CODE,
    PROBE_TIMEOUT_ERROR_CODE,
    STALE_READY_ERROR_CODE,
    WebSearchHealthMonitor,
)
from bridges.web_search.service import (
    SearchPlan,
    WebSearchService,
)

T0 = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)
PROBE_VERSION = "duckduckgo-html-v1"


class FakeClock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _ready(checked_at: datetime = T0) -> WebSearchHealth:
    return WebSearchHealth(
        provider="duckduckgo",
        provider_version=PROBE_VERSION,
        status=WebSearchHealthStatus.READY,
        checked_at=checked_at,
    )


def _connect_error(checked_at: datetime = T0) -> WebSearchHealth:
    return WebSearchHealth(
        provider="duckduckgo",
        provider_version=PROBE_VERSION,
        status=WebSearchHealthStatus.CONNECT_ERROR,
        checked_at=checked_at,
        error_code="web_search_connect",
    )


class _RecordingProbe:
    def __init__(self, *outcomes: WebSearchHealth | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[datetime] = []

    def __call__(self) -> WebSearchHealth:
        now = datetime.now(UTC)
        self.calls.append(now)
        if not self.outcomes:
            return _ready()
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _monitor(
    probe: _RecordingProbe,
    *,
    clock: FakeClock,
    ttl_seconds: int = 30,
    stale_ready_seconds: int = 300,
    auto_refresh: bool = False,
    probe_timeout_seconds: float = 0.2,
) -> WebSearchHealthMonitor:
    return WebSearchHealthMonitor(
        probe,
        clock=clock,
        ttl_seconds=ttl_seconds,
        stale_ready_seconds=stale_ready_seconds,
        probe_timeout_seconds=probe_timeout_seconds,
        auto_refresh=auto_refresh,
        provider_version=PROBE_VERSION,
    )


def test_first_snapshot_is_pending_without_network() -> None:
    clock = FakeClock()
    probe = _RecordingProbe(_ready())
    monitor = _monitor(probe, clock=clock, auto_refresh=False)

    snapshot = monitor.snapshot()

    assert snapshot.pending is True
    assert snapshot.status == WebSearchHealthStatus.UPSTREAM_ERROR
    assert snapshot.error_code == PENDING_ERROR_CODE
    assert snapshot.checked_at is None
    assert snapshot.age_ms is None
    assert snapshot.refresh_count == 0
    assert probe.calls == []
    # peek 绝不触发探测（聊天路径只读）。
    assert monitor.peek().pending is True
    assert probe.calls == []


def test_refresh_now_records_ready_and_latency() -> None:
    clock = FakeClock()
    probe = _RecordingProbe(_ready())
    monitor = _monitor(probe, clock=clock)

    snapshot = monitor.refresh_now()

    assert snapshot.pending is False
    assert snapshot.status == WebSearchHealthStatus.READY
    assert snapshot.checked_at == T0
    assert snapshot.last_success_at == T0
    assert snapshot.error_code is None
    assert snapshot.refresh_count == 1
    assert snapshot.latency_ms is not None and snapshot.latency_ms >= 0
    assert snapshot.age_ms == 0


def test_cache_hit_within_ttl_does_not_reprobe() -> None:
    clock = FakeClock()
    probe = _RecordingProbe(_ready())
    monitor = _monitor(probe, clock=clock, ttl_seconds=30)

    monitor.refresh_now()
    clock.advance(10)
    first = monitor.snapshot()
    clock.advance(10)
    second = monitor.snapshot()

    assert first.status == WebSearchHealthStatus.READY
    assert second.status == WebSearchHealthStatus.READY
    assert second.age_ms == 20_000
    assert len(probe.calls) == 1


def test_expired_snapshot_is_freshness_flagged_then_stale_ready_fails() -> None:
    clock = FakeClock()
    probe = _RecordingProbe(_ready())
    monitor = _monitor(
        probe,
        clock=clock,
        ttl_seconds=30,
        stale_ready_seconds=60,
        auto_refresh=False,
    )

    monitor.refresh_now()
    clock.advance(31)
    flagged = monitor.snapshot()
    assert flagged.status == WebSearchHealthStatus.READY
    assert flagged.stale_ready is True
    assert flagged.age_ms == 31_000

    clock.advance(40)  # 超过 stale_ready 上限（71s > 60s）
    expired = monitor.snapshot()
    assert expired.status == WebSearchHealthStatus.UPSTREAM_ERROR
    assert expired.error_code == STALE_READY_ERROR_CODE
    assert expired.stale_ready is True
    assert expired.last_success_at == T0
    # 旧成功不会无限期伪装成 READY；探测次数没有因过期读取而增加。
    assert len(probe.calls) == 1


def test_failed_refresh_keeps_last_success_and_current_failure() -> None:
    clock = FakeClock()
    probe = _RecordingProbe(_ready(), _connect_error())
    monitor = _monitor(probe, clock=clock, auto_refresh=False)

    monitor.refresh_now()
    clock.advance(5)
    failed = monitor.refresh_now()

    # 连续失败后不再显示旧 READY。
    assert failed.status == WebSearchHealthStatus.CONNECT_ERROR
    assert failed.error_code == "web_search_connect"
    assert failed.stale_ready is False
    assert failed.last_success_at == T0
    assert failed.refresh_count == 2
    assert failed.checked_at == T0 + timedelta(seconds=5)


def test_probe_exception_maps_to_stable_failure() -> None:
    clock = FakeClock()
    probe = _RecordingProbe(
        WebSearchError("web_search_timeout", "联网搜索超时，请重试。")
    )
    monitor = _monitor(probe, clock=clock)

    snapshot = monitor.refresh_now()

    assert snapshot.status == WebSearchHealthStatus.UPSTREAM_ERROR
    assert snapshot.error_code == "web_search_timeout"
    assert snapshot.pending is False


def test_probe_timeout_records_stable_code_without_blocking() -> None:
    clock = FakeClock()
    release = threading.Event()

    def blocking_probe() -> WebSearchHealth:
        release.wait(timeout=5)
        return _ready()

    monitor = _monitor(blocking_probe, clock=clock)

    started = time.monotonic()
    snapshot = monitor.refresh_now()
    elapsed = time.monotonic() - started

    assert snapshot.status == WebSearchHealthStatus.UPSTREAM_ERROR
    assert snapshot.error_code == PROBE_TIMEOUT_ERROR_CODE
    assert snapshot.pending is False
    assert elapsed < 1.0


def test_recovery_updates_ready_without_restart() -> None:
    clock = FakeClock()
    probe = _RecordingProbe(_ready(), _connect_error(), _ready())
    monitor = _monitor(probe, clock=clock, auto_refresh=False)

    monitor.refresh_now()
    clock.advance(1)
    failed = monitor.refresh_now()
    clock.advance(1)
    recovered = monitor.refresh_now()

    assert failed.status == WebSearchHealthStatus.CONNECT_ERROR
    assert recovered.status == WebSearchHealthStatus.READY
    assert recovered.last_success_at == T0 + timedelta(seconds=2)
    assert recovered.error_code is None


def test_concurrent_stale_reads_coalesce_single_refresh() -> None:
    clock = FakeClock()
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow_probe() -> WebSearchHealth:
        calls.append("probe")
        started.set()
        release.wait(timeout=5)
        return _ready()

    monitor = _monitor(
        slow_probe,
        clock=clock,
        ttl_seconds=1,
        auto_refresh=True,
        probe_timeout_seconds=2.0,
    )
    barrier = threading.Barrier(5)
    results: list[bool] = []
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            barrier.wait(timeout=5)
            snapshot = monitor.snapshot()
            results.append(snapshot.pending)
        except BaseException as exc:  # noqa: BLE001 - 收集线程异常
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(5)]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=5), "探测未被首次读取触发"
    # 刷新进行中：并发读取只启动一个探测。
    assert calls == ["probe"]
    release.set()
    for thread in threads:
        thread.join(timeout=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if monitor.peek().refresh_count >= 1:
            break
        time.sleep(0.01)

    assert errors == []
    assert monitor.peek().status == WebSearchHealthStatus.READY
    assert monitor.peek().refresh_count == 1
    assert calls == ["probe"]


class _HealthFakeClient:
    provider_name = "duckduckgo"
    provider_version = PROBE_VERSION

    def __init__(self, health: WebSearchHealth) -> None:
        self.health = health

    def health_check(self) -> WebSearchHealth:
        return self.health


def _plan() -> SearchPlan:
    return SearchPlan(True, "公开主题", "需要事实核查")


def test_initial_projection_shows_degraded_hint_when_search_not_ready() -> None:
    service = WebSearchService(
        client=_HealthFakeClient(_connect_error()),
        health_auto_refresh=False,
    )
    service.refresh_health()

    projection = service.initial_projection(_plan())

    assert projection is not None
    assert projection.status == WebSearchStatus.RECOVERY
    assert projection.error_message is not None
    assert "联网服务异常" in projection.error_message


def test_initial_projection_stays_loading_after_recovery() -> None:
    service = WebSearchService(
        client=_HealthFakeClient(_ready()),
        health_auto_refresh=False,
    )
    service.refresh_health()

    projection = service.initial_projection(_plan())

    assert projection is not None
    assert projection.status == WebSearchStatus.LOADING
    assert projection.error_message is None


def test_initial_projection_without_snapshot_is_plain_loading() -> None:
    service = WebSearchService(
        client=_HealthFakeClient(_ready()),
        health_auto_refresh=False,
    )

    projection = service.initial_projection(_plan())

    assert projection is not None
    assert projection.status == WebSearchStatus.LOADING
    assert projection.error_message is None
