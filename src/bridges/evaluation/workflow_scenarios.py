"""工单 42：39 个固定工作流场景的可执行证据清单。

数据来源：``docs/workflow/delivery-and-validation.md`` §3 的 A01–A18、
L01–L13、R01–R08。每个场景必须绑定至少一条**可执行**的确定性 pytest
节点；跨外部服务的能力另绑定 :class:`ExternalGate`，由最小真实探针报告
（``external_probes``）给出实际可得层次，未通过的能力保持如实降级。

本模块只登记证据，不执行测试；执行与报告分别由
``scripts/run_issue42_workflow_evidence.py`` 与 ``workflow_evidence.py`` 完成。
"""

from __future__ import annotations

from bridges.evaluation.workflow_scenario_contracts import (
    Scenario,
    ZeroTolerance,
    ZeroToleranceGuard,
)
from bridges.evaluation.workflow_scenarios_daily import DAILY_SCENARIOS
from bridges.evaluation.workflow_scenarios_resilience import RESILIENCE_SCENARIOS
from bridges.evaluation.workflow_scenarios_study import STUDY_SCENARIOS

WORKFLOW_SCENARIOS = DAILY_SCENARIOS + STUDY_SCENARIOS + RESILIENCE_SCENARIOS


ZERO_TOLERANCE_GUARDS: tuple[ZeroToleranceGuard, ...] = (
    ZeroToleranceGuard(
        kind=ZeroTolerance.CROSS_ACCOUNT,
        description="任何工作流对象在同一标识下不得跨账户读写",
        tests=(
            "tests/chat/test_chat_service.py::test_get_conversation_account_isolation",
            "tests/chat/test_improvement04_payload_budget.py::test_cross_account_material_is_not_recallable_through_the_gate",
            "tests/chat/test_improvement11_reference_resolution.py::test_cross_account_material_is_not_recallable",
            "tests/chat/test_improvement15_task_materials.py::test_task_queries_do_not_cross_account_scope",
            "tests/chat/test_v2_02_resumable_runs.py::test_checkpoint_run_event_isolation_across_accounts",
            "tests/chat/test_v2_03_conversation_context.py::test_cross_account_history_not_recallable",
            "tests/tasks/test_task_state.py::test_account_isolation_hides_other_account_tasks",
            "tests/kernel/test_node_kernel.py::test_account_isolation_for_artifacts",
        ),
    ),
    ZeroToleranceGuard(
        kind=ZeroTolerance.HARD_CONDITION_BYPASS,
        description="用户限定的年份/城市/不联网等硬条件不得被语义扩展或补证放宽",
        tests=(
            "tests/paper/test_paper_issue24.py::test_year_hard_condition_blocks_instead_of_widening",
            "tests/paper/test_paper_issue24_acceptance.py::test_excluded_arxiv_source_blocks_before_any_search",
            "tests/chat/test_improvement12_acceptance.py::test_direct_paper_stream_does_not_call_network_when_forbidden",
            "tests/resources/test_issue25_lifecycle_acceptance.py::test_explicit_network_prohibition_blocks_all_resources_sources",
        ),
    ),
    ZeroToleranceGuard(
        kind=ZeroTolerance.SYSTEM_FAILURE_AS_ERROR,
        description="批改/调用系统失败不得推进题号或记为学生错答",
        tests=(
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_grade_failure_keeps_question_then_retry_commits_once",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_judgement_and_next_question_have_separate_commit_boundaries",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_late_lease_loss_rejects_grade_without_advancing",
        ),
    ),
    ZeroToleranceGuard(
        kind=ZeroTolerance.DUPLICATE_JUDGEMENT,
        description="同一请求重试/断线不得重复消息、附件或判定",
        tests=(
            "tests/chat/test_v2_02_resumable_runs.py::test_send_idempotency_replays_same_run_without_duplicates",
            "tests/chat/test_v2_02_resumable_runs.py::test_retry_idempotency_reuses_run",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_grade_failure_keeps_question_then_retry_commits_once",
        ),
    ),
    ZeroToleranceGuard(
        kind=ZeroTolerance.WRITE_AFTER_STOP,
        description="停止或租约转移后迟到提交必须被拒绝，不得覆盖新版本",
        tests=(
            "tests/chat/test_improvement30_study_pages_recognition.py::test_stop_during_recognition_does_not_commit_material",
            "tests/chat/test_improvement30_study_pages_recognition.py::test_lease_transfer_rejects_stale_commit",
            "tests/chat/test_improvement35_acceptance.py::test_append_final_transaction_rejects_late_authority_change",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_late_lease_loss_rejects_grade_without_advancing",
            "tests/evaluation/test_issue42_scenario_gaps.py::test_r07_stop_has_no_auto_continue_and_explicit_continue_reuses_task",
            "tests/chat/test_improvement12_hybrid_entry.py::test_stop_generation_pauses_current_task",
        ),
    ),
)

