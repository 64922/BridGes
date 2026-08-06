"""运行器与双跑可复现测试（Issue 40 Verification-1 / AC-11）。"""

from __future__ import annotations

import os

import pytest

from bridges.contracts.evaluation_suite import SuiteRunLock
from bridges.evaluation import suite_data
from bridges.evaluation.runner import EvaluationRunner, build_digest, compare_double_run
from bridges.evaluation.suite_registry import SuiteRegistry
from bridges.evaluation.sut import build_sut_registry


@pytest.fixture
def runner() -> EvaluationRunner:
    registry = SuiteRegistry()
    registry.register(suite_data.build_science_baseline_suite())
    suite = registry.get(suite_data.SUITE_ID)
    return EvaluationRunner(
        suite=suite,
        sut_registry=build_sut_registry(),
        seeds=[42],
        execution_count=1,
    )


@pytest.fixture
def tiny_runner() -> EvaluationRunner:
    """只跑一个案例的轻量运行器（快速测试用）。"""
    suite = suite_data.build_science_baseline_suite()
    tiny = suite.model_copy(deep=True)
    tiny.run_matrix.entries = [
        entry
        for entry in tiny.run_matrix.entries
        if entry.sut_id == "bridges_full"
        and entry.task_id == "task-science"
        and "science-bell-evidence" in entry.case_ids
    ]
    return EvaluationRunner(
        suite=tiny,
        sut_registry=build_sut_registry(),
        seeds=[42],
        execution_count=1,
    )


def test_lock_freezes_reproducibility_metadata(tiny_runner: EvaluationRunner) -> None:
    """AC-1：运行锁冻结套件摘要、数据集、模型/SKILL、种子与执行次数。"""
    outcome = tiny_runner.run()
    lock: SuiteRunLock = outcome.lock
    assert lock.suite_id == suite_data.SUITE_ID
    # 迷你运行器的锁摘要 = 迷你套件摘要（与运行器使用的套件一致）。
    suite = suite_data.build_science_baseline_suite()
    tiny = suite.model_copy(deep=True)
    tiny.run_matrix.entries = [
        entry
        for entry in tiny.run_matrix.entries
        if entry.sut_id == "bridges_full"
        and entry.task_id == "task-science"
        and "science-bell-evidence" in entry.case_ids
    ]
    assert lock.suite_digest == tiny.digest()
    assert lock.dataset_versions
    assert lock.random_seeds == [42]
    assert lock.execution_count == 1
    assert lock.judge_versions
    assert lock.scoring_scale_versions
    assert lock.network_cache_policy == "frozen"
    assert lock.code_commit_or_build_digest == build_digest()
    # 模型运行锁记录了固定绑定。
    assert any(
        m.capability_name == "qwen_text_chat" for m in lock.model_run_locks
    )


def test_build_digest_honors_environment_override(tiny_runner: EvaluationRunner) -> None:
    os.environ["BRIDGES_BUILD_DIGEST"] = "git-eval-deadbeef"
    try:
        assert build_digest() == "git-eval-deadbeef"
    finally:
        del os.environ["BRIDGES_BUILD_DIGEST"]


def test_double_run_is_reproducible(tiny_runner: EvaluationRunner) -> None:
    """Verification-1：干净环境连续两次固定评测，锁/样本/指标可复现。"""
    first = tiny_runner.run()
    second = tiny_runner.run()
    comparison = compare_double_run(first, second, tolerances={"fact_accuracy": 0.5})
    assert comparison["locks_match"], "双跑运行锁摘要必须一致"
    assert comparison["samples_match"], "双跑样本集合必须一致"
    assert comparison["within_tolerance"]
    assert all(
        diff == 0 for diff in comparison["metric_diff_sum"].values()
    ), "确定性模式下双跑指标差异必须为 0"
    # 两次运行的结果集合逐条一致（确定性）。
    results_b = {
        f"{r.sut_id}@{r.case_id}@{r.seed}@{r.execution_index}": r
        for r in second.results
    }
    for result in first.results:
        peer = results_b[f"{result.sut_id}@{result.case_id}@{result.seed}@{result.execution_index}"]
        assert result.status == peer.status
        assert result.outputs == peer.outputs
        assert result.reproduction_command == peer.reproduction_command


def test_reproduction_command_is_one_click_replayable(tiny_runner: EvaluationRunner) -> None:
    """Verification-5：失败案例的命令可一键重放（固定锁/案例/种子/序号）。"""
    outcome = tiny_runner.run()
    for result in outcome.results:
        assert result.reproduction_command.startswith("BridGes evaluate replay")
        assert f"--suite {outcome.lock.suite_id}" in result.reproduction_command
        assert f"--case {result.case_id}" in result.reproduction_command
        assert f"--sut {result.sut_id}" in result.reproduction_command


def test_suite_run_produces_all_suts_and_tasks(runner: EvaluationRunner) -> None:
    """AC-8：运行覆盖完整/基线/参考/消融 × 全部任务。"""
    outcome = runner.run()
    assert outcome.results
    suts = {r.sut_id for r in outcome.results}
    tasks = {r.task_id for r in outcome.results}
    assert {"bridges_full", "qwen_baseline", "open_source_reference"} <= suts
    assert {"ablation_no_profile", "ablation_no_humanizer", "ablation_no_evidence"} <= suts
    assert tasks == {case.task_id for case in suite_data.CASES}
    # 报告已生成且含全部维度。
    assert outcome.report.estimates
    assert outcome.report.failure_stats
    assert outcome.report.cost_latency["total_runs"] == len(outcome.results)
