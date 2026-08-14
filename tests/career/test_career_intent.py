"""生涯规划意图确定性检测测试（Issue 29）。

检测器是确定性规则（显式前缀 + 分级关键词 + 语境词 + 否定式防护），
不依赖模型判断：把「是否为规划意图」交给控制平面，避免模型猜测进入
编排与审计；弱触发情境词（考研/找工作/转行等）不得劫持普通聊天。
"""

from __future__ import annotations

import pytest

from bridges.career.intent import is_career_intent


@pytest.mark.parametrize(
    "content",
    [
        # 显式前缀（前端「生涯规划助手」入口预填）
        "生涯规划助手：我大二在读计算机，想规划考研与实习",
        "生涯规划：请帮我做三年职业规划",
        "职业规划：数据分析方向怎么入门",
        # 强触发词（规划核心词单独命中）
        "我的职业规划是什么方向比较好",
        "想了解生涯规划的一般方法",
        "就业方向怎么选",
        "想规划未来三年的职业发展",
        "帮我看看职业路径怎么安排",
        "求职规划怎么做",
        "我的职业目标还不清晰",
        "想理清晋升路径",
        "人生规划应该从哪开始",
        # 弱触发词 + 规划语境词（明确决策意图）
        "我在纠结要不要转行做产品",
        "考研和就业怎么平衡",
        "考公还是找工作",
        "要不要考研",
        "我想考研，怎么准备",
        "找实习要注意什么",
        "选专业时应该考虑什么",
        "打算转行做数据分析",
        # 学习任务规划（Issue 03 feature 7）：规划动作 + 学习语境词
        "给我规划一下我的学习任务",
        "帮我排一下研究生三年的学习优先级",
        "帮我安排一下复习任务",
        "给我规划一下考研的复习安排",
        "帮我规划一下这学期的课程安排",
        "帮我排一个考研的复习计划",
        "帮我规划一下读研期间的学业安排",
    ],
)
def test_career_intent_hits(content: str) -> None:
    assert is_career_intent(content)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "   ",
        "帮我解释一下光合作用",
        "今天有什么学习建议吗",
        "这篇论文的结论是什么",
        # 弱触发情境词无规划语境：不劫持普通聊天
        "考研英语怎么复习",
        "考研政治有哪些考点",
        "我朋友找工作失败，怎么安慰他",
        "考公的报名时间是什么时候",
        "转行的定义是什么",
        # 否定式防护
        "不要帮我做职业规划，我只想问物理题",
        "今天不想聊职业规划",
        "不用做生涯规划，谢谢",
        "别给我人生规划，聊点别的",
        "我不是在问职业规划",
        "我没做过职业规划，先不说这个",
        "先放弃职业规划的念头吧",
        # 学习任务规划的排除说法与否定（Issue 03 feature 7）
        "今天帮我安排一下学习计划之外的事",
        "帮我安排一下学习之外的事",
        "不用帮我规划学习任务，我自己来",
        "不要安排复习了，先吃饭",
        "别帮我安排复习了，先吃饭",
    ],
)
def test_career_intent_misses(content: str) -> None:
    assert not is_career_intent(content)


def test_career_intent_negation_only_near_keyword() -> None:
    """否定防护只看关键词前紧邻窗口，远处否定不误杀真实意图。"""
    # 「不要」紧邻关键词 → 不触发
    assert not is_career_intent("不要职业规划")
    # 否定词不在关键词前 → 正常触发
    assert is_career_intent("职业规划不要只考虑热门，也考虑兴趣")


def test_career_intent_prefix_beats_negation() -> None:
    """显式前缀必中（前端入口语义），即使正文带否定词。"""
    assert is_career_intent("生涯规划助手：不要建议我转行")


def test_career_intent_question_structure_not_negation() -> None:
    """疑问结构（要不要/该不该）不是拒绝，弱触发词在语境下命中。"""
    assert is_career_intent("要不要考研")
    assert is_career_intent("该不该转行做产品")
    assert is_career_intent("想不想找工作还是继续读研")
