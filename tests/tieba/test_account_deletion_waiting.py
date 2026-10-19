"""工单 14：认证等待与分页等待期间账户删除栅栏。"""

from __future__ import annotations

import json
import queue
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.tieba.browser_runtime import (
    TiebaBrowserError,
    TiebaBrowserRuntime,
    _BrowserWorker,
)


@pytest.mark.parametrize(
    "event",
    [
        {"auth_wait": "started", "reason": "login"},
        {
            "browser_request": "started",
            "request_id": 1,
            "host": "tieba.baidu.com",
            "method": "GET",
            "resource_type": "document",
        },
    ],
)
def test_account_deletion_aborts_during_wait(
    event: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = TiebaBrowserRuntime(tmp_path)
    worker = _BrowserWorker(SimpleNamespace(poll=lambda: None), queue.Queue())
    worker.output.put(json.dumps(event))
    monkeypatch.setattr(runtime, "_spawn", lambda _profile: worker)
    monkeypatch.setattr(runtime, "_send", lambda _worker, _payload: None)
    monkeypatch.setattr(runtime, "_dispose", lambda _worker: None)
    calls = 0

    def guard() -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            runtime.fence_account("alice")
        return None

    with pytest.raises(TiebaBrowserError) as failure:
        runtime.request(
            "alice",
            {"action": "search"},
            deadline=time.monotonic() + 5,
            request_guard=guard,
        )

    assert failure.value.code == "tieba_browser_account_deleting"
    assert calls >= 2
