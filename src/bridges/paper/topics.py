"""提取论文研究主题，保留术语并剥离请求数量与意图。"""

from __future__ import annotations

import re

from bridges.paper.lexicon import AMBIGUOUS_TERMS, INTENT_STOPWORDS, TERM_ENGLISH
from bridges.paper.request_constraints import PAPER_COUNT

_QUOTED = re.compile(r"[「“\"']([^」”\"']{1,80})[」”\"']")
_LATIN_SEQUENCE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*(?:\s+[A-Za-z][A-Za-z0-9+.\-]*)*")
_WHITESPACE = re.compile(r"\s+")


def extract_topic_phrase(text: str) -> str | None:
    """抽取主题短语：引号内 > 词表术语 > 英文串 > 剥离意图词后的剩余短语。"""
    quoted = _QUOTED.search(text)
    if quoted is not None and quoted.group(1).strip():
        return normalize_phrase(quoted.group(1))
    lexicon_hit = _longest_lexicon_term(text)
    if lexicon_hit is not None:
        return lexicon_hit
    latin = _longest_latin_sequence(text)
    if latin is not None and not _is_stopword_only(latin):
        return normalize_phrase(latin)
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
    # 只剥离句首的学习请求，不删除「流形学习」等术语内部的词。
    stripped = re.sub(r"^\s*(?:我)?(?:想|要)?(?:学习|了解)(?:一下)?", " ", text)
    stripped = PAPER_COUNT.sub(" ", stripped)
    for word in sorted(INTENT_STOPWORDS, key=len, reverse=True):
        stripped = stripped.replace(word, " ")
    stripped = _WHITESPACE.sub(" ", stripped).strip(" ，。！？、；：,.!?;:-—~～")
    return stripped.strip()


def _is_stopword_only(value: str) -> bool:
    remaining = _strip_intent_words(value)
    return not remaining or len(remaining) < 2


def normalize_phrase(value: str) -> str:
    collapsed = _WHITESPACE.sub(" ", value).strip(" 「」“”\"'，。！？、；：,.!?;:")
    return collapsed.strip()\n