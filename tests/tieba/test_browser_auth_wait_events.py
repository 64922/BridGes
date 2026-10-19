"""认证等待配对事件：人工等待独立计时，重复或缺失事件不重复延长预算。"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any

import pytest

from bridges.tieba.browser_runtime import TiebaBrowserError, TiebaBrowserRuntime, _BrowserWorker
from bridges.tieba.progress import (
    AuthWaitProgress,
    bind_auth_wait_progress,
    reset_auth_wait_progress,
)


class _Process:
    def poll(self) -> None:
        return None


class _Budget:
    def __init__(self) -> None:
        self.manual_waits: list[int] = []

    def record_manual_wait(self, wait_ms: int) -> None:
        self.manual_waits.append(wait_ms)

    def record_external_call_denial(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def remaining_work_ms(self) -> int:
        return 300_000

    def request_block_reason(self) -> None:
        return None


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        self.now += 0.001
        return self.now


def _worker_with(*events: dict[str, Any]) -> _BrowserWorker:
    worker = _BrowserWorker(_Process(), queue.Queue())  # type: ignore[arg-type]
    for event in events:
        worker.output.put(json.dumps(event, ensure_ascii=False))
    return worker


def _receive(
    monkeypatch: pytest.MonkeyPatch,
    worker: _BrowserWorker,
    *,
    budget: _Budget | None = None,
    stop_event: threading.Event | None = None,
    request_guard: Any = None,
) -> dict[str, Any]:
    clock = _Clock()
    monkeypatch.setattr(
        "bridges.tieba.browser_runtime.time.monotonic", clock.monotonic
    )
    runtime = TiebaBrowserRuntime.__new__(TiebaBrowserRuntime)
    runtime._send = lambda *_args: None  # type: ignore[method-assign]
    return runtime._receive(
        worker,
        stop_event,
        10_000.0,
        budget,  # type: ignore[arg-type]
        request_guard,
    )


def test_paired_auth_wait_extends_budget_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _Budget()
    worker = _worker_with(
        {"auth_wait": "started", "reason": "login"},
        {"auth_wait": "started", "reason": "login"},
        {"auth_wait": "finished", "reason": "login", "outcome": "authenticated"},
        {"auth_wait": "finished", "reason": "login", "outcome": "authenticated"},
        {"ok": True},
    )

    result = _receive(monkeypatch, worker, budget=budget)

    assert result["ok"] is True
    assert len(budget.manual_waits) == 1
    assert budget.manual_waits[0] > 0
    assert result["excluded_wait_ms"] == budget.manual_waits[0]


def test_missing_auth_wait_events_do_not_extend_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _Budget()
    worker = _worker_with({"ok": True})

    result = _receive(monkeypatch, worker, budget=budget)

    assert result.get("excluded_wait_ms", 0) == 0
    assert budget.manual_waits == []


def _wait_until_consumed(worker: _BrowserWorker) -> None:
    for _ in range(200):
        if worker.output.empty():
            return
        threading.Event().wait(0.01)
    raise AssertionError("认证等待事件未被消费")


def _receive_in_thread(
    monkeypatch: pytest.MonkeyPatch,
    worker: _BrowserWorker,
    *,
    budget: _Budget,
    stop_event: threading.Event | None = None,
    request_guard: Any = None,
) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            _receive(
                monkeypatch,
                worker,
                budget=budget,
                stop_event=stop_event,
                request_guard=request_guard,
            )
        except BaseException as error:  # noqa: BLE001 - 测试记录实际异常
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


def test_stop_during_auth_wait_ends_once_without_running_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _Budget()
    stopped = threading.Event()
    worker = _worker_with({"auth_wait": "started", "reason": "login"})
    thread, errors = _receive_in_thread(
        monkeypatch, worker, budget=budget, stop_event=stopped
    )

    _wait_until_consumed(worker)
    stopped.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], TiebaBrowserError)
    assert errors[0].code == "tieba_browser_cancelled"
    assert len(budget.manual_waits) == 1


def test_task_change_during_auth_wait_does_not_execute_old_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _Budget()
    guard = {"code": None}
    worker = _worker_with({"auth_wait": "started", "reason": "login"})
    thread, errors = _receive_in_thread(
        monkeypatch,
        worker,
        budget=budget,
        request_guard=lambda: guard["code"],
    )

    _wait_until_consumed(worker)
    guard["code"] = "tieba_browser_task_changed"
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], TiebaBrowserError)
    assert errors[0].code == "tieba_browser_task_changed"
    assert len(budget.manual_waits) == 1


def test_paired_auth_wait_events_are_forwarded_as_public_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str]] = []
    progress = AuthWaitProgress(
        lambda node, status, _duration: events.append((node, status))
    )
    token = bind_auth_wait_progress(progress)
    try:
        worker = _worker_with(
            {"auth_wait": "started", "reason": "login"},
            {"auth_wait": "started", "reason": "login"},
            {"auth_wait": "finished", "outcome": "authenticated"},
            {"auth_wait": "finished", "outcome": "authenticated"},
            {"ok": True},
        )
        result = _receive(monkeypatch, worker, budget=_Budget())
    finally:
        reset_auth_wait_progress(token)

    assert result["ok"] is True
    assert events == [("tieba.auth", "started"), ("tieba.auth", "completed")]


def test_auth_wait_completion_restores_the_previous_real_node() -> None:
    events: list[tuple[str, str]] = []
    progress = AuthWaitProgress(
        lambda node, status, _duration: events.append((node, status))
    )

    progress.emit("tieba.read", "started", None)
    progress.notify("started")
    progress.notify("completed")
    progress.emit("tieba.read", "completed", 120)

    assert events == [
        ("tieba.read", "started"),
        ("tieba.auth", "started"),
        ("tieba.auth", "completed"),
        ("tieba.read", "started"),
        ("tieba.read", "completed"),
    ]
