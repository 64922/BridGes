"""独立验收：长响应和等待期间取消不得丢证据或启动旧任务。"""

import io
import json
import queue
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bridges.external_call_decision import ExternalCallDecision
from bridges.tieba.browser import BrowserTiebaSearch
from bridges.tieba.browser_runtime import TiebaBrowserError, TiebaBrowserRuntime, _BrowserWorker
from bridges.tieba.budget import TiebaBudget, bind_request_guard


def test_large_worker_json_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = {"replies": [{"content": "宿舍讨论" * 30_000}]}

    class Process:
        stdout = io.StringIO(json.dumps(value, ensure_ascii=False) + "\n")
        stdin = io.StringIO()

        def poll(self) -> None:
            return None

    runtime = TiebaBrowserRuntime(tmp_path)
    monkeypatch.setattr("bridges.tieba.browser_runtime.subprocess.Popen", lambda *a, **k: Process())
    monkeypatch.setattr(runtime, "_dispose", lambda _worker: None)
    try:
        assert runtime.request("alice", {"action": "read"}) == value
    finally:
        runtime.close()


def test_cancelled_while_waiting_for_account_does_not_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    stopped = threading.Event()

    class AccountLock:
        def acquire(self, **kwargs: Any) -> bool:
            stopped.set()
            return True

        def release(self) -> None:
            pass

    runtime._account_locks["alice"] = AccountLock()  # type: ignore[assignment]
    spawned: list[Path] = []
    monkeypatch.setattr(runtime, "_spawn", lambda profile: spawned.append(profile))
    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request("alice", {"action": "search"}, stop_event=stopped)
    assert failure.value.code == "tieba_browser_cancelled"
    assert not spawned


def test_cancelled_during_startup_disposes_worker_without_sending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    stopped = threading.Event()
    sent: list[object] = []
    disposed: list[object] = []

    class Process:
        def poll(self) -> None:
            return None

    worker = _BrowserWorker(Process(), queue.Queue())  # type: ignore[arg-type]

    def spawn(_profile: Path) -> _BrowserWorker:
        stopped.set()
        return worker

    monkeypatch.setattr(runtime, "_spawn", spawn)
    monkeypatch.setattr(runtime, "_send", lambda *args: sent.append(args))
    monkeypatch.setattr(runtime, "_dispose", lambda current: disposed.append(current))
    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request("alice", {"action": "search"}, stop_event=stopped)
    assert failure.value.code == "tieba_browser_cancelled"
    assert not sent
    assert disposed == [worker]
    assert not runtime._workers


def test_runtime_logs_browser_request_result_and_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _RequestLedger()
    budget = TiebaBudget(
        ledger=ledger,  # type: ignore[arg-type]
        account_id="alice",
        run_id="run-1",
        work_deadline=datetime.now(UTC) + timedelta(minutes=5),
    )
    runtime = TiebaBrowserRuntime(tmp_path)
    process = _Process()
    worker = _BrowserWorker(process, queue.Queue())  # type: ignore[arg-type]
    worker.output.put(
        json.dumps(
            {
                "browser_request": "started",
                "request_id": 9,
                "host": "tieba.baidu.com",
                "method": "GET",
                "resource_type": "document",
            }
        )
    )
    worker.output.put(
        json.dumps(
            {
                "browser_request": "finished",
                "request_id": 9,
                "outcome_code": "http_200",
                "duration_ms": 75,
            }
        )
    )
    worker.output.put(json.dumps({"ok": True}))
    runtime._workers["alice"] = worker
    monkeypatch.setattr(runtime, "_dispose", lambda _worker: None)

    assert runtime.request("alice", {"action": "search"}, request_budget=budget) == {
        "ok": True
    }

    assert len(ledger.registrations) == 1
    assert ledger.registrations[0]["purpose"] == (
        "tieba_browser_tieba_baidu_com_document_get"
    )
    assert len(ledger.results) == 1
    assert ledger.results[0]["outcome_code"] == "http_200"
    assert ledger.results[0]["duration_ms"] == 75
    assert '"allowed": true' in process.stdin.getvalue()


