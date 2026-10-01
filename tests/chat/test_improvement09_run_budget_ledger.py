"""改进工单 09：持久化整次运行预算与有限调整额度。

覆盖验收标准（``.scratch/2/issues/09-shared-persistent-run-budget.md``）：

1. 并行和串行消耗同一账本，租约恢复、结构修复及供应商重试均不重置额度；
2. 第二轮自动补证、超额调用或超时等待被代码拒绝；仍保留必要核验/交付空间；
3. 用户停止后没有新调用（账本关闭后拒绝一切消耗），明确继续/重试创建
   新预算运行；
4. 初值/真实实测/调整结果分别记录；消耗与诊断脱敏，不记录凭据；
5. 持久计数、取消竞争和进程恢复的幂等/版本守卫可测试，账本生命周期完整
   （active → exhausted → closed；迁移、导出、删除齐备）。

验证使用可控时钟与确定性替身：冻结截止直接构造（含过去时刻），上限用
注入的小额度验证，不依赖真实睡眠。
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bridges.chat.budget import MODEL_CALL_MIN_TIMEOUT_SECONDS, RunBudget
from bridges.chat.run_budget_ledger import (
    RUN_BUDGET_CONTRACT_VERSION,
    RUN_BUDGET_INITIALS,
    RunBudgetClass,
    RunBudgetLedgerRepository,
    derive_run_budget_class,
    derive_run_budget_plan,
)
from bridges.routing import CapabilityRoute, MainCapability, RouteStatus
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase

#: 以真实时钟为基准的受控时刻（冻结截止的过去/未来都用相对偏移构造，
#: 不依赖真实睡眠）。
_NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def database(tmp_path: Any) -> BridgesDatabase:
    db = BridgesDatabase(str(tmp_path / "bridges.db"))
    db.initialize()
    return db


@pytest.fixture
def repo(database: BridgesDatabase) -> RunBudgetLedgerRepository:
    return RunBudgetLedgerRepository(database)


def _freeze_normal(
    repo: RunBudgetLedgerRepository,
    run_id: str = "run-1",
    *,
    account_id: str = "acc-1",
    deadline: datetime | None = None,
    token_budget: int | None = None,
    now: datetime = _NOW,
) -> Any:
    plan = derive_run_budget_plan(
        RunBudgetClass.NORMAL,
        deadline_at=deadline or (now + timedelta(seconds=60)),
        token_budget=token_budget,
    )
    return repo.freeze_for_run(
        account_id=account_id,
        run_id=run_id,
        conversation_id="conv-1",
        plan=plan,
        now=now,
    )


# ---------------------------------------------------------------------------
# 类别派生与初值方案（任务 2/4）
# ---------------------------------------------------------------------------


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


def _route(capability: MainCapability) -> CapabilityRoute:
    # 派生只读 main_capability；用 model_construct 绕开能力载荷校验
    #（论文路线要求携带完整搜索计划，与本用例无关）。
    return CapabilityRoute.model_construct(
        status=RouteStatus.MATCHED,
        main_capability=capability,
        confidence=0.9,
        reason="评审派生用例",
    )


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
    assert derive_run_budget_class(has_image=True) is RunBudgetClass.DEEP
    assert derive_run_budget_class(has_video=True) is RunBudgetClass.DEEP


# ---------------------------------------------------------------------------
# 冻结、恢复与共享账本（验收 1/5）
# ---------------------------------------------------------------------------


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
    assert snapshot.model_calls_used == 1


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


# ---------------------------------------------------------------------------
# 拒绝语义：第二轮补证 / 超额调用 / 超时等待（验收 2）
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 停止/继续与生命周期（验收 3/5）
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 脱敏与导出/删除（验收 4/5）
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 无账本构造与过期截止（内存版行为回归）
# ---------------------------------------------------------------------------


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
