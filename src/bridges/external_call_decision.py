"""外部调用预算许可决定：区分暂时容量不足与终止原因。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExternalCallReason(StrEnum):
    """一次外部调用许可的真实结果。"""

    ALLOWED = "allowed"
    CAPACITY = "capacity"
    WORK_TIME_EXHAUSTED = "work_time_exhausted"
    RUN_INVALIDATED = "run_invalidated"
    CANCELLED = "cancelled"
    CONTENDED = "contended"
    GUARD_FAILED = "guard_failed"
    QUEUE_FULL = "queue_full"


@dataclass(frozen=True, slots=True)
class ExternalCallDecision:
    """脱敏的许可结果；``active``/``limit``/``remaining_ms`` 用于拒绝记录。"""

    allowed: bool
    reason: str
    active: int = 0
    limit: int = 0
    remaining_ms: int = 0


__all__ = ["ExternalCallDecision", "ExternalCallReason"]
