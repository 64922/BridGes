"""Career 调用面低基数指标（Issue 12 Observability）。

按稳定阶段（``career_generation`` / ``career_repair``）、模型状态与稳定
错误码聚合真实供应商调用数、锁数、缺锁数与持久化失败；每次调用附带
延迟（毫秒）与 usage 元数据。只记录阶段/状态/计数等低基数标签，绝不
采集提示词、规划正文、用户内容或 Key。
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import Counter
from typing import Any


class CareerLockMetrics(ABC):
    """Career 模型运行锁审计指标端口（默认 no-op，可注入真实 sink）。"""

    @abstractmethod
    def record_call(
        self,
        *,
        operation: str,
        status: str,
        duration_ms: int,
        usage: dict[str, Any] | None,
    ) -> None:
        """一次真实供应商调用已发起（含失败调用，按阶段与模型状态聚合）。"""

    @abstractmethod
    def record_missing_run_lock(self) -> None:
        """网关调用未返回运行锁（``career_missing_run_lock`` 缺锁计数）。"""

    @abstractmethod
    def record_persist_failed(self) -> None:
        """recorder 持久化失败（``career_lock_persist_failed``）。"""

    @abstractmethod
    def record_sequence_mismatch(self) -> None:
        """调用序号/完整性异常（``career_call_sequence_mismatch``）。"""

    @abstractmethod
    def record_recorder_missing(self) -> None:
        """服务未注入统一 recorder（评估/替身组合）：调用发生了但没有
        持久化审计证据，运营可据此识别非生产组合仍在运行。"""


class InMemoryCareerLockMetrics(CareerLockMetrics):
    """线程安全的内存实现，主要用于测试与本地诊断。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, ...]] = Counter()
        self._durations_ms: list[int] = []
        self._last_usage: dict[str, Any] | None = None

    def record_call(
        self,
        *,
        operation: str,
        status: str,
        duration_ms: int,
        usage: dict[str, Any] | None,
    ) -> None:
        with self._lock:
            self._counters[("career_lock_call_total", operation, status)] += 1
            self._durations_ms.append(duration_ms)
            if usage is not None:
                self._last_usage = dict(usage)

    def record_missing_run_lock(self) -> None:
        self._bump("career_lock_missing_total")

    def record_persist_failed(self) -> None:
        self._bump("career_lock_persist_failed_total")

    def record_sequence_mismatch(self) -> None:
        self._bump("career_call_sequence_mismatch_total")

    def record_recorder_missing(self) -> None:
        self._bump("career_lock_recorder_missing_total")

    def snapshot(self) -> dict[str, int]:
        """展平 ``metric:label[:label]`` 计数快照（测试/诊断用）。"""
        with self._lock:
            return {
                ":".join(key): count for key, count in self._counters.items()
            }

    def call_count(self) -> int:
        with self._lock:
            return sum(self._counters.values())

    def last_duration_ms(self) -> int | None:
        with self._lock:
            return self._durations_ms[-1] if self._durations_ms else None

    def last_usage(self) -> dict[str, Any] | None:
        with self._lock:
            return (
                dict(self._last_usage) if self._last_usage is not None else None
            )

    def _bump(self, metric: str) -> None:
        with self._lock:
            self._counters[(metric,)] += 1


class _NoopCareerLockMetrics(CareerLockMetrics):
    """丢弃一切的默认实现（未注入时使用）。"""

    def record_call(
        self,
        *,
        operation: str,
        status: str,
        duration_ms: int,
        usage: dict[str, Any] | None,
    ) -> None:
        pass

    def record_missing_run_lock(self) -> None:
        pass

    def record_persist_failed(self) -> None:
        pass

    def record_sequence_mismatch(self) -> None:
        pass

    def record_recorder_missing(self) -> None:
        pass


NOOP_CAREER_LOCK_METRICS: CareerLockMetrics = _NoopCareerLockMetrics()
"""默认 no-op 指标；生产部署按端口注入真实 sink。"""
