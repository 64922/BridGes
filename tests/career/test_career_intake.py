"""生涯规划最小输入判断（Issue 09 intake）单元测试。

intake 是确定性控制平面：在启动完整规划生成（检索/模型调用）之前，
基于用户意图文本判断「目标方向」与「当前阶段」是否足够。任一缺失时
只返回一个最影响方案的关键澄清问题，绝不启动长模型调用。
"""

from __future__ import annotations

from bridges.career.intake import (
    assess_intake,
)


def test_sufficient_direction_and_stage_needs_no_clarification() -> None:
    """方向 + 阶段都明确：信息足够，不要求澄清。"""
    assessment = assess_intake(
        "我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向"
    )
    assert assessment.enough is True
    assert assessment.missing_dimension is None
    assert assessment.question is None


def test_missing_direction_asks_single_direction_question() -> None:
    """有阶段无方向：只问一个方向问题，不启动完整规划。"""
    assessment = assess_intake("我大二了，帮我想想以后的路")
    assert assessment.enough is False
    assert assessment.missing_dimension == "direction"
    assert assessment.question is not None
    assert "方向" in assessment.question


def test_missing_stage_asks_single_stage_question() -> None:
    """有方向无阶段：只问一个阶段问题。"""
    assessment = assess_intake("我想做数据分析，帮我规划一下")
    assert assessment.enough is False
    assert assessment.missing_dimension == "stage"
    assert assessment.question is not None
    assert "阶段" in assessment.question


def test_fully_vague_request_asks_direction_first() -> None:
    """完全模糊请求：方向优先于阶段（方向对方案影响最大）。"""
    assessment = assess_intake("生涯规划助手：帮我规划一下")
    assert assessment.enough is False
    assert assessment.missing_dimension == "direction"


def test_working_professional_with_direction_is_sufficient() -> None:
    """在职身份 + 具体方向：信息足够。"""
    assessment = assess_intake("我工作三年了，想转行做产品经理")
    assert assessment.enough is True


def test_graduate_stage_with_direction_is_sufficient() -> None:
    """研究生阶段 + 方向：信息足够。"""
    assessment = assess_intake("研二在读，方向是计算机视觉，想规划博士还是就业")
    assert assessment.enough is True


def test_empty_intent_is_insufficient() -> None:
    """空意图按缺方向处理（澄清问题可见）。"""
    assessment = assess_intake("   ")
    assert assessment.enough is False
    assert assessment.missing_dimension == "direction"


def test_direction_only_without_stage_asks_stage() -> None:
    """时间范围缺失不阻塞：方向 + 无阶段时只问阶段。"""
    assessment = assess_intake("我对前端开发感兴趣，帮我做职业规划")
    assert assessment.enough is False
    assert assessment.missing_dimension == "stage"
