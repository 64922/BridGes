"""贴吧公开进度：认证等待作为真实节点投影，结束后续接原节点。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token

from bridges.tieba.service_contract import NODE_AUTH_WAIT

EmitNode = Callable[[str, str, int | None], None]
NotifyAuthWait = Callable[[str], None]


class AuthWaitProgress:
    """把内核节点事件与认证等待配对成公开进度。

    认证等待不是节点完成：开始时先发 ``tieba.auth``，结束时只发该等待的
    completed，并重新声明等待前仍在执行的节点，避免界面在等待结束后退回
    “正在生成回答”或把认证等待误当成已完成阶段。
    """

    def __init__(self, emit_node: EmitNode) -> None:
        self._emit_node = emit_node
        self._last_started_node: str | None = None
        self._waiting = False

    def emit(self, node: str, status: str, duration_ms: int | None) -> None:
        if status == "started" and node != NODE_AUTH_WAIT:
            self._last_started_node = node
        self._emit_node(node, status, duration_ms)

    def notify(self, status: str) -> None:
        if status == "started":
            if self._waiting:
                return
            self._waiting = True
        elif status == "completed":
            if not self._waiting:
                return
            self._waiting = False
        else:
            return
        self._emit_node(NODE_AUTH_WAIT, status, None)
        if status == "completed" and self._last_started_node is not None:
            self._emit_node(self._last_started_node, "started", None)


_AUTH_WAIT_NOTIFIER: ContextVar[NotifyAuthWait | None] = ContextVar(
    "tieba_auth_wait_progress", default=None
)


def bind_auth_wait_progress(progress: AuthWaitProgress) -> Token[NotifyAuthWait | None]:
    """在当前执行上下文绑定认证等待进度；只影响本次贴吧运行。"""
    return _AUTH_WAIT_NOTIFIER.set(progress.notify)


def reset_auth_wait_progress(token: Token[NotifyAuthWait | None]) -> None:
    _AUTH_WAIT_NOTIFIER.reset(token)


def notify_auth_wait(status: str) -> None:
    """浏览器接收层在等待开始/结束时通知；未绑定时为无操作。"""
    notifier = _AUTH_WAIT_NOTIFIER.get()
    if notifier is not None:
        notifier(status)


__all__ = [
    "AuthWaitProgress",
    "bind_auth_wait_progress",
    "notify_auth_wait",
    "reset_auth_wait_progress",
]
