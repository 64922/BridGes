"""画像工单 12：确定性反馈路由。

结合起来原回答与实际采用记录，把用户紧跟在回答后的一轮消息识别为
六类反馈之一（事实纠正、状态变化、长期偏好、本轮要求、表达质量、指向
不明），并尽量从原回答的采用快照里定位被纠正的条目。路由只做确定性
判断，不调用模型、不改写任何记忆；写入由画像提交边界完成。

边界：
- 只有存在前置助手回答、且消息形态明确属于反馈时才路由；普通新陈述
  （「我喜欢跑步」「现在升大四了」作为状态更新但仍需语义判断的情形除外）
  返回 ``None``，交回既有抽取/纠正路径。
- 定位不到目标且没有替代值时，事实/状态反馈按「指向不明」处理——只澄清，
  不猜测批量修改、不扩大任何授权。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from bridges.contracts.feedback_correction import (
    FeedbackAdjustment,
    FeedbackCorrectionKind,
    FeedbackCorrectionRoute,
)


@dataclass(frozen=True, slots=True)
class AdoptedFeedbackTarget:
    """原回答实际采用条目在路由中的最小快照（不重新查询仓库）。"""

    profile_item_id: str
    version: int
    text: str
    relation: str = ""


#: 长期偏好的未来时间标记：只有明确指向「以后/每次」才算长期化。
_FUTURE_MARK_RE = re.compile(r"(?:以后|今后|从今以后|从今往后|往后|每次)")
#: 长期偏好内容标记（表达/交流方式），避免把「以后我要考研」当成偏好。
_PREFERENCE_MARK_RE = re.compile(
    r"(?:先给结论|先说结论|结论先行|先给答案|先给重点|先讲重点"
    r"|简短|简洁|精炼|精简|不要长|别太长|详细|展开|具体"
    r"|表格|列点|分点|清单|markdown|Markdown"
    r"|公式|推导|引用|来源|举例|例子|通俗|简单易懂"
    r"|中文|英文|称呼|叫我|语气|严肃|轻松|幽默"
    r"|别(?:再)?(?:问|追问)|不要(?:再)?(?:问|追问|安慰|建议)"
    r"|先(?:给|说|讲|列|写))"
)
#: 本轮时间标记：本次要求只影响当前修正回答。
_NOW_MARK_RE = re.compile(r"(?:今天|这次|这轮|本轮|这回|这次|此时|此刻|赶时间)")
#: 篇幅/速度调整请求。
_LENGTH_MARK_RE = re.compile(
    r"(?:简短|简洁|短一点|短些|说短|长一点|长些|快点|快些|抓紧"
    r"|直接点|直接说|别太长|不要长篇|详细一点|详细些|再详细|只说重点|只讲重点|说重点)"
)
_SHORTER_MARK_RE = re.compile(
    r"(?:简短|简洁|短一点|短些|说短|快点|快些|抓紧|别太长|不要长篇|只说重点|只讲重点|说重点)"
)
_DETAILED_MARK_RE = re.compile(r"(?:长一点|长些|详细一点|详细些|再详细|展开讲|展开说)")
#: 表达质量投诉：指向讲解方式，不指向事实，也不推断能力。
_EXPRESSION_MARK_RE = re.compile(
    r"(?:太难|看不懂|听不懂|没看懂|没听懂|不理解|太复杂|太深|太专业|太绕"
    r"|太晦涩|难懂|不好懂|太抽象|讲得太快|说得太快|云里雾里"
    r"|(?:讲|说|解释|回答|写)(?:得|的)?(?:简单|通俗|直白|清楚|浅显)(?:一?点|一些|些))"
)
#: 事实/状态纠正标记。
_CORRECTION_MARK_RE = re.compile(
    r"(?:记错|说错|写错|搞错|弄错|记混|纠正|更正|不对|错了|不是)"
)
#: 状态变化标记（与原子画像的修订语义保持一致：旧值是真实历史）。
_STATE_CHANGE_MARK_RE = re.compile(
    r"(?:现在已经|已经|不再是|变成|改为|转为|转到|转成|转了|"
    r"升级|升到|升入|升[一二三四五六大高初研博本]|跳级|搬出|搬到|搬走|"
    r"不住了|换了|(?:现在|目前|后来|如今)[^，。；！？]{0,12}了)"
)
#: 指向不明投诉：无法定位时只澄清，不猜测修改。
_UNCLEAR_MARK_RE = re.compile(
    r"(?:不是(?:这个|这|那个)?意思|你没(?:理解|明白|懂)(?:我的意思)?"
    r"|没(?:理解|明白)我的意思|答非所问|不对(?:[。！!？?]|$)|错了(?:[。！!？?]|$))"
)
#: 指向不明的否定指代（「不是这个意思」）：不当作可纠正的具体事实。
_VAGUE_NEGATED_RE = re.compile(r"不(?:再)?是(?:这个|这|那个)?意思")
#: 否定替换：``不是 A，而是 B`` / ``不是 A，我是 B``。
_NEGATED_REPLACEMENT_RE = re.compile(
    r"不(?:再)?是(?P<negated>[^，,。；;！!？?\s]{1,30})"
    r"(?:[，,、]?\s*(?:而)?(?:我)?(?:现在)?(?:是|变成|改为|换成)"
    r"(?P<replacement>[^。！!？?\n]{1,40}))?"
)
_REPLACEMENT_ONLY_RE = re.compile(
    r"(?:改(?:为|成)|换成|纠正为|更正为|调整成)\s*(?P<replacement>[^，,。；;！!？?\s]{1,30})"
)
_GRADE_TOKEN_RE = re.compile(r"大[一二三四五六]|研[一二三]|高[一二三四]|初[一二三四]|博[一二三四]")
#: 分句符（用于取用户替换原话所在的独立分句）。
_CLAUSE_SPLIT_RE = re.compile(r"[。！!？?；;，,\n]+")


def route_feedback(
    content: str,
    *,
    previous_answer: str | None,
    adopted_items: Sequence[AdoptedFeedbackTarget],
) -> FeedbackCorrectionRoute | None:
    """路由一条紧跟回答之后的用户消息；不构成反馈时返回 ``None``。

    ``previous_answer`` 是被反馈的回答正文（没有前置回答时不路由）；
    ``adopted_items`` 是该回答实际采用的条目快照，用于定位被纠正的事实。
    """

    normalized = " ".join(content.split())
    if not normalized or previous_answer is None:
        return None

    preference = _long_term_preference(normalized)
    if preference is not None:
        return preference

    turn_request = _turn_request(normalized)
    if turn_request is not None:
        return turn_request

    expression = _expression_quality(normalized)
    if expression is not None:
        return expression

    correction = _correction(normalized, adopted_items)
    if correction is not None:
        return correction

    if _UNCLEAR_MARK_RE.search(normalized):
        return FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.UNCLEAR,
            requires_clarification=True,
        )
    return None


def _long_term_preference(text: str) -> FeedbackCorrectionRoute | None:
    """「以后先给结论」式的明确长期偏好：未来标记 + 表达方式标记。"""

    if not _FUTURE_MARK_RE.search(text) or not _PREFERENCE_MARK_RE.search(text):
        return None
    if _CORRECTION_MARK_RE.search(text):
        return None
    return FeedbackCorrectionRoute(
        kind=FeedbackCorrectionKind.LONG_TERM_PREFERENCE,
        adjustment=_preference_adjustment(text),
        preference_text=text,
    )


def _preference_adjustment(text: str) -> FeedbackAdjustment:
    if re.search(r"(?:先给结论|先说结论|结论先行|先给答案)", text):
        return FeedbackAdjustment.CONCLUSION_FIRST
    if re.search(r"(?:表格|列点|分点|清单|markdown|Markdown)", text):
        return FeedbackAdjustment.NO_TABLES
    if _SHORTER_MARK_RE.search(text):
        return FeedbackAdjustment.SHORTER
    if _DETAILED_MARK_RE.search(text):
        return FeedbackAdjustment.MORE_DETAILED
    return FeedbackAdjustment.FOLLOW_USER_WORDS


def _turn_request(text: str) -> FeedbackCorrectionRoute | None:
    """「今天简短一点」式的本次要求：只改当前修正回答，不写长期偏好。"""

    if _EXPRESSION_MARK_RE.search(text):
        return None
    if _NOW_MARK_RE.search(text) and _LENGTH_MARK_RE.search(text):
        return FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.TURN_REQUEST,
            adjustment=(
                FeedbackAdjustment.MORE_DETAILED
                if _DETAILED_MARK_RE.search(text)
                and not _SHORTER_MARK_RE.search(text)
                else FeedbackAdjustment.SHORTER
                if _SHORTER_MARK_RE.search(text)
                else FeedbackAdjustment.FOLLOW_USER_WORDS
            ),
        )
    if (
        not _FUTURE_MARK_RE.search(text)
        and re.search(r"(?:再)?(?:简短|简洁|短|长|详细|具体)(?:一?点|一些)", text)
        and not _CORRECTION_MARK_RE.search(text)
    ):
        return FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.TURN_REQUEST,
            adjustment=(
                FeedbackAdjustment.MORE_DETAILED
                if re.search(r"(?:长|详细|具体)(?:一?点|一些)", text)
                else FeedbackAdjustment.SHORTER
            ),
        )
    return None


def _expression_quality(text: str) -> FeedbackCorrectionRoute | None:
    """「解释太难」式的表达投诉：调整表达，不产生能力或掌握度判断。"""

    if not _EXPRESSION_MARK_RE.search(text):
        return None
    if _CORRECTION_MARK_RE.search(text) and not re.search(
        r"(?:没错|说得对|身份没错)", text
    ):
        # 同句既有事实纠正又抱怨表达时，事实优先走纠正路径；这里只在
        # 纯表达投诉（如「身份没错，但解释太难」）时路由。
        return None
    return FeedbackCorrectionRoute(
        kind=FeedbackCorrectionKind.EXPRESSION_QUALITY,
        adjustment=FeedbackAdjustment.SIMPLER,
    )


def _correction(
    text: str, adopted_items: Sequence[AdoptedFeedbackTarget]
) -> FeedbackCorrectionRoute | None:
    """事实纠正/状态变化：结合采用记录定位目标，定位失败转「指向不明」。"""

    state_change = _STATE_CHANGE_MARK_RE.search(text) is not None
    correction_mark = _CORRECTION_MARK_RE.search(text) is not None
    if not state_change and not correction_mark:
        return None

    if _VAGUE_NEGATED_RE.search(text) and _REPLACEMENT_ONLY_RE.search(text) is None:
        # 「不是这个意思」：指代不明，只澄清，不猜测批量修改。
        return FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.UNCLEAR,
            requires_clarification=True,
        )

    negated, replacement = _extract_replacement(text)
    if negated is None and replacement is None and not state_change:
        # 只有「不对/记错了」这类无对象的说法：不猜测修改。
        return FeedbackCorrectionRoute(
            kind=FeedbackCorrectionKind.UNCLEAR,
            requires_clarification=True,
        )

    kind = (
        FeedbackCorrectionKind.FACT_CORRECTION
        if correction_mark and negated is not None
        else FeedbackCorrectionKind.STATE_CHANGE
        if state_change
        else FeedbackCorrectionKind.FACT_CORRECTION
    )
    target = _locate_target(
        negated=negated,
        replacement=replacement,
        text=text,
        adopted_items=adopted_items,
    )
    new_fact_text = _new_fact_text(text, negated=negated, replacement=replacement)
    if new_fact_text is None and state_change:
        # 状态变化没有「不是 A 是 B」结构：用户原句就是新状态的明确表述。
        new_fact_text = text
    if target is None and new_fact_text is None:
        if _UNCLEAR_MARK_RE.search(text):
            return FeedbackCorrectionRoute(
                kind=FeedbackCorrectionKind.UNCLEAR,
                requires_clarification=True,
            )
        # 明确认错但既无定位也无新值：保留为纠正类但只如实报告未解决。
        return FeedbackCorrectionRoute(kind=kind)
    return FeedbackCorrectionRoute(
        kind=kind,
        target_item_id=target.profile_item_id if target is not None else None,
        target_item_version=target.version if target is not None else None,
        target_item_text=target.text if target is not None else None,
        new_fact_text=new_fact_text,
        # 只有替代值或新状态算「用户给出的新值」；单纯否定旧值时宁可不写，
        # 定位不到旧条目的情形尤其不能把否定句当作新事实记住。
        has_replacement=replacement is not None or state_change,
    )


def _extract_replacement(text: str) -> tuple[str | None, str | None]:
    """抽取「不是 A（，是 B）」或「改成 B」的否定值与替代值。"""

    match = _NEGATED_REPLACEMENT_RE.search(text)
    if match is not None:
        negated = (match.group("negated") or "").strip("，,。；;：: 、")
        replacement = (match.group("replacement") or "").strip("，,。；;：: 、")
        return (
            negated or None,
            replacement or None,
        )
    match = _REPLACEMENT_ONLY_RE.search(text)
    if match is not None:
        replacement = (match.group("replacement") or "").strip("，,。；;：: 、")
        return None, replacement or None
    return None, None


def _new_fact_text(
    text: str, *, negated: str | None, replacement: str | None
) -> str | None:
    """用户本轮明确给出的新事实正文：取用户原句的对应分句，不伪造措辞。"""

    if replacement is not None:
        clause = _clause_with(text, replacement)
        if clause:
            return _clean_value_clause(clause)
        return f"我是{replacement}" if not replacement.startswith("我") else replacement
    if negated is not None:
        clause = _clause_with(text, negated)
        if clause:
            return " ".join(clause.split())
    return None


def _clean_value_clause(clause: str) -> str:
    """「是大三」这类短值分句去掉孤立系词，保留「我是…」完整说法。"""

    cleaned = " ".join(clause.split())
    if cleaned.startswith("是") and not cleaned.startswith("是我") and len(cleaned) <= 12:
        return cleaned[1:].strip()
    return cleaned


def _clause_with(text: str, value: str) -> str | None:
    """返回包含指定值的独立分句（去空白）；找不到返回 ``None``。"""

    for clause in _CLAUSE_SPLIT_RE.split(text):
        cleaned = clause.strip()
        if cleaned and value in cleaned:
            return " ".join(cleaned.split())
    return None


def _locate_target(
    *,
    negated: str | None,
    replacement: str | None,
    text: str,
    adopted_items: Sequence[AdoptedFeedbackTarget],
) -> AdoptedFeedbackTarget | None:
    """从原回答实际采用条目里定位被纠正项；多候选取匹配最长者。"""

    # 状态匹配只在没有否定对象时兜底（「现在升大四了」）：单纯否定旧值的
    # 事实纠正不得借年级 token 改到无关条目上（「我不是大四」不是「我在读
    # 大四」的反向状态变化）。
    allow_state_match = negated is None and replacement is None
    best: tuple[int, AdoptedFeedbackTarget] | None = None
    for item in adopted_items:
        matched = _match_length(item.text, negated)
        if matched == 0 and allow_state_match:
            matched = _state_match_length(item.text, text)
        if matched == 0:
            continue
        if best is None or matched > best[0]:
            best = (matched, item)
    return best[1] if best is not None else None


def _match_length(item_text: str, value: str | None) -> int:
    """条目正文包含被否定值的匹配长度（二分词表按整串包含判定）。"""

    if not value or len(value) < 2:
        return 0
    if value.casefold() in item_text.casefold():
        return len(value)
    return 0


def _state_match_length(item_text: str, text: str) -> int:
    """状态变化的定位：条目里出现与旧值一致的年级/状态 token。"""

    new_grades = set(_GRADE_TOKEN_RE.findall(text))
    if not new_grades:
        return 0
    old_tokens = set(_GRADE_TOKEN_RE.findall(item_text))
    stale = old_tokens - new_grades
    if not stale:
        return 0
    return max(len(token) for token in stale)


__all__ = [
    "AdoptedFeedbackTarget",
    "route_feedback",
]
