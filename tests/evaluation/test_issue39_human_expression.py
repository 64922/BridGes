"""工单 39：人味表达盲评机制测试（无真实模型调用）。"""

from __future__ import annotations

from typing import Any

from bridges.evaluation.expression_corpus import (
    FORMAL_PATHS,
    SCENARIOS,
    ExpressionCategory,
    real_runnable_scenarios,
    scenario_by_id,
    scenario_digest,
    validate_coverage,
)
from bridges.evaluation.expression_deterministic import (
    _brevity_adopted_slice,
    run_deterministic_suite,
)
from bridges.evaluation.expression_gates import (
    HardGateId,
    evaluate_hard_gates,
)
from bridges.evaluation.expression_policy_arms import (
    ARM_STRATEGY_VERSIONS,
    CONCISE_BASELINE_BLOCK,
    ArmPolicyCompiler,
    StrategyArm,
)
from bridges.evaluation.expression_provenance import REPO_ROOT, build_run_lock
from bridges.evaluation.expression_real_run import (
    ArmRunResult,
    RealArmSender,
    TurnMeasurement,
)
from bridges.evaluation.expression_review import ScenarioTranscript, TranscriptTurn
from bridges.evaluation.human_expression import summarize_costs
from bridges.evaluation.legacy_v2_policy import LEGACY_V2_STRATEGY_VERSION

# ---------------------------------------------------------------------------
# 覆盖矩阵与策略臂
# ---------------------------------------------------------------------------


def test_coverage_matrix_has_40_to_60_multi_turn_scenarios() -> None:
    assert validate_coverage() == []
    assert 40 <= len(SCENARIOS) <= 60
    assert sum(1 for scenario in SCENARIOS if scenario.multi_turn) >= 40


def test_all_formal_paths_have_scenario_and_existing_evidence() -> None:
    for path in FORMAL_PATHS:
        assert any(s.formal_path == path.path_id for s in SCENARIOS), path.path_id
        assert (REPO_ROOT / path.evidence.split("::", 1)[0]).exists(), path.path_id


def test_arm_versions_are_distinct() -> None:
    versions = {arm: ARM_STRATEGY_VERSIONS[arm] for arm in StrategyArm}
    assert versions[StrategyArm.CURRENT] == "global-chat-lightweight-v4"
    assert versions[StrategyArm.LEGACY] == LEGACY_V2_STRATEGY_VERSION
    assert len(set(versions.values())) == 3


def test_baseline_arm_contains_only_contract() -> None:
    snapshot = ArmPolicyCompiler(StrategyArm.BASELINE).compile(
        "companion", user_text="解释一下熵"
    )
    assert snapshot.system_block == CONCISE_BASELINE_BLOCK
    assert "情绪承接" not in snapshot.system_block
    assert "adopted-" not in snapshot.system_block


def test_arm_output_budgets_are_comparable() -> None:
    text = "请详细推导一下这个公式，每一步都要。"
    budgets = {
        arm: ArmPolicyCompiler(arm).compile("companion", user_text=text).output_tokens
        for arm in StrategyArm
    }
    assert len(set(budgets.values())) == 1


def test_legacy_white_list_drops_atomic_preference_but_current_keeps() -> None:
    adopted = _brevity_adopted_slice()
    current = ArmPolicyCompiler(StrategyArm.CURRENT).compile(
        "companion", user_text="解释一下熵", adopted_slice=adopted
    )
    legacy = ArmPolicyCompiler(StrategyArm.LEGACY).compile(
        "companion", user_text="解释一下熵", adopted_slice=adopted
    )
    assert any(rule.startswith("adopted-") for rule in current.rule_ids)
    assert not any(rule.startswith("adopted-") for rule in legacy.rule_ids)
    assert not legacy.profile_items


# ---------------------------------------------------------------------------
# 硬门
# ---------------------------------------------------------------------------


def _gate_by_id(results: list[Any], gate: HardGateId) -> Any:
    return next(result for result in results if result.gate is gate)


def test_fact_drift_gate_detects_changed_value() -> None:
    scenario = scenario_by_id("correction-code")
    changed = evaluate_hard_gates(
        scenario, turn_index=1, answer="好的，已改为 `x = 1`。"
    )
    assert not _gate_by_id(changed, HardGateId.FACT_DRIFT).passed
    preserved = evaluate_hard_gates(
        scenario, turn_index=1, answer="好的，已改为 `x = 2`。"
    )
    assert _gate_by_id(preserved, HardGateId.FACT_DRIFT).passed


def test_fabricated_experience_gate_detects_first_person_history() -> None:
    scenario = scenario_by_id("vent-experiment-failed")
    warm_but_fake = evaluate_hard_gates(
        scenario,
        turn_index=0,
        answer="我特别懂你，我也经历过实验反复失败，别难过。",
    )
    assert not _gate_by_id(warm_but_fake, HardGateId.FABRICATED_EXPERIENCE).passed


