"""学习目标与路线修订的确定性触发词判定。

这里只是「是否值得调用结构化规划」的确定性触发器：命中后才由规划角色
判断是不是系统性目标；未命中绝不调用规划。误命中的代价是一次计入预算
的规划调用，不产生任何写入。普通具体问题（问概念、求解某题）不在此列。
"""

from __future__ import annotations

#: 用户明确表达宽泛学习意图的触发词。
GOAL_CANDIDATE_MARKERS: tuple[str, ...] = (
    "我想学",
    "我想要学",
    "我要学",
    "带我学",
    "带我入门",
    "帮我学",
    "教我学",
    "想学会",
    "想系统",
    "系统学",
    "系统地学",
    "从头学",
    "从零学",
    "从零开始学",
    "从头开始学",
    "入门",
    "学会",
    "怎么学",
    "如何学",
    "学习计划",
    "学习路线",
    "学习目标",
)

#: 用户明确要求调整路线顺序、深度或节点的触发词。
ADJUSTMENT_MARKERS: tuple[str, ...] = (
    "调整",
    "改成",
    "换成",
    "重新安排",
    "换个顺序",
    "换顺序",
    "先学",
    "后学",
    "直接学",
    "跳到",
    "讲深",
    "讲浅",
    "深一点",
    "浅一点",
    "详细点",
    "简单点",
    "基础一点",
    "太难",
    "太深",
    "不要讲这个",
    "补充基础",
)


def goal_candidate(text: str) -> bool:
    """用户消息是否显式表达学习目标，值得交给规划角色判断。"""

    return any(marker in text for marker in GOAL_CANDIDATE_MARKERS)


def adjustment_candidate(text: str) -> bool:
    """用户消息是否显式要求调整既有路线，值得交给规划角色判断。"""

    return any(marker in text for marker in ADJUSTMENT_MARKERS)