def test_runtime_blocks_browser_request_when_work_time_is_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _RequestLedger(allowed=False)
    budget = TiebaBudget(
        ledger=ledger,  # type: ignore[arg-type]
        account_id="alice",
        run_id="run-1",
        work_deadline=datetime.now(UTC) + timedelta(minutes=5),
    )
    runtime = TiebaBrowserRuntime(tmp_path)
    process = _Process()
    worker = _BrowserWorker(process, queue.Queue())  # type: ignore[arg-type]
    worker.output.put(
        json.dumps(
            {
                "browser_request": "started",
                "request_id": 3,
                "host": "tieba.baidu.com",
                "method": "GET",
                "resource_type": "xhr",
            }
        )
    )
    runtime._workers["alice"] = worker
    monkeypatch.setattr(runtime, "_dispose", lambda _worker: None)

    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request("alice", {"action": "search"}, request_budget=budget)

    assert failure.value.code == "tieba_browser_work_time_exhausted"
    assert ledger.registrations
    assert ledger.results == []
    assert '"allowed": false' in process.stdin.getvalue()


def test_runtime_blocks_browser_request_when_task_version_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _RequestLedger()
    budget = TiebaBudget(
        ledger=ledger,  # type: ignore[arg-type]
        account_id="alice",
        run_id="run-1",
        work_deadline=datetime.now(UTC) + timedelta(minutes=5),
        request_guard=lambda: "tieba_browser_task_changed",
    )
    runtime = TiebaBrowserRuntime(tmp_path)
    process = _Process()
    worker = _BrowserWorker(process, queue.Queue())  # type: ignore[arg-type]
    worker.output.put(
        json.dumps(
            {
                "browser_request": "started",
                "request_id": 11,
                "host": "tieba.baidu.com",
                "method": "GET",
                "resource_type": "document",
            }
        )
    )
    runtime._workers["alice"] = worker
    monkeypatch.setattr(runtime, "_dispose", lambda _worker: None)

    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request("alice", {"action": "search"}, request_budget=budget)

    assert failure.value.code == "tieba_browser_task_changed"
    assert ledger.registrations == []
    assert ledger.results == []
    assert '"allowed": false' in process.stdin.getvalue()


def test_runtime_blocks_browser_request_when_task_changes_without_a_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    process = _Process()
    worker = _BrowserWorker(process, queue.Queue())  # type: ignore[arg-type]
    worker.output.put(
        json.dumps(
            {
                "browser_request": "started",
                "request_id": 12,
                "host": "tieba.baidu.com",
                "method": "GET",
                "resource_type": "document",
            }
        )
    )
    runtime._workers["alice"] = worker
    monkeypatch.setattr(runtime, "_dispose", lambda _worker: None)

    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request(
            "alice",
            {"action": "search"},
            request_guard=lambda: "tieba_browser_task_changed",
        )

    assert failure.value.code == "tieba_browser_task_changed"
    assert '"allowed": false' in process.stdin.getvalue()


def test_search_forwards_bound_guard_without_a_budget() -> None:
    def guard() -> str:
        return "tieba_browser_task_changed"

    class Runtime:
        def request(self, *_args: Any, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["request_budget"] is None
            assert kwargs["request_guard"] is guard
            raise TiebaBrowserError("tieba_browser_task_changed", "任务已变化。")

    class PublicSearch:
        calls = 0

        def search_public(self, *_args: Any, **_kwargs: Any) -> Any:
            self.calls += 1
            raise AssertionError("任务变化后不应启动公开检索回退")

    public = PublicSearch()
    search = BrowserTiebaSearch(Runtime(), public)  # type: ignore[arg-type]
    with bind_request_guard(guard):
        outcome = search.search_public(
            "alice",
            query="site:tieba.baidu.com/p/ 华东交通大学吧 宿舍 热水",
            reason="task guard regression",
        )

    assert outcome.record.error_code == "tieba_browser_task_changed"
    assert public.calls == 0


class _Process:
    def __init__(self) -> None:
        self.stdin = io.StringIO()

    def poll(self) -> None:
        return None


class _RequestLedger:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.registrations: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []

    def register_external_call_decision(
        self, **values: Any
    ) -> ExternalCallDecision:
        self.registrations.append(values)
        return ExternalCallDecision(
            allowed=self.allowed,
            reason="allowed" if self.allowed else "work_time_exhausted",
        )

    def record_external_call_denial(self, **_values: Any) -> None:
        return None

    def load(self, _account_id: str, _run_id: str) -> None:
        return None

    def record_external_call_result(self, **values: Any) -> None:
        self.results.append(values)
