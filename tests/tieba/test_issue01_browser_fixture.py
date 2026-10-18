"""本地浏览器响应夹具：路由等待容量时不得阻塞完成事件而死锁。"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from bridges.chat.run_budget_ledger import (
    RunBudgetLedgerRepository,
    derive_tieba_run_budget_plan,
)
from bridges.storage.database import BridgesDatabase
from bridges.tieba.browser_request_budget import BrowserRequestBudget
from bridges.tieba.browser_requests import BrowserRequestTracker
from bridges.tieba.budget import TiebaBudget

HTML = b"""<!doctype html><html><head><link rel="icon" href="data:,"></head><body>
<script>
Promise.all([
  fetch('/a?ms=500').then(r => r.text()),
  fetch('/b?ms=500').then(r => r.text()),
  fetch('/c?ms=50').then(r => r.text()),
]).then(values => { document.title = 'done:' + values.join(','); });
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 接口
        _delay(self.path)
        if self.path.startswith("/index"):
            body = HTML
            content_type = "text/html; charset=utf-8"
        else:
            body = ("ok:" + self.path).encode()
            content_type = "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return None


def _delay(path: str) -> None:
    if "ms=" not in path:
        return
    raw = path.split("ms=", 1)[1].split("&", 1)[0]
    time.sleep(int(raw) / 1000)


def test_route_wait_for_capacity_pumps_completion_events(
    tmp_path: Path,
) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    database = BridgesDatabase(str(tmp_path / "bridges.db"))
    database.initialize()
    ledger = RunBudgetLedgerRepository(database)
    now = datetime.now(UTC)
    plan = derive_tieba_run_budget_plan(created_at=now - timedelta(seconds=45))
    snapshot = ledger.freeze_for_run(
        account_id="account-1",
        run_id="run-1",
        conversation_id="conversation-1",
        plan=plan,
        now=now,
    )
    budget = TiebaBudget(
        ledger=ledger,
        account_id="account-1",
        run_id="run-1",
        work_deadline=snapshot.plan.deadline_at
        - timedelta(milliseconds=snapshot.plan.verify_deliver_reserve_ms),
    )

    closed = threading.Event()
    holder: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    peak_active = 0
    saw_waiting = False

    def send(decision: dict[str, Any]) -> None:
        holder["tracker"].decide(
            int(decision["request_id"]),
            bool(decision["allowed"]),
        )

    scheduler = BrowserRequestBudget(
        budget,
        send,
        deadline=time.monotonic() + 15,
    )

    def emit(event: object) -> None:
        nonlocal peak_active, saw_waiting
        assert isinstance(event, dict)
        events.append(event)
        scheduler.handle(event)
        peak_active = max(peak_active, scheduler.active_count)
        saw_waiting = saw_waiting or scheduler.waiting_count > 0

    tracker = BrowserRequestTracker(closed, emit)
    holder["tracker"] = tracker

    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    headless=True,
                    channel="chrome",
                    args=[
                        "--host-resolver-rules=MAP tieba.baidu.com 127.0.0.1",
                        "--no-proxy-server",
                    ],
                )
            except Exception as error:  # pragma: no cover - 环境缺少 Chrome
                pytest.skip(f"Chrome 不可用：{error}")
            try:
                context = browser.new_context()
                tracker.install(context)
                tracker.set_deadline(time.monotonic() + 15)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(
                    f"http://tieba.baidu.com:{port}/index",
                    wait_until="domcontentloaded",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.title.startsWith('done:')",
                    timeout=10000,
                )
                tracker.wait_until_idle(context, time.monotonic() + 5)
            finally:
                browser.close()
    finally:
        server.shutdown()

    assert peak_active <= 2
    assert saw_waiting, "第三个并发请求应该进入等待队列"
    assert scheduler.active_count == 0
    assert scheduler.waiting_count == 0
    snapshot = ledger.load("account-1", "run-1")
    assert snapshot is not None
    assert snapshot.external_calls_active == 0
    assert snapshot.external_calls_used == 4
    entries = ledger.list_entries("account-1", "run-1")
    assert len([item for item in entries if item.kind == "external_call"]) == 4
    assert len([item for item in entries if item.kind == "external_call_result"]) == 4
    assert not [item for item in entries if item.kind == "external_call_denied"]
    assert [event for event in events if event.get("browser_request") == "started"]
