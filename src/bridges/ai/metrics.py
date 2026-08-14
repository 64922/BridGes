"""Low-cardinality metrics for model run lock recording.

The recorder emits counters through this port so operators can observe
persistence health without touching lock rows or parsing logs. All labels are
stable, low-cardinality values (capability name, status, operation); no account,
object, prompt or body ever appears as a label.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import Counter


class ModelRunLockMetrics(ABC):
    """Counter sink for model run lock recording events."""

    @abstractmethod
    def record_recorded(self, *, capability_name: str, status: str) -> None:
        """A lock was durably recorded (or idempotently replayed)."""

    @abstractmethod
    def record_conflict(self, *, capability_name: str) -> None:
        """Recording was rejected because the lock_id carried different content."""

    @abstractmethod
    def record_recovery_pending(self, *, operation: str) -> None:
        """A lock needs outbox/recovery closure (reserved for later issues)."""

    @abstractmethod
    def record_missing_business_ref(self, *, capability_name: str) -> None:
        """A persisted lock has no business association (legacy row)."""


class InMemoryModelRunLockMetrics(ModelRunLockMetrics):
    """Thread-safe in-memory counter implementation, primarily for tests.

    Production deployments can either consume :meth:`snapshot` periodically or
    provide their own adapter over the same port.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, ...]] = Counter()

    def record_recorded(self, *, capability_name: str, status: str) -> None:
        self._bump("model_lock_record_total", capability_name, status)

    def record_conflict(self, *, capability_name: str) -> None:
        self._bump("model_lock_record_conflict_total", capability_name)

    def record_recovery_pending(self, *, operation: str) -> None:
        self._bump("model_lock_recovery_pending_total", operation)

    def record_missing_business_ref(self, *, capability_name: str) -> None:
        self._bump("model_lock_missing_business_ref_total", capability_name)

    def snapshot(self) -> dict[str, int]:
        """Return flat ``metric:label[:label]`` -> count counters."""
        with self._lock:
            return {
                ":".join(key): count for key, count in self._counters.items()
            }

    def _bump(self, metric: str, *labels: str) -> None:
        with self._lock:
            self._counters[(metric, *labels)] += 1


class _NoopModelRunLockMetrics(ModelRunLockMetrics):
    """Metrics sink that discards everything (default when none injected)."""

    def record_recorded(self, *, capability_name: str, status: str) -> None:
        pass

    def record_conflict(self, *, capability_name: str) -> None:
        pass

    def record_recovery_pending(self, *, operation: str) -> None:
        pass

    def record_missing_business_ref(self, *, capability_name: str) -> None:
        pass


NOOP_MODEL_RUN_LOCK_METRICS: ModelRunLockMetrics = _NoopModelRunLockMetrics()
"""Default no-op metrics sink; inject a real one per deployment."""
