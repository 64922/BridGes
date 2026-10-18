"""认证等待配对状态：同一段等待只开始一次、结束一次。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AuthWaitTracker:
    """记录一段人工登录/验证等待；重复开始或结束不重复计时。"""

    started_at: float | None = None
    reason: str | None = None
    total_ms: int = 0

    @property
    def active(self) -> bool:
        return self.started_at is not None

    def start(self, reason: str | None, now: float) -> bool:
        if self.started_at is not None:
            return False
        self.started_at = now
        self.reason = reason
        return True

    def finish(self, now: float) -> int | None:
        if self.started_at is None:
            return None
        elapsed_ms = max(0, int((now - self.started_at) * 1000))
        self.total_ms += elapsed_ms
        self.started_at = None
        self.reason = None
        return elapsed_ms


__all__ = ["AuthWaitTracker"]
