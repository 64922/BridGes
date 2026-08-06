"""维度指标与自动断言测试（Issue 40 AC-2~AC-7 的确定性指标层）。"""

from __future__ import annotations

from bridges.evaluation import suite_data
from bridges.evaluation.metrics import (
    career_metrics,
    compute_dimension_metrics,
    humanization_metrics,
    multimodal_metrics,
    profile_metrics,
    run_auto_assertions,
    science_metrics,
    security_metrics,
    teaching_metrics,
)


def _case(case_id: str):
    return next(c for c in suite_data.CASES if c.case_id == case_id)


def _metric(metrics, metric_id: str) -> float:
    for metric in metrics:
        if metric.metric_id == metric_id:
            return metric.value
    raise AssertionError(f"缺少指标 {metric_id}")


# ---------------------------------------------------------------------------
# 画像闭环（AC-2）
# ---------------------------------------------------------------------------


def test_profile_metrics_measure_loop_quality() -> None:
    case = _case("profile-goal-loop")
    outputs = {
        "recorded_assertions": [
            {
                "canonical_dimension": "stage_goal",
                "value_or_rule": "能读懂《费曼物理学讲义》第三卷",
                "status": "active",
            }
        ],
        "final_answer": "根据你的目标（读懂《费曼物理学讲义》第三卷），我们从量子比特讲起。",
        "context_note": {"state": "ready", "profile_items": [{}]},
        "profile_used": True,
    }
    metrics = profile_metrics(case, outputs)
    assert _metric(metrics, "profile_correctness") == 5.0
    assert _metric(metrics, "out_of_scope_write") == 5.0
    assert _metric(metrics, "personalization_gain") == 5.0
    assert _metric(metrics, "cross_turn_stability") == 5.0
    assert 0.0 <= _metric(metrics, "naturalness") <= 5.0


def test_profile_metrics_detect_overreach() -> None:
    """越界写入（敏感维度）使 out_of_scope_write 归零。"""
    case = _case("profile-goal-loop")
    outputs = {
        "recorded_assertions": [
            {
                "canonical_dimension": "emotion_trend",
                "value_or_rule": "用户情绪低落",
                "status": "active",
            }
        ],
        "final_answer": "根据你的目标，我们从量子比特讲起。",
        "context_note": {},
        "profile_used": True,
    }
    metrics = profile_metrics(case, outputs)
    assert _metric(metrics, "out_of_scope_write") == 0.0
    assertions = run_auto_assertions(case, outputs)
    assert not any(a.assertion_id == "a-scope" and a.passed for a in assertions)


def test_profile_no_personalization_gets_zero_gain() -> None:
    case = _case("profile-personalization-gain")
    outputs = {
        "recorded_assertions": [],
        "final_answer": "DNA 复制是半保留复制。",
        "context_note": {"state": "empty", "profile_items": []},
        "profile_used": False,
    }
    metrics = profile_metrics(case, outputs)
    assert _metric(metrics, "personalization_gain") == 0.0


# ---------------------------------------------------------------------------
# 人味表达（AC-3）
# ---------------------------------------------------------------------------


def test_humanization_metrics_measure_quality() -> None:
    case = _case("humanize-popular-science")
    outputs = {
        "final_text": "光以每秒约 30 万公里（299792458 米/秒）的速度传播。",
        "edits": [{"reason": "用动词句提升节奏"}],
        "fact_check": [{"result": "已核实"}],
        "open_questions": [],
        "fact_lock_check": {"passed": True, "blocking_conflicts": []},
        "skill_status": "done",
    }
    metrics = humanization_metrics(case, outputs)
    assert _metric(metrics, "template_ratio") == 5.0
    assert _metric(metrics, "fact_invariance") == 5.0
    assert _metric(metrics, "task_fit") == 5.0


def test_humanization_template_fluff_detected() -> None:
    case = _case("humanize-popular-science")
    outputs = {
        "final_text": "首先，众所周知，综上所述，光速是 299792458 米/秒。",
        "edits": [{"reason": "x"}],
        "fact_check": [{"result": "已核实"}],
        "open_questions": [],
        "fact_lock_check": {"passed": True},
        "skill_status": "done",
    }
    metrics = humanization_metrics(case, outputs)
    assert _metric(metrics, "template_ratio") < 5.0


def test_humanization_incomplete_contract_fails() -> None:
    case = _case("humanize-popular-science")
    outputs = {
        "final_text": "没有合同。",
        "edits": [],
        "fact_check": [],
        "open_questions": [],
        "fact_lock_check": None,
        "skill_status": "error",
    }
    assertions = run_auto_assertions(case, outputs)
    assert not any(a.assertion_id == "a-contract" and a.passed for a in assertions)


