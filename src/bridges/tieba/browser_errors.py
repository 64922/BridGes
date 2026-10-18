"""贴吧浏览器错误的稳定错误码容器（运行时不回显上游页面或内部日志）。"""

from __future__ import annotations


class TiebaBrowserError(RuntimeError):
    """只携带稳定错误码，不回显上游页面或浏览器内部日志。"""

    def __init__(self, code: str, message: str, *, excluded_wait_ms: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.excluded_wait_ms = max(0, excluded_wait_ms)


__all__ = ["TiebaBrowserError"]