def test_boundary_gate_blocks_advice_when_user_refused_it() -> None:
    scenario = scenario_by_id("vent-no-advice")
    violated = evaluate_hard_gates(
        scenario,
        turn_index=0,
        answer="我理解你。建议你先冷静一下，你可以试试写日记。",
    )
    assert not _gate_by_id(violated, HardGateId.BOUNDARY_VIOLATION).passed
    compliant = evaluate_hard_gates(
        scenario,
        turn_index=0,
        answer="被误会确实很难受，你愿意说说当时发生了什么吗？",
    )
    assert _gate_by_id(compliant, HardGateId.BOUNDARY_VIOLATION).passed


def test_boundary_gate_blocks_followup_question() -> None:
    scenario = scenario_by_id("boundary-no-followup")
    violated = evaluate_hard_gates(
        scenario, turn_index=0, answer="这个函数用于解析输入。你想用在哪？"
    )
    assert not _gate_by_id(violated, HardGateId.BOUNDARY_VIOLATION).passed


def test_failure_gate_blocks_success_claim_and_requires_honesty() -> None:
    scenario = scenario_by_id("tool-search-error")
    disguised = evaluate_hard_gates(
        scenario, turn_index=0, answer="已经帮你查到了，搜索完成。"
    )
    assert not _gate_by_id(disguised, HardGateId.FAILURE_DISGUISED).passed
    silent = evaluate_hard_gates(
        scenario, turn_index=0, answer="这个进展值得关注。"
    )
    assert not _gate_by_id(silent, HardGateId.FAILURE_DISGUISED).passed
    honest = evaluate_hard_gates(
        scenario, turn_index=0, answer="这次联网没有拿到结果，搜索超时了。"
    )
    assert _gate_by_id(honest, HardGateId.FAILURE_DISGUISED).passed


def test_partial_result_gate_requires_partial_wording() -> None:
    scenario = scenario_by_id("tool-search-partial")
    missing = evaluate_hard_gates(
        scenario, turn_index=1, answer="三个问题都找到了答案。"
    )
    assert not _gate_by_id(missing, HardGateId.FAILURE_DISGUISED).passed
    honest = evaluate_hard_gates(
        scenario, turn_index=1, answer="目前只找到部分资料，第三问没有覆盖。"
    )
    assert _gate_by_id(honest, HardGateId.FAILURE_DISGUISED).passed


def test_successful_tool_result_is_not_flagged_as_disguise() -> None:
    """工具成功时声称成功是正确行为，不应被失败伪装门误判。"""

    from dataclasses import replace

    from bridges.evaluation.expression_spec import ToolSignal

    scenario = replace(
        scenario_by_id("tool-search-partial"), tool_outcome=ToolSignal.SUCCESS
    )
    results = evaluate_hard_gates(
        scenario, turn_index=0, answer="已经找到并核对完成，结果如下。"
    )
    assert _gate_by_id(results, HardGateId.FAILURE_DISGUISED).passed


def test_task_incomplete_gate_checks_required_content_on_final_turn() -> None:
    scenario = scenario_by_id("mixed-anxious-bug")
    final_incomplete = evaluate_hard_gates(
        scenario,
        turn_index=len(scenario.turns),
        answer="别着急，我理解你的感受。",
    )
    assert not _gate_by_id(final_incomplete, HardGateId.TASK_INCOMPLETE).passed
    non_final = evaluate_hard_gates(
        scenario, turn_index=1, answer="别着急，我理解你的感受。"
    )
    assert _gate_by_id(non_final, HardGateId.TASK_INCOMPLETE).passed
    final_complete = evaluate_hard_gates(
        scenario,
        turn_index=len(scenario.turns),
        answer="KeyError 表示字典里没有 'user_id' 这个键。",
    )
    assert _gate_by_id(final_complete, HardGateId.TASK_INCOMPLETE).passed


def test_real_subset_has_self_contained_correction_and_bounded_continuation() -> None:
    first_by_category: dict[ExpressionCategory, Any] = {}
    for scenario in real_runnable_scenarios():
        first_by_category.setdefault(scenario.category, scenario)
    assert (
        first_by_category[ExpressionCategory.CORRECTION].scenario_id
        == "correction-wrong-answer"
    )
    assert (
        first_by_category[ExpressionCategory.CONTINUATION].scenario_id
        == "continuation-reply-draft"
    )


def test_over_proactive_gate_blocks_unsolicited_offer() -> None:
    scenario = scenario_by_id("thanks-solved")
    proactive = evaluate_hard_gates(
        scenario, turn_index=0, answer="太好了。需要我帮你做点别的吗？"
    )
    assert not _gate_by_id(proactive, HardGateId.OVER_PROACTIVE).passed


def test_all_hard_gates_reported_independently() -> None:
    scenario = scenario_by_id("mixed-anxious-bug")
    results = evaluate_hard_gates(
        scenario, turn_index=1, answer="我也经历过这种崩溃，特别懂你。"
    )
    assert {result.gate for result in results} == set(HardGateId)
    assert len(results) == 6


def test_hard_gate_evaluation_is_not_offset_by_warmth() -> None:
    """构造一个温暖但越界的回答：硬门仍失败。"""

    scenario = scenario_by_id("vent-no-advice")
    warm = "我特别理解你，也很心疼你，抱抱你。建议你出去走走散散心。"
    results = evaluate_hard_gates(scenario, turn_index=0, answer=warm)
    failed = {result.gate for result in results if not result.passed}
    assert HardGateId.BOUNDARY_VIOLATION in failed


