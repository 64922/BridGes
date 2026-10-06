"""工单 39：原创场景矩阵公共入口（类型/路径/场景/覆盖校验）。

场景数据按日常表达与路径覆盖拆分在两个数据模块，本模块负责汇总、
查询、分布与覆盖校验；硬门、策略臂与评测编排见同包其他模块。
"""

from __future__ import annotations

import hashlib
import json

from bridges.evaluation.expression_scenarios_daily import SCENARIOS as DAILY_SCENARIOS
from bridges.evaluation.expression_scenarios_paths import SCENARIOS as PATH_SCENARIOS
from bridges.evaluation.expression_spec import (
    FORMAL_PATH_IDS,
    FORMAL_PATHS,
    REQUIRED_CATEGORIES,
    ExpressionCategory,
    ExpressionScenario,
    FormalPath,
    ToolSignal,
)

SCENARIOS: tuple[ExpressionScenario, ...] = DAILY_SCENARIOS + PATH_SCENARIOS


def scenario_by_id(scenario_id: str) -> ExpressionScenario:
    for scenario in SCENARIOS:
        if scenario.scenario_id == scenario_id:
            return scenario
    raise KeyError(f"场景不存在：{scenario_id}")


def real_runnable_scenarios() -> tuple[ExpressionScenario, ...]:
    return tuple(scenario for scenario in SCENARIOS if scenario.real_runnable)


def validate_coverage() -> list[str]:
    """覆盖矩阵结构校验；返回问题列表（空表示通过）。"""

    problems: list[str] = []
    if not (40 <= len(SCENARIOS) <= 60):
        problems.append(f"场景总数应在 40–60 之间，实际 {len(SCENARIOS)}。")
    ids = [scenario.scenario_id for scenario in SCENARIOS]
    if len(ids) != len(set(ids)):
        problems.append("场景标识重复。")
    categories = {scenario.category for scenario in SCENARIOS}
    for category in REQUIRED_CATEGORIES:
        if category not in categories:
            problems.append(f"缺少类别：{category.value}。")
    paths = {scenario.formal_path for scenario in SCENARIOS}
    for path_id in sorted(FORMAL_PATH_IDS):
        if path_id not in paths:
            problems.append(f"正式路径无场景覆盖：{path_id}。")
    if sum(1 for scenario in SCENARIOS if scenario.multi_turn) < 40:
        problems.append("连续多轮场景不足 40 组。")
    if not any(scenario.tool_outcome is ToolSignal.ERROR for scenario in SCENARIOS):
        problems.append("缺少工具失败场景。")
    if not any(scenario.tool_outcome is ToolSignal.PARTIAL for scenario in SCENARIOS):
        problems.append("缺少工具部分结果场景。")
    if not any(scenario.boundary for scenario in SCENARIOS):
        problems.append("缺少明确边界场景。")
    if not any(scenario.expects_continuation for scenario in SCENARIOS):
        problems.append("缺少续接/纠正场景。")
    if not any(scenario.detail_required for scenario in SCENARIOS):
        problems.append("缺少长任务场景。")
    if not any(scenario.profile_facts for scenario in SCENARIOS):
        problems.append("缺少明确偏好场景。")
    known_paths = {
        path.path_id for path in FORMAL_PATHS if path.render_kind != "fixed_template"
    }
    for scenario in SCENARIOS:
        if scenario.formal_path not in FORMAL_PATH_IDS:
            problems.append(f"未登记路径：{scenario.scenario_id}/{scenario.formal_path}。")
        if (
            scenario.formal_path in known_paths
            and scenario.real_runnable
            and not scenario.turns
        ):
            problems.append(f"可真实运行场景缺少轮次：{scenario.scenario_id}。")
    return problems


def coverage_matrix() -> list[dict[str, object]]:
    """场景 × 类别 × 路径的审计矩阵（报告用）。"""

    return [
        {
            "scenario_id": scenario.scenario_id,
            "title": scenario.title,
            "category": scenario.category.value,
            "formal_path": scenario.formal_path,
            "turn_count": len(scenario.turns),
            "multi_turn": scenario.multi_turn,
            "tool_outcome": scenario.tool_outcome.value,
            "boundary": scenario.boundary,
            "expects_continuation": scenario.expects_continuation,
            "detail_required": scenario.detail_required,
            "real_runnable": scenario.real_runnable,
            "profile_fact_count": len(scenario.profile_facts),
            "protected_fact_count": len(scenario.protected_facts),
            "tags": list(scenario.tags),
        }
        for scenario in SCENARIOS
    ]


def scenario_digest() -> str:
    """全部场景声明内容的规范摘要（进入运行锁与报告）。"""

    payload = [
        {
            "scenario_id": scenario.scenario_id,
            "category": scenario.category.value,
            "formal_path": scenario.formal_path,
            "turns": list(scenario.turns),
            "profile_facts": list(scenario.profile_facts),
            "tool_outcome": scenario.tool_outcome.value,
            "protected_facts": list(scenario.protected_facts),
            "boundary": scenario.boundary,
            "required_any": list(scenario.required_any),
            "forbidden_any": list(scenario.forbidden_any),
            "detail_required": scenario.detail_required,
            "expects_continuation": scenario.expects_continuation,
            "real_runnable": scenario.real_runnable,
        }
        for scenario in SCENARIOS
    ]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def scenario_distribution() -> list[dict[str, object]]:
    """类别分布（总数/真实可运行/多轮；报告用，按场景声明顺序）。"""

    order: list[ExpressionCategory] = []
    counts: dict[ExpressionCategory, dict[str, int]] = {}
    for scenario in SCENARIOS:
        if scenario.category not in counts:
            order.append(scenario.category)
            counts[scenario.category] = {
                "total": 0,
                "real_runnable": 0,
                "multi_turn": 0,
            }
        entry = counts[scenario.category]
        entry["total"] += 1
        if scenario.real_runnable:
            entry["real_runnable"] += 1
        if scenario.multi_turn:
            entry["multi_turn"] += 1
    return [
        {"category": category.value, **counts[category]} for category in order
    ]


__all__ = [
    "ExpressionCategory",
    "ExpressionScenario",
    "FORMAL_PATHS",
    "FORMAL_PATH_IDS",
    "FormalPath",
    "REQUIRED_CATEGORIES",
    "SCENARIOS",
    "ToolSignal",
    "coverage_matrix",
    "real_runnable_scenarios",
    "scenario_by_id",
    "scenario_digest",
    "scenario_distribution",
    "validate_coverage",
]
