"""``resources.parse``：保留原词、识别学习目的，只在必要时追问学习层次。

解析是确定性的（词表 + 规则，无模型、无 I/O）：

1. **原始专业名词**逐字保留——中文走词表、英文串原样取用；译名只作为扩展词
   进入查询，绝不替换原词（``resources.parse`` 的硬要求）。
2. **学习目的**从常见说法识别（备考／项目实战／科研…），用于正文措辞与阶段搭配。
3. **学习层次**按「本句原话 → 可靠前文 → 目的推定」依次取；仍不确定且它确实
   影响推荐时只追问这一项并持久化等待状态。用户回答「随便／都行」时按通用
   顺序继续，不再追问（既不臆造层次，也不把用户困在同一个问题上）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from bridges.contracts.modules import ModuleWaitState
from bridges.resources.contracts import (
    LEVEL_LABELS,
    ResourceMedia,
    ResourcesClarification,
    ResourcesGoalKind,
    ResourcesLevel,
    ResourcesTermAnalysis,
)
from bridges.resources.lexicon import (
    GOAL_HINTS,
    GOAL_KIND_HINTS,
    INTENT_STOPWORDS,
    LATIN_INTENT_WORDS,
    LEVEL_CANDIDATES,
    LEVEL_HINTS,
    MEDIA_BOOKS_HINTS,
    MEDIA_VIDEOS_HINTS,
    PRACTICE_PROJECT_HINTS,
    TERM_ENGLISH,
    TOPIC_STOPWORDS,
)

if TYPE_CHECKING:
    from bridges.chat.task_materials import ModuleTaskContext


#: 澄清后恢复时读取的原词键（等待状态 ``context`` 的稳定字段名）。
PENDING_ORIGINAL_PHRASE = "original_phrase"
PENDING_GOAL = "goal"
PENDING_MISSING = "missing"
PENDING_GOAL_KIND = "goal_kind"
PENDING_MEDIA = "media"
PENDING_REQUESTED_BOOKS = "requested_books"
PENDING_REQUESTED_VIDEOS = "requested_videos"
PENDING_LANGUAGE = "language"
PENDING_TIME_BUDGET = "time_budget"
PENDING_BASIS = "basis_evidence"
PENDING_PRACTICE = "needs_practice_project"

#: 参与层次判定的最近用户消息条数上限（够用即止，不把整段历史塞进解析）。
CONTEXT_LOOKBACK_MESSAGES = 6

#: 主题词的最短长度（单字主题无法检索，宁可不猜）。
MIN_TERM_LENGTH = 2

#: 拉丁词与中文词同时出现时，整句作为主题词的长度上限。
MAX_COMBINED_TERM_LENGTH = 30

#: 用户把层次交给我们的说法：命中即不再追问（按通用顺序给资料）。
LEVEL_DEFERRED_SIGNALS: tuple[str, ...] = (
    "随便", "都行", "都可以", "你看着办", "你决定", "我不确定", "不知道",
    "没想好", "都看看",
)

_QUOTED = re.compile(r"[「“\"']([^」”\"']{1,80})[」”\"']")
_LATIN_SEQUENCE = re.compile(r"[A-Za-z][A-Za-z0-9+.#\-]*(?:\s+[A-Za-z][A-Za-z0-9+.#\-]*)*")
_LATIN_HAS_LETTER = re.compile(r"[A-Za-z]")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,40}")
_CLAUSE_SPLIT = re.compile(r"[，,。；;！!？?、\n]+")
_WHITESPACE = re.compile(r"\s+")

#: 词表键按长度倒序（长键优先，避免「机器学习」被更短的键切走）。
_TERM_KEYS: tuple[str, ...] = tuple(sorted(TERM_ENGLISH, key=len, reverse=True))
#: 中文意图词按长度倒序（先剥长词，避免「推荐一下」被「推荐」切碎）。
_INTENT_WORDS: tuple[str, ...] = tuple(sorted(INTENT_STOPWORDS, key=len, reverse=True))
#: 单次扫描的意图词替换：从左到右选第一个命中的写法，因此重叠时取该位置
#: 能匹配的最长写法（「快速了解一下」先剥「快速了解」再剥「一下」，不会把
#: 「快速了解」被更长的「了解一下」从中间切断）。
_INTENT_PATTERN = re.compile("|".join(re.escape(word) for word in _INTENT_WORDS))
#: 拉丁意图词按词边界剥离（避免把 Transformer 的 a、Python 的 on 切掉）。
_LATIN_INTENT_PATTERN = re.compile(
    r"\b(?:" + "|".join(sorted(LATIN_INTENT_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

#: 用户明确要求的数量（「两本书」「3 个视频」；媒介随各自数量区分）。
_COUNT_PATTERN = re.compile(
    r"(?P<num>[0-9]+|[零一二两三四五六七八九十]+)\s*(?:本|部|个|条|门|套)?\s*"
    r"(?P<kind>图书|教材|视频|书)"
)
#: 数量与名词被修饰语隔开时（「三本机器学习的书」）的宽松形态：必须带量词，
#: 避免把「进一步了解…」这类表达误读成数量。
_COUNT_LOOSE_PATTERN = re.compile(
    r"(?P<num>[0-9]+|[零一二两三四五六七八九十]+)\s*(?:本|部|个|条|门|套|张)\s*"
    r"[^，,。；;！!？?、\s]{0,12}?(?P<kind>图书|教材|视频|书)"
)
_CN_DIGITS: dict[str, int] = {
    "零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

#: 用户明确的时间约束（「两周」「一个月」「每天半小时」）。
_TIME_PATTERN = re.compile(
    r"(?:(?:每天|每日|每周末|每周)\s*(?:[0-9]+|[一二两三四五六七八九十半]+)\s*(?:小时|分钟)"
    r"|(?:[0-9]+|[一二两三四五六七八九十半]+)\s*(?:天|周|个?月|年|小时|分钟)"
    r"|每天|每日|每周末|每周|周末|假期|寒假|暑假)"
)

#: 已表达的基础证据（用户说过的已有基础；只作标注，不当作能力结论）。
_BASIS_PATTERN = re.compile(
    r"(?:学过|学过一点|有一点基础|有一定基础|有点基础|基础不牢|零基础|没基础|没学过|"
    r"刚接触|做过|用过|了解一些)"
)


@dataclass(frozen=True)
class _RequestConditions:
    """解析出的选择条件（目的/媒介/数量/语言/时间/基础/实践项目）。"""

    goal_kind: ResourcesGoalKind | None = None
    media: ResourceMedia | None = None
    requested_books: int | None = None
    requested_videos: int | None = None
    language: str | None = None
    time_budget: str | None = None
    basis_evidence: str | None = None
    needs_practice_project: bool = False


def read_conditions(text: str) -> _RequestConditions:
    """从本轮原话里读出选择条件（确定性词表/规则；缺失保持 None）。"""
    return _RequestConditions(
        goal_kind=detect_goal_kind(text),
        media=detect_media(text),
        requested_books=_detect_count(text, book=True),
        requested_videos=_detect_count(text, book=False),
        language=detect_language(text),
        time_budget=_detect_time_budget(text),
        basis_evidence=detect_basis_evidence(text),
        needs_practice_project=any(hint in text for hint in PRACTICE_PROJECT_HINTS),
    )


def detect_goal_kind(text: str) -> ResourcesGoalKind | None:
    """三类学习目的：取原文中最靠后的命中（与层次判定同一策略）。"""
    best: tuple[int, ResourcesGoalKind] | None = None
    for goal_kind, hints in GOAL_KIND_HINTS.items():
        for hint in hints:
            position = text.rfind(hint)
            if position < 0:
                continue
            if best is None or position > best[0]:
                best = (position, goal_kind)
    if best is not None:
        return best[1]
    # 旧目的说法兜底到三类，保证计划与组织总有一条明确路线。
    goal = detect_goal(text)
    if goal == "备考":
        return ResourcesGoalKind.EXAM_PREP
    if goal == "入门了解":
        return ResourcesGoalKind.QUICK_CONCEPT
    if goal in {"科研", "工作面试", "项目实战"}:
        return ResourcesGoalKind.SYSTEMATIC
    return None


def detect_media(text: str) -> ResourceMedia | None:
    """明确只要书/只要视频时返回对应媒介；未表达为 None（两路都试）。"""
    books = any(hint in text for hint in MEDIA_BOOKS_HINTS)
    videos = any(hint in text for hint in MEDIA_VIDEOS_HINTS)
    if books and not videos:
        return ResourceMedia.BOOKS
    if videos and not books:
        return ResourceMedia.VIDEOS
    return None


def detect_language(text: str) -> str | None:
    if "中英" in text:
        return "中英文"
    if "英文" in text or "英语" in text:
        return "英文"
    if "中文" in text or "汉语" in text:
        return "中文"
    return None


def detect_basis_evidence(text: str) -> str | None:
    for clause in _CLAUSE_SPLIT.split(text):
        matched = _BASIS_PATTERN.search(clause)
        if matched:
            if matched.group(0) in {
                "学过", "学过一点", "没学过", "刚接触", "做过", "用过", "了解一些",
            }:
                return clause.strip()
            return matched.group(0)
    return None


def _detect_count(text: str, *, book: bool) -> int | None:
    for pattern in (_COUNT_PATTERN, _COUNT_LOOSE_PATTERN):
        for match in pattern.finditer(text):
            kind = match.group("kind")
            is_book = kind in {"书", "图书", "教材"}
            if is_book != book:
                continue
            raw = match.group("num")
            value = int(raw) if raw.isdigit() else _CN_DIGITS.get(raw)
            if value is None and "十" in raw:
                tens, ones = raw.split("十", 1)
                if (not tens or tens in _CN_DIGITS) and (not ones or ones in _CN_DIGITS):
                    value = _CN_DIGITS.get(tens, 1) * 10 + _CN_DIGITS.get(ones, 0)
            if value is not None and value >= 0:
                return value
    return None


def _detect_time_budget(text: str) -> str | None:
    match = _TIME_PATTERN.search(text)
    if match is None:
        return None
    return match.group(0).strip()


def parse_resources_request(
    content: str,
    *,
    prior_context: Sequence[str] = (),
    pending: ModuleWaitState | None = None,
    module_context: ModuleTaskContext | None = None,
) -> ResourcesTermAnalysis:
    """解析一轮资料请求；缺少会改变选择的条件时返回唯一的一个澄清问题。

    ``pending`` 非空表示上一轮已提问、本轮 ``content`` 是该问题的回答：解析
    从等待处恢复（原词与条件沿用等待状态里的记录），而不是把回答当成一个
    全新的资料请求。低影响缺项（媒介、时间）不追问，只在正文明确标注假设。
    """
    text = content.strip()
    if module_context is not None and module_context.used_task_scope:
        # 有效字段提供续接语义，来源原文只作背景，不能复活撤销条件。
        fields = {
            condition.kind: condition.text for condition in module_context.effective_conditions
        }
        conditions = _merge_conditions(read_conditions(text), _conditions_from_fields(fields))
        term = (
            extract_topic_phrase(module_context.topic_hint)
            if (
                (pending is not None and pending.kind == "clarification")
                or text.strip(" ，。！？") in {"继续", "继续推荐", "接着", "再推荐", "继续解释"}
            )
            else extract_topic_phrase(text) or extract_topic_phrase(module_context.topic_hint)
        )
        goal = detect_goal(text) or detect_goal(fields.get("goal", ""))
        level, basis = detect_level(text)
        if level is None and "level" in fields:
            level, basis = detect_level(fields["level"])
        return _resolve_analysis(
            term,
            goal=goal,
            level=level,
            level_basis=basis,
            conditions=conditions,
            deferred=_defers_level(text),
        )
    if pending is not None and pending.kind == "clarification":
        return _resume_from_clarification(text, pending, prior_context=prior_context)
    return _parse_fresh(text, prior_context=prior_context)


def pending_payload(analysis: ResourcesTermAnalysis, *, missing: str) -> dict[str, object]:
    """澄清等待状态的恢复载荷（原词 / 条件 / 缺失项，均为可序列化值）。"""
    payload: dict[str, object] = {
        PENDING_ORIGINAL_PHRASE: analysis.original_phrase,
        PENDING_GOAL: analysis.goal,
        PENDING_MISSING: missing,
        PENDING_GOAL_KIND: analysis.goal_kind.value if analysis.goal_kind else None,
        PENDING_MEDIA: analysis.media.value if analysis.media else None,
        PENDING_REQUESTED_BOOKS: analysis.requested_books,
        PENDING_REQUESTED_VIDEOS: analysis.requested_videos,
        PENDING_LANGUAGE: analysis.language,
        PENDING_TIME_BUDGET: analysis.time_budget,
        PENDING_BASIS: analysis.basis_evidence,
        PENDING_PRACTICE: analysis.needs_practice_project,
    }
    return payload


# ---------------------------------------------------------------------------
# 全新请求
# ---------------------------------------------------------------------------


def _parse_fresh(text: str, *, prior_context: Sequence[str]) -> ResourcesTermAnalysis:
    goal = detect_goal(text)
    conditions = read_conditions(text)
    term = extract_topic_phrase(text)
    level, basis = detect_level(text, prior_context=prior_context)
    if level is None and conditions.goal_kind is ResourcesGoalKind.QUICK_CONCEPT:
        # 快速概念路线不因缺少层次而追问：低影响缺项按明确标注的假设处理。
        basis = None
    return _resolve_analysis(
        term,
        goal=goal,
        level=level,
        level_basis=basis,
        conditions=conditions,
        deferred=_defers_level(text),
    )


def _resolve_analysis(
    term: str | None,
    *,
    goal: str | None,
    level: ResourcesLevel | None,
    level_basis: str | None,
    conditions: _RequestConditions,
    deferred: bool,
) -> ResourcesTermAnalysis:
    """把解析结果收敛为分析或唯一的一个澄清（层次只在影响选择时追问）。"""
    if term is None:
        return _needs_topic(goal=goal, conditions=conditions)
    if (
        level is None
        and not deferred
        and conditions.goal_kind is not ResourcesGoalKind.QUICK_CONCEPT
    ):
        return _needs_level(term, goal=goal, conditions=conditions)
    if deferred:
        level_basis = "你未指定层次，按通用顺序整理"
    return _analysis_for_term(
        term,
        goal=goal,
        level=level,
        level_basis=level_basis,
        conditions=conditions,
        assumptions=_assumptions(conditions, level=level, deferred=deferred),
    )


def extract_topic_phrase(text: str) -> str | None:
    """抽取主题词：引号内 > 拉丁＋中文组合句 > 词表术语 > 英文串 > 中文短语。"""
    quoted = _QUOTED.search(text)
    if quoted is not None:
        inner = _WHITESPACE.sub(" ", quoted.group(1)).strip()
        if len(inner) >= MIN_TERM_LENGTH:
            return inner
    for clause in _CLAUSE_SPLIT.split(text):
        # 词表里的专业名词先于意图词识别：「机器学习资料」不能被当成
        # 「学习资料」这个意图词切掉（原始专业名词逐字保留是硬要求）。
        raw_lexicon = _lexicon_match(clause)
        stripped = _strip_intents(clause)
        if not stripped:
            if raw_lexicon is not None:
                return raw_lexicon
            continue
        lexicon = _lexicon_match(stripped) or raw_lexicon
        latin = _latin_match(stripped)
        if (
            lexicon is not None
            and latin is not None
            and len(stripped) <= MAX_COMBINED_TERM_LENGTH
        ):
            # 「Python 数据分析」这类中英并列的表达整句保留，两个词都进查询。
            return stripped
        candidate = lexicon or latin or _cjk_match(stripped)
        if candidate is not None:
            return candidate
    return None


def detect_goal(text: str) -> str | None:
    """识别学习目的；命中多个时取最具体的一个（备考 > 项目 > 科研 > 面试 > 入门）。"""
    for goal in ("备考", "项目实战", "科研", "工作面试", "入门了解"):
        if any(hint in text for hint in GOAL_HINTS[goal]):
            return goal
    return None


def detect_level(
    text: str, *, prior_context: Sequence[str] = ()
) -> tuple[ResourcesLevel | None, str | None]:
    """按「本句原话 → 可靠前文 → 目的推定」判定层次，并给出判定依据。"""
    explicit = _level_from_text(text)
    if explicit is not None:
        level, word = explicit
        return level, f"你说了「{word}」"
    for message in reversed(list(prior_context)[-CONTEXT_LOOKBACK_MESSAGES:]):
        prior = _level_from_text(message)
        if prior is not None:
            level, word = prior
            return level, f"前文里你提到过「{word}」"
    goal = detect_goal(text)
    derived = _level_from_goal(goal)
    if derived is not None:
        return derived, f"按你的目的（{goal}）推定"
    return None, None


def expansions_for(term: str) -> list[str]:
    """术语的扩展词（把中文部分换成英文词表写法）；原词本身不在扩展列表里。

    扩展只作补充：``_query_for`` 把它追加在原词之后，绝不替换原词。
    """
    if not _LATIN_HAS_LETTER.search(term) and not any(key in term for key in _TERM_KEYS):
        return []
    translated = term
    for key in _TERM_KEYS:
        if key in translated:
            translated = translated.replace(key, TERM_ENGLISH[key])
    translated = _WHITESPACE.sub(" ", translated).strip()
    if translated == term or not _LATIN_HAS_LETTER.search(translated):
        return []
    return [translated]


def level_label(level: ResourcesLevel | None) -> str | None:
    if level is None:
        return None
    return LEVEL_LABELS.get(level)


# ---------------------------------------------------------------------------
# 从等待状态恢复
# ---------------------------------------------------------------------------


def _resume_from_clarification(
    answer: str, pending: ModuleWaitState, *, prior_context: Sequence[str]
) -> ResourcesTermAnalysis:
    """把用户回答并回等待中的解析状态；仍读不出层次时保留原词再问一次。"""
    payload = pending.context
    original_phrase = str(payload.get(PENDING_ORIGINAL_PHRASE) or "").strip()
    raw_goal = payload.get(PENDING_GOAL)
    goal = str(raw_goal) if raw_goal else None
    if not original_phrase:
        # 等待状态没有可用的恢复载荷：按全新请求解析回答本身。
        return _parse_fresh(answer, prior_context=prior_context)
    # 回答里也可能补充学习目的或条件（「有点基础，主要是想应付期末」）：一并采纳。
    goal = detect_goal(answer) or goal
    conditions = _merge_conditions(
        read_conditions(answer), _conditions_from_payload(payload)
    )
    from_answer = _level_from_text(answer)
    if from_answer is not None:
        level, word = from_answer
        return _analysis_for_term(
            original_phrase,
            goal=goal,
            level=level,
            level_basis=f"你说了「{word}」",
            conditions=conditions,
            assumptions=_assumptions(conditions, level=level, deferred=False),
        )
    if _defers_level(answer):
        return _analysis_for_term(
            original_phrase,
            goal=goal,
            level=None,
            level_basis="你未指定层次，按通用顺序整理",
            conditions=conditions,
            assumptions=_assumptions(conditions, level=None, deferred=True),
        )
    return _needs_level(original_phrase, goal=goal, conditions=conditions)


# ---------------------------------------------------------------------------
# 构造解析结果
# ---------------------------------------------------------------------------


def _analysis_for_term(
    term: str,
    *,
    goal: str | None,
    level: ResourcesLevel | None,
    level_basis: str | None,
    conditions: _RequestConditions,
    assumptions: Sequence[str],
) -> ResourcesTermAnalysis:
    expansions = expansions_for(term)
    if term in TERM_ENGLISH:
        confidence = 0.9
    elif _LATIN_HAS_LETTER.search(term):
        confidence = 0.85
    else:
        confidence = 0.7
    return ResourcesTermAnalysis(
        original_phrase=term,
        normalized_term=term,
        expansions=expansions,
        confidence=confidence,
        goal=goal,
        level=level,
        level_basis=level_basis,
        goal_kind=conditions.goal_kind,
        media=conditions.media,
        requested_books=conditions.requested_books,
        requested_videos=conditions.requested_videos,
        language=conditions.language,
        time_budget=conditions.time_budget,
        basis_evidence=conditions.basis_evidence,
        assumptions=list(assumptions),
        needs_practice_project=conditions.needs_practice_project,
        final_query=_query_for(term, expansions),
    )


def _needs_level(
    term: str, *, goal: str | None, conditions: _RequestConditions
) -> ResourcesTermAnalysis:
    question = (
        f"为了挑对「{term}」的图书和视频，先确认一个问题："
        "你现在的学习层次是哪一档——零基础入门、有一定基础，还是进阶提高？"
    )
    return ResourcesTermAnalysis(
        original_phrase=term,
        normalized_term=term,
        expansions=expansions_for(term),
        confidence=0.6,
        goal=goal,
        level=None,
        goal_kind=conditions.goal_kind,
        media=conditions.media,
        requested_books=conditions.requested_books,
        requested_videos=conditions.requested_videos,
        language=conditions.language,
        time_budget=conditions.time_budget,
        basis_evidence=conditions.basis_evidence,
        needs_practice_project=conditions.needs_practice_project,
        final_query=_query_for(term, expansions_for(term)),
        clarification=ResourcesClarification(
            question=question,
            missing="level",
            original_phrase=term,
            candidates=list(LEVEL_CANDIDATES),
        ),
    )


def _needs_topic(
    *, goal: str | None, conditions: _RequestConditions
) -> ResourcesTermAnalysis:
    return ResourcesTermAnalysis(
        original_phrase="",
        normalized_term="",
        expansions=[],
        confidence=0.2,
        goal=goal,
        level=None,
        goal_kind=conditions.goal_kind,
        media=conditions.media,
        requested_books=conditions.requested_books,
        requested_videos=conditions.requested_videos,
        language=conditions.language,
        time_budget=conditions.time_budget,
        basis_evidence=conditions.basis_evidence,
        needs_practice_project=conditions.needs_practice_project,
        final_query="",
        clarification=ResourcesClarification(
            question="你想学哪个方向？告诉我专业名词或课程名，我再去找对应的图书和视频。",
            missing="topic",
            original_phrase="",
            candidates=[],
        ),
    )


def _assumptions(
    conditions: _RequestConditions,
    *,
    level: ResourcesLevel | None,
    deferred: bool,
) -> list[str]:
    """低影响缺项的明确假设（只在正文标注，不追加追问）。"""
    notes: list[str] = []
    if deferred:
        notes.append("你未指定学习层次，本轮按通用顺序整理（可随时纠正）。")
    elif level is None and conditions.goal_kind is ResourcesGoalKind.QUICK_CONCEPT:
        notes.append("你没说明当前基础；快速概念路线按入门概览组织，随时可以让我调整深浅。")
    if conditions.media is None:
        notes.append("你没限定只要书或只要视频，本轮两路都试；需要单一媒介时告诉我即可。")
    if conditions.time_budget is None and conditions.goal_kind is ResourcesGoalKind.EXAM_PREP:
        notes.append("你没给备考时间范围，本轮按不限时整理；告诉我截止时间后我会压缩或展开。")
    return notes


def _conditions_from_fields(fields: dict[str, str]) -> _RequestConditions:
    """任务有效字段里的选择条件（goal/medium/language）。"""
    goal_text = fields.get("goal", "") or ""
    media_text = fields.get("medium", "") or ""
    language = (fields.get("language", "") or "").strip() or None
    return _RequestConditions(
        goal_kind=detect_goal_kind(goal_text) if goal_text else None,
        media=detect_media(media_text) if media_text else None,
        language=language,
    )


def _conditions_from_payload(payload: dict[str, object]) -> _RequestConditions:
    """等待状态里保存的条件（澄清恢复时沿用）。"""

    def _enum_value(enum_type: type[StrEnum], key: str) -> StrEnum | None:
        raw = payload.get(key)
        if not raw:
            return None
        try:
            return enum_type(str(raw))
        except ValueError:
            return None

    def _optional_int(key: str) -> int | None:
        raw = payload.get(key)
        return int(raw) if isinstance(raw, int) else None

    def _optional_str(key: str) -> str | None:
        raw = payload.get(key)
        return str(raw) if isinstance(raw, str) and raw else None

    goal_kind = _enum_value(ResourcesGoalKind, PENDING_GOAL_KIND)
    media = _enum_value(ResourceMedia, PENDING_MEDIA)
    return _RequestConditions(
        goal_kind=goal_kind if isinstance(goal_kind, ResourcesGoalKind) else None,
        media=media if isinstance(media, ResourceMedia) else None,
        requested_books=_optional_int(PENDING_REQUESTED_BOOKS),
        requested_videos=_optional_int(PENDING_REQUESTED_VIDEOS),
        language=_optional_str(PENDING_LANGUAGE),
        time_budget=_optional_str(PENDING_TIME_BUDGET),
        basis_evidence=_optional_str(PENDING_BASIS),
        needs_practice_project=bool(payload.get(PENDING_PRACTICE)),
    )


def _merge_conditions(
    primary: _RequestConditions, fallback: _RequestConditions
) -> _RequestConditions:
    """本轮原话优先于等待/任务里的旧条件（缺失才沿用）。"""

    def _int_or_fallback(current: int | None, previous: int | None) -> int | None:
        return current if current is not None else previous

    return _RequestConditions(
        goal_kind=primary.goal_kind or fallback.goal_kind,
        media=primary.media or fallback.media,
        requested_books=_int_or_fallback(
            primary.requested_books, fallback.requested_books
        ),
        requested_videos=_int_or_fallback(
            primary.requested_videos, fallback.requested_videos
        ),
        language=primary.language or fallback.language,
        time_budget=primary.time_budget or fallback.time_budget,
        basis_evidence=primary.basis_evidence or fallback.basis_evidence,
        needs_practice_project=(
            primary.needs_practice_project or fallback.needs_practice_project
        ),
    )


def _query_for(term: str, expansions: Sequence[str]) -> str:
    """最终查询词：原词在前，扩展词只作补充（最多一个，避免查询被扩写淹没）。"""
    parts = [term, *expansions[:1]]
    return _WHITESPACE.sub(" ", " ".join(part for part in parts if part)).strip()


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _lexicon_match(text: str) -> str | None:
    for key in _TERM_KEYS:
        if key in text:
            return key
    return None


def _latin_match(text: str) -> str | None:
    match = _LATIN_SEQUENCE.search(text)
    if match is None:
        return None
    phrase = _WHITESPACE.sub(" ", match.group(0)).strip()
    if len(phrase) < MIN_TERM_LENGTH or phrase.lower() in TOPIC_STOPWORDS:
        return None
    return phrase


def _cjk_match(text: str) -> str | None:
    for match in _CJK_RUN.finditer(text):
        candidate = match.group(0).strip()
        if len(candidate) >= MIN_TERM_LENGTH and candidate not in TOPIC_STOPWORDS:
            return candidate
    return None


def _level_from_text(text: str) -> tuple[ResourcesLevel, str] | None:
    """命中层次关键词时返回（层次，命中的词）；同时命中多个时取最靠后的一个。"""
    best: tuple[int, ResourcesLevel, str] | None = None
    for level in (ResourcesLevel.BEGINNER, ResourcesLevel.BASIC, ResourcesLevel.ADVANCED):
        for word in LEVEL_HINTS[level]:
            position = text.rfind(word)
            if position < 0:
                continue
            if best is None or position > best[0]:
                best = (position, level, word)
    if best is None:
        return None
    return best[1], best[2]


def _level_from_goal(goal: str | None) -> ResourcesLevel | None:
    if goal == "备考":
        return ResourcesLevel.BASIC
    if goal == "入门了解":
        return ResourcesLevel.BEGINNER
    if goal in {"科研", "工作面试"}:
        return ResourcesLevel.ADVANCED
    return None


def _defers_level(text: str) -> bool:
    return any(signal in text for signal in LEVEL_DEFERRED_SIGNALS)


def _strip_intents(text: str) -> str:
    """剥掉意图／指代词，留下候选主题词（中文按子串、拉丁按词边界）。"""
    stripped = _INTENT_PATTERN.sub(" ", text)
    stripped = _LATIN_INTENT_PATTERN.sub(" ", stripped)
    return _WHITESPACE.sub(" ", stripped).strip()
