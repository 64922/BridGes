"""论文请求的数量、时间、来源和指定论文限制；与研究主题分别解析。"""

from __future__ import annotations

import re
from datetime import datetime

from bridges.paper.contracts import PaperConstraints, PaperSortIntent
from bridges.paper.lexicon import (
    BEGINNER_HINTS,
    CHINESE_YEAR_OFFSETS,
    CLASSIC_HINTS,
    LATEST_HINTS,
    SURVEY_HINTS,
)

#: 篇数只在量词前识别，不把年份或术语中的数字当作数量。
PAPER_COUNT = re.compile(r"(?<![A-Za-z0-9])([一二两三四五六七八九十]+|\d+)\s*篇")

_YEAR_RANGE = re.compile(r"(\d{4})\s*[-–—~～至到]\s*(\d{4})")
_YEAR_SINCE = re.compile(r"(\d{4})\s*年?\s*(?:以后|之后|以来|起|后)")
_YEAR_PLAIN = re.compile(r"(\d{4})\s*年")
_YEARS_BACK = re.compile(r"近\s*([一二三四五六七八九十\d]+)\s*年")
_ARXIV_ID = re.compile(r"(?:arxiv(?:\.org/(?:abs|pdf)/|\s*[:：]?\s*))"
    r"([a-z-]+/\d{7}|\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
#: 无 ``arxiv`` 前缀的裸标识（仅在原文确实提到 arXiv 时启用，避免误抓数字）。
_ARXIV_ID_BARE = re.compile(r"\b([a-z-]+/\d{7}|\d{4}\.\d{4,5}(?:v\d+)?)\b")
_PAPER_TITLE = re.compile(r"《([^》]+)》")
#: 明确标注“标题/题目/paper”的引号标题（与主题引号区分）。
_PAPER_TITLE_QUOTED = re.compile(
    r"(?:标题|题目|论文名)\s*(?:是|为|：|:)?\s*[“\"「『']([^”\"」』']{2,120})[”\"」』']"
)



def parse_constraints(text: str, *, now: datetime) -> PaperConstraints:
    lowered = text.lower()
    year_from, year_to = parse_years(text, now=now)
    sort_intent, prefer_survey = parse_intent(lowered)
    allowed: list[str] = []
    excluded: list[str] = []
    for clause in re.split(r"[，。；;,]|(?:但|不过)", lowered):
        sources = [name for name in ("arxiv", "crossref", "openalex") if name in clause]
        if re.search(r"不要|不用|禁止|排除|不查|别查|不使用|without|exclude|avoid", clause):
            excluded.extend(sources)
        elif re.search(r"仅|只|限定|来源为|来源是|only|from", clause):
            allowed.extend(sources)
    identity = _ARXIV_ID.search(text)
    if identity is None and "arxiv" in lowered:
        identity = _ARXIV_ID_BARE.search(text)
    count_match = PAPER_COUNT.search(text)
    count_token = count_match.group(1) if count_match else ""
    count = int(count_token) if count_token.isdigit() else CHINESE_YEAR_OFFSETS.get(count_token)
    title = _PAPER_TITLE.search(text) or _PAPER_TITLE_QUOTED.search(text)
    return PaperConstraints(
        requested_count=count if count and count > 0 else None,
        year_from=year_from,
        year_to=year_to,
        sort_intent=sort_intent,
        prefer_survey=prefer_survey,
        arxiv_id=identity.group(1) if identity else None,
        paper_title=title.group(1).strip() if title else None,
        allowed_sources=list(dict.fromkeys(allowed)),
        excluded_sources=list(dict.fromkeys(excluded)),
    )


def parse_years(text: str, *, now: datetime) -> tuple[int | None, int | None]:
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


def parse_intent(lowered: str) -> tuple[PaperSortIntent, bool]:
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
