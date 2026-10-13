"""Issue 09：持久化整次运行预算与有限调整额度回归测试。"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bridges.chat.budget import MODEL_CALL_MIN_TIMEOUT_SECONDS, RunBudget
from bridges.chat.run_budget_ledger import (
    RUN_BUDGET_CONTRACT_VERSION,
    RunBudgetClass,
    RunBudgetLedgerRepository,
)
from bridges.storage.database import BridgesDatabase
from tests.chat.run_budget_ledger_support import _NOW, _freeze_normal


def test_verify_deliver_reserve_preserved_for_delivery(repo: Any) -> None:
    """交付前阶段的预算按「总预算 − 预留」收紧：预留空间只留给核验/交付。"""
    # 截止在测试执行时刻取现：全量套件下模块导入到本测试之间可能已流
    # 逝数分钟，导入时刻冻结会让 60 秒截止提前过期（剩余归零误报）。
    _freeze_normal(repo, deadline=datetime.now(UTC) + timedelta(seconds=60))
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    budget = RunBudget.from_ledger_snapshot(
        "run-1",
        total_budget_ms=snapshot.plan.total_budget_ms,
        deadline_utc=snapshot.plan.deadline_at,
        reserve_ms=snapshot.plan.verify_deliver_reserve_ms,
        ledger=repo,
        account_id="acc-1",
    )
    # 单次调用窗口不可能吃掉预留：timeout ≤ 总剩余 − 预留 − 交接预留
    timeout_ms = budget.model_call_timeout_ms()
    assert timeout_ms <= budget.remaining_ms() - snapshot.plan.verify_deliver_reserve_ms
    # 重试门同样按交付前剩余计算
    assert not budget.can_retry_model_call(
        backoff_ms=budget.work_remaining_ms()
    )
    # 搜索阶段截止不越过预留线
    deadlines = budget.public_search_deadlines({"web"})
    assert deadlines.stage_deadline <= budget.work_deadline() + 1e-6


def test_closed_ledger_refuses_all_new_consumption(repo: Any) -> None:
    """用户停止 → 账本关闭：此后任何路径都没有新调用。"""
    _freeze_normal(repo)
    assert repo.close(account_id="acc-1", run_id="run-1", now=_NOW)
    assert not repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="late@1", now=_NOW
    )
    assert not repo.register_external_call(
        account_id="acc-1", run_id="run-1", call_key="public_search:web", now=_NOW
    )
    assert not repo.begin_adjustment(
        account_id="acc-1", run_id="run-1", reason_code="late", now=_NOW
    )
    assert not repo.can_wait_until("acc-1", "run-1", _NOW + timedelta(seconds=1))
    # 关闭幂等：重复关闭返回 False，不产生第二条流水
    assert not repo.close(account_id="acc-1", run_id="run-1", now=_NOW)


def test_exhausted_ledger_still_refuses_and_records_reason(repo: Any) -> None:
    _freeze_normal(repo)
    assert repo.mark_exhausted(
        account_id="acc-1", run_id="run-1", reason_code="budget_exhausted", now=_NOW
    )
    assert not repo.mark_exhausted(
        account_id="acc-1", run_id="run-1", reason_code="again", now=_NOW
    )
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    assert snapshot.status == "exhausted"
    assert not repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="late@1", now=_NOW
    )


def test_exhausted_without_reason_persists_default_reason(repo: Any) -> None:
    """编排层不指明原因调用 mark_exhausted 时，耗尽终态仍持久化留痕。"""
    _freeze_normal(repo)
    budget = RunBudget.from_ledger_snapshot(
        "run-1",
        total_budget_ms=60_000,
        deadline_utc=datetime.now(UTC) + timedelta(seconds=60),
        reserve_ms=15_000,
        ledger=repo,
        account_id="acc-1",
    )
    budget.mark_exhausted()
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    assert snapshot.status == "exhausted"
    assert snapshot.exhausted_reason == "budget_exceeded"


def test_adjustment_result_is_recorded_as_separate_entry(repo: Any) -> None:
    """初值、实测与调整结果分开记录：调整轮结束后补记 adjustment_end。"""
    _freeze_normal(repo)
    assert repo.begin_adjustment(
        account_id="acc-1", run_id="run-1", reason_code="content_fix", now=_NOW
    )
    assert repo.end_adjustment(
        account_id="acc-1",
        run_id="run-1",
        outcome_code="adjustment_applied",
        now=_NOW,
        detail={"registered_nodes": 2},
    )
    kinds = [entry.kind for entry in repo.list_entries("acc-1", "run-1")]
    assert "adjustment_begin" in kinds and "adjustment_end" in kinds
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    assert snapshot.adjustment_rounds_used == 1
    # 关闭后拒绝补记
    repo.close(account_id="acc-1", run_id="run-1", now=_NOW)
    assert not repo.end_adjustment(
        account_id="acc-1", run_id="run-1", outcome_code="late", now=_NOW
    )


def test_compat_ensure_anchors_deadline_to_run_creation(repo: Any) -> None:
    """兼容补齐（账本缺失）：截止锚定运行创建时刻，绝不向后延。"""
    created_at = _NOW - timedelta(seconds=30)
    snapshot = repo.ensure_for_run(
        account_id="acc-1",
        run_id="run-compat",
        conversation_id="conv-1",
        budget_class=RunBudgetClass.NORMAL,
        run_created_at=created_at,
        now=_NOW,
    )
    assert snapshot.plan.deadline_at == created_at + timedelta(seconds=60)
    # 幂等：已有账本行时原样返回，不重锚
    again = repo.ensure_for_run(
        account_id="acc-1",
        run_id="run-compat",
        conversation_id="conv-1",
        budget_class=RunBudgetClass.NORMAL,
        run_created_at=created_at,
        now=_NOW,
    )
    assert again.plan.deadline_at == snapshot.plan.deadline_at
    kinds = [entry.kind for entry in repo.list_entries("acc-1", "run-compat")]
    assert "compat_created" in kinds


def test_version_guard_and_threaded_writes_do_not_lose_counts(
    repo: Any,
) -> None:
    """并发登记：乐观版本守卫下计数不丢失、流水 seq 单调不冲突。"""
    _freeze_normal(repo)
    successes = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        granted = repo.register_model_call(
            account_id="acc-1",
            run_id="run-1",
            call_key=f"cap-{index}@1",
            now=_NOW,
        )
        with lock:
            successes.append(granted)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    assert snapshot.model_calls_used == sum(1 for ok in successes if ok)
    assert snapshot.version == 1 + snapshot.model_calls_used
    seqs = [entry.seq for entry in repo.list_entries("acc-1", "run-1")]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))


def test_ledger_survives_process_restart(
    tmp_path: Any, repo: Any
) -> None:
    """进程恢复：同一数据库重开新实例，冻结计划与计数完整可读。"""
    _freeze_normal(repo)
    repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="qwen_text_chat@1", now=_NOW
    )
    reopened = BridgesDatabase(str(tmp_path / "bridges.db"))
    reopened.initialize()
    reloaded = RunBudgetLedgerRepository(reopened).load("acc-1", "run-1")
    assert reloaded is not None
    assert reloaded.plan.total_budget_ms == 60_000
    assert reloaded.model_calls_used == 1
    assert reloaded.status == "active"


def test_entries_carry_only_redacted_metering(repo: Any) -> None:
    _freeze_normal(repo, token_budget=100_000)
    repo.register_model_call(
        account_id="acc-1",
        run_id="run-1",
        call_key="qwen_text_chat@1",
        purpose="generation",
        now=_NOW,
    )
    repo.record_model_call_result(
        account_id="acc-1",
        run_id="run-1",
        call_key="qwen_text_chat@1",
        outcome_code="call_completed",
        duration_ms=1500,
        input_tokens=500,
        output_tokens=200,
        now=_NOW,
    )
    repo.begin_adjustment(
        account_id="acc-1", run_id="run-1", reason_code="structural_fix", now=_NOW
    )
    entries = repo.list_entries("acc-1", "run-1")
    assert len(entries) == 3
    for entry in entries:
        payload = json.dumps(entry.detail, ensure_ascii=False)
        # 流水只含标识/码/计数：不出现消息正文或任何凭据字段
        assert "api_key" not in payload and "secret" not in payload
        assert entry.input_tokens in (None, 500)
        assert entry.output_tokens in (None, 200)
    purposes = {entry.purpose for entry in entries}
    assert purposes == {"generation", None}


@pytest.mark.parametrize("kind", ["model", "external", "retry", "adjustment"])
def test_consumption_cannot_cross_frozen_work_deadline(repo: Any, kind: str) -> None:
    """冻结截止及预留线必须由持久仓库直接守门。"""
    _freeze_normal(repo, deadline=_NOW + timedelta(seconds=15))
    kwargs = {"account_id": "acc-1", "run_id": "run-1", "now": _NOW}
    if kind == "model":
        allowed = repo.register_model_call(call_key="model:1", **kwargs)
    elif kind == "external":
        allowed = repo.register_external_call(call_key="external:1", **kwargs)
    elif kind == "retry":
        allowed = repo.register_transient_retry(call_key="model:1", **kwargs)
    else:
        allowed = repo.begin_adjustment(**kwargs)
    assert not allowed
    assert repo.list_entries("acc-1", "run-1") == []


def test_duplicate_release_does_not_free_another_call(repo: Any) -> None:
    _freeze_normal(repo)
    kwargs = {"account_id": "acc-1", "run_id": "run-1", "now": _NOW}
    assert repo.register_external_call(call_key="a", **kwargs)
    assert repo.register_external_call(call_key="b", **kwargs)
    repo.record_external_call_result(call_key="a", **kwargs)
    repo.record_external_call_result(call_key="a", **kwargs)
    assert repo.register_external_call(call_key="c", **kwargs)
    assert not repo.register_external_call(call_key="d", **kwargs)
    assert repo.load("acc-1", "run-1").external_calls_active == 2


def test_late_results_meter_once_after_exhaustion(repo: Any) -> None:
    _freeze_normal(repo)
    kwargs = {"account_id": "acc-1", "run_id": "run-1", "now": _NOW}
    assert repo.register_model_call(call_key="a", purpose="generation", **kwargs)
    assert repo.register_external_call(call_key="b", **kwargs)
    repo.mark_exhausted(reason_code="budget_exceeded", **kwargs)
    for _ in range(2):
        repo.record_model_call_result(call_key="a", input_tokens=12, output_tokens=4, **kwargs)
        repo.record_external_call_result(call_key="b", **kwargs)
    snapshot = repo.load("acc-1", "run-1")
    assert (snapshot.input_tokens_used, snapshot.output_tokens_used) == (12, 4)
    assert snapshot.external_calls_active == 0
    result = next(e for e in repo.list_entries("acc-1", "run-1") if e.kind == "model_call_result")
    assert (result.input_tokens, result.output_tokens, result.purpose) == (12, 4, "generation")


def test_unknown_contract_refuses_consumption(repo: Any, database: Any) -> None:
    _freeze_normal(repo)
    with database.transaction():
        database.scoped("acc-1").execute(
            "UPDATE run_budget_ledger SET contract_version = 'future-v2' WHERE account_id = ?",
            ("acc-1",),
        )
    assert not repo.load("acc-1", "run-1").active
    assert not repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="a", now=_NOW
    )


def test_retry_consumes_actual_call_limit(repo: Any, database: Any) -> None:
    _freeze_normal(repo)
    with database.transaction():
        database.scoped("acc-1").execute(
            "UPDATE run_budget_ledger SET model_call_limit = 2 WHERE account_id = ?", ("acc-1",)
        )
    kwargs = {"account_id": "acc-1", "run_id": "run-1", "now": _NOW}
    assert repo.register_model_call(call_key="a", **kwargs)
    assert repo.register_transient_retry(call_key="a", **kwargs)
    assert not repo.register_model_call(call_key="b", **kwargs)
    assert repo.load("acc-1", "run-1").model_calls_used == 2


def test_process_restart_recovers_only_dead_process_slots(repo: Any, monkeypatch: Any) -> None:
    import bridges.chat.run_budget_ledger as ledger_module

    _freeze_normal(repo)
    kwargs = {"account_id": "acc-1", "run_id": "run-1", "now": _NOW}
    assert repo.register_external_call(call_key="old", **kwargs)
    repo.recover_external_calls("acc-1", "run-1", now=_NOW)
    assert repo.load("acc-1", "run-1").external_calls_active == 1
    monkeypatch.setattr(ledger_module, "_EXECUTION_PROCESS", "new-process")
    repo.recover_external_calls("acc-1", "run-1", now=_NOW)
    repo.recover_external_calls("acc-1", "run-1", now=_NOW)
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot.external_calls_active == 0
    assert snapshot.external_calls_used == 1


def test_freeze_competition_returns_same_plan(repo: Any) -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshots = list(pool.map(lambda _: _freeze_normal(repo), range(2)))
    assert snapshots[0] == snapshots[1]


def test_export_and_delete_cover_ledger_tables(
    database: BridgesDatabase, repo: Any
) -> None:
    from bridges.lifecycle.catalog import delete_account_rows, export_rows

    _freeze_normal(repo)
    repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="qwen_text_chat@1", now=_NOW
    )
    ledger_rows = export_rows(database, "acc-1", "run_budget_ledger")
    entry_rows = export_rows(database, "acc-1", "run_budget_entries")
    assert len(ledger_rows) == 1 and len(entry_rows) == 1
    assert ledger_rows[0]["budget_class"] == "normal"
    assert ledger_rows[0]["contract_version"] == RUN_BUDGET_CONTRACT_VERSION
    delete_account_rows(database, "acc-1")
    assert export_rows(database, "acc-1", "run_budget_ledger") == []
    assert export_rows(database, "acc-1", "run_budget_entries") == []


def test_budget_without_ledger_keeps_issue06_behavior() -> None:
    budget = RunBudget("run-plain")
    assert not budget.has_ledger
    assert budget.register_model_call("cap@1")
    assert budget.begin_adjustment()
    budget.mark_exhausted()
    assert budget.expired()
    assert budget.model_call_timeout_ms() == int(
        MODEL_CALL_MIN_TIMEOUT_SECONDS * 1000
    )


def test_expired_frozen_deadline_degrades_immediately(repo: Any) -> None:
    """冻结截止已过（排队过久/恢复过晚）：立即按预算耗尽降级。"""
    _freeze_normal(repo, deadline=_NOW - timedelta(seconds=1))
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    budget = RunBudget.from_ledger_snapshot(
        "run-1",
        total_budget_ms=snapshot.plan.total_budget_ms,
        deadline_utc=snapshot.plan.deadline_at,
        reserve_ms=snapshot.plan.verify_deliver_reserve_ms,
        ledger=repo,
        account_id="acc-1",
    )
    assert budget.expired()
    assert budget.model_call_timeout_ms() == int(
        MODEL_CALL_MIN_TIMEOUT_SECONDS * 1000
    )
