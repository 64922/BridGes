"""显式分批计划的完整性、共享守卫和完成引用恢复验证。"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bridges.chat.budget import RunBudget
from bridges.chat.budget_batches import (
    BatchReceipt,
    BatchSourceRef,
    BoundedBatchExecutor,
    BoundedBatchPlan,
    BudgetBatch,
)
from bridges.chat.run_budget_ledger import (
    RunBudgetClass,
    RunBudgetLedgerRepository,
    RunBudgetRecipeCosts,
    derive_run_budget_plan,
)
from bridges.storage.database import BridgesDatabase


@pytest.fixture
def state(tmp_path: Any) -> tuple[Any, ...]:
    database = BridgesDatabase(tmp_path / "budget.db")
    database.initialize()
    ledger = RunBudgetLedgerRepository(database)
    now = datetime.now(UTC)
    snapshot = ledger.freeze_for_run(
        account_id="a", run_id="r", conversation_id="c", now=now,
        plan=derive_run_budget_plan(
            RunBudgetClass.NORMAL, deadline_at=now + timedelta(seconds=60), token_budget=1000,
        ),
    )
    budget = RunBudget.from_ledger_snapshot(
        "r", total_budget_ms=snapshot.plan.total_budget_ms, deadline_utc=snapshot.plan.deadline_at,
        reserve_ms=snapshot.plan.verify_deliver_reserve_ms, ledger=ledger, account_id="a",
    )
    sources = (BatchSourceRef("s1", "v1", "1-2"), BatchSourceRef("s2", "v1", "3-4"))
    plan = BoundedBatchPlan(
        "p", "v1", "原任务指纹", sources,
        tuple(BudgetBatch(str(i), 1000, 1, 100, (source,)) for i, source in enumerate(sources)),
    )
    return database, ledger, budget, plan


def _execute(state: tuple[Any, ...], callback: Any, **kwargs: Any) -> Any:
    _, ledger, budget, plan = state
    return BoundedBatchExecutor(ledger).execute(
        "a", "r", plan, budget, callback, task_fingerprint="原任务指纹",
        required_sources=plan.required_sources, stop=kwargs.get("stop", threading.Event()),
    )


def test_recipe_costs_include_output_candidates_repairs_and_retries(state: Any) -> None:
    _, ledger, _, _ = state
    costs = RunBudgetRecipeCosts("recipe-v1", 2, 3, 1, 100, 20)
    plan = derive_run_budget_plan(
        RunBudgetClass.DEEP, deadline_at=datetime.now(UTC) + timedelta(seconds=120),
        recipe_costs=costs,
    )
    assert (plan.model_call_limit, plan.token_budget) == (12, 1440)
    ledger.freeze_for_run(account_id="a", run_id="other", conversation_id="c", plan=plan,
                          now=datetime.now(UTC))
    assert ledger.load("a", "other").plan.recipe_costs == costs
    with pytest.raises(ValueError):
        RunBudgetRecipeCosts("v1", -1, 0, 0, 1, 1).limits(1)


def test_batches_use_shared_budget_and_replay_completed_refs(state: Any) -> None:
    _, ledger, budget, _ = state
    calls = []

    def run(batch: Any, shared: Any, deadline: float, stop: Any) -> BatchReceipt:
        assert shared is budget
        assert deadline <= shared.work_deadline()
        calls.append(batch.batch_id)
        assert shared.register_model_call(f"batch:{batch.batch_id}")
        assert not shared.register_model_call(f"extra:{batch.batch_id}")
        return BatchReceipt(batch.batch_id, (f"artifact:{batch.batch_id}:v1",), batch.sources)

    first = _execute(state, run)
    second = _execute(state, run)
    assert first.blocked_reason is None
    assert second == first
    assert calls == ["0", "1"]
    assert ledger.load("a", "r").model_calls_used == 2
    completed = [e for e in ledger.list_entries("a", "r") if e.kind == "batch_completed"]
    assert len(completed) == 2
    assert completed[0].detail["sources"][0]["source_id"] == "s1"


def test_incomplete_plan_cannot_silently_shrink_task(state: Any) -> None:
    from dataclasses import replace

    database, ledger, budget, plan = state
    truncated = replace(plan, batches=plan.batches[:1])
    result = _execute((database, ledger, budget, truncated), lambda *args: pytest.fail("不能执行"))
    assert result.blocked_reason == "batch_plan_incomplete"
    assert ledger.list_entries("a", "r") == []


def test_changed_frozen_plan_is_refused(state: Any) -> None:
    from dataclasses import replace

    _, ledger, budget, plan = state
    first = _execute(state, lambda b, *args: BatchReceipt(b.batch_id, ("artifact:v1",), b.sources))
    assert first.blocked_reason is None
    changed = replace(plan, version="v2")
    result = _execute((state[0], ledger, budget, changed), lambda *args: pytest.fail("不能重写"))
    assert result.blocked_reason == "batch_plan_changed"


def test_restart_reuses_only_completed_batches(state: Any, monkeypatch: Any) -> None:
    import bridges.chat.run_budget_ledger as ledger_module

    _, ledger, _, plan = state
    calls = []

    def crash(batch: Any, *args: Any) -> BatchReceipt:
        calls.append(batch.batch_id)
        if batch.batch_id == "1":
            raise RuntimeError("模拟失败")
        return BatchReceipt(batch.batch_id, ("artifact:0:v1",), batch.sources)

    first = _execute(state, crash)
    assert first.blocked_reason == "batch_failed"
    assert len(first.receipts) == 1
    monkeypatch.setattr(ledger_module, "_EXECUTION_PROCESS", "恢复进程")
    second = _execute(
        state, lambda b, *args: BatchReceipt(b.batch_id, ("artifact:1:v1",), b.sources)
    )
    assert second.blocked_reason is None
    assert len(second.receipts) == len(plan.batches)
    assert calls == ["0", "1"]
    assert second.receipts[0] == first.receipts[0]


def test_stop_in_first_batch_does_not_start_second(state: Any) -> None:
    stop = threading.Event()
    calls = []

    def run(batch: Any, *args: Any) -> BatchReceipt:
        calls.append(batch.batch_id)
        stop.set()
        return BatchReceipt(batch.batch_id, ("artifact:v1",), batch.sources)

    result = _execute(state, run, stop=stop)
    assert result.blocked_reason is not None
    assert calls == ["0"]
    assert result.receipts == ()


def test_batch_timeout_blocks_remaining_batches_without_sleep(state: Any, monkeypatch: Any) -> None:
    import bridges.chat.budget_batches as batches_module

    real_clock = batches_module.time.monotonic
    advance = [0.0]
    monkeypatch.setattr(batches_module.time, "monotonic", lambda: real_clock() + advance[0])
    calls = []

    def run(batch: Any, *args: Any) -> BatchReceipt:
        calls.append(batch.batch_id)
        advance[0] = 2.0
        return BatchReceipt(batch.batch_id, ("artifact:v1",), batch.sources)

    result = _execute(state, run)
    assert result.blocked_reason is not None
    assert calls == ["0"]
    assert result.receipts == ()


def test_failed_batch_recovery_preserves_first_consumption_and_deadline(
    state: Any, monkeypatch: Any,
) -> None:
    import bridges.chat.run_budget_ledger as ledger_module

    _, ledger, _, _ = state

    def fail(batch: Any, shared: Any, *args: Any) -> BatchReceipt:
        assert shared.register_model_call("before-crash")
        raise RuntimeError("模型已消耗一次后失败")

    assert _execute(state, fail).blocked_reason == "batch_failed"
    original = next(e for e in ledger.list_entries("a", "r") if e.kind == "batch_started")
    monkeypatch.setattr(ledger_module, "_EXECUTION_PROCESS", "恢复进程")

    def recover(batch: Any, shared: Any, *args: Any) -> BatchReceipt:
        assert not shared.register_model_call("after-crash")
        raise RuntimeError("本批额度不能恢复为全新额度")

    assert _execute(state, recover).blocked_reason == "batch_failed"
    restored = [e for e in ledger.list_entries("a", "r") if e.kind == "batch_started"][-1]
    assert restored.detail["deadline_at"] == original.detail["deadline_at"]
    assert restored.detail["calls_before"] == original.detail["calls_before"]
    assert restored.detail["tokens_before"] == original.detail["tokens_before"]
    assert ledger.load("a", "r").model_calls_used == 1


def test_repository_refuses_late_or_duplicate_batch_completion(state: Any) -> None:
    import json
    from dataclasses import asdict

    _, ledger, _, plan = state
    now = datetime.now(UTC)
    assert ledger.freeze_batch_plan("a", "r", "p", json.loads(json.dumps(asdict(plan))), now=now)
    assert not ledger.finish_batch("a", "r", "p:0", now=now)
    assert ledger.begin_batch("a", "r", "p:0", now=now)
    assert not ledger.finish_batch("a", "r", "p:0", now=now + timedelta(seconds=2))
    assert not ledger.begin_batch("a", "r", "p:0", now=now + timedelta(seconds=2))
    assert ledger.finish_batch("a", "r", "p:0", now=now + timedelta(milliseconds=500))
    assert not ledger.finish_batch("a", "r", "p:0", now=now + timedelta(milliseconds=600))
