"""工单 39：人味表达盲评量表（五维、两两四选一、版本化）。

选项固定为 A / B / 平局 / 都不选：评审者不知道该选择对应哪套策略，
只判断回答本身；数值评分锚点不适用于两两选择，因此这里用两侧相对的
判定说明代替。任何问题或锚点变化都必须升版本并进入运行锁。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 量表版本进入运行锁与报告；v2 起锚点改为两两相对判定。
SCALE_VERSION = "human-expression-scale-v2"


@dataclass(frozen=True)
class ReviewDimension:
    dimension_id: str
    label: str
    question: str
    anchors: str


#: 五个独立维度；自然度只在帮助/分寸非劣之后参与放行判断。
REVIEW_DIMENSIONS: tuple[ReviewDimension, ...] = (
    ReviewDimension(
        "understand",
        "听懂",
        "回答是否理解并回应了用户在本轮的真实请求？",
        "以哪一侧更准确抓住意图与约束为准；两侧相当选平局，都答非所问选都不选",
    ),
    ReviewDimension(
        "help",
        "帮助",
        "回答是否实际完成了任务或推进了问题？",
        "以哪一侧更实际完成任务或给出可执行下一步为准；都没帮助选都不选",
    ),
    ReviewDimension(
        "natural",
        "自然度",
        "表达是否像有分寸的伙伴，而不是套话或机械模板？",
        "以哪一侧更自然、更贴合语境、更少套话或模板感为准",
    ),
    ReviewDimension(
        "boundary",
        "分寸",
        "是否尊重用户本轮明确边界（不要建议/安慰/追问，只给答案，要求详细等）？",
        "以哪一侧更尊重明确边界为准；越界即劣势，完全相当选平局",
    ),
    ReviewDimension(
        "continuity",
        "连续性",
        "多轮对话中是否承接了前文任务、纠正或未完成事项，而不是只看最后一句？",
        "以哪一侧更准确接回前文任务、纠正或未完成事项为准",
    ),
)

REVIEW_CHOICES: tuple[str, ...] = ("label_a", "label_b", "tie", "neither")

__all__ = ["REVIEW_CHOICES", "REVIEW_DIMENSIONS", "SCALE_VERSION", "ReviewDimension"]
