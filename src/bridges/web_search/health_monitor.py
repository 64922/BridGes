"""运行期 DuckDuckGo 健康快照监视器（Issue 04）。

职责边界：
- 短期缓存：TTL 内并发读取只读内存快照，不访问公网。
- 过期合并刷新：过期后至多一个后台刷新执行，其余请求读取带新鲜度标识的
  快照（``stale_ready``）。
- 失败语义：刷新失败保留 ``last_success_at`` 与当前失败状态，绝不用旧成功
  无限期伪装 READY；旧成功超过 ``stale_ready_seconds`` 后以
  ``web_search_stale_ready`` 失败关闭。
- 恢复语义：下一次真实探测成功即回到 READY 并更新 ``last_success_at``，
  无需进程重启。
- 严格超时：探测在线程中执行，最多等待 ``probe_timeout_seconds``；超时记录
  ``web_search_probe_timeout``，守护线程不阻塞进程退出。

本模块不发起用户查询、不保存响应正文、不接触任何凭据或代理配置。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from bridges.web_search.contracts import (
    PRIMARY_WEB_SEARCH_PROVIDER,
    WebSearchHealth,
    WebSearchHealthSnapshot,
    WebSearchHealthStatus,
)

#: 探测超时后的稳定错误码（观测告警之一）。
PROBE_TIMEOUT_ERROR_CODE = "web_search_probe_timeout"
#: 从未完成首次探测的稳定错误码。
PENDING_ERROR_CODE = "web_search_health_pending"
#: 旧成功超过上限后的稳定错误码。
STALE_READY_ERROR_CODE = "web_search_stale_ready"

DEFAULT_HEALTH_TTL_SECONDS = 30
DEFAULT_STALE_READY_SECONDS = 300
DEFAULT_HEALTH_PROBE_TIMEOUT_SECONDS = 5.0


class WebSearchHealthMonitor:
    """带短期缓存与并发合并的 DDG 健康快照监视器。"""

    def __init__(
        self,
        probe: Callable[[], WebSearchHealth],
        *,
        clock: Callable[[], datetime] | None = None,
        ttl_seconds: int = DEFAULT_HEALTH_TTL_SECONDS,
        stale_ready_seconds: int = DEFAULT_STALE_READY_SECONDS,
        probe_timeout_seconds: float = DEFAULT_HEALTH_PROBE_TIMEOUT_SECONDS,
        auto_refresh: bool = True,
        provider_version: str = "unknown",
    ) -> None:
        self._probe = probe
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._stale_ready_seconds = max(self._ttl_seconds, int(stale_ready_seconds))
        self._probe_timeout_seconds = max(0.1, float(probe_timeout_seconds))
        self._auto_refresh = auto_refresh
        self._provider_version = provider_version
        self._lock: RLock = RLock()
        self._snapshot: WebSearchHealthSnapshot | None = None
        self._refresh_running = False

    # -- 读取 ------------------------------------------------------------

    def snapshot(self) -> WebSearchHealthSnapshot:
        """返回当前快照；缺失或过期时触发一次合并刷新（不阻塞调用方）。

        首次调用返回 ``pending`` 快照；测试/无自动刷新环境可通过
        :meth:`refresh_now` 显式完成探测。
        """
        now = self._clock()
        with self._lock:
            snap = self._snapshot
        age = _age_seconds(snap.checked_at, now) if snap is not None else None
        if snap is None or (age is not None and age >= self._ttl_seconds):
            self._maybe_start_refresh()
        return self._visible(now)

    def peek(self) -> WebSearchHealthSnapshot:
        """只读当前快照，不触发任何刷新；从未探测时返回 pending 快照。

        聊天降级提示使用本方法，避免用户搜索路径反向触发健康探测。
        """
        return self._visible(self._clock())

    def refresh_now(self) -> WebSearchHealthSnapshot:
        """同步执行一次真实、有界探测并更新快照（发布探针/诊断命令用）。"""
        health, latency_ms, ok = self._run_probe()
        with self._lock:
            self._record(health, latency_ms, ok, checked_at=self._clock())
            return self._visible(self._clock())

    # -- 刷新 ------------------------------------------------------------

    def _maybe_start_refresh(self) -> None:
        if not self._auto_refresh:
            return
        with self._lock:
            if self._refresh_running:
                return
            self._refresh_running = True
        threading.Thread(
            target=self._refresh_worker,
            name="web-health-refresh",
            daemon=True,
        ).start()

    def _refresh_worker(self) -> None:
        try:
            health, latency_ms, ok = self._run_probe()
            with self._lock:
                self._record(health, latency_ms, ok, checked_at=self._clock())
        finally:
            with self._lock:
                self._refresh_running = False

    def _run_probe(self) -> tuple[WebSearchHealth | None, int | None, bool]:
        """在守护线程中执行探测并受 ``probe_timeout_seconds`` 约束。

        超时后线程继续在后台收敛（httpx 自身有超时），不会阻塞进程退出，
        也不会覆盖更新的快照。探测抛出的异常会折叠为带稳定错误码的失败
        健康证据（保留 ``WebSearchError.code``），只有线程超时才使用
        ``web_search_probe_timeout``。
        """
        started = time.monotonic()
        holder: list[Any] = []

        def run() -> None:
            try:
                holder.append(self._probe())
            except BaseException as exc:  # noqa: BLE001 - 交给调用方分类
                holder.append(exc)

        thread = threading.Thread(target=run, name="web-health-probe", daemon=True)
        thread.start()
        thread.join(self._probe_timeout_seconds)
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        if thread.is_alive():
            return None, latency_ms, False
        value = holder[0]
        if isinstance(value, BaseException):
            code = getattr(value, "code", None)
            return (
                WebSearchHealth(
                    provider=PRIMARY_WEB_SEARCH_PROVIDER,
                    provider_version=self._provider_version,
                    status=WebSearchHealthStatus.UPSTREAM_ERROR,
                    checked_at=self._clock(),
                    error_code=(
                        str(code)
                        if isinstance(code, str) and code
                        else PROBE_TIMEOUT_ERROR_CODE
                    ),
                ),
                latency_ms,
                False,
            )
        return value, latency_ms, True

    def _record(
        self,
        health: WebSearchHealth | None,
        latency_ms: int | None,
        ok: bool,
        *,
        checked_at: datetime,
    ) -> None:
        """把一次探测结果写入快照；失败保留 ``last_success_at`` 与当前失败。"""
        previous = self._snapshot
        refresh_count = (previous.refresh_count if previous is not None else 0) + 1
        last_success_at = (
            previous.last_success_at if previous is not None else None
        )
        if not ok or health is None:
            status = WebSearchHealthStatus.UPSTREAM_ERROR
            error_code = PROBE_TIMEOUT_ERROR_CODE
            if health is not None:
                status = health.status
                error_code = health.error_code or error_code
            self._snapshot = WebSearchHealthSnapshot(
                provider=PRIMARY_WEB_SEARCH_PROVIDER,
                provider_version=self._provider_version,
                status=status,
                checked_at=checked_at,
                latency_ms=latency_ms,
                last_success_at=last_success_at,
                error_code=error_code,
                stale_ready=False,
                pending=False,
                refresh_count=refresh_count,
            )
            return
        self._snapshot = WebSearchHealthSnapshot(
            provider=health.provider or PRIMARY_WEB_SEARCH_PROVIDER,
            provider_version=health.provider_version or self._provider_version,
            status=health.status,
            checked_at=checked_at,
            latency_ms=latency_ms,
            last_success_at=(
                checked_at
                if health.status == WebSearchHealthStatus.READY
                else last_success_at
            ),
            error_code=health.error_code,
            stale_ready=False,
            pending=False,
            refresh_count=refresh_count,
        )

    def _visible(self, now: datetime) -> WebSearchHealthSnapshot:
        """计算带新鲜度标识的可读快照，不修改内部状态。"""
        with self._lock:
            snap = self._snapshot
            if snap is None:
                return WebSearchHealthSnapshot(
                    provider=PRIMARY_WEB_SEARCH_PROVIDER,
                    provider_version=self._provider_version,
                    status=WebSearchHealthStatus.UPSTREAM_ERROR,
                    error_code=PENDING_ERROR_CODE,
                    pending=True,
                )
            age_ms = _age_ms(snap.checked_at, now)
            stale = (
                snap.status == WebSearchHealthStatus.READY
                and age_ms is not None
                and age_ms >= self._ttl_seconds * 1000
            )
            if (
                stale
                and age_ms is not None
                and age_ms >= self._stale_ready_seconds * 1000
            ):
                # 旧成功超过上限：不再把最后一次成功当作当前 READY。
                return snap.model_copy(
                    update={
                        "age_ms": age_ms,
                        "status": WebSearchHealthStatus.UPSTREAM_ERROR,
                        "error_code": STALE_READY_ERROR_CODE,
                        "stale_ready": True,
                    }
                )
            return snap.model_copy(update={"age_ms": age_ms, "stale_ready": stale})


def _age_seconds(checked_at: datetime | None, now: datetime) -> float | None:
    if checked_at is None:
        return None
    delta = (now - checked_at).total_seconds()
    return delta if delta >= 0 else 0.0


def _age_ms(checked_at: datetime | None, now: datetime) -> int | None:
    seconds = _age_seconds(checked_at, now)
    return None if seconds is None else int(seconds * 1000)
