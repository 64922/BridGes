"""Issue 07：目标触发词、规划合同与路线校验的确定性单元测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bridges.study.goal_intent import adjustment_candidate, goal_candidate
from bridges.study.goal_planning import (
    _PLANNER_SYSTEM_PROMPT,
    MAX_ROUTE_NODES,
    StudyGoalPlan,
)


@pytest.mark.parametrize(
    "text",
    [
        "我想学线性代数，主要为了理解机器学习里的矩阵",
        "带我学线性代数",
        "我想系统学概率论",
        "帮我学一下微积分",
        "从零开始学数据结构",
    ],
)
def test_goal_candidate_detects_explicit_learning_intent(text: str) -> None:
    assert goal_candidate(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "互斥是不是独立？",
        "函数里的自变量是什么？",
        "编程里的递归怎么理解？",
        "能给一个反例吗？",
        "再给一个简单例子。",
    ],
)
def test_goal_candidate_ignores_local_questions(text: str) -> None:
    """普通具体问题不触发规划（不强制建立长期目标或完整课程）。"""

    assert goal_candidate(text) is False


@pytest.mark.parametrize(
    "text",
    ["调整顺序：先学特征值", "直接把矩阵这块讲浅一点", "跳到奇异值分解"],
)
def test_adjustment_candidate_detects_route_requests(text: str) -> None:
    assert adjustment_candidate(text) is True


def test_adjustment_candidate_ignores_ordinary_followups() -> None:
    assert adjustment_candidate("什么是特征值？") is False
    assert adjustment_candidate("能给一个反例吗？") is False


def test_planner_prompt_keeps_user_authority_and_lightweight_route() -> None:
    """C01：提示词为本项目自研合同，明确用户目的权威与路线可调整。"""

    assert "用户的学习目的与明确限制是权威事实" in _PLANNER_SYSTEM_PROMPT
    assert "不要统一先做入门考试" in _PLANNER_SYSTEM_PROMPT
    assert "用户的学习目的不可更改" in _PLANNER_SYSTEM_PROMPT
    assert f"不超过 {MAX_ROUTE_NODES} 个知识点" in _PLANNER_SYSTEM_PROMPT
    assert "猜测不得成为用户约束" in _PLANNER_SYSTEM_PROMPT


def _plan(route: object) -> dict[str, object]:
    return {
        "kind": "systematic_goal",
        "observable_result": "",
        "scope": "",
        "constraints": "",
        "clarification_question": "",
        "route": route,
        "note": "",
    }


def test_plan_parses_valid_route() -> None:
    plan = StudyGoalPlan.model_validate(
        _plan(
            {
                "nodes": [
                    {"node_id": "n1", "title": "向量", "prerequisites": []},
                    {
                        "node_id": "n2",
                        "title": "矩阵",
                        "prerequisites": ["n1"],
                        "depth": "基础",
                    },
                ],
                "current_node_id": "n2",
                "revision_reason": "首次形成路线",
            }
        )
    )
    assert plan.kind == "systematic_goal"
    assert plan.route is not None
    assert plan.route.current_node_id == "n2"
    assert plan.route.nodes[1].prerequisites == ["n1"]


def test_plan_rejects_unknown_prerequisites() -> None:
    with pytest.raises(ValidationError):
        StudyGoalPlan.model_validate(
            _plan(
                {
                    "nodes": [
                        {"node_id": "n1", "title": "向量", "prerequisites": ["n9"]},
                    ],
                    "current_node_id": "n1",
                }
            )
        )


def test_plan_rejects_current_node_outside_route() -> None:
    with pytest.raises(ValidationError):
        StudyGoalPlan.model_validate(
            _plan(
                {
                    "nodes": [{"node_id": "n1", "title": "向量"}],
                    "current_node_id": "n2",
                }
            )
        )


def test_plan_rejects_duplicate_node_ids_and_overlong_route() -> None:
    with pytest.raises(ValidationError):
        StudyGoalPlan.model_validate(
            _plan(
                {
                    "nodes": [
                        {"node_id": "n1", "title": "向量"},
                        {"node_id": "n1", "title": "矩阵"},
                    ],
                    "current_node_id": "n1",
                }
            )
        )
    with pytest.raises(ValidationError):
        StudyGoalPlan.model_validate(
            _plan(
                {
                    "nodes": [
                        {"node_id": f"n{index}", "title": f"知识点{index}"}
                        for index in range(MAX_ROUTE_NODES + 1)
                    ],
                    "current_node_id": "n0",
                }
            )
        )


def test_plan_accepts_clarification_without_route() -> None:
    plan = StudyGoalPlan.model_validate(
        {
            "kind": "systematic_goal",
            "clarification_question": "你之前接触过哪些内容？",
            "route": None,
        }
    )
    assert plan.route is None
    assert plan.clarification_question == "你之前接触过哪些内容？"
