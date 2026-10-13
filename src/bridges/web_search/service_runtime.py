"""搜索工作线程、截止时间与取消辅助。"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future
from contextvars import copy_context
from dataclasses import dataclass
from inspect import Parameter, signature
from threading import Event, Thread
from typing import Any, cast

from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import WebSearchResult


class _SearchStopSignal:
    """搜索内部停止信号：不把预算到期误写成用户取消。"""

    def __init__(self, parent: Event | None) -> None:
        self._parent = parent
        self._local = Event()

    def is_set(self) -> bool:
        return self._local.is_set() or bool(
            self._parent is not None and self._parent.is_set()
        )

    def user_is_set(self) -> bool:
        return bool(self._parent is not None and self._parent.is_set())

    def set(self) -> None:
        self._local.set()

    def wait(self, timeout: float | None = None) -> bool:
        """以短轮询观察父级用户取消，同时支持固定退避。"""
        if self.is_set():
            return True
        end = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if end is None else end - time.monotonic()
            if remaining is not None and remaining <= 0:
                return self.is_set()
            self._local.wait(0.05 if remaining is None else min(0.05, remaining))
        return True

@dataclass(frozen=True)
class _TimedFutureResult:
    """future 的真实完成时刻与结果；用于截止交接时重新消费。"""

    value: object
    finished_at: float

def _submit_daemon(call: Callable[[], object], *, name: str) -> Future[_TimedFutureResult]:
    """提交一个可取消观察的守护线程任务，不把迟到 provider 挂住进程退出。"""

    future: Future[_TimedFutureResult] = Future()
    context = copy_context()

    def run() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            try:
                value = context.run(call)
            except BaseException as exc:  # noqa: BLE001 - 交给消费层分类
                value = exc
            future.set_result(_TimedFutureResult(value, time.monotonic()))
        except BaseException as exc:  # noqa: BLE001 - future 仍须收敛
            future.set_exception(exc)

    Thread(target=run, daemon=True, name=name).start()
    return future

def _remaining_seconds(deadline: float) -> float:
    return max(0.001, deadline - time.monotonic())

def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))

def _user_cancelled(stop_event: Event | None) -> bool:
    """区分用户取消与编排器为截止时间发出的内部停止信号。"""
    if stop_event is None:
        return False
    marker = getattr(stop_event, "user_is_set", None)
    return bool(marker()) if callable(marker) else stop_event.is_set()

def _invoke_search_client(
    client: Any,
    query: str,
    *,
    deadline: float,
    stop_event: Event | None,
) -> list[WebSearchResult]:
    """把同一绝对截止时间传给客户端，同时兼容旧测试替身。"""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")
    search = client.search
    try:
        parameters = tuple(signature(search).parameters.values())
    except (TypeError, ValueError):
        parameters = ()
        accepts_kwargs = True
    else:
        accepts_kwargs = any(
            parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters
        )
    names = {
        parameter.name
        for parameter in parameters
        if parameter.kind
        in {Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY}
    }
    kwargs: dict[str, Any] = {}
    if accepts_kwargs or "timeout" in names:
        kwargs["timeout"] = remaining
    if accepts_kwargs or "deadline" in names:
        kwargs["deadline"] = deadline
    if accepts_kwargs or "stop_event" in names:
        kwargs["stop_event"] = stop_event
    return cast(list[WebSearchResult], search(query, **kwargs))
