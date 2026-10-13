"""Issue 09：持久化整次运行预算与有限调整额度回归测试。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from bridges.chat.budget import RunBudget
from bridges.chat.run_budget_ledger import (
    RUN_BUDGET_INITIALS,
    RunBudgetClass,
    RunBudgetLedgerRepository,
    derive_run_budget_class,
    derive_study_run_budget_plan,
    derive_tieba_run_budget_plan,
)
from bridges.routing import MainCapability
from bridges.storage.database import BridgesDatabase
from tests.chat.run_budget_ledger_support import _NOW, _freeze_normal, _route


def test_initial_values_follow_confirmed_scheme() -> None:
    """已确认初值：普通 60s/预留 15s；深入 120s/预留 30s；并行 2、初筛
    20、深读 3/5；调整 1 轮、传输重试 1 次；轻量沿用已验证 120s。"""
    normal = RUN_BUDGET_INITIALS[RunBudgetClass.NORMAL]
    deep = RUN_BUDGET_INITIALS[RunBudgetClass.DEEP]
    lightweight = RUN_BUDGET_INITIALS[RunBudgetClass.LIGHTWEIGHT]
    assert normal.total_budget_ms == 60_000
    assert normal.verify_deliver_reserve_ms == 15_000
    assert deep.total_budget_ms == 120_000
    assert deep.verify_deliver_reserve_ms == 30_000
    assert lightweight.total_budget_ms == 120_000
    assert lightweight.verify_deliver_reserve_ms == 0
    # 调用数上限为保守守门初值：正常流程 1–2 次真实调用 + 一轮共享修复
    # 的余量；普通交流含工具轮，与 normal 同为 6，deep 留结构修复余量。
    assert normal.model_call_limit == 6
    assert deep.model_call_limit == 8
    assert lightweight.model_call_limit == 6
    for initials in (normal, deep, lightweight):
        assert initials.external_parallel_max == 2
        assert initials.candidate_screen_max == 20
        assert initials.adjustment_rounds_max == 1
        assert initials.transient_retry_max == 1
    assert normal.deep_read_max == 3
    assert deep.deep_read_max == 5


def test_budget_class_derivation() -> None:
    """派生：学习/论文/图片/视频/职业规划等重材料运行 → deep；普通闲聊、
    澄清、润色等纯交流 → lightweight（沿用已验证 120 秒前台硬上限）。
    normal（普通检索 60 秒）留给配方驱动的检索编排，由票 10/12/37 接线，
    本票生产路径不派生。"""
    assert derive_run_budget_class() is RunBudgetClass.LIGHTWEIGHT
    assert derive_run_budget_class(mode="daily") is RunBudgetClass.LIGHTWEIGHT
    assert derive_run_budget_class(
        route=_route(MainCapability.ORDINARY_CHAT)
    ) is RunBudgetClass.LIGHTWEIGHT
    assert derive_run_budget_class(
        route=_route(MainCapability.CLARIFICATION)
    ) is RunBudgetClass.LIGHTWEIGHT
    assert derive_run_budget_class(mode="study") is RunBudgetClass.DEEP
    assert derive_run_budget_class(
        route=_route(MainCapability.PAPER_SEARCH)
    ) is RunBudgetClass.DEEP
    assert derive_run_budget_class(
        route=_route(MainCapability.IMAGE)
    ) is RunBudgetClass.DEEP
    assert derive_run_budget_class(
        route=_route(MainCapability.VIDEO)
    ) is RunBudgetClass.DEEP
    assert derive_run_budget_class(
        route=_route(MainCapability.CAREER)
    ) is RunBudgetClass.DEEP
    assert derive_run_budget_class(
        route=_route(MainCapability.TIEBA)
    ) is RunBudgetClass.DEEP
    assert derive_run_budget_class(has_image=True) is RunBudgetClass.DEEP
    assert derive_run_budget_class(has_video=True) is RunBudgetClass.DEEP


def test_study_budget_scales_with_pages_and_has_a_hard_cap() -> None:
    three_pages = derive_study_run_budget_plan(page_count=3, created_at=_NOW)
    assert three_pages.total_budget_ms == 780_000
    assert three_pages.deadline_at == _NOW + timedelta(seconds=780)
    assert three_pages.model_call_limit == 18

    capped = derive_study_run_budget_plan(page_count=10, created_at=_NOW)
    assert capped.total_budget_ms == 900_000
    assert capped.model_call_limit == 46

    no_photos = derive_study_run_budget_plan(page_count=0, created_at=_NOW)
    assert no_photos.total_budget_ms == RUN_BUDGET_INITIALS[RunBudgetClass.DEEP].total_budget_ms


def test_tieba_budget_has_five_minute_cap_and_delivery_reserve() -> None:
    plan = derive_tieba_run_budget_plan(created_at=_NOW, token_budget=96_000)
    assert plan.total_budget_ms == 300_000
    assert plan.deadline_at == _NOW + timedelta(minutes=5)
    assert plan.verify_deliver_reserve_ms == 45_000
    assert plan.deep_read_max == 8
    assert plan.token_budget == 96_000


def test_lease_recovery_reloads_ledger_without_reset(
    database: BridgesDatabase, repo: Any
) -> None:
    """租约恢复：重新加载同一账本行，截止时间与已耗计数不重置。"""
    snapshot = _freeze_normal(repo)
    assert repo.register_model_call(
        account_id="acc-1", run_id="run-1", call_key="qwen_text_chat@1", now=_NOW
    )
    repo.record_model_call_result(
        account_id="acc-1",
        run_id="run-1",
        call_key="qwen_text_chat@1",
        attempt=1,
        outcome_code="call_completed",
        input_tokens=1200,
        output_tokens=300,
        now=_NOW,
    )
    # 模拟进程恢复：全新仓库实例（同一数据库）重新加载
    recovered = RunBudgetLedgerRepository(database).load("acc-1", "run-1")
    assert recovered is not None
    assert recovered.plan.deadline_at == snapshot.plan.deadline_at
    assert recovered.model_calls_used == 1
    assert recovered.input_tokens_used == 1200
    # 恢复后的预算控制器沿用冻结截止：已耗时间不归零（可控时钟验证）
    budget = RunBudget.from_ledger_snapshot(
        "run-1",
        total_budget_ms=recovered.plan.total_budget_ms,
        deadline_utc=recovered.plan.deadline_at,
        reserve_ms=recovered.plan.verify_deliver_reserve_ms,
        ledger=RunBudgetLedgerRepository(database),
        account_id="acc-1",
    )
    assert budget.has_ledger
    remaining_after_recovery = budget.remaining_ms()
    # 冻结截止在 _NOW + 60s；真实时间流逝极短，剩余应接近 60s 而不是
    # 被重置回「全新 120s/60s 起点再加一遍」的更大值。
    assert remaining_after_recovery <= 60_000


def test_transient_retry_capped_once_per_registered_call(repo: Any) -> None:
    """每个登记的临时传输失败最多重试 1 次；不同调用各自享有额度。"""
    _freeze_normal(repo)
    first = repo.register_transient_retry(
        account_id="acc-1",
        run_id="run-1",
        call_key="qwen_text_chat@1",
        error_code="rate_limited",
        now=_NOW,
    )
    duplicate = repo.register_transient_retry(
        account_id="acc-1",
        run_id="run-1",
        call_key="qwen_text_chat@1",
        error_code="rate_limited",
        now=_NOW,
    )
    other_call = repo.register_transient_retry(
        account_id="acc-1",
        run_id="run-1",
        call_key="qwen_vision@1",
        error_code="transient",
        now=_NOW,
    )
    assert first is True
    # 重复登记被幂等守卫拦截：不重复计数、不再放行
    assert duplicate is False
    assert other_call is True
    snapshot = repo.load("acc-1", "run-1")
    assert snapshot is not None
    assert snapshot.transient_retries_used == 2
    retry_entries = [
        entry
        for entry in repo.list_entries("acc-1", "run-1")
        if entry.kind == "transient_retry"
    ]
    assert len(retry_entries) == 2


def test_new_budget_run_for_explicit_continue(repo: Any) -> None:
    """明确继续/重试创建新运行、新账本：计数从零冻结，旧账本不受影响。"""
    _freeze_normal(repo, run_id="run-1")
    repo.close(account_id="acc-1", run_id="run-1", now=_NOW)
    _freeze_normal(repo, run_id="run-2", deadline=_NOW + timedelta(seconds=60))
    assert repo.register_model_call(
        account_id="acc-1", run_id="run-2", call_key="qwen_text_chat@1", now=_NOW
    )
    new_snapshot = repo.load("acc-1", "run-2")
    old_snapshot = repo.load("acc-1", "run-1")
    assert new_snapshot is not None and old_snapshot is not None
    assert new_snapshot.model_calls_used == 1
    assert new_snapshot.status == "active"
    assert old_snapshot.status == "closed"
    assert old_snapshot.model_calls_used == 0
