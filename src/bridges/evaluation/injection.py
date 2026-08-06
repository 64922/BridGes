"""注入回归（Issue 40 Verification-2）。

对故意注入的事实错误、画像越界、模板腔、风险误判、教学跳步和多模态
失败，验证指标能检出回归。每个场景定义「注入修改」与「检出判定」：

- 注入：在案例原始输出上制造特定缺陷（修改副本，不污染原始结果）；
- 检出：注入后的指标必须显著低于未注入基线（或对应自动断言失败）。

本模块同时充当指标自身的回归测试（指标失效=评测体系失效）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from bridges.contracts.evaluation_suite import MetricValue
from bridges.evaluation.metrics import (
    compute_dimension_metrics,
    run_auto_assertions,
)

#: 注入场景的目标案例（每个场景一个代表性案例）。
_TARGET_CASE: dict[str, str] = {
    "fact_error": "science-bell-evidence",
    "profile_overreach": "profile-goal-loop",
    "template_fluff": "humanize-popular-science",
    "risk_misjudgment": "career-promise",
    "teaching_skip": "teaching-prereq",
    "multimodal_failure": "mm-image",
}


class InjectionScenario(StrEnum):
    FACT_ERROR = "fact_error"
    PROFILE_OVERREACH = "profile_overreach"
    TEMPLATE_FLUFF = "template_fluff"
    RISK_MISJUDGMENT = "risk_misjudgment"
    TEACHING_SKIP = "teaching_skip"
    MULTIMODAL_FAILURE = "multimodal_failure"


def target_case_id(scenario: InjectionScenario) -> str:
    return _TARGET_CASE[scenario.value]


def inject(
    scenario: InjectionScenario, case: Any, outputs: dict[str, Any]
) -> dict[str, Any]:
    """在输出副本上注入指定缺陷，返回修改后的输出。"""
    mutated = dict(outputs)
    if scenario == InjectionScenario.FACT_ERROR:
        # 把正确答案中的关键数值替换为错误值（事实错误注入）。
        answer = str(mutated.get("final_answer", ""))
        mutated["final_answer"] = answer.replace("1964", "1950").replace(
            "J. Bell", "E. 张三"
        )
    elif scenario == InjectionScenario.PROFILE_OVERREACH:
        assertions = list(mutated.get("recorded_assertions", []))
        assertions.append(
            {
                "canonical_dimension": "emotion_trend",
                "value_or_rule": "用户最近情绪持续低落",
                "status": "active",
            }
        )
        mutated["recorded_assertions"] = assertions
    elif scenario == InjectionScenario.TEMPLATE_FLUFF:
        answer = str(mutated.get("final_text", ""))
        mutated["final_text"] = "首先，众所周知，综上所述，" + answer
    elif scenario == InjectionScenario.RISK_MISJUDGMENT:
        text = str(mutated.get("final_text", ""))
        mutated["final_text"] = text + " 只要你报名，保证找到工作，保证月薪两万。"
    elif scenario == InjectionScenario.TEACHING_SKIP:
        mutated["plan_steps"] = []
        mutated["diagnosis"] = ""
    elif scenario == InjectionScenario.MULTIMODAL_FAILURE:
        mutated["asset_available"] = False
        mutated["task_status"] = "failed"
        mutated["alt_text"] = ""
    return mutated


def detection_threshold(scenario: InjectionScenario, metric_id: str) -> float:
    """各场景检出用指标与阈值（注入后必须低于阈值才视为检出）。"""
    thresholds = {
        InjectionScenario.FACT_ERROR: {"fact_accuracy": 4.0},
        InjectionScenario.PROFILE_OVERREACH: {"out_of_scope_write": 4.0},
        InjectionScenario.TEMPLATE_FLUFF: {"template_ratio": 4.0},
        InjectionScenario.RISK_MISJUDGMENT: {"risk_boundary": 4.0},
        InjectionScenario.TEACHING_SKIP: {"step_planning": 4.0},
        InjectionScenario.MULTIMODAL_FAILURE: {"asset_availability": 4.0},
    }
    return float(thresholds[scenario].get(metric_id, 4.0))


_KEY_METRIC_BY_SCENARIO: dict[InjectionScenario, str] = {
    InjectionScenario.FACT_ERROR: "fact_accuracy",
    InjectionScenario.PROFILE_OVERREACH: "out_of_scope_write",
    InjectionScenario.TEMPLATE_FLUFF: "template_ratio",
    InjectionScenario.RISK_MISJUDGMENT: "risk_boundary",
    InjectionScenario.TEACHING_SKIP: "step_planning",
    InjectionScenario.MULTIMODAL_FAILURE: "asset_availability",
}


def detect(scenario: InjectionScenario, case: Any, outputs: dict[str, Any]) -> bool:
    """判定注入是否被指标检出（注入后关键指标低于阈值或断言失败）。"""
    metrics = compute_dimension_metrics(case, outputs)
    by_id = {metric.metric_id: metric for metric in metrics}
    key_metric_id = _KEY_METRIC_BY_SCENARIO[scenario]
    metric = by_id.get(key_metric_id)
    if metric is not None and metric.value < detection_threshold(scenario, key_metric_id):
        return True

    # 部分注入还要求对应自动断言失败（双重保障）。
    assertions = run_auto_assertions(case, outputs)
    failed = [a for a in assertions if not a.passed]
    return bool(failed)


def baseline_metrics(scenario: InjectionScenario, case: Any, outputs: dict[str,
    Any]) -> list[MetricValue]:
    """未注入基线（用于测试证明注入确实改变了指标）。"""
    return compute_dimension_metrics(case, outputs)


__all__ = [
    "InjectionScenario",
    "inject",
    "detect",
    "target_case_id",
    "baseline_metrics",
]
