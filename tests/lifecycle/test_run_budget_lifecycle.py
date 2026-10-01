"""运行预算及分批冻结计划的真实加密备份与恢复。"""

from datetime import UTC, datetime, timedelta

from harness import Harness

from bridges.chat.run_budget_ledger import (
    RunBudgetClass,
    RunBudgetLedgerRepository,
    RunBudgetRecipeCosts,
    derive_run_budget_plan,
)


def test_budget_backup_restore_keeps_plan_counters_and_entries(tmp_path) -> None:
    harness = Harness(tmp_path)
    ledger = RunBudgetLedgerRepository(harness.database)
    now = datetime.now(UTC)
    ledger.freeze_for_run(
        account_id=harness.acc1, run_id="r", conversation_id="c", now=now,
        plan=derive_run_budget_plan(
            RunBudgetClass.NORMAL, deadline_at=now + timedelta(seconds=60),
            recipe_costs=RunBudgetRecipeCosts("v1", 1, 1, 1, 100, 20),
        ),
    )
    assert ledger.register_model_call(
        account_id=harness.acc1, run_id="r", call_key="m1", purpose="generation", now=now
    )
    ledger.record_model_call_result(
        account_id=harness.acc1, run_id="r", call_key="m1", input_tokens=15, output_tokens=5,
        now=now,
    )
    assert ledger.freeze_batch_plan(
        harness.acc1, "r", "p1", {"source_refs": ["s1:v1:1-2"]}, now=now
    )
    snapshot = ledger.load(harness.acc1, "r")
    entries = ledger.list_entries(harness.acc1, "r")
    _, backup = harness.backup.create_backup("预算恢复口令")
    ledger.close(account_id=harness.acc1, run_id="r", now=now)
    preview = harness.backup.restore_backup("预算恢复口令", backup, confirmation="恢复")
    assert preview.ok, preview.reasons
    assert ledger.load(harness.acc1, "r") == snapshot
    assert ledger.list_entries(harness.acc1, "r") == entries
