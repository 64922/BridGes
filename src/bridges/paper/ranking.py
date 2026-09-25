"""``paper.rank``：主题与来源核对、阅读顺序与选择理由。

核对先于生成（``docs/v2/architecture.md`` 第 8 节）：只有标题或摘要确实覆盖
原始术语（或本轮确认的扩展词）的候选才进入推荐；一篇都不覆盖时**停止推荐并
请用户澄清**，绝不凑满篇数。

排序使用真实证据：综述/教程优先打头，奠基工作用实际取得的引用数据（Crossref
／OpenAlex 补充）分辨，较新研究按来源年份排在后面。没有引用数据时如实写进
未核实项，不假装知道哪篇是奠基作。

用户提到的年份范围是真实过滤条件（不是摆设）：范围外有命中就报出被排除的篇数，
范围外才有结果时保留候选并说明，绝不悄悄忽略用户说过的范围。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bridges.paper.contracts import (
    ROLE_FOUNDATION,
    ROLE_LABELS,
    ROLE_RECENT,
    ROLE_SURVEY,
    ROLE_TUTORIAL,
    PaperQueryPlan,
    PaperRecommendation,
    PaperSortIntent,
    PaperTermAnalysis,
)
from bridges.paper.planning import (
    DEFAULT_TARGET_COUNT,
    MAX_TARGET_COUNT,
    MIN_TARGET_COUNT,
)
from bridges.paper.sources import EnrichedMetadata, PaperCandidate

SURVEY_MARKERS = ("survey", "review", "overview", "综述", "回顾")
TUTORIAL_MARKERS = ("tutorial", "introduction to", "primer", "教程", "入门")
RECENT_WINDOW_YEARS = 2

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class RankOutcome:
    """排序结果：推荐列表（已按阅读顺序）、证据边界说明与主题不匹配标记。"""

    recommendations: list[PaperRecommendation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    topic_mismatch: bool = False


def rank_candidates(
    analysis: PaperTermAnalysis,
    plan: PaperQueryPlan,
    candidates: list[PaperCandidate],
    metadata: dict[str, EnrichedMetadata],
    *,
    target_count: int = DEFAULT_TARGET_COUNT,
) -> RankOutcome:
    """核对主题匹配后排序；核对的词必须能在标题/摘要里真的找到。

    核对分两步：**原始术语**必须被覆盖（原词覆盖门），消歧或译名产生了扩展
    词时，还必须有扩展词语境覆盖——否则「电力系统的 transformer」会混进机
    器学习结果（反之亦然）。
    """
    target = max(MIN_TARGET_COUNT, min(MAX_TARGET_COUNT, target_count))
    primary = _primary_keyword(analysis)
    context_keywords = _context_keywords(analysis)
    context_rejected = 0
    matched: list[tuple[PaperCandidate, list[str]]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.arxiv_id in seen:
            continue
        matched_primary = _matches(candidate, [primary]) if primary else []
        if not matched_primary:
            continue
        matched_context = _matches(candidate, context_keywords)
        if context_keywords and not matched_context:
            context_rejected += 1
            continue
        seen.add(candidate.arxiv_id)
        matched.append((candidate, [*matched_primary, *matched_context]))
    if not matched:
        rejected_notes = ["检索结果里没有标题或摘要覆盖原始术语的论文，未生成推荐。"]
        if context_rejected:
            rejected_notes.append(
                f"另有 {context_rejected} 篇只命中原词、但语境不匹配（"
                f"{analysis.context_label or '扩展词'}），已排除。"
            )
        return RankOutcome(notes=rejected_notes, topic_mismatch=True)
    scored, year_dropped, year_widened = _filter_by_year(analysis, matched)
    desired = min(target, max(MIN_TARGET_COUNT, len(scored)))
    roles = _assign_roles(scored, metadata)
    ordered = _reading_order(scored, roles, metadata, analysis.constraints.sort_intent)
    selected = ordered[:desired]
    recommendations = [
        _recommendation(
            index=index,
            candidate=candidate,
            matches=matches,
            role=roles[candidate.arxiv_id],
            metadata=metadata.get(candidate.arxiv_id),
            sort_intent=analysis.constraints.sort_intent,
            total=len(selected),
        )
        for index, (candidate, matches) in enumerate(selected, start=1)
    ]
    notes: list[str] = []
    if context_rejected:
        notes.append(
            f"另有 {context_rejected} 篇只命中原词、但语境不匹配（"
            f"{analysis.context_label or '扩展词'}），已排除。"
        )
    year_label = _year_range_label(analysis)
    if year_label is not None:
        if year_widened:
            notes.append(
                f"你提到的年份范围（{year_label}）内没有主题匹配的结果，"
                "下面是范围外的候选（年份已逐篇标注）；可放宽年份后重试。"
            )
        elif year_dropped:
            notes.append(
                f"已按你提到的年份范围（{year_label}）过滤："
                f"排除 {len(year_dropped)} 篇范围外的论文。"
            )
    if analysis.constraints.prefer_survey and not any(
        roles[candidate.arxiv_id] in {ROLE_SURVEY, ROLE_TUTORIAL} for candidate, _ in selected
    ):
        notes.append("你提到要综述/回顾类论文，但本轮命中的结果里没有综述或教程标记，未替你补造。")
    if len(selected) < target:
        notes.append(
            f"本轮只找到 {len(selected)} 篇主题匹配的真实论文（目标 {target} 篇），"
            "没有补造结果。"
        )
    if analysis.constraints.sort_intent is PaperSortIntent.CLASSIC and not any(
        item.cited_by_count is not None for item in metadata.values()
    ):
        notes.append("未取得引用数据，无法按引用量分辨奠基工作，顺序仅依据来源年份与类型。")
    return RankOutcome(recommendations=recommendations, notes=notes)


def _filter_by_year(
    analysis: PaperTermAnalysis, matched: list[tuple[PaperCandidate, list[str]]]
) -> tuple[list[tuple[PaperCandidate, list[str]]], list[tuple[PaperCandidate, list[str]]], bool]:
    """按用户提到的年份范围过滤；范围外会清空结果时保留全部并如实说明（不悄悄忽略）。"""
    year_from = analysis.constraints.year_from
    year_to = analysis.constraints.year_to
    if year_from is None and year_to is None:
        return matched, [], False
    kept: list[tuple[PaperCandidate, list[str]]] = []
    dropped: list[tuple[PaperCandidate, list[str]]] = []
    for item in matched:
        year = item[0].published_at.year
        if (year_from is None or year >= year_from) and (year_to is None or year <= year_to):
            kept.append(item)
        else:
            dropped.append(item)
    if not kept:
        return matched, dropped, True
    return kept, dropped, False


def _year_range_label(analysis: PaperTermAnalysis) -> str | None:
    year_from = analysis.constraints.year_from
    year_to = analysis.constraints.year_to
    if year_from is None and year_to is None:
        return None
    if year_from is not None and year_to is not None:
        return f"{year_from}–{year_to} 年" if year_from != year_to else f"{year_from} 年"
    if year_from is not None:
        return f"{year_from} 年起"
    return f"{year_to} 年及以前"


def cover_original_phrase(
    analysis: PaperTermAnalysis, recommendations: list[PaperRecommendation]
) -> bool:
    """推荐是否覆盖原始术语（生成前的最后一道原词覆盖核对）。"""
    primary = _primary_keyword(analysis)
    if not primary:
        return False
    for item in recommendations:
        haystack = f"{item.title} {item.match_basis}".lower()
        if primary in haystack or all(
            word in haystack for word in _WORD.findall(primary)
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _primary_keyword(analysis: PaperTermAnalysis) -> str:
    """原始术语对应的检索主词（原词覆盖门的依据）。"""
    return analysis.final_query.strip().lower() or analysis.normalized_term.strip().lower()


def _context_keywords(analysis: PaperTermAnalysis) -> list[str]:
    """语境/扩展词（消歧与译名产生的补充线索）。"""
    return [item.strip().lower() for item in analysis.expansions if item.strip()]


def _matches(candidate: PaperCandidate, keywords: list[str]) -> list[str]:
    haystack = f"{candidate.title} {candidate.abstract}".lower()
    matches: list[str] = []
    for keyword in keywords:
        if keyword in haystack:
            matches.append(keyword)
            continue
        words = _WORD.findall(keyword)
        # 多词短语允许按词元覆盖（摘要有曲折变化时仍能核对），单词必须精确出现。
        if len(words) > 1 and all(word in haystack for word in words):
            matches.append(keyword)
    return matches


def _assign_roles(
    scored: list[tuple[PaperCandidate, list[str]]],
    metadata: dict[str, EnrichedMetadata],
) -> dict[str, str]:
    roles: dict[str, str] = {}
    citation_values = [
        item.cited_by_count
        for item in metadata.values()
        if item.cited_by_count is not None
    ]
    citation_median = (
        sorted(citation_values)[len(citation_values) // 2] if citation_values else None
    )
    newest_year = max((item[0].published_at.year for item in scored), default=0)
    for candidate, _matched in scored:
        lowered = f"{candidate.title} {candidate.abstract}".lower()
        enriched = metadata.get(candidate.arxiv_id)
        citations = enriched.cited_by_count if enriched is not None else None
        if any(marker in lowered for marker in SURVEY_MARKERS):
            roles[candidate.arxiv_id] = ROLE_SURVEY
        elif any(marker in lowered for marker in TUTORIAL_MARKERS):
            roles[candidate.arxiv_id] = ROLE_TUTORIAL
        elif (
            citations is not None
            and citation_median is not None
            and citations >= citation_median
            and candidate.published_at.year <= newest_year - 1
        ):
            roles[candidate.arxiv_id] = ROLE_FOUNDATION
        elif candidate.published_at.year >= newest_year - (RECENT_WINDOW_YEARS - 1):
            roles[candidate.arxiv_id] = ROLE_RECENT
        else:
            roles[candidate.arxiv_id] = ROLE_FOUNDATION
    return roles


def _reading_order(
    scored: list[tuple[PaperCandidate, list[str]]],
    roles: dict[str, str],
    metadata: dict[str, EnrichedMetadata],
    sort_intent: PaperSortIntent,
) -> list[tuple[PaperCandidate, list[str]]]:
    """阅读顺序：入门材料打头，其次奠基，最后较新研究。

    「最新」意图把较新研究提前到综述之后，方便先看前沿；「经典」意图把奠基
    工作提前。无论意图如何，都不改变"先综述、后具体"的入门阅读顺序。
    """
    priority = {
        ROLE_SURVEY: 0,
        ROLE_TUTORIAL: 1,
        ROLE_FOUNDATION: 2,
        ROLE_RECENT: 3,
    }
    if sort_intent is PaperSortIntent.LATEST:
        priority = {
            ROLE_SURVEY: 0,
            ROLE_RECENT: 1,
            ROLE_TUTORIAL: 2,
            ROLE_FOUNDATION: 3,
        }
    elif sort_intent is PaperSortIntent.CLASSIC:
        priority = {
            ROLE_SURVEY: 0,
            ROLE_FOUNDATION: 1,
            ROLE_TUTORIAL: 2,
            ROLE_RECENT: 3,
        }

    def key(item: tuple[PaperCandidate, list[str]]) -> tuple[int, int, str]:
        candidate = item[0]
        enriched = metadata.get(candidate.arxiv_id)
        citations = enriched.cited_by_count if enriched is not None else None
        # 同角色内：有引用数据时按引用降序（取负值），否则按年份降序。
        citation_rank = -(citations or 0)
        return (priority[roles[candidate.arxiv_id]], citation_rank, candidate.title)

    return sorted(scored, key=key)


def _recommendation(
    *,
    index: int,
    candidate: PaperCandidate,
    matches: list[str],
    role: str,
    metadata: EnrichedMetadata | None,
    sort_intent: PaperSortIntent,
    total: int,
) -> PaperRecommendation:
    unverified: list[str] = ["未通读全文，判断依据为标题、摘要与来源元数据。"]
    if metadata is None:
        unverified.append("未取得 Crossref／OpenAlex 发表信息。")
    # arXiv 候选自带来源给出的 PDF 链接；元数据补充发现开放获取链接时同样算取得全文。
    full_text_available = bool(candidate.pdf_url) or bool(metadata and metadata.full_text_url)
    if not full_text_available:
        unverified.append("来源未返回可获取的全文链接，本轮只核对标题与摘要。")
    year = candidate.published_at.year
    venue = metadata.venue if metadata is not None else None
    cited = metadata.cited_by_count if metadata is not None else None
    return PaperRecommendation(
        order=index,
        arxiv_id=candidate.arxiv_id,
        title=candidate.title,
        authors=list(candidate.authors),
        published_year=year,
        source="arxiv",
        abs_url=candidate.abs_url,
        pdf_url=candidate.pdf_url,
        primary_category=candidate.primary_category,
        full_text_available=full_text_available,
        role=role,
        reason_zh=_reason_zh(
            role=role,
            year=year,
            venue=venue,
            cited=cited,
            index=index,
            total=total,
            sort_intent=sort_intent,
        ),
        match_basis=(
            f"标题或摘要覆盖检索词：{'、'.join(matches)}"
            + (
                f"；arXiv 主类别 {candidate.primary_category}。"
                if candidate.primary_category
                else "。"
            )
        ),
        unverified=unverified,
    )


def _reason_zh(
    *,
    role: str,
    year: int,
    venue: str | None,
    cited: int | None,
    index: int,
    total: int,
    sort_intent: PaperSortIntent,
) -> str:
    label = ROLE_LABELS.get(role, "相关研究")
    position = (
        "建议第 1 篇先读" if index == 1 else f"建议第 {index} 篇读（共 {total} 篇）"
    )
    pieces = [f"{year} 年，{label}，{position}"]
    if venue:
        pieces.append(f"发表信息（Crossref／OpenAlex）：{venue}")
    if cited is not None:
        pieces.append(f"记录引用数 {cited}（仅作辅助，不等于质量）")
    if sort_intent is PaperSortIntent.BEGINNER and role in {ROLE_SURVEY, ROLE_TUTORIAL}:
        pieces.append("你提到入门，优先放综述/教程类材料")
    if sort_intent is PaperSortIntent.CLASSIC and role is ROLE_FOUNDATION:
        pieces.append("你提到经典，按实际取得的年份与引用证据排前")
    return "；".join(pieces) + "。"
