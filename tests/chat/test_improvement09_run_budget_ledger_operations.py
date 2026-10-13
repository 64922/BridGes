"""Issue 09：持久化整次运行预算与有限调整额度回归测试。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from bridges.chat.run_budget_ledger import (
    RUN_BUDGET_CONTRACT_VERSION,
    RUN_BUDGET_INITIALS,
    RunBudgetClass,
)
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase
from tests.chat.run_budget_ledger_support import _NOW, _freeze_normal


def test_migration_creates_ledger_tables_with_contract_version(
    database: BridgesDatabase,
) -> None:
    assert SCHEMA_VERSION >= 62
    tables = {
        row["name"]
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {"run_budget_ledger", "run_budget_entries"} <= tables


def test_freeze_is_idempotent_and_keeps_frozen_deadline(repo: Any) -> None:
    first = _freeze_normal(repo)
    second = _freeze_normal(
        repo, deadline=_NOW + timedelta(seconds=999)
    )
    assert second.plan.deadline_at == first.plan.deadline_at
    assert second.plan.total_budget_ms == first.plan.total_budget_ms
    assert second.contract_version == RUN_BUDGET_CONTRACT_VERSION


def test_parallel_and_serial_consume_same_ledger(repo: Any) -> None:
    """并行分支、串行调用记在同一账本行：计数累计、并发占位共享。"""
    _freeze_normal(repo)
    assert repo.register_external_call(
        account_id="acc-1", run_id="run-1", call_key="public_search:web", now=_NOW
    )
    assert repo.register_external_call(
        account_id="acc-1", run_id="run-1", call_key="public_search:arxiv", now=_NOW
    )
    # 并行上限 2：第三个分支被拒绝
    assert not repo.register_external_call(
        account_id="acc-1", run_id="run-1", call_key="public_search:third", now=_NOW
    )
    # 串行的模型调用与外部调用消耗同一账本行
    assert repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="qwen_text_chat@1", now=_NOW
    )
    repo.record_external_call_result(
        account_id="acc-1", run_id="run-1", call_key="public_search:web", now=_NOW
    )
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    assert snapshot.model_calls_used == 1
    assert snapshot.external_calls_used == 2
    assert snapshot.external_calls_active == 1


def test_external_auth_wait_extends_deadline_without_resetting_counters(repo: Any) -> None:
    before = _freeze_normal(repo)
    assert repo.register_external_call(
        account_id="acc-1", run_id="run-1", call_key="tieba.search:1", now=_NOW
    )
    repo.record_external_call_result(
        account_id="acc-1",
        run_id="run-1",
        call_key="tieba.search:1",
        outcome_code="completed",
        excluded_wait_ms=9_500,
        now=_NOW + timedelta(seconds=2),
    )
    after = repo.load("acc-1", "run-1")
    assert after is not None
    assert after.plan.deadline_at == before.plan.deadline_at + timedelta(milliseconds=9_500)
    assert after.external_calls_used == before.external_calls_used + 1
    assert repo.list_entries("acc-1", "run-1")[-1].detail["excluded_wait_ms"] == 9_500


def test_structural_fix_and_vendor_retry_share_one_round(repo: Any) -> None:
    """结构修复（调整轮）与供应商重试消耗同一账本，不重置任何计数。"""
    _freeze_normal(repo)
    assert repo.begin_adjustment(
        account_id="acc-1", run_id="run-1", reason_code="structural_fix", now=_NOW
    )
    assert repo.register_transient_retry(
        account_id="acc-1",
        run_id="run-1",
        call_key="qwen_text_chat@1",
        error_code="rate_limited",
        now=_NOW,
    )
    assert repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="qwen_text_chat@1", now=_NOW
    )
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    assert snapshot.adjustment_rounds_used == 1
    assert snapshot.transient_retries_used == 1
    assert snapshot.model_calls_used == 2


def test_second_adjustment_round_is_refused(repo: Any) -> None:
    _freeze_normal(repo)
    assert repo.begin_adjustment(
        account_id="acc-1", run_id="run-1", reason_code="first_round", now=_NOW
    )
    assert not repo.begin_adjustment(
        account_id="acc-1", run_id="run-1", reason_code="second_round", now=_NOW
    )
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    assert snapshot.adjustment_rounds_used == 1


def test_over_limit_model_calls_are_refused(repo: Any) -> None:
    _freeze_normal(repo)
    limit = RUN_BUDGET_INITIALS[RunBudgetClass.NORMAL].model_call_limit
    for index in range(limit):
        assert repo.register_model_call(
            account_id="acc-1",
            run_id="run-1",
            call_key=f"cap-{index}@1",
            now=_NOW,
        )
    assert not repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="over-limit@1", now=_NOW
    )
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    assert snapshot.model_calls_used == limit


def test_token_budget_meters_and_refuses_when_exhausted(repo: Any) -> None:
    _freeze_normal(repo, token_budget=100)
    assert repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="a@1", now=_NOW
    )
    repo.record_model_call_result(
        account_id="acc-1",
        run_id="run-1",
        call_key="a@1",
        input_tokens=100,
        outcome_code="call_completed",
        now=_NOW,
    )
    assert not repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="b@1", now=_NOW
    )


def test_token_gate_counts_output_tokens_as_consumption(repo: Any) -> None:
    """token 上限按已耗输入+输出合计事前守门：输出同样吃掉预算。"""
    _freeze_normal(repo, token_budget=100)
    assert repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="a@1", now=_NOW
    )
    repo.record_model_call_result(
        account_id="acc-1",
        run_id="run-1",
        call_key="a@1",
        input_tokens=40,
        output_tokens=70,
        outcome_code="call_completed",
        now=_NOW,
    )
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    assert snapshot.input_tokens_used == 40
    assert snapshot.output_tokens_used == 70
    assert not repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="b@1", now=_NOW
    )


def test_rate_limit_wait_beyond_remaining_budget_is_refused(repo: Any) -> None:
    _freeze_normal(repo)
    assert repo.can_wait_until("acc-1", "run-1", _NOW + timedelta(seconds=30))
    assert not repo.can_wait_until("acc-1", "run-1", _NOW + timedelta(seconds=300))
