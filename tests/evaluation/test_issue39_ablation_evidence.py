"""工单 39：真实消融证据模块的确定性回归（无模型调用）。"""

from __future__ import annotations

from bridges.chat.lightweight_policy import ToolOutcome
from bridges.evaluation.expression_ablation_run import (
    AblationId,
    AblationPolicyCompiler,
    SignalCondition,
    ablation_probe,
    signal_effect,
    verify_ablation_evidence,
)
from bridges.evaluation.expression_deterministic import _brevity_adopted_slice
from bridges.evaluation.expression_policy_arms import StrategyArm


def _compile(
    ablation: AblationId, condition: SignalCondition, **kwargs: object
) -> dict[str, object]:
    compiler = AblationPolicyCompiler(StrategyArm.CURRENT, ablation, condition)
    snapshot = compiler.compile("companion", **kwargs)
    return {
        "constraints": list(snapshot.constraints),
        "rule_ids": list(snapshot.rule_ids),
        "output_tokens": snapshot.output_tokens,
    }


def test_prior_context_is_suppressed_only_in_absent_condition() -> None:
    kwargs = {
        "user_text": "继续，把后半段写完。",
        "continuation_text": "写一篇两千字的故事。",
    }
    present = _compile(AblationId.PRIOR_CONTEXT, SignalCondition.PRESENT, **kwargs)
    absent = _compile(AblationId.PRIOR_CONTEXT, SignalCondition.ABSENT, **kwargs)
    assert "detail_requested" in present["constraints"]
    assert "detail_requested" not in absent["constraints"]


def test_explicit_preferences_require_adopted_slice() -> None:
    adopted = _brevity_adopted_slice()
    kwargs = {"user_text": "解释一下熵是什么", "adopted_slice": adopted}
    present = _compile(AblationId.EXPLICIT_PREFERENCES, SignalCondition.PRESENT, **kwargs)
    absent = _compile(AblationId.EXPLICIT_PREFERENCES, SignalCondition.ABSENT, **kwargs)
    assert any(rule.startswith("adopted-") for rule in present["rule_ids"])
    assert not any(rule.startswith("adopted-") for rule in absent["rule_ids"])


def test_conditional_acknowledgement_signal_is_injected_and_removed() -> None:
    kwargs = {
        "user_text": "先给能确认的部分。",
        "continuation_text": "帮我找一下这三个问题的资料。",
    }
    present = _compile(
        AblationId.CONDITIONAL_ACKNOWLEDGEMENT, SignalCondition.PRESENT, **kwargs
    )
    absent = _compile(
        AblationId.CONDITIONAL_ACKNOWLEDGEMENT, SignalCondition.ABSENT, **kwargs
    )
    assert "partial_results" in present["constraints"]
    assert "partial-results-stated" in present["rule_ids"]
    assert "partial_results" not in absent["constraints"]
    assert "partial-results-stated" not in absent["rule_ids"]


def test_conditional_absent_compiler_ignores_pipeline_partial_signal() -> None:
    compiler = AblationPolicyCompiler(
        StrategyArm.CURRENT,
        AblationId.CONDITIONAL_ACKNOWLEDGEMENT,
        SignalCondition.ABSENT,
    )
    snapshot = compiler.compile(
        "companion", user_text="先给能确认的部分。", tool_outcome=ToolOutcome.PARTIAL
    )
    assert "partial_results" not in snapshot.constraints


def test_probe_scenarios_are_resolvable() -> None:
    assert ablation_probe(AblationId.PRIOR_CONTEXT).turns
    assert ablation_probe(AblationId.EXPLICIT_PREFERENCES).scenario_id == "pref-brief"
    assert (
        ablation_probe(AblationId.CONDITIONAL_ACKNOWLEDGEMENT).scenario_id
        == "tool-search-partial"
    )


def test_signal_effect_requires_present_and_absent_difference() -> None:
    effective, _ = signal_effect(
        AblationId.PRIOR_CONTEXT,
        [{"constraints": ["detail_requested"], "rule_ids": []}],
        [{"constraints": [], "rule_ids": []}],
    )
    assert effective
    not_effective, _ = signal_effect(
        AblationId.PRIOR_CONTEXT,
        [{"constraints": ["detail_requested"], "rule_ids": []}],
        [{"constraints": ["detail_requested"], "rule_ids": []}],
    )
    assert not not_effective


def test_signal_effect_compares_last_turn_only() -> None:
    """第一轮约束可能来自请求本身，不能掩盖续接轮的对照差异。"""

    effective, _ = signal_effect(
        AblationId.PRIOR_CONTEXT,
        [
            {"constraints": ["detail_requested"], "rule_ids": []},
            {"constraints": ["detail_requested"], "rule_ids": []},
        ],
        [
            {"constraints": ["detail_requested"], "rule_ids": []},
            {"constraints": [], "rule_ids": []},
        ],
    )
    assert effective
    effective_conditional, _ = signal_effect(
        AblationId.CONDITIONAL_ACKNOWLEDGEMENT,
        [
            {"constraints": ["partial_results"], "rule_ids": ["partial-results-stated"]},
            {"constraints": ["partial_results"], "rule_ids": ["partial-results-stated"]},
        ],
        [
            {"constraints": ["partial_results"], "rule_ids": ["partial-results-stated"]},
            {"constraints": [], "rule_ids": []},
        ],
    )
    assert effective_conditional


def _entry(ablation_id: str, locks: list[dict[str, object]]) -> dict[str, object]:
    return {
        "ablation_id": ablation_id,
        "signal_effective": True,
        "conditions": [
            {
                "condition": SignalCondition.PRESENT.value,
                "transcript": [
                    {"user": "u", "assistant": "a", "status": "done", "error_code": None}
                ],
                "model_locks": locks,
            },
            {
                "condition": SignalCondition.ABSENT.value,
                "transcript": [
                    {"user": "u", "assistant": "a", "status": "done", "error_code": None}
                ],
                "model_locks": locks,
            },
        ],
    }


def test_verify_ablation_evidence_rejects_unfinished_turns() -> None:
    payload = {
        "ablations": [
            _entry(ablation.value, [{"lock_id": "l"}]) for ablation in AblationId
        ]
    }
    payload["ablations"][0]["conditions"][0]["transcript"] = [
        {
            "user": "u",
            "assistant": "",
            "status": "error",
            "error_code": "output_budget_exceeded",
        }
    ]
    problems = verify_ablation_evidence(payload)
    assert any("未真实完成" in problem for problem in problems)


def test_verify_ablation_evidence_flags_missing_locks() -> None:
    payload = {
        "ablations": [
            _entry(AblationId.PRIOR_CONTEXT.value, [{"lock_id": "l"}]),
            _entry(AblationId.EXPLICIT_PREFERENCES.value, [{"lock_id": "l"}]),
            _entry(AblationId.CONDITIONAL_ACKNOWLEDGEMENT.value, []),
        ]
    }
    problems = verify_ablation_evidence(payload)
    assert len(problems) == 2
    assert all("conditional-acknowledgement" in problem for problem in problems)


def test_verify_ablation_evidence_accepts_complete_payload() -> None:
    payload = {
        "ablations": [
            _entry(ablation.value, [{"lock_id": "l", "actual_model_id": "m"}])
            for ablation in AblationId
        ]
    }
    assert verify_ablation_evidence(payload) == []
