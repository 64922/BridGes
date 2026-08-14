"""Issue 03：``_parallel_search`` 截止交接的确定性边界测试。

用受控 event 让 future 分别在子 deadline 前、交接窗口内、子 deadline 后
与用户取消后完成，断言结果、调用次数与墙钟上限，覆盖验收标准：
- future 在截止边界附近完成时，其真实成功投影或真实 WebSearchError
  被消费一次且仅一次，不会被旧 done 快照替换成合成超时（AC2/AC7）；
- DDG 在 provider deadline 内形成的真实 timeout 保留提供方尝试元数据，
  只有到 stage 硬截止仍无投影才使用 ``web_search_stage_timeout``（AC3）；
- 用户取消始终投影为 ``web_search_cancelled`` 且 ``can_retry=false``；
- 执行器不留下阻止测试/进程退出的非守护等待（AC6）。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from bridges.chat.lifecycle import GenerationLifecycle
from bridges.chat.repository import ConversationRepository
from bridges.chat.turn import (
    _SEARCH_CANCELLED,
    _SEARCH_PROVIDER_TIMEOUT,
    _SEARCH_TIMEOUT,
    TurnOrchestrator,
)
from bridges.storage.database import BridgesDatabase
from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import (
    WebSearchProjection,
    WebSearchResult,
    WebSearchStatus,
)


def _projection(error_code: str | None = None) -> WebSearchProjection:
    return WebSearchProjection(
        status=WebSearchStatus.ERROR if error_code else WebSearchStatus.SUCCESS,
        trigger_reason="测试触发公网搜索",
        query_summary="公开主题",
        error_code=error_code,
        error_message=(
            "联网搜索超时，请重试。" if error_code == "web_search_timeout" else None
        ),
        results=(
            []
            if error_code
            else [
                WebSearchResult(
                    result_id="web-1",
                    title="公开来源",
                    site="example.com",
                    url="https://example.com/source",
                    snippet="公开摘要。",
                    accessed_at=datetime.now(UTC),
                )
            ]
        ),
        searched_at=datetime.now(UTC),
        can_retry=True if error_code else False,
    )


def _orchestrator() -> TurnOrchestrator:
    database = BridgesDatabase(":memory:")
    database.initialize()
    repository = ConversationRepository(database)
    return TurnOrchestrator(
        repository=repository,
        lifecycle=GenerationLifecycle(),
    )


class _EventGatedCall:
    """由测试放行的搜索调用：记录调用次数，按需返回真实结果/错误。"""

    def __init__(
        self,
        gate: threading.Event,
        *,
        result: Any = None,
        error: WebSearchError | None = None,
        delay_after_release: float = 0.0,
    ) -> None:
        self._gate = gate
        self._result = result
        self._error = error
        self._delay = delay_after_release
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        if not self._gate.wait(timeout=10):
            raise AssertionError("测试闸门未在超时前放行。")
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._result


def test_future_completing_before_sub_deadline_consumes_real_value() -> None:
    gate = threading.Event()
    gate.set()
    call = _EventGatedCall(gate, result=_projection())
    orchestrator = _orchestrator()

    started = time.monotonic()
    results = orchestrator._parallel_search(  # noqa: SLF001 - 白盒边界测试
        [("web", call)],
        deadline=started + 1.0,
        stage_deadline=started + 1.75,
    )

    assert results["web"] is not _SEARCH_TIMEOUT
    assert results["web"] is not _SEARCH_PROVIDER_TIMEOUT
    assert results["web"] is not _SEARCH_CANCELLED
    assert isinstance(results["web"], WebSearchProjection)
    assert results["web"].status == WebSearchStatus.SUCCESS
    assert call.calls == 1
    assert time.monotonic() - started < 1.2


def test_future_completing_in_handoff_window_keeps_real_success() -> None:
    """AC2：provider deadline 后、stage deadline 前完成的真实成功必须被消费。"""
    gate = threading.Event()
    call = _EventGatedCall(gate, result=_projection())
    orchestrator = _orchestrator()

    started = time.monotonic()
    provider_deadline = started + 0.2
    stage_deadline = started + 0.95
    release_at = started + 0.4  # 恰在交接窗口内（provider 后、stage 前）

    def _release() -> None:
        time.sleep(max(0.0, release_at - time.monotonic()))
        gate.set()

    releaser = threading.Thread(target=_release, daemon=True, name="test-release")
    releaser.start()
    results = orchestrator._parallel_search(  # noqa: SLF001
        [("web", call)],
        deadline=provider_deadline,
        stage_deadline=stage_deadline,
    )

    assert isinstance(results["web"], WebSearchProjection)
    assert results["web"].status == WebSearchStatus.SUCCESS
    assert results["web"].results, "真实成功投影的结果不得被合成超时丢弃"
    assert call.calls == 1
    assert time.monotonic() - started < 1.2


def test_future_completing_in_handoff_window_keeps_real_error() -> None:
    """AC7：交接窗口内完成的真实 provider 错误必须保留，不覆写为稀疏超时。"""
    gate = threading.Event()
    call = _EventGatedCall(
        gate,
        error=WebSearchError(
            "web_search_connect",
            "当前无法连接公网搜索，请检查网络后重试。",
        ),
    )
    orchestrator = _orchestrator()

    started = time.monotonic()
    provider_deadline = started + 0.2
    stage_deadline = started + 0.95
    release_at = started + 0.4

    def _release() -> None:
        time.sleep(max(0.0, release_at - time.monotonic()))
        gate.set()

    threading.Thread(target=_release, daemon=True, name="test-release").start()
    results = orchestrator._parallel_search(  # noqa: SLF001
        [("web", call)],
        deadline=provider_deadline,
        stage_deadline=stage_deadline,
    )

    assert isinstance(results["web"], WebSearchError)
    assert results["web"].code == "web_search_connect"
    assert call.calls == 1


def test_future_completing_in_handoff_window_keeps_real_provider_timeout() -> None:
    """AC3：DDG 在 provider deadline 内形成的真实 timeout 保留元数据。"""
    gate = threading.Event()
    call = _EventGatedCall(gate, result=_projection(error_code="web_search_timeout"))
    orchestrator = _orchestrator()

    started = time.monotonic()
    provider_deadline = started + 0.2
    stage_deadline = started + 0.95
    release_at = started + 0.4

    def _release() -> None:
        time.sleep(max(0.0, release_at - time.monotonic()))
        gate.set()

    threading.Thread(target=_release, daemon=True, name="test-release").start()
    results = orchestrator._parallel_search(  # noqa: SLF001
        [("web", call)],
        deadline=provider_deadline,
        stage_deadline=stage_deadline,
    )

    assert isinstance(results["web"], WebSearchProjection)
    assert results["web"].error_code == "web_search_timeout"
    assert results["web"].can_retry is True


def test_non_cooperative_future_past_stage_deadline_is_stage_timeout() -> None:
    """AC3：直到 stage 硬截止仍无投影的 future 使用 web_search_stage_timeout。"""
    gate = threading.Event()
    call = _EventGatedCall(gate, result=_projection())
    orchestrator = _orchestrator()

    started = time.monotonic()
    provider_deadline = started + 0.15
    stage_deadline = started + 0.9
    # 从不放行：future 全程未完成，墙钟必须被 stage 截止封顶。
    results = orchestrator._parallel_search(  # noqa: SLF001
        [("web", call)],
        deadline=provider_deadline,
        stage_deadline=stage_deadline,
    )
    elapsed = time.monotonic() - started

    assert results["web"] is _SEARCH_TIMEOUT
    assert call.calls == 1
    assert elapsed < 1.2, f"非合作 future 不得拖垮主流程（实耗 {elapsed:.2f}s）"
    gate.set()  # 释放守护线程，避免遗留等待


def test_user_cancel_projects_cancelled_for_all_calls() -> None:
    gate = threading.Event()
    call = _EventGatedCall(gate, result=_projection())
    orchestrator = _orchestrator()
    user_stop = threading.Event()

    started = time.monotonic()
    provider_deadline = started + 1.0
    stage_deadline = started + 1.75

    def _cancel() -> None:
        time.sleep(0.1)
        user_stop.set()

    threading.Thread(target=_cancel, daemon=True, name="test-cancel").start()
    results = orchestrator._parallel_search(  # noqa: SLF001
        [("web", call), ("arxiv", None)],
        deadline=provider_deadline,
        stage_deadline=stage_deadline,
        stop_event=user_stop,
    )

    assert results["web"] is _SEARCH_CANCELLED
    assert results["arxiv"] is None
    assert call.calls == 1
    assert time.monotonic() - started < 1.0
    gate.set()


def test_entry_after_provider_deadline_but_before_stage_is_provider_timeout() -> None:
    """进入编排时 provider deadline 已过而 stage 未到：web 记为 provider timeout。"""
    orchestrator = _orchestrator()
    started = time.monotonic()
    provider_deadline = started - 0.05
    stage_deadline = started + 0.7

    results = orchestrator._parallel_search(  # noqa: SLF001
        [("web", _EventGatedCall(threading.Event(), result=_projection()))],
        deadline=provider_deadline,
        stage_deadline=stage_deadline,
    )

    assert results["web"] is _SEARCH_PROVIDER_TIMEOUT


def test_parallel_search_leaves_no_non_daemon_threads_behind() -> None:
    """AC6：慢 future 的守护线程不阻止测试/进程退出。"""
    gate = threading.Event()
    call = _EventGatedCall(gate, result=_projection())
    orchestrator = _orchestrator()

    started = time.monotonic()
    results = orchestrator._parallel_search(  # noqa: SLF001
        [("web", call)],
        deadline=started + 0.1,
        stage_deadline=started + 0.85,
    )

    assert results["web"] is _SEARCH_TIMEOUT
    threads = [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("public-search-")
    ]
    assert all(thread.daemon for thread in threads)
    gate.set()
