"""Issue 10：自然语言生涯规划路由边界。"""

import pytest

from bridges.chat.routing import RouteCapability, classify_message


@pytest.mark.parametrize(
    "content",
    [
        "帮我规划考研后的职业方向",
        "我该不该转行？现在很纠结",
        "大三计算机学生，想知道毕业后应该怎么规划求职方向",
        "我在科研和企业之间怎么选择长期发展路线？",
    ],
)
def test_typical_career_questions_route_to_planning(content: str) -> None:
    decision = classify_message(content)

    assert decision.capability == RouteCapability.CAREER_PLANNING
    assert decision.career_contract is not None
    assert decision.career_contract.target
    assert decision.career_contract.version


@pytest.mark.parametrize(
    "content",
    [
        "考研英语怎么复习",
        "帮我制定一个机器学习学习计划",
        "请帮我润色这段简历",
        "职业规划是什么？",
        "我想了解职业规划",
        "职业发展前景怎么样？",
    ],
)
def test_learning_humanizer_and_general_career_chat_do_not_route_to_planning(
    content: str,
) -> None:
    decision = classify_message(content)

    assert decision.capability != RouteCapability.CAREER_PLANNING
    assert decision.capability != RouteCapability.CLARIFY


def test_independent_tasks_get_one_clarification_without_selecting_a_side() -> None:
    decision = classify_message("帮我规划职业方向，顺便润色一下简历")

    assert decision.capability == RouteCapability.CLARIFY
    assert decision.clarification
    assert decision.conflict_capabilities == [
        RouteCapability.CAREER_PLANNING,
        RouteCapability.HUMANIZER,
    ]


def test_plan_contract_keeps_explicit_time_constraints_and_evidence_requirements() -> None:
    decision = classify_message(
        "我大二，想规划未来三年的数据分析方向，地点先考虑上海，参考知识库里的材料"
    )

    assert decision.capability == RouteCapability.CAREER_PLANNING
    contract = decision.career_contract
    assert contract is not None
    assert "三年" in (contract.time_horizon or "")
    assert any("上海" in item for item in contract.constraints)
    assert "authorized_knowledge_base" in contract.evidence_requirements
