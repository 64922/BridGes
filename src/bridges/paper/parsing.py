"""``paper.parse``：原始短语、规范化值、扩展词、置信度与最终查询。

解析规则（``docs/v2/workflows.md`` 第 2 节）：

- **原词逐字保留**：``original_phrase`` 是用户原文里的写法，绝不替换；
- **先消歧再检索**：命中歧义术语且上下文不能消歧时，返回**一个**澄清问题
  （携带候选语境），不偷偷替用户选一个含义；
- **扩展只作补充**：译名/同义词进入 ``expansions``，不替掉原词；
- **置信度如实**：命中词表且语境明确时高，仅从原文抽取时中等，中文未登记
  译名（arXiv 只能按英文检索）时偏低，便于下游说明证据边界。

上下文来源只有两处：本轮用户原文，以及调用方传入的**已确认会话前文**
（最近若干条用户消息）。两者都不足以消歧时问用户，而不是猜测。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from bridges.contracts.modules import ModuleWaitState
from bridges.paper.contracts import (
    PaperClarification,
    PaperConstraints,
    PaperContextCandidate,
    PaperSortIntent,
    PaperTermAnalysis,
)
from bridges.paper.lexicon import (
    AMBIGUOUS_TERMS,
    BEGINNER_HINTS,
    CHINESE_YEAR_OFFSETS,
    CLASSIC_HINTS,
    INTENT_STOPWORDS,
    LATEST_HINTS,
    SURVEY_HINTS,
    TERM_ENGLISH,
    AmbiguousTerm,
    TermContext,
)

#: 澄清后恢复时读取的原词键（等待状态 ``context`` 的稳定字段名）。
PENDING_ORIGINAL_PHRASE = "original_phrase"
PENDING_TERM = "ambiguous_term"
PENDING_CONSTRAINTS = "constraints"

#: 参与上下文消歧的最近用户消息条数上限（够用即止，不把整段历史塞进解析）。
CONTEXT_LOOKBACK_MESSAGES = 6

_QUOTED = re.compile(r"[「“\"']([^」”\"']{1,80})[」”\"']")
_LATIN_SEQUENCE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*(?:\s+[A-Za-z][A-Za-z0-9+.\-]*)*")
_YEAR_RANGE = re.compile(r"(\d{4})\s*[-–—~～至到]\s*(\d{4})")
_YEAR_SINCE = re.compile(r"(\d{4})\s*年?\s*(?:以后|之后|以来|起|后)")
_YEAR_PLAIN = re.compile(r"(\d{4})\s*年")
_YEARS_BACK = re.compile(r"近\s*([一二三四五六七八九十\d]+)\s*年")
_WHITESPACE = re.compile(r"\s+")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN_ONLY = re.compile(r"[a-z0-9 ]+")

#: 语境候选的稳定顺序（平局时的确定性依据）。
_CONTEXT_ORDER: tuple[str, ...] = tuple(
    context.key for term in AMBIGUOUS_TERMS for context in term.contexts
)


def parse_paper_request(
    content: str,
    *,
    prior_context: Sequence[str] = (),
    pending: ModuleWaitState | None = None,
    now: datetime | None = None,
) -> PaperTermAnalysis:
    """解析一轮论文请求；缺失或歧义时返回单一澄清问题。

    ``pending`` 非空表示上一轮已提问、本轮 ``content`` 是该问题的回答：解析
    从等待处恢复（原短语与限制沿用等待状态里的记录），而不是把回答当成全新
    请求重新解析。
    """
    current = now or datetime.now(UTC)
    text = content.strip()
    if pending is not None and pending.kind == "clarification":
        return _resume_from_clarification(text, pending, prior_context=prior_context, now=current)
    return _parse_fresh(text, prior_context=prior_context, now=current)


# ---------------------------------------------------------------------------
# 全新请求
# ---------------------------------------------------------------------------


def _parse_fresh(
    text: str, *, prior_context: Sequence[str], now: datetime
) -> PaperTermAnalysis:
    constraints = _parse_constraints(text, now=now)
    candidates = _context_candidates(prior_context)
    ambiguity = detect_ambiguous_term(text)
    if ambiguity is not None:
        return _resolve_ambiguous(
            text,
            ambiguity,
            prior_context=prior_context,
            candidates=candidates,
            constraints=constraints,
        )
    topic = extract_topic_phrase(text)
    if topic is None:
        return _needs_topic(text)
    return _analysis_for_topic(
        original_phrase=topic,
        text=text,
        constraints=constraints,
        context_key=None,
        context_label=None,
        context_confidence=None,
    )


def _needs_topic(text: str) -> PaperTermAnalysis:
    question = "想找哪个研究主题的论文？请给出术语或研究方向的名称。"
    return PaperTermAnalysis(
        original_phrase=text[:80],
        normalized_term="",
        expansions=[],
        confidence=0.0,
        constraints=PaperConstraints(),
        final_query="",
        clarification=PaperClarification(
            question=question,
            missing="topic",
            original_phrase=text[:80],
        ),
    )


def _resolve_ambiguous(
    text: str,
    term: AmbiguousTerm,
    *,
    prior_context: Sequence[str],
    candidates: dict[str, int],
    constraints: PaperConstraints,
) -> PaperTermAnalysis:
    """歧义术语：先看本轮与前文语境，语境不足才问用户。"""
    original_phrase = _original_phrase_for(text, term)
    current_hits = _context_hits(text, term)
    prior_hits = {key: count for key, count in candidates.items() if count > 0}
    merged = _merge_hits(current_hits, prior_hits)
    if merged:
        key, count, from_current = merged
        context = _context_by_key(term, key)
        confidence = min(0.95, 0.75 + 0.1 * count + (0.1 if from_current else 0.0))
        return _analysis_for_topic(
            original_phrase=original_phrase,
            text=text,
            constraints=constraints,
            context_key=context.key,
            context_label=context.label,
            context_confidence=confidence,
            context_query_terms=context.query_terms,
        )
    return _domain_clarification(original_phrase, term, constraints=constraints)


def _domain_clarification(
    original_phrase: str, term: AmbiguousTerm, *, constraints: PaperConstraints
) -> PaperTermAnalysis:
    """语境未能消歧（首次提问或回答仍不明确）：保留原词，只问那一项。"""
    return PaperTermAnalysis(
        original_phrase=original_phrase,
        normalized_term=_normalize(original_phrase),
        expansions=[],
        confidence=0.3,
        constraints=constraints,
        final_query="",
        clarification=PaperClarification(
            question=_clarification_question(original_phrase, term),
            missing="domain",
            original_phrase=original_phrase,
            candidates=[
                PaperContextCandidate(
                    key=context.key,
                    label=context.label,
                    query_terms=context.query_terms,
                    keywords=context.keywords,
                )
                for context in term.contexts
            ],
        ),
    )


def _clarification_question(original_phrase: str, term: AmbiguousTerm) -> str:
    """只问一项：让用户在候选语境中选一个（附一句"也可以直接说你想要的领域"）。"""
    options = "；".join(
        f"{index}. {context.label}" for index, context in enumerate(term.contexts, start=1)
    )
    return (
        f"「{original_phrase}」可能指不同领域：{options}。"
        "你想找哪一个？也可以直接补充你要用的领域或场景。"
    )


# ---------------------------------------------------------------------------
# 从等待状态恢复
# ---------------------------------------------------------------------------


def _resume_from_clarification(
    answer: str,
    pending: ModuleWaitState,
    *,
    prior_context: Sequence[str],
    now: datetime,
) -> PaperTermAnalysis:
    """把用户回答并回等待中的解析状态；仍不能消歧时再问一项。"""
    payload = pending.context
    original_phrase = str(payload.get(PENDING_ORIGINAL_PHRASE) or pending.question)
    term_key = str(payload.get(PENDING_TERM) or "").lower()
    term = next((item for item in AMBIGUOUS_TERMS if item.term == term_key), None)
    constraints = _constraints_from_payload(payload, now=now)
    if term is None:
        # 等待状态没有可用的消歧载荷：按全新请求解析回答本身。
        return _parse_fresh(answer, prior_context=prior_context, now=now)
    hits = _context_hits(answer, term)
    if hits:
        key, count = max(hits.items(), key=lambda item: item[1])
        context = _context_by_key(term, key)
        confidence = min(0.95, 0.8 + 0.1 * count)
        return _analysis_for_topic(
            original_phrase=original_phrase,
            text=answer,
            constraints=constraints,
            context_key=context.key,
            context_label=context.label,
            context_confidence=confidence,
            context_query_terms=context.query_terms,
        )
    # 回答既没命中候选语境也没命中语境词：保留原词再问一次（仍然只问一项）。
    return _domain_clarification(original_phrase, term, constraints=constraints)


# ---------------------------------------------------------------------------
# 主题、扩展词与限制
# ---------------------------------------------------------------------------


def extract_topic_phrase(text: str) -> str | None:
    """抽取主题短语：引号内 > 词表术语 > 英文串 > 剥离意图词后的剩余短语。"""
    quoted = _QUOTED.search(text)
    if quoted is not None and quoted.group(1).strip():
        return _normalize(quoted.group(1))
    lexicon_hit = _longest_lexicon_term(text)
    if lexicon_hit is not None:
        return lexicon_hit
    latin = _longest_latin_sequence(text)
    if latin is not None and not _is_stopword_only(latin):
        return _normalize(latin)
    stripped = _strip_intent_words(text)
    if len(stripped) >= 2 and not _is_stopword_only(stripped):
        return stripped
    return None


def _longest_lexicon_term(text: str) -> str | None:
    lowered = text.lower()
    hits: list[str] = []
    for chinese in TERM_ENGLISH:
        if chinese in text:
            hits.append(chinese)
    for term in AMBIGUOUS_TERMS:
        for alias in term.aliases:
            if alias.lower() in lowered:
                hits.append(alias)
    if not hits:
        return None
    return max(hits, key=len)


def _longest_latin_sequence(text: str) -> str | None:
    sequences = [match.group(0).strip() for match in _LATIN_SEQUENCE.finditer(text)]
    meaningful = [item for item in sequences if not _is_stopword_only(item)]
    if not meaningful:
        return None
    return max(meaningful, key=lambda item: (len(item.split()), len(item)))


def _strip_intent_words(text: str) -> str:
    stripped = text
    for word in sorted(INTENT_STOPWORDS, key=len, reverse=True):
        stripped = stripped.replace(word, " ")
    stripped = _WHITESPACE.sub(" ", stripped).strip(" ，。！？、；：,.!?;:-—~～")
    return stripped.strip()


def _is_stopword_only(value: str) -> bool:
    remaining = _strip_intent_words(value)
    return not remaining or len(remaining) < 2


def _analysis_for_topic(
    *,
    original_phrase: str,
    text: str,
    constraints: PaperConstraints,
    context_key: str | None,
    context_label: str | None,
    context_confidence: float | None,
    context_query_terms: tuple[str, ...] = (),
) -> PaperTermAnalysis:
    normalized = _normalize(original_phrase)
    expansions, query_term, confidence = _expansions_and_query(
        normalized, text, context_query_terms=context_query_terms
    )
    if context_confidence is not None:
        confidence = context_confidence
    return PaperTermAnalysis(
        original_phrase=original_phrase,
        normalized_term=normalized,
        expansions=expansions,
        confidence=confidence,
        context_key=context_key,
        context_label=context_label,
        constraints=constraints,
        final_query=query_term,
        clarification=None,
    )


def _expansions_and_query(
    normalized: str, text: str, *, context_query_terms: tuple[str, ...]
) -> tuple[list[str], str, float]:
    """返回 (扩展词, 最终查询主词, 置信度)。

    主词始终对应**用户原始术语**：中文术语取其登记译名（同一术语，不是同义
    替换），英文术语保持原样；扩展词只作为额外检索线索。
    """
    expansions: list[str] = []
    primary = normalized
    confidence = 0.75
    chinese = _CJK.search(normalized)
    if chinese is not None:
        translated = _translate_chinese(normalized)
        if translated is not None:
            primary = translated
            confidence = 0.85
        else:
            # 未登记译名的中文术语：arXiv 按英文检索，原词无法直接命中时如实
            # 降低置信度，并在结果里说明证据边界（不虚构译名）。
            confidence = 0.55
    for term in context_query_terms:
        if term.lower() != primary.lower() and term not in expansions:
            expansions.append(term)
    for english in _lexicon_expansions(text, exclude={primary, *expansions}):
        expansions.append(english)
    return expansions, primary, confidence


def _translate_chinese(normalized: str) -> str | None:
    if normalized in TERM_ENGLISH:
        return TERM_ENGLISH[normalized]
    for chinese, english in sorted(TERM_ENGLISH.items(), key=lambda item: -len(item[0])):
        if chinese in normalized:
            return english
    return None


def _lexicon_expansions(text: str, *, exclude: set[str]) -> list[str]:
    found: list[str] = []
    for chinese, english in TERM_ENGLISH.items():
        if chinese in text and english not in exclude:
            found.append(english)
    return found


def _normalize(value: str) -> str:
    collapsed = _WHITESPACE.sub(" ", value).strip(" 「」“”\"'，。！？、；：,.!?;:")
    return collapsed.strip()


def _original_phrase_for(text: str, term: AmbiguousTerm) -> str:
    """保留用户在原文里的写法（别名中第一个真实出现的写法，保持原大小写）。"""
    lowered = text.lower()
    best: str | None = None
    for alias in term.aliases:
        index = lowered.find(alias.lower())
        if index < 0:
            continue
        found = text[index : index + len(alias)]
        if best is None or len(found) > len(best):
            best = found
    return best or term.term


def detect_ambiguous_term(text: str) -> AmbiguousTerm | None:
    lowered = text.lower()
    best: tuple[int, AmbiguousTerm] | None = None
    for term in AMBIGUOUS_TERMS:
        for alias in term.aliases:
            if alias.lower() in lowered and (best is None or len(alias) > best[0]):
                best = (len(alias), term)
    return best[1] if best is not None else None


def _keyword_present(lowered: str, keyword: str) -> bool:
    """语境词命中判定：纯拉丁词按词边界匹配，避免 "ai" 命中 "said"。"""
    lowered_keyword = keyword.lower()
    if _LATIN_ONLY.fullmatch(lowered_keyword):
        pattern = rf"(?<![a-z0-9]){re.escape(lowered_keyword)}(?![a-z0-9])"
        return re.search(pattern, lowered) is not None
    return lowered_keyword in lowered


def _context_hits(text: str, term: AmbiguousTerm) -> dict[str, int]:
    lowered = text.lower()
    hits: dict[str, int] = {}
    for context in term.contexts:
        count = sum(1 for keyword in context.keywords if _keyword_present(lowered, keyword))
        if count:
            hits[context.key] = count
    return hits


def _context_candidates(prior_context: Sequence[str]) -> dict[str, int]:
    """前文语境计数：只统计出现过的语境词（不含本轮原文）。"""
    hits: dict[str, int] = {}
    for message in list(prior_context)[-CONTEXT_LOOKBACK_MESSAGES:]:
        lowered = message.lower()
        for term in AMBIGUOUS_TERMS:
            for context in term.contexts:
                count = sum(
                    1 for keyword in context.keywords if _keyword_present(lowered, keyword)
                )
                if count:
                    hits[context.key] = hits.get(context.key, 0) + count
    return hits


def _merge_hits(
    current: dict[str, int], prior: dict[str, int]
) -> tuple[str, int, bool] | None:
    """合并本轮与前文命中：本轮优先，平局取词表顺序（确定性）。"""
    if current:
        key = max(current, key=lambda item: current[item])
        return key, current[key], True
    if prior:
        ranked = sorted(prior.items(), key=lambda item: (-item[1], _context_order(item[0])))
        key, count = ranked[0]
        return key, count, False
    return None


def _context_order(key: str) -> int:
    return _CONTEXT_ORDER.index(key) if key in _CONTEXT_ORDER else len(_CONTEXT_ORDER)


def _context_by_key(term: AmbiguousTerm, key: str) -> TermContext:
    for context in term.contexts:
        if context.key == key:
            return context
    return term.contexts[0]


# ---------------------------------------------------------------------------
# 限制（年份/排序意图/类型）
# ---------------------------------------------------------------------------


def _parse_constraints(text: str, *, now: datetime) -> PaperConstraints:
    lowered = text.lower()
    year_from, year_to = _parse_years(text, now=now)
    sort_intent, prefer_survey = _parse_intent(lowered)
    return PaperConstraints(
        year_from=year_from,
        year_to=year_to,
        sort_intent=sort_intent,
        prefer_survey=prefer_survey,
    )


def _parse_years(text: str, *, now: datetime) -> tuple[int | None, int | None]:
    match = _YEAR_RANGE.search(text)
    if match is not None:
        start, end = int(match.group(1)), int(match.group(2))
        return (min(start, end), max(start, end))
    match = _YEAR_SINCE.search(text)
    if match is not None:
        return int(match.group(1)), None
    match = _YEARS_BACK.search(text)
    if match is not None:
        token = match.group(1)
        span = int(token) if token.isdigit() else CHINESE_YEAR_OFFSETS.get(token, 0)
        if span > 0:
            return now.year - span, None
    match = _YEAR_PLAIN.search(text)
    if match is not None:
        year = int(match.group(1))
        return year, year
    return None, None


def _parse_intent(lowered: str) -> tuple[PaperSortIntent, bool]:
    prefer_survey = any(hint in lowered for hint in SURVEY_HINTS)
    scores = {
        PaperSortIntent.CLASSIC: sum(1 for hint in CLASSIC_HINTS if hint in lowered),
        PaperSortIntent.LATEST: sum(1 for hint in LATEST_HINTS if hint in lowered),
        PaperSortIntent.BEGINNER: sum(1 for hint in BEGINNER_HINTS if hint in lowered),
    }
    # 命中数多者优先；平局按 CLASSIC > LATEST > BEGINNER 的固定顺序（确定性）。
    best = max(scores, key=lambda intent: (scores[intent], -_INTENT_ORDER.index(intent)))
    if scores[best] == 0:
        return PaperSortIntent.RELEVANCE, prefer_survey
    return best, prefer_survey


_INTENT_ORDER: tuple[PaperSortIntent, ...] = (
    PaperSortIntent.CLASSIC,
    PaperSortIntent.LATEST,
    PaperSortIntent.BEGINNER,
    PaperSortIntent.RELEVANCE,
)


def _constraints_from_payload(payload: dict[str, object], *, now: datetime) -> PaperConstraints:
    raw = payload.get(PENDING_CONSTRAINTS)
    if isinstance(raw, dict):
        try:
            return PaperConstraints.model_validate(raw)
        except ValueError:
            return PaperConstraints()
    del now
    return PaperConstraints()


def pending_payload(
    analysis: PaperTermAnalysis, *, ambiguous_term: str | None
) -> dict[str, object]:
    """澄清等待状态的恢复载荷（原词 / 歧义术语 / 限制，均为可序列化值）。"""
    return {
        PENDING_ORIGINAL_PHRASE: analysis.original_phrase,
        PENDING_TERM: ambiguous_term,
        PENDING_CONSTRAINTS: analysis.constraints.model_dump(mode="json"),
    }
