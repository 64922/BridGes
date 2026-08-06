"""注入回归测试（Issue 40 Verification-2）。

对故意注入的事实错误、画像越界、模板腔、风险误判、教学跳步和多模态
失败，验证指标能检出回归——指标失效即评测体系失效。
"""

from __future__ import annotations

import pytest

from bridges.evaluation import suite_data
from bridges.evaluation.injection import (
    InjectionScenario,
    detect,
    inject,
    target_case_id,
)


@pytest.mark.parametrize(
    "scenario",
    [
        InjectionScenario.FACT_ERROR,
        InjectionScenario.PROFILE_OVERREACH,
        InjectionScenario.TEMPLATE_FLUFF,
        InjectionScenario.RISK_MISJUDGMENT,
        InjectionScenario.TEACHING_SKIP,
        InjectionScenario.MULTIMODAL_FAILURE,
    ],
)
def test_injection_is_detected(scenario: InjectionScenario) -> None:
    """六类注入全部能被指标/断言检出。"""
    case_id = target_case_id(scenario)
    case = next(c for c in suite_data.CASES if c.case_id == case_id)
    # 基线输出（由案例脚本的"好答案"构造）。
    outputs = _baseline_outputs_for(case)
    injected = inject(scenario, case, outputs)
    assert injected != outputs, "注入必须改变输出"
    assert detect(scenario, case, injected), f"{scenario.value} 注入未被检出"
    # 未注入的基线不应被误报为检出。
    assert not detect(scenario, case, outputs), f"{scenario.value} 基线被误报"


def _baseline_outputs_for(case) -> dict:
    """按案例类型构造未注入的"好答案"输出。"""
    script = case.initial_state.get("script", {})
    rules = script.get("rules", [])
    default = ""
    for rule in rules:
        if rule.get("match", "") == "":
            default = rule.get("answer", "")
    # 事实错误注入以"带证据的好答案"为基线（脚本默认答案不含关键数值）。
    if case.case_id == "science-bell-evidence":
        default = (
            "贝尔不等式由 J. Bell 在 1964 年提出：局部隐变量理论必须满足该不等式"
            "约束，而量子力学预言的纠缠态测量结果会违反它。（来源：[1] 贝尔不等式笔记）"
        )
    if case.case_id == "profile-goal-loop":
        default = (
            "根据你的兴趣（量子力学，正在读《费曼物理学讲义》），"
            "我们从量子比特讲起。"
        )
    if case.case_id == "profile-goal-loop":
        return {
            "final_answer": str(default),
            "final_text": str(default),
            "recorded_assertions": [
                {
                    "canonical_dimension": "interest_preference",
                    "value_or_rule": "量子力学，正在读《费曼物理学讲义》",
                    "status": "active",
                }
            ],
            "context_note": {"state": "ready",
                "profile_items": [{}]},
            "profile_used": True,
            "citations": [],
            "tool_calls": [],
        }
    if case.case_id == "career-promise":
        return {
            "final_answer": "培训能提升技能，但无人能保证就业或月薪；任何承诺都应警惕。",
            "final_text": "培训能提升技能，但无人能保证就业或月薪；任何承诺都应警惕。",
            "facts": [],
            "assumptions": [{"title": "承诺不可信"}],
            "warnings": [],
            "boundary_statement": "本回答不作就业、薪酬或录取保证。",
            "tool_calls": [],
        }
    if isinstance(default, dict):
        return {
            "final_text": str(default.get("final_text", "")),
            "final_answer": str(default.get("final_text", "")),
            "edits": [{"reason": "x"}],
            "fact_check": [{"result": "已核实"}],
            "open_questions": [],
            "fact_lock_check": {"passed": True},
            "skill_status": "done",
            "recorded_assertions": [
                {
                    "canonical_dimension": "stage_goal",
                    "value_or_rule": "费曼物理学讲义",
                    "status": "active",
                }
            ],
            "context_note": {"state": "ready",
                "profile_items": [{}]},
            "profile_used": True,
        }
    return {
        "final_answer": str(default),
        "final_text": str(default),
        "answer": str(default),
        "citations": [{"citation_id": "c1"}],
        "recorded_assertions": [],
        "context_note": {},
        "profile_used": False,
        "diagnosis": "先备诊断",
        "plan_steps": ["步骤1"],
        "quiz": {"question": "q"},
        "web_search_triggered": False,
        "facts": [],
        "assumptions": [{"title": "假设"}],
        "warnings": ["警示"],
        "boundary_statement": "边界声明",
        "refused": True,
        "transcript": str(script.get("transcript", "")),
        "asset_available": True,
        "alt_text": "替代说明",
        "model_id": "qwen-image-2.0-pro-2026-06-22",
        "recovered": True,
        "task_status": "succeeded",
        "tool_calls": [],
    }


def test_injected_fact_error_breaks_claim_present() -> None:
    """事实错误注入使 claim_present 断言失败。"""
    case = next(
        c for c in suite_data.CASES if c.case_id == "science-bell-evidence"
    )
    outputs = _baseline_outputs_for(case)
    injected = inject(InjectionScenario.FACT_ERROR, case, outputs)
    assert "1950" in injected["final_answer"]
    from bridges.evaluation.metrics import run_auto_assertions

    assertions = run_auto_assertions(case, injected)
    assert not any(a.assertion_id == "a-fact" and a.passed for a in assertions)


def test_injected_profile_overreach_breaks_scope() -> None:
    case = next(c for c in suite_data.CASES if c.case_id == "profile-goal-loop")
    outputs = _baseline_outputs_for(case)
    injected = inject(InjectionScenario.PROFILE_OVERREACH, case, outputs)
    from bridges.evaluation.metrics import run_auto_assertions

    assertions = run_auto_assertions(case, injected)
    assert not any(a.assertion_id == "a-scope" and a.passed for a in assertions)
