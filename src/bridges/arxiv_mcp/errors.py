"""arXiv MCP 错误类型与默认上游状态映射。"""

from __future__ import annotations

_DEFAULT_UPSTREAM_STATUS = {
    "arxiv_timeout": "timeout",
    "arxiv_offline": "network",
    "arxiv_rate_limit": "http_429",
    "arxiv_permission": "permission",
    "arxiv_request": "http_4xx",
    "arxiv_parse": "parse",
    "arxiv_cancelled": "cancelled",
    "arxiv_startup": "startup",
    "arxiv_handshake": "handshake",
    "arxiv_worker_exit": "worker_exit",
    "arxiv_internal": "internal",
    "arxiv_backpressure": "backpressure",
}


class ArxivMcpError(Exception):
    """论文 MCP 的稳定错误码与用户可见中文提示。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        permission: bool = False,
        upstream_status: str | None = None,
        retryable: bool | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.permission = permission
        self.upstream_status = upstream_status or _DEFAULT_UPSTREAM_STATUS.get(code)
        self.retryable = (
            retryable if retryable is not None else code != "arxiv_cancelled"
        )
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)
