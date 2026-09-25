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
from datetime import UTC, datetime

from bridges.contracts.modules import ModuleWaitState
from bridges.resources.contracts import (
    LEVEL_LABELS,
    ResourcesClarification,
    ResourcesLevel,
    ResourcesTermAnalysis,
)
from bridges.resources.lexicon import (
    GOAL_HINTS,
    INTENT_STOPWORDS,
    LATIN_INTENT_WORDS,
    LEVEL_CANDIDATES,
    LEVEL_HINTS,
    TERM_ENGLISH,
    TOPIC_STOPWORDS,
)

#: 澄清后恢复时读取的原词键（等待状态 ``context`` 的稳定字段名）。
PENDING_ORIGINAL_PHRASE = "original_phrase"
PENDING_GOAL = "goal"
PENDING_MISSING = "missing"

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
#: 拉丁意图词按词边界剥离（避免把 Transformer 的 a、Python 的 on 切掉）。
_LATIN_INTENT_PATTERN = re.compile(
    r"\b(?:" + "|".join(sorted(LATIN_INTENT_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def parse_resources_request(
    content: str,
    *,
    prior_context: Sequence[str] = (),
    pending: ModuleWaitState | None = None,
    now: datetime | None = None,
) -> ResourcesTermAnalysis:
    """解析一轮资料请求；缺少层次且它影响推荐时返回唯一的一个澄清问题。

    ``pending`` 非空表示上一轮已提问、本轮 ``content`` 是该问题的回答：解析
    从等待处恢复（原词与目的沿用等待状态里的记录），而不是把回答当成一个
    全新的资料请求。
    """
    current = now or datetime.now(UTC)
    text = content.strip()
    if pending is not None and pending.kind == "clarification":
        return _resume_from_clarification(text, pending, prior_context=prior_context)
    return _parse_fresh(text, prior_context=prior_context, now=current)


def pending_payload(analysis: ResourcesTermAnalysis, *, missing: str) -> dict[str, object]:
    """澄清等待状态的恢复载荷（原词 / 目的 / 缺失项，均为可序列化值）。"""
    return {
        PENDING_ORIGINAL_PHRASE: analysis.original_phrase,
        PENDING_GOAL: analysis.goal,
        PENDING_MISSING: missing,
    }


# ---------------------------------------------------------------------------
# 全新请求
# ---------------------------------------------------------------------------


def _parse_fresh(
    text: str, *, prior_context: Sequence[str], now: datetime
) -> ResourcesTermAnalysis:
    del now
    goal = detect_goal(text)
    term = extract_topic_phrase(text)
    if term is None:
        return _needs_topic(goal=goal)
    level, basis = detect_level(text, prior_context=prior_context)
    if level is not None:
        return _analysis_for_term(term, goal=goal, level=level, level_basis=basis)
    if _defers_level(text):
        # 用户把层次交给我们：不追问，按通用顺序给（正文如实说明）。
        return _analysis_for_term(
            term, goal=goal, level=None, level_basis="你未指定层次，按通用顺序整理"
        )
    return _needs_level(term, goal=goal)


def extract_topic_phrase(text: str) -> str | None:
    """抽取主题词：引号内 > 拉丁＋中文组合句 > 词表术语 > 英文串 > 中文短语。"""
    quoted = _QUOTED.search(text)
    if quoted is not None:
        inner = _WHITESPACE.sub(" ", quoted.group(1)).strip()
        if len(inner) >= MIN_TERM_LENGTH:
            return inner
    for clause in _CLAUSE_SPLIT.split(text):
        stripped = _strip_intents(clause)
        if not stripped:
            continue
        lexicon = _lexicon_match(stripped)
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
        return _parse_fresh(answer, prior_context=prior_context, now=datetime.now(UTC))
    # 回答里也可能补充学习目的（「有点基础，主要是想应付期末」）：一并采纳。
    goal = detect_goal(answer) or goal
    from_answer = _level_from_text(answer)
    if from_answer is not None:
        level, word = from_answer
        return _analysis_for_term(
            original_phrase, goal=goal, level=level, level_basis=f"你说了「{word}」"
        )
    if _defers_level(answer):
        return _analysis_for_term(
            original_phrase,
            goal=goal,
            level=None,
            level_basis="你未指定层次，按通用顺序整理",
        )
    return _needs_level(original_phrase, goal=goal)


# ---------------------------------------------------------------------------
# 构造解析结果
# ---------------------------------------------------------------------------


def _analysis_for_term(
    term: str,
    *,
    goal: str | None,
    level: ResourcesLevel | None,
    level_basis: str | None,
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
        final_query=_query_for(term, expansions),
    )


def _needs_level(term: str, *, goal: str | None) -> ResourcesTermAnalysis:
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
        final_query=_query_for(term, expansions_for(term)),
        clarification=ResourcesClarification(
            question=question,
            missing="level",
            original_phrase=term,
            candidates=list(LEVEL_CANDIDATES),
        ),
    )


def _needs_topic(*, goal: str | None) -> ResourcesTermAnalysis:
    return ResourcesTermAnalysis(
        original_phrase="",
        normalized_term="",
        expansions=[],
        confidence=0.2,
        goal=goal,
        level=None,
        final_query="",
        clarification=ResourcesClarification(
            question="你想学哪个方向？告诉我专业名词或课程名，我再去找对应的图书和视频。",
            missing="topic",
            original_phrase="",
            candidates=[],
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
    stripped = text
    for word in _INTENT_WORDS:
        if word in stripped:
            stripped = stripped.replace(word, " ")
    stripped = _LATIN_INTENT_PATTERN.sub(" ", stripped)
    return _WHITESPACE.sub(" ", stripped).strip()
