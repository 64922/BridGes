"""贴吧工单 01：并发容量等待与真实预算原因回归。"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bridges.chat.run_budget_ledger import (
    RunBudgetLedgerRepository,
    derive_tieba_run_budget_plan,
)
from bridges.contracts.modules import ModuleQueryStatus
from bridges.storage.database import (
    MIGRATIONS,
    SCHEMA_VERSION,
    BridgesDatabase,
)
from bridges.tieba.browser_request_budget import BrowserRequestBudget
from bridges.tieba.budget import TiebaBudget
from bridges.tieba.evidence_projection import build_projection
from bridges.tieba.evidence_search import search_candidates
from bridges.tieba.evidence_time import time_filter_state
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.presenting import render_empty_content
from bridges.tieba.searching import HitDiagnostics, SearchOutcome, query_record

ACCOUNT_ID = "account-1"
RUN_ID = "run-1"


def _budget(
    tmp_path: Path,
    *,
    elapsed: timedelta = timedelta(seconds=45),
) -> tuple[BridgesDatabase, RunBudgetLedgerRepository, TiebaBudget]:
    database = BridgesDatabase(str(tmp_path / "bridges.db"))
    database.initialize()
    ledger = RunBudgetLedgerRepository(database)
    now = datetime.now(UTC)
    plan = derive_tieba_run_budget_plan(created_at=now - elapsed)
    snapshot = ledger.freeze_for_run(
        account_id=ACCOUNT_ID,
        run_id=RUN_ID,
        conversation_id="conversation-1",
        plan=plan,
        now=now,
    )
    budget = TiebaBudget(
        ledger=ledger,
        account_id=ACCOUNT_ID,
        run_id=RUN_ID,
        work_deadline=snapshot.plan.deadline_at
        - timedelta(milliseconds=snapshot.plan.verify_deliver_reserve_ms),
    )
    return database, ledger, budget


def _started(request_id: int, resource_type: str = "xhr") -> dict[str, Any]:
    return {
        "browser_request": "started",
        "request_id": request_id,
        "host": "tieba.baidu.com",
        "method": "GET",
        "resource_type": resource_type,
    }


def _finished(request_id: int, outcome_code: str = "http_200") -> dict[str, Any]:
    return {
        "browser_request": "finished",
        "request_id": request_id,
        "outcome_code": outcome_code,
        "duration_ms": 12,
    }


def test_third_concurrent_request_waits_instead_of_exhausting_budget(
    tmp_path: Path,
) -> None:
    """剩余约 255 秒、上限 2、同时 3 个请求：第三个等待而不是伪耗尽。"""
    _, ledger, budget = _budget(tmp_path)
    sent: list[dict[str, Any]] = []
    scheduler = BrowserRequestBudget(
        budget,
        sent.append,
        deadline=time.monotonic() + 10,
    )

    assert scheduler.handle(_started(1)) is True
    assert scheduler.handle(_started(2)) is True
    assert scheduler.active_count == 2
    # 旧实现把并发占位已满当作预算耗尽；新实现必须有界排队。
    assert scheduler.handle(_started(3)) is None
    assert scheduler.waiting_count == 1
    assert scheduler.denial_code is None
    assert "tieba_browser_run_budget_exhausted" not in sent

    snapshot = ledger.load(ACCOUNT_ID, RUN_ID)
    assert snapshot is not None
    assert snapshot.external_calls_active == 2
    assert snapshot.external_calls_used == 2

    assert scheduler.handle(_finished(1)) is True
    assert scheduler.active_count == 2
    assert scheduler.waiting_count == 0
    assert {
        "type": "request_decision",
        "request_id": 3,
        "allowed": True,
    } in sent

    assert scheduler.handle(_finished(2)) is True
    assert scheduler.handle(_finished(3)) is True
    snapshot = ledger.load(ACCOUNT_ID, RUN_ID)
    assert snapshot is not None
    assert snapshot.external_calls_active == 0
    assert snapshot.external_calls_used == 3
    entries = ledger.list_entries(ACCOUNT_ID, RUN_ID)
    assert len([item for item in entries if item.kind == "external_call"]) == 3
    assert len([item for item in entries if item.kind == "external_call_result"]) == 3
    assert not [item for item in entries if item.kind == "external_call_denied"]


def test_work_time_exhaustion_records_reason_and_counters(tmp_path: Path) -> None:
    _, ledger, budget = _budget(tmp_path, elapsed=timedelta(seconds=301))
    sent: list[dict[str, Any]] = []
    scheduler = BrowserRequestBudget(budget, sent.append, deadline=time.monotonic() + 5)

    assert scheduler.handle(_started(1)) is False
    assert scheduler.denial_code == "tieba_browser_work_time_exhausted"
    assert sent[-1]["allowed"] is False

    entries = ledger.list_entries(ACCOUNT_ID, RUN_ID)
    denied = [item for item in entries if item.kind == "external_call_denied"]
    assert len(denied) == 1
    assert denied[0].outcome_code == "work_time_exhausted"
    assert denied[0].detail["denial_code"] == "tieba_browser_work_time_exhausted"
    assert denied[0].detail["external_calls_active"] == 0
    assert denied[0].detail["external_parallel_max"] == 2
    assert denied[0].detail["remaining_work_ms"] == 0


def test_cancelled_guard_and_invalidated_reasons_are_distinct(
    tmp_path: Path,
) -> None:
    _, ledger, budget = _budget(tmp_path)
    stopped = threading.Event()
    sent: list[dict[str, Any]] = []
    scheduler = BrowserRequestBudget(
        budget,
        sent.append,
        stop_event=stopped,
        deadline=time.monotonic() + 5,
    )
    stopped.set()
    assert scheduler.handle(_started(1)) is False
    assert scheduler.denial_code == "tieba_browser_cancelled"

    sent.clear()
    scheduler = BrowserRequestBudget(
        budget,
        sent.append,
        request_guard=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        deadline=time.monotonic() + 5,
    )
    assert scheduler.handle(_started(2)) is False
    assert scheduler.denial_code == "tieba_browser_request_guard_failed"

    sent.clear()
    scheduler = BrowserRequestBudget(budget, sent.append, deadline=time.monotonic() + 5)
    assert scheduler.handle(_started(3)) is True
    assert scheduler.handle(_finished(3)) is True
    assert ledger.mark_exhausted(
        account_id=ACCOUNT_ID,
        run_id=RUN_ID,
        reason_code="work_time_exhausted",
        now=datetime.now(UTC),
    )
    assert scheduler.handle(_started(4)) is False
    assert scheduler.denial_code == "tieba_browser_run_invalidated"

    denied = [
        item
        for item in ledger.list_entries(ACCOUNT_ID, RUN_ID)
        if item.kind == "external_call_denied"
    ]
    assert [item.outcome_code for item in denied] == [
        "cancelled",
        "guard_failed",
        "run_invalidated",
    ]
    for item in denied:
        assert item.detail["external_parallel_max"] == 2
        assert "remaining_work_ms" in item.detail


def test_pending_and_waiting_release_without_leaks_after_cancel(tmp_path: Path) -> None:
    _, ledger, budget = _budget(tmp_path)
    sent: list[dict[str, Any]] = []
    scheduler = BrowserRequestBudget(budget, sent.append, deadline=time.monotonic() + 5)

    assert scheduler.handle(_started(1)) is True
    assert scheduler.handle(_started(2)) is True
    assert scheduler.handle(_started(3)) is None
    assert scheduler.waiting_count == 1

    scheduler.finish_pending("browser_action_interrupted")

    assert scheduler.active_count == 0
    assert scheduler.waiting_count == 0
    assert sent[-1] == {
        "type": "request_decision",
        "request_id": 3,
        "allowed": False,
    }
    snapshot = ledger.load(ACCOUNT_ID, RUN_ID)
    assert snapshot is not None
    assert snapshot.external_calls_active == 0
    entries = ledger.list_entries(ACCOUNT_ID, RUN_ID)
    assert [item.kind for item in entries].count("external_call") == 2
    assert [item.kind for item in entries].count("external_call_result") == 2


def test_waiting_expires_at_action_deadline_without_leaking(tmp_path: Path) -> None:
    _, ledger, budget = _budget(tmp_path)
    sent: list[dict[str, Any]] = []
    scheduler = BrowserRequestBudget(
        budget,
        sent.append,
        deadline=time.monotonic() + 0.05,
    )

    assert scheduler.handle(_started(1)) is True
    assert scheduler.handle(_started(2)) is True
    assert scheduler.handle(_started(3)) is None
    time.sleep(0.08)

    assert scheduler.tick() == "tieba_browser_work_time_exhausted"
    assert scheduler.waiting_count == 0
    scheduler.finish_pending("browser_action_interrupted")

    snapshot = ledger.load(ACCOUNT_ID, RUN_ID)
    assert snapshot is not None
    assert snapshot.external_calls_active == 0
    denied = [
        item
        for item in ledger.list_entries(ACCOUNT_ID, RUN_ID)
        if item.kind == "external_call_denied"
    ]
    assert len(denied) == 1
    assert denied[0].outcome_code == "work_time_exhausted"


class _SequencedSearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search_public(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome:
        del account_id, reason, stop_event, deadline
        self.queries.append(query)
        if len(self.queries) == 1:
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.EMPTY,
                    evidence_count=0,
                ),
                hits=(),
                candidates=(),
                rejected=(),
                diagnostics=HitDiagnostics(),
            )
        return SearchOutcome(
            record=query_record(
                query=query,
                status=ModuleQueryStatus.ERROR,
                evidence_count=0,
                error_code="tieba_browser_work_time_exhausted",
                error_message="本轮剩余贴吧工作预算已耗尽，未发起后续页面请求。",
                retryable=False,
            ),
            hits=(),
            candidates=(),
            rejected=(),
            diagnostics=HitDiagnostics(),
        )


def test_true_budget_exhaustion_stops_synonym_rounds_and_does_not_claim_none_sent(
) -> None:
    port = _SequencedSearch()
    analysis = parse_tieba_request("华东交通大学吧 宿舍")
    attempts = search_candidates(
        port,
        ACCOUNT_ID,
        analysis,
        stop_event=None,
        deadline_seconds=50,
    )

    assert len(port.queries) == 2
    assert attempts.partial is False
    project = build_projection(
        analysis=analysis,
        records=attempts.records,
        confirmed=[],
        rejected=list(attempts.rejected),
        unconfirmed=[],
        sections=[],
        time_filter=time_filter_state(analysis, []),
        official_checks=[],
        attempts=attempts,
    )
    assert project.status.value == "empty"
    assert project.retryable is False
    assert project.error_code is None
    assert project.error_message is None
    assert any(
        "未发起后续页面请求" in note for note in project.evidence_boundary
    )
    content = render_empty_content(project)
    assert "未发起后续页面请求" in content
    assert "未发起新的贴吧请求" not in content


def test_schema_v90_preserves_entries_and_accepts_denial_kind(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v89.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        for version in range(1, 90):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '89')"
        )
        connection.execute(
            """
            INSERT INTO run_budget_ledger (
                run_id, account_id, conversation_id, budget_class,
                total_budget_ms, deadline_at, verify_deliver_reserve_ms,
                model_call_limit, external_parallel_max, candidate_screen_max,
                deep_read_max, adjustment_rounds_max, transient_retry_max,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, 'normal', 300000, ?, 45000,
                4, 2, 4, 4, 1, 1, ?, ?
            )
            """,
            (
                RUN_ID,
                ACCOUNT_ID,
                "conversation-1",
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO run_budget_entries (
                run_id, seq, account_id, kind, purpose, created_at
            ) VALUES (?, 1, ?, 'external_call', 'browser', ?)
            """,
            (RUN_ID, ACCOUNT_ID, datetime.now(UTC).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()

    database = BridgesDatabase(str(path))
    assert database.initialize() == SCHEMA_VERSION
    rows = database.connection.execute(
        "SELECT seq, kind FROM run_budget_entries ORDER BY seq"
    ).fetchall()
    assert [(row["seq"], row["kind"]) for row in rows] == [(1, "external_call")]
    database.connection.execute(
        """
        INSERT INTO run_budget_entries (
            run_id, seq, account_id, kind, purpose, outcome_code,
            detail_json, created_at
        ) VALUES (?, 2, ?, 'external_call_denied', 'browser',
                  'work_time_exhausted', '{}', ?)
        """,
        (RUN_ID, ACCOUNT_ID, datetime.now(UTC).isoformat()),
    )
    database.connection.commit()
    assert database.connection.execute(
        "SELECT COUNT(*) AS count FROM run_budget_entries"
    ).fetchone()["count"] == 2
