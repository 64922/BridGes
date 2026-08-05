"""生涯规划意图的确定性检测器（Issue 29）。

生涯规划在既有日常陪伴/学习模式中按「明确生涯规划意图」触发。检测器是
确定性规则（前缀 + 分级关键词 + 语境词 + 否定式防护），不依赖模型判断
——把"是否为规划意图"交给确定性控制平面，避免模型猜测进入编排与审计。

- 显式前缀（前端「生涯规划助手」入口预填）必中；
- 强触发词（生涯规划/职业规划/就业方向等规划核心词）单独命中即触发；
- 弱触发词（考研/考公/找工作/转行等情境词）需在同一消息中命中规划
  语境词（规划/选择/方向/要不要/想等），避免劫持普通聊天
  （如「考研英语怎么复习」「我朋友找工作失败怎么安慰他」）；
- 否定式防护：关键词前紧邻窗口内出现「否定动词短语」则不触发；
  疑问结构（要不要/想不想/该不该）优先于否定短语放行。
"""

from __future__ import annotations

#: 显式前缀：前端「生涯规划助手」入口预填「生涯规划助手：」，视为明确意图。
_PREFIX_MARKERS: tuple[str, ...] = (
    "生涯规划助手：",
    "生涯规划：",
    "职业规划：",
)

#: 强触发词：规划核心词，单独命中即视为明确生涯规划意图。
_STRONG_KEYWORDS: tuple[str, ...] = (
    "生涯规划",
    "职业规划",
    "职业发展",
    "职业路径",
    "职业方向",
    "生涯方向",
    "就业方向",
    "求职规划",
    "职业选择",
    "晋升路径",
    "职业目标",
    "未来规划",
    "人生规划",
)

#: 弱触发词：学业/职业情境词，需同时命中规划语境词（见 ``_CONTEXT_WORDS``）。
_WEAK_KEYWORDS: tuple[str, ...] = (
    "考研",
    "考公",
    "考编",
    "找工作",
    "找实习",
    "转行",
    "选专业",
)

#: 规划语境词：与弱触发词同句出现时才构成明确的规划决策意图。
_CONTEXT_WORDS: tuple[str, ...] = (
    "规划",
    "安排",
    "选择",
    "方向",
    "目标",
    "出路",
    "发展",
    "前景",
    "纠结",
    "要不要",
    "该不该",
    "还是",
    "平衡",
    "打算",
    "考虑",
    "建议",
    "想",
    "准备",
    "注意",
)

#: 否定式防护：关键词前紧邻窗口内出现「否定动词短语」则不触发。
#: 用短语而非单字，避免「要不要转行」中的「要/不要」误伤疑问句；
#: 「不要/放弃/不是在」等裸词仅在紧邻关键词时生效（疑问结构优先放行）。
_NEGATION_PHRASES: tuple[str, ...] = (
    "不要",
    "不是在",
    "放弃",
    "不要做",
    "不要聊",
    "不要问",
    "不要提",
    "不要想",
    "不要说",
    "不要讲",
    "不要帮",
    "不要给我",
    "不用做",
    "不用聊",
    "不用问",
    "不用帮",
    "不想聊",
    "不想做",
    "不想谈",
    "不想问",
    "不想提",
    "别做",
    "别聊",
    "别问",
    "别说",
    "别想",
    "别帮我",
    "别给我",
    "无需做",
    "无需聊",
    "不必做",
    "不是问",
    "没做过",
    "没想做",
    "没计划",
    "放弃做",
    "不提",
)

#: 疑问结构：关键词前出现即视为疑问而非拒绝（「要不要/想不想/该不该」）。
_QUESTION_MARKERS: tuple[str, ...] = (
    "要不要",
    "想不想",
    "该不该",
    "应不应该",
    "是否要",
)

#: 否定防护的向前扫描窗口长度（字符），覆盖「不要帮我做」等结构。
_NEGATION_WINDOW = 8


def is_career_intent(content: str) -> bool:
    """判断一条用户消息是否携带明确的生涯规划意图。

    >>> is_career_intent("生涯规划助手：我想做数据分析")
    True
    >>> is_career_intent("我的职业规划是什么方向比较好")
    True
    >>> is_career_intent("考研英语怎么复习")
    False
    >>> is_career_intent("今天不想聊职业规划")
    False
    """
    text = (content or "").strip()
    if not text:
        return False
    for prefix in _PREFIX_MARKERS:
        if text.startswith(prefix):
            return True
    for keyword in _STRONG_KEYWORDS:
        if _keyword_hits(text, keyword):
            return True
    for keyword in _WEAK_KEYWORDS:
        if _keyword_hits(text, keyword) and any(word in text for word in _CONTEXT_WORDS):
            return True
    return False


def _keyword_hits(text: str, keyword: str) -> bool:
    """关键词命中且关键词前无拒绝性否定（疑问结构放行）。"""
    index = text.find(keyword)
    while index != -1:
        before = text[max(0, index - _NEGATION_WINDOW) : index]
        if not _negated_before(before):
            return True
        index = text.find(keyword, index + 1)
    return False


def _negated_before(before: str) -> bool:
    """关键词前窗口是否构成拒绝（疑问结构优先于否定短语）。"""
    if any(marker in before for marker in _QUESTION_MARKERS):
        return False
    return any(phrase in before for phrase in _NEGATION_PHRASES)