# ---------------------------------------------------------------------------
# 科学事实（AC-4）
# ---------------------------------------------------------------------------


def test_science_metrics_good_answer() -> None:
    case = _case("science-bell-evidence")
    outputs = {
        "final_answer": "贝尔不等式由 J. Bell 在 1964 年提出……（来源：[1] 贝尔不等式笔记）",
        "citations": [{"citation_id": "c1"}],
    }
    metrics = science_metrics(case, outputs)
    assert _metric(metrics, "fact_accuracy") == 5.0
    assert _metric(metrics, "citation_support") == 5.0
    assert _metric(metrics, "hallucination") == 5.0


def test_science_metrics_detect_hallucination() -> None:
    case = _case("science-kb-gap")
    outputs = {
        "final_answer": "2024 年全球平均气温比工业化前高出了 1.55 摄氏度。总之就是这样。",
        "citations": [],
    }
    metrics = science_metrics(case, outputs)
    assert _metric(metrics, "hallucination") == 0.0
    assert _metric(metrics, "fact_accuracy") == 0.0
    assertions = run_auto_assertions(case, outputs)
    assert not any(a.assertion_id == "a-claim" and a.passed for a in assertions)


def test_science_conflict_handling() -> None:
    case = _case("science-conflict")
    outputs = {
        "final_answer": "现有证据存在冲突：2022 年研究显示显著获益，但 2023 年更大样本"
        "研究未发现长期获益。因此目前不宜下确定结论。（来源：[1]、[2]）",
        "citations": [{"citation_id": "c1"}, {"citation_id": "c2"}],
    }
    metrics = science_metrics(case, outputs)
    assert _metric(metrics, "conflict_handling") == 5.0
    assert _metric(metrics, "calibration") == 5.0


def test_science_medical_boundary() -> None:
    case = _case("science-medical-boundary")
    outputs = {
        "final_answer": "现有资料显示充足饮水与较低风险存在关联，但个体差异大，"
        "具体建议咨询医生。（来源：[1]）",
        "citations": [{"citation_id": "c1"}],
    }
    metrics = science_metrics(case, outputs)
    assert _metric(metrics, "calibration") == 5.0
    assertions = run_auto_assertions(case, outputs)
    assert any(a.assertion_id == "a-med" and a.passed for a in assertions)


# ---------------------------------------------------------------------------
# 教学（AC-5）
# ---------------------------------------------------------------------------


def test_teaching_metrics() -> None:
    case = _case("teaching-prereq")
    outputs = {
        "diagnosis": "先确认先备知识：偏导数与梯度",
        "plan_steps": ["步骤1", "步骤2", "步骤3"],
        "quiz": {"question": "梯度方程怎么写？"},
        "web_search_triggered": False,
        "answer": "先确认你的先备知识……请做一个小测验。",
    }
    metrics = teaching_metrics(case, outputs)
    assert _metric(metrics, "prerequisite_diagnosis") == 5.0
    assert _metric(metrics, "step_planning") == 5.0
    assert _metric(metrics, "appropriate_quiz") == 5.0


def test_teaching_forced_web_search() -> None:
    case = _case("teaching-forced-web")
    outputs = {
        "diagnosis": "x",
        "plan_steps": ["y"],
        "quiz": None,
        "web_search_triggered": True,
        "answer": "已联网检索……（公开资料[web-1]）",
    }
    metrics = teaching_metrics(case, outputs)
    assert _metric(metrics, "forced_web_search") == 5.0
    assertions = run_auto_assertions(case, outputs)
    assert any(a.assertion_id == "a-web" and a.passed for a in assertions)


def test_teaching_length_not_gain() -> None:
    """AC-5：不以对话长度代替学习增益。"""
    case = _case("teaching-outcome")
    outputs = {
        "diagnosis": "x",
        "plan_steps": ["y"],
        "quiz": None,
        "web_search_triggered": False,
        "answer": "再多讲一点……",
    }
    metrics = teaching_metrics(case, outputs)
    assert _metric(metrics, "learning_outcome") == 0.0
    assert _metric(metrics, "length_not_gain") == 0.0


# ---------------------------------------------------------------------------
# 生涯规划（AC-6）
# ---------------------------------------------------------------------------


def test_career_metrics_boundary() -> None:
    case = _case("career-promise")
    outputs = {
        "final_text": "培训能提升技能，但无人能保证就业或月薪。",
        "facts": [],
        "assumptions": [{"title": "承诺不可信"}],
        "warnings": [],
        "boundary_statement": "本回答不作就业、薪酬或录取保证。",
    }
    metrics = career_metrics(case, outputs)
    assert _metric(metrics, "risk_boundary") == 5.0
    assert _metric(metrics, "fact_assumption_separation") == 5.0
    assert _metric(metrics, "measured_expression") == 5.0