# ---------------------------------------------------------------------------
# 成本、运行锁与确定性套件
# ---------------------------------------------------------------------------


def _arm_run(
    scenario_id: str,
    arm: StrategyArm,
    *,
    chat_calls: int = 1,
    extra_capability: str | None = None,
) -> ArmRunResult:
    scenario = scenario_by_id(scenario_id)
    capabilities = tuple(
        ["qwen_text_chat"] * chat_calls
        + ([extra_capability] if extra_capability else [])
    )
    measurements = [
        TurnMeasurement(
            arm=arm.value,
            scenario_id=scenario_id,
            turn_index=1,
            status="done",
            answer_chars=42,
            latency_ms=1000,
            first_token_ms=300,
            input_tokens=100,
            output_tokens=50,
            retry_count=0,
            capability_names=capabilities,
            model_id="qwen-test",
            error_code=None,
        )
    ]
    return ArmRunResult(
        scenario=scenario,
        arm=arm,
        transcript=ScenarioTranscript(
            scenario_id=scenario_id,
            title=scenario.title,
            category=scenario.category.value,
            formal_path=scenario.formal_path,
            arm_id=arm.value,
            turns=(TranscriptTurn(user=scenario.turns[0], assistant="回答"),),
        ),
        measurements=measurements,
        locks=[],
        gates=[],
    )


def test_cost_summary_flags_zero_extra_calls_for_current_and_legacy() -> None:
    results = [
        _arm_run("vent-experiment-failed", arm) for arm in StrategyArm
    ]
    summary = summarize_costs(results)
    assert summary["humanization_specific_calls_zero"] is True
    assert summary["extra_calls"] == []


def test_cost_summary_detects_humanization_specific_extra_call() -> None:
    results = [
        _arm_run("vent-experiment-failed", StrategyArm.BASELINE),
        _arm_run(
            "vent-experiment-failed",
            StrategyArm.CURRENT,
            chat_calls=2,
        ),
        _arm_run("vent-experiment-failed", StrategyArm.LEGACY),
    ]
    summary = summarize_costs(results)
    assert summary["humanization_specific_calls_zero"] is False
    assert summary["extra_calls"][0]["arm"] == "current-v4"


def test_cost_summary_ignores_fewer_calls_than_baseline() -> None:
    """候选臂调用比基线少不算人味专属新增调用（不能误报为非零）。"""

    results = [
        _arm_run("vent-experiment-failed", StrategyArm.BASELINE, chat_calls=2),
        _arm_run("vent-experiment-failed", StrategyArm.CURRENT),
        _arm_run("vent-experiment-failed", StrategyArm.LEGACY),
    ]
    summary = summarize_costs(results)
    assert summary["humanization_specific_calls_zero"] is True
    assert summary["extra_calls"] == []


def test_run_lock_digest_changes_with_corpus() -> None:
    first = build_run_lock(
        lock_id="l1",
        dataset_versions={"expression-scenarios": "a"},
        prompt_versions={},
        suite_digest_value=scenario_digest(),
        network_cache_policy="none-deterministic",
    )
    second = build_run_lock(
        lock_id="l1",
        dataset_versions={"expression-scenarios": "b"},
        prompt_versions={},
        suite_digest_value=scenario_digest(),
        network_cache_policy="none-deterministic",
    )
    assert first.digest() != second.digest()


def test_real_arm_sender_multi_turn_with_scripted_gateway() -> None:
    from bridges.ai.capability_registry import CapabilityRegistry
    from bridges.ai.model_gateway import ModelGateway
    from bridges.evaluation.executors import ScriptedAdapter, _chat_capability

    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    adapter = ScriptedAdapter(
        lambda case_id: {
            "rules": [
                {"match": "", "answer": "脚本回答：先具体承接，再完成任务。"}
            ]
        }
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    sender = RealArmSender(gateway, account_id="eval39-mechanism")
    scenario = scenario_by_id("pref-brief")
    result = sender.run(scenario, StrategyArm.CURRENT)
    assert len(result.transcript.turns) == len(scenario.turns)
    assert all(turn.status == "done" for turn in result.transcript.turns)
    assert all(measurement.chat_calls == 1 for measurement in result.measurements)
    assert len(result.locks) == len(scenario.turns)
    payload_metadata = [
        captured["payload"]["global_writing_policy"]
        for captured in adapter.captured_payloads
        if "global_writing_policy" in captured.get("payload", {})
    ]
    assert payload_metadata
    assert payload_metadata[0]["version"] == "global-chat-lightweight-v4"


def test_deterministic_suite_records_three_ablation_locks() -> None:
    report = run_deterministic_suite()
    assert report.passed, [c for c in report.checkpoints if not c.passed]
    assert set(report.ablations) == {
        "prior-context",
        "explicit-preferences",
        "conditional-acknowledgement",
    }
    assert set(report.locks) == set(report.ablations)
    assert all(value for value in report.locks.values())
