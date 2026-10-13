"""聊天服务公开的领域错误类型。"""

from __future__ import annotations


class ChatDomainError(Exception):
    """聊天领域的可预期失败（由 API 层映射为 HTTP 状态与错误体）。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)