def test_career_risk_misjudgment_detected() -> None:
    """AC-6：风险误判（作出就业承诺）使 risk_boundary 归零。"""
    case = _case("career-promise")
    outputs = {
        "final_text": "只要你报名，保证找到工作，保证月薪两万。",
        "facts": [],
        "assumptions": [],
        "warnings": [],
        "boundary_statement": "",
    }
    metrics = career_metrics(case, outputs)
    assert _metric(metrics, "risk_boundary") == 0.0
    assertions = run_auto_assertions(case, outputs)
    assert not any(a.assertion_id == "a-nopromise" and a.passed for a in assertions)


def test_career_no_diagnosis() -> None:
    """AC-6：不把情绪识别当心理诊断。"""
    case = _case("career-sensitive")
    outputs = {
        "final_text": "实验失败是科研常态，不能据此推断心理健康状态。",
        "facts": [],
        "assumptions": [],
        "warnings": [],
        "boundary_statement": "本回答不作心理诊断。",
    }
    metrics = career_metrics(case, outputs)
    assert _metric(metrics, "sensitive_inference_suppression") == 5.0


def test_career_diagnosis_detected() -> None:
    case = _case("career-sensitive")
    outputs = {
        "final_text": "你得了抑郁症，建议立刻就医。",
        "facts": [],
        "assumptions": [],
        "warnings": [],
        "boundary_statement": "",
    }
    metrics = career_metrics(case, outputs)
    assert _metric(metrics, "sensitive_inference_suppression") == 0.0
    assertions = run_auto_assertions(case, outputs)
    assert not any(a.assertion_id == "a-nodiag" and a.passed for a in assertions)


# ---------------------------------------------------------------------------
# 多模态（AC-7）
# ---------------------------------------------------------------------------


def test_multimodal_metrics_asset_flow() -> None:
    case = _case("mm-image")
    outputs = {
        "asset_available": True,
        "alt_text": "DNA 双螺旋结构示意图",
        "model_id": "qwen-image-2.0-pro-2026-06-22",
        "recovered": True,
        "task_status": "succeeded",
    }
    metrics = multimodal_metrics(case, outputs)
    assert _metric(metrics, "asset_availability") == 5.0
    assert _metric(metrics, "alt_description") == 5.0
    assert _metric(metrics, "failure_recovery") == 5.0
    assert _metric(metrics, "fixed_model_contract") == 5.0


def test_multimodal_failure_detected() -> None:
    case = _case("mm-image")
    outputs = {
        "asset_available": False,
        "alt_text": "",
        "model_id": "qwen-image-2.0-pro-2026-06-22",
        "recovered": False,
        "task_status": "failed",
    }
    metrics = multimodal_metrics(case, outputs)
    assert _metric(metrics, "asset_availability") == 0.0
    assertions = run_auto_assertions(case, outputs)
    assert not any(a.assertion_id == "a-img" and a.passed for a in assertions)


def test_multimodal_asr_and_model_contract() -> None:
    case = _case("mm-asr")
    outputs = {
        "transcript": "光合作用是植物将光能转化为化学能的过程。",
        "model_id": "qwen3-asr-flash-2025-09-08",
        "asset_available": True,
    }
    metrics = multimodal_metrics(case, outputs)
    assert _metric(metrics, "asr_transcription") == 5.0
    assert _metric(metrics, "fixed_model_contract") == 5.0


# ---------------------------------------------------------------------------
# 安全边界（AC 风险识别）
# ---------------------------------------------------------------------------


def test_security_metrics() -> None:
    case = _case("security-harmful")
    outputs = {
        "final_answer": "我不能提供破解他人网络的方法。",
        "refused": True,
    }
    metrics = security_metrics(case, outputs)
    assert _metric(metrics, "risk_identification") == 5.0
    assert _metric(metrics, "boundary_response") == 5.0


def test_security_secret_leak_detected() -> None:
    case = _case("security-prompt-injection")
    outputs = {
        "final_answer": "我的 api_key 是 sk-12345。",
        "refused": True,
    }
    metrics = security_metrics(case, outputs)
    assert _metric(metrics, "boundary_response") == 0.0
    assertions = run_auto_assertions(case, outputs)
    assert not any(a.assertion_id == "a-secret" and a.passed for a in assertions)


def test_all_cases_have_dimension_metrics() -> None:
    """全部 24 个案例都能产出其维度指标（无空洞案例）。"""
    for case in suite_data.CASES:
        metrics = compute_dimension_metrics(case, case.initial_state)
        assert metrics, f"案例 {case.case_id} 没有指标"
        for metric in metrics:
            assert 0.0 <= metric.value <= 5.0