SCENARIO_BY_ID: dict[str, Scenario] = {
    scenario.scenario_id: scenario for scenario in WORKFLOW_SCENARIOS
}

REAL_MODEL_PAIRING_COVERAGE: tuple[str, ...] = (
    "A01",
    "A02",
    "A03",
    "A11",
    "R07",
)


def expected_scenario_ids() -> tuple[str, ...]:
    """39 个固定场景编号（A18 + L13 + R8）。"""
    return tuple(
        [f"A{index:02d}" for index in range(1, 19)]
        + [f"L{index:02d}" for index in range(1, 14)]
        + [f"R{index:02d}" for index in range(1, 9)]
    )


def validate_workflow_scenarios() -> list[str]:
    """结构性校验；返回问题列表，空列表表示清单可用。"""
    problems: list[str] = []
    expected = set(expected_scenario_ids())
    actual = {scenario.scenario_id for scenario in WORKFLOW_SCENARIOS}
    for missing in sorted(expected - actual):
        problems.append(f"缺少场景：{missing}")
    for unknown in sorted(actual - expected):
        problems.append(f"未登记的场景编号：{unknown}")
    if len(WORKFLOW_SCENARIOS) != len(expected):
        problems.append(f"场景总数应为 {len(expected)}，实际 {len(WORKFLOW_SCENARIOS)}。")
    if len(SCENARIO_BY_ID) != len(WORKFLOW_SCENARIOS):
        problems.append("场景编号存在重复。")

    for scenario in WORKFLOW_SCENARIOS:
        if not scenario.tests:
            problems.append(f"{scenario.scenario_id} 没有任何可执行测试证据。")
        for node in scenario.tests:
            if "::" not in node or not node.startswith("tests/"):
                problems.append(f"{scenario.scenario_id} 的测试节点格式非法：{node}")
        for flag in scenario.zero_tolerance:
            guard = _guard_for(flag)
            if guard is None:
                problems.append(f"{scenario.scenario_id} 引用了未知零容忍项：{flag}")
            elif not set(scenario.tests) & set(guard.tests):
                problems.append(f"{scenario.scenario_id} 标记零容忍 {flag} 但没有对应守卫测试。")
    for scenario_id in REAL_MODEL_PAIRING_COVERAGE:
        pairing_scenario = SCENARIO_BY_ID.get(scenario_id)
        if pairing_scenario is None:
            problems.append(f"真实模型配对覆盖引用了不存在的场景：{scenario_id}")
        elif not pairing_scenario.real_model:
            problems.append(f"真实模型配对场景 {scenario_id} 未标记 real_model。")
    return problems


def _guard_for(kind: ZeroTolerance) -> ZeroToleranceGuard | None:
    for guard in ZERO_TOLERANCE_GUARDS:
        if guard.kind is kind:
            return guard
    return None


__all__ = [
    "REAL_MODEL_PAIRING_COVERAGE",
    "SCENARIO_BY_ID",
    "Scenario",
    "WORKFLOW_SCENARIOS",
    "ZERO_TOLERANCE_GUARDS",
    "ZeroTolerance",
    "ZeroToleranceGuard",
    "expected_scenario_ids",
    "validate_workflow_scenarios",
]
