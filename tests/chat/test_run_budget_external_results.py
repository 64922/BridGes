"""共享运行账本对外部请求结果保留耗时。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bridges.chat.run_budget_ledger import (
    RunBudgetClass,
    RunBudgetLedgerRepository,
    derive_run_budget_plan,
)
from bridges.storage.database import BridgesDatabase


def test_external_result_duration_persists_in_run_entries(tmp_path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    ledger = RunBudgetLedgerRepository(database)
    now = datetime.now(UTC)
    ledger.freeze_for_run(
        account_id="account-1",
        run_id="run-1",
        conversation_id="conversation-1",
        plan=derive_run_budget_plan(
            RunBudgetClass.NORMAL, deadline_at=now + timedelta(minutes=5)
        ),
        now=now,
    )

    assert ledger.register_external_call(
        account_id="account-1",
        run_id="run-1",
        call_key="tieba.browser.request:1",
        purpose="tieba_browser_tieba_baidu_com_document_get",
        now=now,
    )
    ledger.record_external_call_result(
        account_id="account-1",
        run_id="run-1",
        call_key="tieba.browser.request:1",
        outcome_code="http_200",
        duration_ms=128,
        now=now + timedelta(milliseconds=128),
    )

    result = ledger.list_entries("account-1", "run-1")[-1]
    assert result.kind == "external_call_result"
    assert result.outcome_code == "http_200"
    assert result.duration_ms == 128
