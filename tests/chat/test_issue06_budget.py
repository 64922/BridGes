"""Issue 06 T1 单元测试：统一阶段时钟与预算控制器（RunBudget）。

覆盖阶段转换指标、总预算耗尽、重试门与首 token 聚合；不依赖任何
外部服务（纯内存时钟）。
"""

from __future__ import annotations

from bridges.chat.budget import (
    RESULT_OK,
    RESULT_SKIPPED,
    RESULT_TIMEOUT,
    TOTAL_BUDGET_MS,
    RunBudget,
    RunStage,
)


def test_stage_transitions_record_desensitized_metrics() -> None:
    """阶段 enter/exit 记录脱敏指标（毫秒/结果码/类别/计数，无正文）。"""
    budget = RunBudget("run-1")
    assert budget.enter(RunStage.LOCAL_RETRIEVAL) is True
    assert budget.enter(RunStage.MODEL_GENERATION) is True  # 结束上一阶段
    budget.exit(
        RunStage.MODEL_GENERATION,
        result=RESULT_OK,
        category="qwen_text_chat",
        count=2,
        first_token_ms=340,
    )
    metrics = budget.metrics()
    assert len(metrics) == 2
    local, generation = metrics
    assert local.stage == RunStage.LOCAL_RETRIEVAL
    assert local.result_code == RESULT_OK
    assert local.duration_ms >= 0
    assert generation.category == "qwen_text_chat"
    assert generation.count == 2
    assert generation.first_token_ms == 340
    # 脱敏契约：指标字段不携带消息/文档/搜索正文
    assert all(
        field not in metric.to_dict() for metric in metrics for field in ("content", "query")
    )


def test_budget_exhaustion_blocks_new_stages_and_marks_skipped() -> None:
    """预算耗尽：enter 返回 False，未进入阶段记 skipped。"""
    budget = RunBudget("run-2", total_ms=0)
    assert budget.expired() is True
    assert budget.remaining_ms() == 0
    assert budget.enter(RunStage.PUBLIC_SEARCH) is False
    budget.exit(RunStage.PUBLIC_SEARCH)
    metrics = budget.metrics()
    assert len(metrics) == 1
    assert metrics[0].result_code == RESULT_SKIPPED
    assert metrics[0].duration_ms == 0


def test_retry_gate_checks_remaining_budget() -> None:
    """重试门：仅当剩余预算足够「最小调用窗口 + 交接预留」时放行。

    Issue 06 第七轮：网关与技能修复门共用 ``can_retry_model_call`` 接缝，
    旧的估算成本门 ``can_retry`` 已移除（去重）。
    """
    budget = RunBudget("run-3", total_ms=60_000)
    assert budget.can_retry_model_call() is True
    # 模拟消耗预算（直接推进剩余预算：用总预算极小值验证）
    small = RunBudget("run-4", total_ms=0)
    assert small.can_retry_model_call() is False


def test_first_token_aggregates_minimum() -> None:
    """first_token_ms 取模型阶段最小值（重试场景取首次可见块）。"""
    budget = RunBudget("run-5")
    budget.enter(RunStage.MODEL_GENERATION)
    budget.exit(
        RunStage.MODEL_GENERATION,
        result=RESULT_TIMEOUT,
        category="qwen_text_chat",
        first_token_ms=900,
    )
    budget.enter(RunStage.MODEL_GENERATION)
    budget.exit(
        RunStage.MODEL_GENERATION,
        result=RESULT_OK,
        category="qwen_text_chat",
        first_token_ms=300,
    )
    assert budget.first_token_ms() == 300


def test_exit_is_idempotent_after_enter_closure() -> None:
    """enter 衔接关闭后 exit 幂等：不重复记录指标（双轴审查修复）。"""
    budget = RunBudget("run-6")
    assert budget.enter(RunStage.MODEL_GENERATION) is True
    assert budget.enter(RunStage.QUALITY_CHECK) is True  # 衔接关闭 MODEL_GENERATION
    budget.exit(RunStage.MODEL_GENERATION)  # finally 兜底调用：幂等返回
    metrics = budget.metrics()
    assert len(metrics) == 1, "enter 关闭 + exit 兜底不得双计"
    assert metrics[0].stage == RunStage.MODEL_GENERATION
    assert metrics[0].result_code == RESULT_OK


def test_exit_skipped_only_for_never_entered_stage() -> None:
    """从未进入的阶段（预算耗尽跳过）exit 记 skipped。"""
    budget = RunBudget("run-7", total_ms=0)
    assert budget.enter(RunStage.QUALITY_CHECK) is False
    budget.exit(RunStage.QUALITY_CHECK)
    assert budget.metrics()[0].result_code == RESULT_SKIPPED


def test_default_total_budget_is_120_seconds() -> None:
    """前台 run 硬上限默认 120 秒（验收标准）。"""
    assert TOTAL_BUDGET_MS == 120_000
