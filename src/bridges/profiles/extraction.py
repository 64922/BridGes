"""确定性记忆意图提取（Issue 26，ADR-0002）。

把聊天文本中的明确"记住 / 不要记住 / 只在本对话使用"意图映射为可见的
类别、范围与证据，并识别许可内的低风险观察与单次情绪信号。全部规则为
确定性正则与词表匹配，不依赖模糊模型猜测：无法映射类别时宁可不写，也
不臆断授权或类别。

提取结果按意图类型路由（见 :class:`ProfileService.process_conversation_message`）：

- 明确记住：低风险/基础类别直接写入证据化记录；敏感类别（情绪趋势、
  重要经历、正在面对的问题）只进候选箱；
- 明确不记：丢弃观察并撤回同值既有记录，阻止未来自动写入同内容；
- 只在本对话使用：只提示，不写入长期画像，并以丢弃观察阻止后续自动写入；
- 低风险观察（目标/兴趣/表达习惯）：仅在对应类别与场景许可开启时才自动
  写入（默认关闭，模型不能代开）；
- 单次情绪：仅作为本次会话情境提示，不产生任何长期画像事实。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import replace as dataclasses_replace
from enum import StrEnum

from bridges.contracts.profiles import (
    AUTO_WRITABLE_DIMENSIONS,
    USER_CONFIRMED_DIMENSIONS,
    ProfileDimension,
)

#: 显式"记住"意图的类别关键词 → 画像类别。同一词在多类别间有歧义时
#: 取最贴近 AC 的三类（目标/偏好/习惯），敏感词（情绪/经历/问题）单独映射。
_CATEGORY_KEYWORDS: dict[str, ProfileDimension] = {
    "目标": ProfileDimension.STAGE_GOAL,
    "梦想": ProfileDimension.STAGE_GOAL,
    "计划": ProfileDimension.STAGE_GOAL,
    "打算": ProfileDimension.STAGE_GOAL,
    "喜欢": ProfileDimension.INTEREST_PREFERENCE,
    "偏好": ProfileDimension.INTEREST_PREFERENCE,
    "兴趣": ProfileDimension.INTEREST_PREFERENCE,
    "习惯": ProfileDimension.EXPRESSION_HABIT,
    "称呼": ProfileDimension.EXPRESSION_HABIT,
    "表达": ProfileDimension.EXPRESSION_HABIT,
    "名字": ProfileDimension.BASIC_INFORMATION,
    "生日": ProfileDimension.BASIC_INFORMATION,
    "年龄": ProfileDimension.BASIC_INFORMATION,
    "信息": ProfileDimension.BASIC_INFORMATION,
    "知识": ProfileDimension.KNOWLEDGE_STATE,
    "学过": ProfileDimension.KNOWLEDGE_STATE,
    "掌握": ProfileDimension.KNOWLEDGE_STATE,
    "情绪": ProfileDimension.EMOTION_TREND,
    "心情": ProfileDimension.EMOTION_TREND,
    "经历": ProfileDimension.IMPORTANT_EXPERIENCE,
    "故事": ProfileDimension.IMPORTANT_EXPERIENCE,
    "往事": ProfileDimension.IMPORTANT_EXPERIENCE,
    "问题": ProfileDimension.CURRENT_PROBLEM,
    "困难": ProfileDimension.CURRENT_PROBLEM,
    "烦恼": ProfileDimension.CURRENT_PROBLEM,
    "困扰": ProfileDimension.CURRENT_PROBLEM,
}

#: 单次情绪词表：出现即视为当前会话情境信号，绝不进入长期画像。
_EMOTION_WORDS = re.compile(
    r"(开心|高兴|难过|伤心|焦虑|紧张|烦躁|疲惫|累|低落|沮丧|生气|愤怒|委屈|"
    r"失望|害怕|担心|不安|兴奋|期待|轻松|迷茫|孤独|崩溃|压力)"
)

#: 低风险自动写入模式（仅在许可开启时生效；显式意图存在时整句跳过）。
#: ``require_hints`` 为 True 的模式要求值命中表达风格词，避免把"我习惯早起"
#: 误记为表达习惯；"叫我X"本身即称呼类表达习惯，不需额外提示词。
_AUTO_WRITE_PATTERNS: list[tuple[ProfileDimension, re.Pattern[str], bool]] = [
    (
        ProfileDimension.STAGE_GOAL,
        re.compile(r"(?:我的|我)?(?:阶段|学习|年度|短期)?目标(?:是|为)?\s*[:：]?\s*([^。！？!?；;，,]+)"),
        False,
    ),
    (
        ProfileDimension.STAGE_GOAL,
        re.compile(r"我(?:计划|打算)\s*([^。！？!?；;，,]{2,})"),
        False,
    ),
    (
        ProfileDimension.INTEREST_PREFERENCE,
        re.compile(r"我(?:更)?(?:喜欢|偏爱|偏好)\s*([^。！？!?；;，,]{2,})"),
        False,
    ),
    (
        ProfileDimension.EXPRESSION_HABIT,
        re.compile(r"(?:请|可以)?(?:叫我|称呼我)\s*([^。！？!?；;，,]{1,})"),
        False,
    ),
    (
        ProfileDimension.EXPRESSION_HABIT,
        re.compile(r"我习惯(?:用|以)?\s*([^。！？!?；;，,]{2,})"),
        True,
    ),
]

#: 表达习惯值需要命中其中一词才算表达风格，避免把"我习惯早起"误记为表达习惯。
_EXPRESSION_HABIT_HINTS = re.compile(
    r"(直接|简洁|详细|书面|口语|称呼|中文|英文|语速|礼貌|正式|轻松|温和|幽默|"
    r"举例|比喻|结构|分段|长|短)"
)

#: 明确记住：\"记住\" + 可选类别词 + 分隔 + 值。类别词缺省时维度为 None，
#: 由服务层给出"请说明类别"的可见提示，绝不猜测类别。
_CATEGORY_KEYWORDS_JOINED = "|".join(
    re.escape(k) for k in sorted(_CATEGORY_KEYWORDS, key=len, reverse=True)
)
_REMEMBER_PATTERN = re.compile(
    r"记住(?:我的|我|一下|这条|这段|这个)?"
    rf"(?P<cat>{_CATEGORY_KEYWORDS_JOINED})?"
    r"(?:是|为)?\s*[:：,，]?\s*(?P<value>[^。！？!?；;]+)"
)

#: 明确不记：整句匹配即命中；可带被忽略的对象文本（服务层按类别词与
#: 值做既有记录撤回匹配）。"忘了/忘记"是描述性陈述（如"我忘了密码"）
#: 而非记忆指令，不纳入，避免日常语句误发"不再记录"通知。
_FORGET_PATTERNS = re.compile(
    r"(?:不要记住|别记住|别记|不用记住|不用记|忘掉|"
    r"删掉(?:这条)?(?:记忆|记录)?|别存|不记录)"
)

#: 只在本对话使用：内容留在会话内，不进入长期画像。
_SESSION_ONLY_PATTERNS = re.compile(
    r"(?:只(?:在)?本(?:次|这)?对话(?:里|中|内)?(?:使用|用)|"
    r"只在(?:这次|本次|这个对话)|不(?:要|用)?长期(?:记住|记录|保存)|"
    r"不用长期记|只这一次)"
)

#: 显式意图短语：命中后该句不再走低风险自动写入（显式意图优先）。
_EXPLICIT_INTENT_PATTERNS = re.compile(
    r"(?:记住|不要记住|别记住|别记|不用记住|不用记|忘掉|忘了|忘记|"
    r"只在本对话|只在这次|不长期记住|不用长期记录|别存|不记录|删掉)"
)


class MemoryIntentKind(StrEnum):
    """提取出的记忆意图类型。"""

    REMEMBER = "remember"
    FORGET = "forget"
    SESSION_ONLY = "session_only"
    AUTO_WRITE = "auto_write"
    TRANSIENT = "transient"


@dataclass(frozen=True)
class MemoryIntent:
    """一条可追溯的记忆意图：类型、类别、值、场景与证据。

    ``scene`` 由调用方传入（对话模式），用于许可场景匹配；``evidence``
    为来源消息文本，写入通知与审计。
    """

    kind: MemoryIntentKind
    dimension: ProfileDimension | None
    value: str | None
    scene: str
    evidence: str


def _clean_value(value: str) -> str:
    """去掉值两侧的空白与冗余量词，避免同义句产生不同去重键。"""
    cleaned = value.strip().strip("，,。.！!？?；;").strip()
    return re.sub(r"^(我的|我|自己的)", "", cleaned).strip()


def _dimension_for_keyword(keyword: str | None) -> ProfileDimension | None:
    if not keyword:
        return None
    return _CATEGORY_KEYWORDS.get(keyword)


def _parse_remember(content: str) -> MemoryIntent | None:
    """解析"记住…"并映射到可见类别；无类别时维度为 None（不猜测）。

    否定式（不要记住/别记住/不用记住）属于 forget 意图，前面带"不/别"
    的"记住"不产生 remember 意图，避免同一句同时命中两种意图。类别词
    出现在值文本中时（如"记住，我最近的目标是过雅思"），按可见关键词
    映射拆分出类别与值。
    """
    match = _REMEMBER_PATTERN.search(content)
    if match is None:
        return None
    if match.start() > 0:
        prefix = content[max(0, match.start() - 3) : match.start()]
        if "不" in prefix or "别" in prefix:
            return None
    value = _clean_value(match.group("value") or "")
    if not value:
        return None
    dimension = _dimension_for_keyword(match.group("cat"))
    if dimension is None:
        for keyword, candidate_dimension in sorted(
            _CATEGORY_KEYWORDS.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if keyword in value:
                remainder = _clean_value(
                    value.split(keyword, 1)[1].lstrip("是：:，, ")
                )
                if not remainder:
                    # 只说了类别没说内容，无可记录的事实。
                    return None
                dimension, value = candidate_dimension, remainder
                break
    return MemoryIntent(
        kind=MemoryIntentKind.REMEMBER,
        dimension=dimension,
        value=value,
        scene="",
        evidence=content,
    )


def _parse_forget(content: str) -> MemoryIntent | None:
    """解析"不要记住…"；附带值文本供服务层做既有记录撤回匹配。"""
    match = _FORGET_PATTERNS.search(content)
    if match is None:
        return None
    rest = content[match.end() :].strip().lstrip("，,。.！!？?；;")
    rest = _clean_value(rest)
    return MemoryIntent(
        kind=MemoryIntentKind.FORGET,
        dimension=None,
        value=rest or None,
        scene="",
        evidence=content,
    )


def _parse_session_only(content: str) -> MemoryIntent | None:
    """解析"只在本对话使用…"。"""
    match = _SESSION_ONLY_PATTERNS.search(content)
    if match is None:
        return None
    rest = content[match.end() :].strip().lstrip("，,。.！!？?；;")
    rest = _clean_value(rest)
    return MemoryIntent(
        kind=MemoryIntentKind.SESSION_ONLY,
        dimension=None,
        value=rest or None,
        scene="",
        evidence=content,
    )


def _parse_auto_write(content: str) -> MemoryIntent | None:
    """解析许可内的低风险观察（目标/兴趣/表达习惯）。"""
    for dimension, pattern, require_hints in _AUTO_WRITE_PATTERNS:
        match = pattern.search(content)
        if match is None:
            continue
        raw = _clean_value(match.group(1))
        if len(raw) < 2 or raw in {"你", "你们", "这个", "这样", "这些"}:
            continue
        if require_hints and not _EXPRESSION_HABIT_HINTS.search(raw):
            continue
        return MemoryIntent(
            kind=MemoryIntentKind.AUTO_WRITE,
            dimension=dimension,
            value=raw,
            scene="",
            evidence=content,
        )
    return None


def _parse_transient(content: str) -> MemoryIntent | None:
    """解析单次情绪：仅会话情境信号，不进入长期画像。"""
    if _EMOTION_WORDS.search(content) is None:
        return None
    return MemoryIntent(
        kind=MemoryIntentKind.TRANSIENT,
        dimension=ProfileDimension.EMOTION_TREND,
        value=None,
        scene="",
        evidence=content,
    )


class MemoryIntentExtractor:
    """确定性记忆意图提取器：一句话至多产出一条意图，显式意图优先。

    顺序保证：明确记住 / 不记 / 仅会话 优先于低风险观察，低风险观察
    优先于单次情绪提示；显式意图命中时低风险观察整体跳过。
    """

    def extract(self, content: str, *, scene: str) -> list[MemoryIntent]:
        """从一条用户消息中提取记忆意图（可能为空列表）。"""
        text = (content or "").strip()
        if not text:
            return []

        explicit: list[MemoryIntent] = []
        remember = _parse_remember(text)
        if remember is not None:
            explicit.append(remember)
        forget = _parse_forget(text)
        if forget is not None:
            explicit.append(forget)
        session_only = _parse_session_only(text)
        if session_only is not None:
            explicit.append(session_only)

        if explicit:
            return [dataclasses_replace(intent, scene=scene) for intent in explicit]

        if _EXPLICIT_INTENT_PATTERNS.search(text) is not None:
            # 显式意图短语存在但未完整命中（如"别把这件事说出去"）：
            # 保守起见不产生低风险观察，避免把拒绝语境当成可记录事实。
            return []

        auto_write = _parse_auto_write(text)
        if auto_write is not None:
            return [dataclasses_replace(auto_write, scene=scene)]

        transient = _parse_transient(text)
        if transient is not None:
            return [dataclasses_replace(transient, scene=scene)]

        return []


def is_sensitive_dimension(dimension: ProfileDimension) -> bool:
    """该类别只能形成候选，必须由用户确认后才能跨会话使用。"""
    return dimension in USER_CONFIRMED_DIMENSIONS


def is_auto_writable_dimension(dimension: ProfileDimension) -> bool:
    """该类别属于低风险自动写入范围（仍需许可与去重门）。"""
    return dimension in AUTO_WRITABLE_DIMENSIONS
