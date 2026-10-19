"""工单 14：账户隔离、删除栅栏与专用浏览器资源生命周期回归。"""

from __future__ import annotations

import json
import queue
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.contracts.modules import ModuleQueryStatus
from bridges.runtime.lock import DataDirectoryLock
from bridges.tieba.browser import BrowserTiebaSearch
from bridges.tieba.browser_runtime import (
    ACCOUNT_DELETION_MARKER,
    TiebaBrowserError,
    TiebaBrowserRuntime,
    _BrowserWorker,
    fence_account_profile,
    profile_path,
)
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaQuestionAnalysis,
    TiebaReadResult,
    TiebaSearchHit,
)
from bridges.tieba.evidence import read_candidates
from bridges.tieba.evidence_search import SearchAttempts
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.reading import TiebaThreadReader
from bridges.tieba.searching import HitDiagnostics, SearchOutcome


class _LiveProcess:
    """最少进程替身：可由测试标记退出。"""

    def __init__(self) -> None:
        self.alive = True

    def poll(self) -> int | None:
        return None if self.alive else 1


def _runtime_spy(runtime: TiebaBrowserRuntime, monkeypatch: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "spawned": [],
        "sent": [],
        "disposed": [],
    }

    def spawn(profile: Path) -> _BrowserWorker:
        worker = _BrowserWorker(_LiveProcess(), queue.Queue())  # type: ignore[arg-type]
        state["spawned"].append((profile, worker))
        return worker

    def dispose(worker: _BrowserWorker) -> None:
        state["disposed"].append(worker)

    monkeypatch.setattr(runtime, "_spawn", spawn)
    monkeypatch.setattr(runtime, "_dispose", dispose)
    return state


