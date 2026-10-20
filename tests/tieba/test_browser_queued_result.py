"""排队请求事件不能冒充采集结果，也不能污染同一 worker 的下一轮。"""

from __future__ import annotations

import json
import queue
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.browser import BrowserTiebaSearch
from bridges.tieba.browser_errors import TiebaBrowserError
from bridges.tieba.browser_receive import receive_result
from bridges.tieba.budget import bind_request_budget
from tests.tieba.test_issue01_browser_concurrency import (
    ACCOUNT_ID,
    RUN_ID,
    _budget,
    _finished,
    _started,
)


def _enqueue_requests(output: queue.Queue[str | None]) -> None:
    # 同一页面同时发出三个请求，真实账本只允许两个在途，第三个必须排队。
    for event in (
        _started(1),
        _started(2),
        _started(3),
        _finished(1),
        _finished(2),
        _finished(3),
    ):
        output.put(json.dumps(event))


def test_queued_event_does_not_become_successful_empty_search(tmp_path: Path) -> None:
    _, ledger, budget = _budget(tmp_path)
    worker = SimpleNamespace(output=queue.Queue())
    sent: list[dict[str, Any]] = []

    class Runtime:
        def request(self, _account: str, payload: Any, **kwargs: Any) -> dict[str, Any]:
            _enqueue_requests(worker.output)
            worker.output.put(
                json.dumps(
                    {
                        "hits": [
                            {
                                "url": "https://tieba.baidu.com/p/101",
                                "title": payload["terms"],
                                "snippet": "华东交通大学吧 宿舍条件讨论",
                            }
                        ]
                    }
                )
            )
            return receive_result(
                worker,
                stop_event=None,
                deadline=time.monotonic() + 5,
                request_budget=kwargs["request_budget"],
                request_guard=None,
                send=sent.append,
            )

    class Public:
        def search_public(self, *_: Any, **__: Any) -> Any:
            raise AssertionError("已取得真实候选，不应回退公开索引")

    search = BrowserTiebaSearch(Runtime(), Public())  # type: ignore[arg-type]
    with bind_request_budget(budget):
        for terms in ("宿舍 条件", "寝室 条件"):
            outcome = search.search_public(
                ACCOUNT_ID,
                query=f"site:tieba.baidu.com/p/ 华东交通大学吧 {terms}",
                reason="回归截图中的连续查询",
            )
            assert outcome.record.status is ModuleQueryStatus.SUCCESS
            assert len(outcome.candidates) == 1
            assert outcome.candidates[0].title == terms
            assert worker.output.empty()

    assert all(item["allowed"] for item in sent)
    snapshot = ledger.load(ACCOUNT_ID, RUN_ID)
    assert snapshot is not None
    assert snapshot.external_calls_active == 0
    assert snapshot.external_calls_used == 6
    entries = ledger.list_entries(ACCOUNT_ID, RUN_ID)
    results = [item for item in entries if item.kind == "external_call_result"]
    assert len(results) == 6
    assert all(item.outcome_code == "http_200" for item in results)


@pytest.mark.parametrize("terminal", ["error", "exit"])
def test_failure_after_queued_event_is_not_reported_as_success(
    tmp_path: Path,
    terminal: str,
) -> None:
    _, ledger, budget = _budget(tmp_path)
    worker = SimpleNamespace(output=queue.Queue())
    sent: list[dict[str, Any]] = []
    for request_id in (1, 2, 3):
        worker.output.put(json.dumps(_started(request_id)))
    worker.output.put(
        json.dumps({"error": "tieba_browser_timeout", "message": "搜索超时"})
        if terminal == "error"
        else None
    )

    with pytest.raises(TiebaBrowserError) as caught:
        receive_result(
            worker,
            stop_event=None,
            deadline=time.monotonic() + 5,
            request_budget=budget,
            request_guard=None,
            send=sent.append,
        )

    assert caught.value.code == (
        "tieba_browser_timeout" if terminal == "error" else "tieba_browser_unavailable"
    )
    snapshot = ledger.load(ACCOUNT_ID, RUN_ID)
    assert snapshot is not None
    assert snapshot.external_calls_active == 0
    assert sent[-1] == {"type": "request_decision", "request_id": 3, "allowed": False}
