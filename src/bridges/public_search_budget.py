"""公网搜索的共享预算常量与逐请求钩子。

该模块不依赖聊天编排包，供搜索服务和聊天预算控制器共同读取，避免
提供方层导入 ``bridges.chat`` 时形成循环依赖。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar, Token
from typing import TypeAlias

PUBLIC_SEARCH_STAGE_SECONDS = 8.0
SEARCH_HANDOFF_RESERVE_SECONDS = 0.75
SEARCH_RETRY_BACKOFF_SECONDS = 0.20
SEARCH_MIN_REQUEST_WINDOW_SECONDS = 0.05

RequestTracker: TypeAlias = Callable[[str], AbstractContextManager[str | None]]
OutcomeReporter: TypeAlias = Callable[[str], None]
_REQUEST_TRACKER: ContextVar[RequestTracker | None] = ContextVar(
    "public_search_request_tracker", default=None
)
_OUTCOME_REPORTER: ContextVar[OutcomeReporter | None] = ContextVar(
    "public_search_request_outcome", default=None
)


@contextmanager
def bind_request_tracker(tracker: RequestTracker) -> Iterator[None]:
    token: Token[RequestTracker | None] = _REQUEST_TRACKER.set(tracker)
    try:
        yield
    finally:
        _REQUEST_TRACKER.reset(token)


@contextmanager
def track_request(purpose: str) -> Iterator[str | None]:
    """Track one physical provider HTTP request when a caller supplies a ledger."""
    tracker = _REQUEST_TRACKER.get()
    if tracker is None:
        yield None
        return
    with tracker(purpose) as denial:
        yield denial


@contextmanager
def bind_outcome_reporter(reporter: OutcomeReporter) -> Iterator[None]:
    token: Token[OutcomeReporter | None] = _OUTCOME_REPORTER.set(reporter)
    try:
        yield
    finally:
        _OUTCOME_REPORTER.reset(token)


def report_request_outcome(outcome_code: str) -> None:
    reporter = _OUTCOME_REPORTER.get()
    if reporter is not None:
        reporter(outcome_code)