def test_dual_account_requests_use_isolated_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    state = _runtime_spy(runtime, monkeypatch)
    intervals: dict[str, tuple[float, float]] = {}

    def account_for(worker: _BrowserWorker) -> str:
        for profile, candidate in state["spawned"]:
            if candidate is worker:
                return "alice" if profile == profile_path(tmp_path, "alice") else "bob"
        raise AssertionError("未知 worker")

    def send(worker: _BrowserWorker, payload: dict[str, Any]) -> None:
        state["sent"].append((account_for(worker), payload["token"]))
        started = time.monotonic()
        time.sleep(0.12)
        intervals[account_for(worker)] = (started, time.monotonic())
        worker.output.put(
            json.dumps({"account": account_for(worker), "token": payload["token"]})
        )

    monkeypatch.setattr(runtime, "_send", send)
    barrier = threading.Barrier(2)
    results: dict[str, dict[str, Any]] = {}

    def request(account: str) -> None:
        barrier.wait()
        results[account] = runtime.request(
            account,
            {"action": "search", "token": account},
            deadline=time.monotonic() + 2,
        )

    threads = [threading.Thread(target=request, args=(name,)) for name in ("alice", "bob")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    runtime.close()

    assert results == {
        "alice": {"account": "alice", "token": "alice"},
        "bob": {"account": "bob", "token": "bob"},
    }
    profiles = {profile for profile, _worker in state["spawned"]}
    assert profiles == {profile_path(tmp_path, "alice"), profile_path(tmp_path, "bob")}
    assert intervals["alice"][0] < intervals["bob"][1]
    assert intervals["bob"][0] < intervals["alice"][1]
    assert state["sent"] == [("alice", "alice"), ("bob", "bob")] or state["sent"] == [
        ("bob", "bob"),
        ("alice", "alice"),
    ]


def test_same_account_concurrent_requests_are_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    state = _runtime_spy(runtime, monkeypatch)
    intervals: list[tuple[float, float]] = []

    def send(worker: _BrowserWorker, payload: dict[str, Any]) -> None:
        started = time.monotonic()
        time.sleep(0.08)
        intervals.append((started, time.monotonic()))
        worker.output.put(json.dumps({"token": payload["token"]}))

    monkeypatch.setattr(runtime, "_send", send)
    barrier = threading.Barrier(2)
    results: list[dict[str, Any]] = []

    def request(token: str) -> None:
        barrier.wait()
        results.append(
            runtime.request(
                "alice",
                {"action": "search", "token": token},
                deadline=time.monotonic() + 2,
            )
        )

    threads = [threading.Thread(target=request, args=(token,)) for token in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    runtime.close()

    assert sorted(item["token"] for item in results) == ["one", "two"]
    assert len(state["spawned"]) == 1
    assert len(intervals) == 2
    first, second = sorted(intervals)
    assert first[1] <= second[0]


@pytest.mark.parametrize("source", ["runtime", "marker"])
def test_account_deletion_aborts_inflight_browser_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    state = _runtime_spy(runtime, monkeypatch)
    requested = threading.Event()

    def send(_worker: _BrowserWorker, _payload: dict[str, Any]) -> None:
        requested.set()

    monkeypatch.setattr(runtime, "_send", send)
    failures: list[TiebaBrowserError] = []

    def request() -> None:
        try:
            runtime.request(
                "alice",
                {"action": "search", "terms": "宿舍"},
                deadline=time.monotonic() + 1.5,
            )
        except TiebaBrowserError as error:
            failures.append(error)

    worker_thread = threading.Thread(target=request)
    worker_thread.start()
    assert requested.wait(timeout=1)
    if source == "runtime":
        runtime.fence_account("alice")
    else:
        fence_account_profile(tmp_path, "alice")
    worker_thread.join(timeout=2)

    assert not worker_thread.is_alive()
    assert [error.code for error in failures] == ["tieba_browser_account_deleting"]
    assert state["disposed"] == [state["spawned"][0][1]]
    assert "alice" not in runtime._workers
    runtime.close()



def test_account_deletion_during_browser_start_disposes_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    state = _runtime_spy(runtime, monkeypatch)

    def spawn(profile: Path) -> _BrowserWorker:
        fence_account_profile(tmp_path, "alice")
        worker = _BrowserWorker(_LiveProcess(), queue.Queue())  # type: ignore[arg-type]
        state["spawned"].append((profile, worker))
        return worker

    monkeypatch.setattr(runtime, "_spawn", spawn)
    monkeypatch.setattr(
        runtime, "_send", lambda worker, payload: state["sent"].append((worker, payload))
    )

    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request("alice", {"action": "search", "terms": "宿舍"})

    assert failure.value.code == "tieba_browser_account_deleting"
    assert state["sent"] == []
    assert state["disposed"] == [state["spawned"][0][1]]
    assert "alice" not in runtime._workers
    runtime.close()


def test_deleted_account_cannot_recreate_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    profile = profile_path(tmp_path, "alice")
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Cookies").write_text("session", encoding="utf-8")
    runtime.remove_account("alice")
    state = _runtime_spy(runtime, monkeypatch)

    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request("alice", {"action": "search", "terms": "宿舍"})

    assert failure.value.code == "tieba_browser_account_deleting"
    assert state["spawned"] == []
    assert {entry.name for entry in profile.iterdir()} == {
        ".bridges.lock",
        ACCOUNT_DELETION_MARKER,
    }


def test_session_cleanup_busy_is_retryable_and_other_profile_is_untouched(
    tmp_path: Path,
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    alice = profile_path(tmp_path, "alice")
    (alice / "Default").mkdir(parents=True)
    (alice / "Default" / "Cookies").write_text("alice-session", encoding="utf-8")
    bob = profile_path(tmp_path, "bob")
    (bob / "Default").mkdir(parents=True)
    (bob / "Default" / "Cookies").write_text("bob-session", encoding="utf-8")

    with DataDirectoryLock(alice):
        with pytest.raises(TiebaBrowserError) as failure:
            runtime.remove_account("alice")
        assert failure.value.code == "tieba_browser_busy"
        assert (alice / "Default" / "Cookies").exists()

    runtime.remove_account("alice")
    assert {entry.name for entry in alice.iterdir()} == {
        ".bridges.lock",
        ACCOUNT_DELETION_MARKER,
    }
    assert (bob / "Default" / "Cookies").read_text(encoding="utf-8") == "bob-session"


def test_worker_crash_is_replaced_and_old_process_disposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    state = _runtime_spy(runtime, monkeypatch)
    monkeypatch.setattr(runtime, "_send", lambda _worker, _payload: None)
    monkeypatch.setattr(runtime, "_receive", lambda *_args: {"ok": True})

    assert runtime.request("alice", {"action": "search"}) == {"ok": True}
    first_profile, first_worker = state["spawned"][0]
    first_worker.process.alive = False
    assert runtime.request("alice", {"action": "search"}) == {"ok": True}
    runtime.close()

    assert len(state["spawned"]) == 2
    assert state["spawned"][1][0] == first_profile
    assert first_worker in state["disposed"]


def test_runtime_close_disposes_all_sessions_and_rejects_new_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    state = _runtime_spy(runtime, monkeypatch)
    monkeypatch.setattr(runtime, "_send", lambda _worker, _payload: None)
    monkeypatch.setattr(runtime, "_receive", lambda *_args: {"ok": True})

    runtime.request("alice", {"action": "search"})
    runtime.request("bob", {"action": "search"})
    runtime.close()

    assert {worker for _profile, worker in state["spawned"]} == set(state["disposed"])
    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request("alice", {"action": "search"})
    assert failure.value.code == "tieba_browser_busy"


def test_browser_search_does_not_fall_back_after_account_deletion() -> None:
    class Runtime:
        def request(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise TiebaBrowserError(
                "tieba_browser_account_deleting", "账户正在删除，贴吧专用会话不可继续使用。"
            )

    class Public:
        calls = 0

        def search_public(self, *_args: Any, **_kwargs: Any) -> SearchOutcome:
            self.calls += 1
            raise AssertionError("账户删除后不得回退到公开检索")

    public = Public()
    outcome = BrowserTiebaSearch(Runtime(), public).search_public(  # type: ignore[arg-type]
        "alice",
        query="site:tieba.baidu.com/p/ 华东交通大学吧 宿舍",
        reason="账户删除回归",
    )

    assert outcome.record.status is ModuleQueryStatus.ERROR
    assert outcome.record.error_code == "tieba_browser_account_deleting"
    assert outcome.record.retryable is False
    assert public.calls == 0


def test_read_candidates_stops_after_account_deletion() -> None:
    analysis: TiebaQuestionAnalysis = parse_tieba_request("华东交通大学吧 宿舍条件")
    hits = (
        TiebaSearchHit(url="https://tieba.baidu.com/p/1", title="宿舍", snippet=""),
        TiebaSearchHit(url="https://tieba.baidu.com/p/2", title="宿舍", snippet=""),
    )
    attempts = SearchAttempts(
        hits=hits,
        rejected=(),
        records=[],
        diagnostics=HitDiagnostics(raw_hits=2, usable=2),
    )

    class Reader(TiebaThreadReader):
        def __init__(self) -> None:
            self.calls = 0

        def read(
            self,
            url: str,
            *,
            pages_limit: int = 2,
            stop_event: object | None = None,
            deadline: float | None = None,
        ) -> TiebaReadResult:
            del pages_limit, stop_event, deadline
            self.calls += 1
            return TiebaReadResult(
                url=url,
                thread_id=url.rsplit("/", 1)[-1],
                status=ReadStatus.ERROR,
                error_code="tieba_browser_account_deleting",
                error_message="账户正在删除，贴吧专用会话不可继续使用。",
                retrieved_at=datetime.now(UTC),
            )

    reader = Reader()
    outcome = read_candidates(
        reader,
        attempts,
        analysis=analysis,
        stop_event=None,
        deadline_seconds=5,
    )

    assert reader.calls == 1
    assert outcome.partial is True
    assert outcome.closeout_reason == "account_deleting"
