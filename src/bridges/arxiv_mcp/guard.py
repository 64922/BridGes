"""arXiv 上游请求节流与限流冷却状态机（服务层单例持有）。

- :class:`ArxivThrottle`：进程级最小间隔串行调度。所有 worker（含
  临时并发 worker）的上游请求都经服务层单例的这一把锁排队，保证任意
  两个上游请求的发起时刻至少间隔 ``min_interval`` 秒；等待计入阶段
  预算，剩余预算不足时不发起请求（``wait_for_request_slot`` 返回
  None）。
- :class:`ArxivCooldown`：429/超时终态后的上游冷却。冷却期内的重试
  （含手动重试）不发起上游请求，直接返回准确错误码与剩余等待秒数。
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Any

from bridges.arxiv_mcp.client import _user_cancelled
from bridges.arxiv_mcp.limits import (
    ARXIV_COOLDOWN_SECONDS,
    ARXIV_MIN_REQUEST_INTERVAL_SECONDS,
)

#: 节流等待的短轮询步长（秒）：兼顾取消响应与忙等开销。
_POLL_STEP_SECONDS = 0.05


class ArxivThrottle:
    """进程级上游请求最小间隔的串行化调度。

    线程安全：``wait_for_request_slot`` 持有内部锁完成「计算等待 →
    等待 → 记录发起时刻」，因此并发调用方会按到达顺序排入同一 3 秒
    窗口，串行化覆盖主 worker 与全部临时并发 worker。锚点是上游请求
    的发起（dispatch）时刻：子进程 worker 的 spawn 与握手只会让真实
    HTTP 发送更晚，不会缩短间隔。
    """

    def __init__(
        self,
        *,
        min_interval: float = ARXIV_MIN_REQUEST_INTERVAL_SECONDS,
        enabled: bool = True,
        sleeper: Any = None,
        clock: Any = None,
    ) -> None:
        self._min_interval = max(0.0, min_interval)
        self._enabled = enabled
        self._sleeper = sleeper or time.sleep
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def wait_for_request_slot(
        self,
        *,
        deadline: float | None,
        stop_event: Any = None,
    ) -> float | None:
        """等待直到允许发起一次上游请求。

        返回本次实际等待秒数（0 表示无需等待）；返回 None 表示剩余阶段
        预算不足，调用方不应发起上游请求（按超时降级）。等待期间用户
        取消会提前结束等待，返回已等待时长，由调用方按取消语义处理。
        """
        if not self._enabled:
            return 0.0
        with self._lock:
            now = self._clock()
            wait = max(0.0, self._min_interval - (now - self._last_request_at))
            if deadline is not None and now + wait >= deadline:
                return None
            if wait > 0:
                self._wait(wait, stop_event=stop_event)
            self._last_request_at = self._clock()
            return max(0.0, self._last_request_at - now)

    def _wait(self, seconds: float, *, stop_event: Any) -> None:
        end = self._clock() + seconds
        while True:
            if _user_cancelled(stop_event):
                return
            remaining = end - self._clock()
            if remaining <= 0:
                return
            self._sleeper(min(_POLL_STEP_SECONDS, remaining))


@dataclass(frozen=True)
class CooldownRejection:
    """冷却期内一次拒绝的完整错误信息（错误码与既有分类一致）。"""

    code: str
    message: str
    upstream_status: str | None
    retry_after_seconds: int


class ArxivCooldown:
    """429/超时终态后的上游冷却状态机。

    冷却为进程级全局状态（arXiv 限流按出口 IP/账户计数，与具体查询
    无关）：进入冷却后，任意查询的搜索请求都会在发往上游前被拒绝，
    并携带准确的 ``retry_after_seconds``。冷却到期后自动恢复真实请求。
    """

    def __init__(
        self,
        *,
        cooldown_seconds: float = ARXIV_COOLDOWN_SECONDS,
        enabled: bool = True,
        clock: Any = None,
    ) -> None:
        self._cooldown_seconds = max(0.0, cooldown_seconds)
        self._enabled = enabled
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._until: float | None = None
        self._code: str | None = None
        self._message: str | None = None
        self._upstream_status: str | None = None
        self._rejections = 0

    @property
    def rejections(self) -> int:
        """冷却期内累计拒绝次数（脱敏计数，供审计）。"""
        with self._lock:
            return self._rejections

    def activate(
        self, code: str, message: str, upstream_status: str | None = None
    ) -> None:
        """429/超时终态后进入冷却（取更晚的到期时刻，延长式）。"""
        if not self._enabled:
            return
        until = self._clock() + self._cooldown_seconds
        with self._lock:
            if self._until is None or until > self._until:
                self._until = until
                self._code = code
                self._message = message
                self._upstream_status = upstream_status

    def remaining_seconds(self) -> int:
        """剩余冷却秒数（向上取整；0 表示不在冷却期）。"""
        with self._lock:
            if self._until is None:
                return 0
            remaining = max(0, math.ceil(self._until - self._clock()))
            if remaining == 0:
                self._until = None
                self._code = None
                self._message = None
                self._upstream_status = None
            return remaining

    def reject(self) -> CooldownRejection | None:
        """冷却期内的拒绝；不在冷却期时返回 None（调用方正常发起请求）。"""
        remaining = self.remaining_seconds()
        if remaining == 0:
            return None
        with self._lock:
            self._rejections += 1
            return CooldownRejection(
                code=self._code or "arxiv_rate_limit",
                message=self._message or "arXiv 请求过于频繁，请稍后重试。",
                upstream_status=self._upstream_status or "http_429",
                retry_after_seconds=remaining,
            )
