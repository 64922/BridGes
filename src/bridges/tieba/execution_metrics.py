"""贴吧运行的阶段与请求节奏测量。"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from bridges.tieba.run_metadata import (
    TiebaBrowserCleanup,
    TiebaRequestBreakdown,
)

_REQUEST_TYPES = {
    "search": "search",
    "read": "read",
    "official": "official",
    "browser": "browser",
}


def request_type_of(purpose: str) -> str:
    """把账本用途归并为稳定的请求类别。"""
    if purpose.startswith("tieba_browser_") or purpose.startswith("browser-request"):
        return _REQUEST_TYPES["browser"]
    if purpose.startswith("tieba_official"):
        return _REQUEST_TYPES["official"]
    if purpose in {"tieba.read", "tieba_read", "tieba_thread_read"}:
        return _REQUEST_TYPES["read"]
    if purpose.startswith("tieba.search") or "search" in purpose or purpose.startswith("tavily"):
        return _REQUEST_TYPES["search"]
    return "other"


class TiebaExecutionMetrics:
    """同一次运行共享的轻量测量：阶段、请求与浏览器清理。"""

    def __init__(self, *, cleanup_tolerance_ms: int = 13_000) -> None:
        self._lock = threading.Lock()
        self._stage_timings_ms: dict[str, int] = {}
        self._cleanup_durations_ms: list[int] = []
        self._cleanup_tolerance_ms = max(0, int(cleanup_tolerance_ms))

    def record_stage(self, node: str, duration_ms: int) -> None:
        with self._lock:
            self._stage_timings_ms[node] = max(0, int(duration_ms))

    def stage_timings_ms(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stage_timings_ms)

    def record_cleanup(self, duration_ms: int) -> None:
        with self._lock:
            self._cleanup_durations_ms.append(max(0, int(duration_ms)))

    def browser_cleanup_summary(self) -> TiebaBrowserCleanup | None:
        with self._lock:
            durations = list(self._cleanup_durations_ms)
        if not durations:
            return None
        return TiebaBrowserCleanup(
            count=len(durations),
            max_ms=max(durations),
            tolerance_ms=self._cleanup_tolerance_ms,
            timed_out=sum(
                1 for duration in durations if duration > self._cleanup_tolerance_ms
            ),
        )

    def request_breakdown(
        self, ledger: Any, account_id: str, run_id: str
    ) -> list[TiebaRequestBreakdown]:
        list_entries = getattr(ledger, "list_entries", None)
        if not callable(list_entries):
            return []
        try:
            entries = list_entries(account_id, run_id)
        except Exception:
            return []
        purposes = {
            entry.call_key: entry.purpose
            for entry in entries
            if entry.kind == "external_call" and entry.call_key
        }
        totals: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
        for entry in entries:
            if entry.kind != "external_call_result" or not entry.call_key:
                continue
            purpose = entry.purpose or purposes.get(entry.call_key) or "unknown"
            request_type = request_type_of(purpose)
            bucket = totals[(request_type, purpose)]
            bucket[0] += 1
            bucket[1] += max(0, int(entry.duration_ms or 0))
            queue_ms = entry.detail.get("queue_ms") if isinstance(entry.detail, dict) else 0
            bucket[2] += max(0, int(queue_ms or 0))
        return [
            TiebaRequestBreakdown(
                request_type=request_type,
                purpose=purpose,
                count=values[0],
                network_ms=values[1],
                queued_ms=values[2],
            )
            for (request_type, purpose), values in sorted(totals.items())
        ]


__all__ = ["TiebaExecutionMetrics", "request_type_of"]
