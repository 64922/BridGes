"""``resources.match``：用证据核对主题覆盖与先修适配（未知保持未知）。

匹配是确定性的，但证据分层明确：

1. **证据层次**：实际读取到目录/主题标签为 ``catalog``，读取到简介为
   ``intro``，只有标题/时长/点赞/公开计数等弱信号为 ``title``。主线候选
   必须有 ``intro`` 或 ``catalog`` 支持；``title`` 只能作为补充/候选。
2. **主题覆盖**：标题、目录、主题标签或简介任一命中本轮原词/扩展词即视为
   覆盖；命中处如实写进 ``match_basis``，不把标题命中当成内容适配。
3. **先修适配**：阶段标记（入门/进阶）来自标题与已读内容；视频时长只在
   标记相同时作弱排序信号，绝不单独证明适用性。
4. **重复版本**：同题同作者的书目只保留书目信息更完整的一条；同一视频按
   ``video_id`` 去重。被排除的条数与原因如实记录。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bridges.resources.contracts import (
    STAGE_ADVANCED,
    STAGE_BEGINNER,
    STAGE_FOUNDATION,
    STAGE_UNKNOWN,
    BookReadEvidence,
    ResourceEvidenceLevel,
    ResourcesGoalKind,
    ResourcesLevel,
    ResourcesTermAnalysis,
)
from bridges.resources.lexicon import ADVANCED_MARKERS, BEGINNER_MARKERS
from bridges.resources.sources import (
    BILIBILI_SOURCE,
    OPENALEX_SOURCE,
    OPENLIBRARY_SOURCE,
    BookCandidate,
    VideoCandidate,
)

#: 点赞过此数视为「有公开反馈」（口碑弱信号，只用于同阶段取舍）。
POPULAR_LIKE_THRESHOLD = 100

#: 来源 → 中文名（正文与证据说明共用一份；未列出的来源原样显示）。
SOURCE_LABELS: dict[str, str] = {
    OPENLIBRARY_SOURCE: "Open Library 书目",
    OPENALEX_SOURCE: "OpenAlex 图书记录",
    BILIBILI_SOURCE: "哔哩哔哩公开视频页",
}

#: 未读目录/简介时的统一边界说明（正文与证据说明共用）。
TITLE_ONLY_SCOPE = "仅标题与书目元数据（未读取目录/简介）"


@dataclass(frozen=True)
class EvaluatedBook:
    """一本完成匹配评估的图书（证据层次与覆盖依据已记录）。"""

    candidate: BookCandidate
    stage: str
    score: int
    covered: bool
    evidence_level: ResourceEvidenceLevel
    evidence: BookReadEvidence | None
    match_basis: str
    read_scope: str
    title_hits: tuple[str, ...] = ()
    content_covered: bool = False
    suitability_basis: str = ""


@dataclass(frozen=True)
class EvaluatedVideo:
    """一条完成匹配评估的视频（页面读取范围已记录）。"""

    candidate: VideoCandidate
    stage: str
    score: int
    covered: bool
    evidence_level: ResourceEvidenceLevel
    match_basis: str
    read_scope: str
    title_hits: tuple[str, ...] = ()
    content_covered: bool = False
    suitability_basis: str = ""


@dataclass(frozen=True)
class MatchOutcome:
    """匹配结果：评估后的图书与视频、排除计数与证据说明。"""

    books: list[EvaluatedBook] = field(default_factory=list)
    videos: list[EvaluatedVideo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    book_excluded: int = 0
    video_excluded: int = 0
    rejected_videos: int = 0
    topic_mismatch: bool = False


def match_resources(
    analysis: ResourcesTermAnalysis,
    books: list[BookCandidate],
    videos: list[VideoCandidate],
    *,
    book_evidence: Mapping[str, BookReadEvidence] | None = None,
    book_sources: Mapping[str, int] | None = None,
    rejected_videos: int = 0,
) -> MatchOutcome:
    """把两个来源的候选评估为可组织的主线/补充素材（不做数量选择）。"""
    evidence_map = dict(book_evidence or {})
    terms = match_terms(analysis)
    notes: list[str] = []
    evaluated_books, book_excluded, dedupe_books = _evaluate_books(
        books, terms, analysis, evidence_map
    )
    evaluated_videos, video_excluded, dedupe_videos = _evaluate_videos(
        videos, terms, analysis
    )
    covered = [item for item in evaluated_books if item.covered] + [
        item for item in evaluated_videos if item.covered
    ]
    if not covered:
        return MatchOutcome(
            notes=[
                "检索回来的图书与视频标题、简介或目录都没有覆盖你的原始说法「"
                f"{analysis.original_phrase}」。",
                *_source_notes(book_sources or {}),
            ],
            book_excluded=book_excluded,
            video_excluded=video_excluded,
            rejected_videos=rejected_videos,
            topic_mismatch=True,
        )
    if book_excluded:
        notes.append(f"有 {book_excluded} 条书目因主题不匹配或重复版本被排除。")
    if dedupe_books:
        notes.append(f"有 {dedupe_books} 条同题书目按完整度去重（重复版本受控）。")
    if video_excluded:
        notes.append(f"有 {video_excluded} 条视频因主题不匹配或重复被排除。")
    if dedupe_videos:
        notes.append(f"有 {dedupe_videos} 条视频按 video_id 去重。")
    if rejected_videos:
        notes.append(
            f"有 {rejected_videos} 条搜索结果未通过哔哩哔哩元数据核对"
            "（不可见或取不到元数据），已移除。"
        )
    notes.extend(_source_notes(dict(book_sources or {})))
    return MatchOutcome(
        books=evaluated_books,
        videos=evaluated_videos,
        notes=notes,
        book_excluded=book_excluded,
        video_excluded=video_excluded,
        rejected_videos=rejected_videos,
    )


def cover_original_phrase(
    analysis: ResourcesTermAnalysis, items: Sequence[Any]
) -> bool:
    """条目里是否至少有一条真正覆盖了本轮原词（终态前的最后一道门）。"""
    terms = match_terms(analysis)
    for item in items:
        text = getattr(item, "title", "")
        if _covers(text, (analysis.original_phrase,)):
            return True
        match_basis = getattr(item, "match_basis", "")
        if match_basis and _covers(match_basis, terms):
            return True
    return False


def match_terms(analysis: ResourcesTermAnalysis) -> tuple[str, ...]:
    """参与主题匹配的词：原词优先，扩展词只作补充。"""
    terms = [analysis.original_phrase.strip(), analysis.normalized_term.strip()]
    terms.extend(analysis.expansions)
    return tuple(dict.fromkeys(term for term in terms if term))


# ---------------------------------------------------------------------------
# 图书评估
# ---------------------------------------------------------------------------


def _evaluate_books(
    books: list[BookCandidate],
    terms: tuple[str, ...],
    analysis: ResourcesTermAnalysis,
    evidence_map: Mapping[str, BookReadEvidence],
) -> tuple[list[EvaluatedBook], int, int]:
    kept: dict[str, EvaluatedBook] = {}
    excluded = 0
    deduped = 0
    for candidate in books:
        evidence = evidence_map.get(candidate.url)
        evaluated = _evaluate_book(candidate, terms, analysis, evidence)
        if not evaluated.covered:
            excluded += 1
            continue
        key = _dedupe_key(candidate)
        existing = kept.get(key)
        if existing is None:
            kept[key] = evaluated
            continue
        deduped += 1
        if _book_completeness(evaluated) < _book_completeness(existing):
            kept[key] = evaluated
    return list(kept.values()), excluded, deduped


def _evaluate_book(
    candidate: BookCandidate,
    terms: tuple[str, ...],
    analysis: ResourcesTermAnalysis,
    evidence: BookReadEvidence | None,
) -> EvaluatedBook:
    title_hits = tuple(term for term in terms if term and _covers(candidate.title, (term,)))
    content_text = _evidence_text(evidence)
    content_hits = (
        tuple(term for term in terms if term and _covers(content_text, (term,)))
        if content_text
        else ()
    )
    covered = bool(title_hits or content_hits)
    evidence_level = _book_evidence_level(evidence)
    stage = _stage_from_markers(content_text)
    read_scope = evidence.scope if evidence is not None else TITLE_ONLY_SCOPE
    if evidence is not None and evidence.error:
        read_scope = f"尝试读取失败（{evidence.error}），未取得目录/简介"
    hits_text = "、".join(dict.fromkeys((*title_hits, *content_hits))) or "（无）"
    basis_parts = [f"命中「{hits_text}」"]
    if title_hits:
        basis_parts.append("标题命中")
    if content_hits:
        basis_parts.append("已读内容命中")
    if content_text:
        basis_parts.append(f"已读内容摘录：{content_text[:500]}")
    suitability = _suitability_basis(content_text, analysis)
    basis_parts.append(suitability or "已读内容未确认本轮先修与目的适配")
    basis_parts.append(f"实际读取：{read_scope}")
    basis_parts.append(f"来源：{SOURCE_LABELS.get(candidate.source, candidate.source)}")
    score = _book_score(candidate, analysis, title_hits, content_hits, evidence_level, stage)
    return EvaluatedBook(
        candidate=candidate,
        stage=stage,
        score=score,
        covered=covered,
        evidence_level=evidence_level,
        evidence=evidence,
        match_basis="；".join(basis_parts) + "。",
        read_scope=read_scope,
        title_hits=title_hits,
        content_covered=bool(content_hits) and evidence_level is not ResourceEvidenceLevel.TITLE,
        suitability_basis=suitability,
    )


def _book_score(
    candidate: BookCandidate,
    analysis: ResourcesTermAnalysis,
    title_hits: tuple[str, ...],
    content_hits: tuple[str, ...],
    evidence_level: ResourceEvidenceLevel,
    stage: str,
) -> int:
    score = 0
    if analysis.original_phrase and _covers(candidate.title, (analysis.original_phrase,)):
        score += 3
    elif analysis.expansions and _covers(candidate.title, tuple(analysis.expansions)):
        score += 2
    score += min(len(content_hits), 3)
    if evidence_level is ResourceEvidenceLevel.CATALOG:
        score += 3
    elif evidence_level is ResourceEvidenceLevel.INTRO:
        score += 2
    if candidate.isbn:
        score += 1
    if candidate.publisher:
        score += 1
    if candidate.source == OPENLIBRARY_SOURCE:
        score += 1
    score += _level_fit(analysis.level, stage)
    return score


def _book_completeness(item: EvaluatedBook) -> tuple[int, int, int, int]:
    """重复版本去重时保留更完整、证据更强的一条。"""
    return (
        0 if item.evidence_level is ResourceEvidenceLevel.CATALOG else 1,
        0 if item.evidence_level is ResourceEvidenceLevel.INTRO else 1,
        0 if item.candidate.isbn else 1,
        0 if item.candidate.publisher else 1,
    )


def _book_evidence_level(evidence: BookReadEvidence | None) -> ResourceEvidenceLevel:
    if evidence is None or evidence.error:
        return ResourceEvidenceLevel.TITLE
    if evidence.catalog or evidence.subjects:
        return ResourceEvidenceLevel.CATALOG
    if evidence.description:
        return ResourceEvidenceLevel.INTRO
    return ResourceEvidenceLevel.TITLE


def _evidence_text(evidence: BookReadEvidence | None) -> str:
    if evidence is None or evidence.error:
        return ""
    return " ".join(
        [
            evidence.description,
            *evidence.subjects,
            *evidence.catalog,
        ]
    ).strip()


# ---------------------------------------------------------------------------
# 视频评估
# ---------------------------------------------------------------------------


def _evaluate_videos(
    videos: list[VideoCandidate],
    terms: tuple[str, ...],
    analysis: ResourcesTermAnalysis,
) -> tuple[list[EvaluatedVideo], int, int]:
    kept: list[EvaluatedVideo] = []
    excluded = 0
    deduped = 0
    seen: set[str] = set()
    for candidate in videos:
        if candidate.video_id in seen:
            excluded += 1
            deduped += 1
            continue
        evaluated = _evaluate_video(candidate, terms, analysis)
        if not evaluated.covered:
            excluded += 1
            continue
        seen.add(candidate.video_id)
        kept.append(evaluated)
    return kept, excluded, deduped


def _evaluate_video(
    candidate: VideoCandidate,
    terms: tuple[str, ...],
    analysis: ResourcesTermAnalysis,
) -> EvaluatedVideo:
    title_hits = tuple(term for term in terms if term and _covers(candidate.title, (term,)))
    description = candidate.description.strip()
    content_hits = (
        tuple(term for term in terms if term and _covers(description, (term,)))
        if description
        else ()
    )
    covered = bool(title_hits or content_hits)
    read_scope = (
        "哔哩哔哩公开视频页：标题、作者、发布时间、时长、简介（未观看视频）"
        if description
        else "哔哩哔哩公开视频页：标题、作者、发布时间、时长（该页未提供简介）"
    )
    evidence_level = (
        ResourceEvidenceLevel.INTRO if description else ResourceEvidenceLevel.TITLE
    )
    stage = _stage_from_markers(description)
    hits_text = "、".join(dict.fromkeys((*title_hits, *content_hits))) or "（无）"
    basis_parts = [f"命中「{hits_text}」"]
    basis_parts.append(f"实际读取：{read_scope}")
    basis_parts.append(f"来源：{SOURCE_LABELS[BILIBILI_SOURCE]}")
    if description:
        basis_parts.append(f"已读简介摘录：{description[:500]}")
    suitability = _suitability_basis(description, analysis)
    basis_parts.append(suitability or "已读简介未确认本轮先修与目的适配")
    score = _video_score(candidate, analysis, title_hits, content_hits, evidence_level, stage)
    return EvaluatedVideo(
        candidate=candidate,
        stage=stage,
        score=score,
        covered=covered,
        evidence_level=evidence_level,
        match_basis="；".join(basis_parts) + "。",
        read_scope=read_scope,
        title_hits=title_hits,
        content_covered=bool(content_hits),
        suitability_basis=suitability,
    )


def _video_score(
    candidate: VideoCandidate,
    analysis: ResourcesTermAnalysis,
    title_hits: tuple[str, ...],
    content_hits: tuple[str, ...],
    evidence_level: ResourceEvidenceLevel,
    stage: str,
) -> int:
    score = 0
    if analysis.original_phrase and _covers(candidate.title, (analysis.original_phrase,)):
        score += 3
    elif analysis.expansions and _covers(candidate.title, tuple(analysis.expansions)):
        score += 2
    score += min(len(content_hits), 2)
    if evidence_level is ResourceEvidenceLevel.INTRO:
        score += 2
    if candidate.uploader:
        score += 1
    if candidate.duration_seconds:
        score += 1
    # 公开点赞只作同阶段取舍的弱信号，绝不当作质量或适配结论。
    if candidate.like_count is not None and candidate.like_count >= POPULAR_LIKE_THRESHOLD:
        score += 1
    score += _level_fit(analysis.level, stage)
    return score


# ---------------------------------------------------------------------------
# 阶段与层次（与既有模块共用同一套标记规则）
# ---------------------------------------------------------------------------


def _stage_from_markers(text: str) -> str:
    """阶段只由可读文字标记判断；时长/点赞不参与适配结论。"""
    beginner = _marker_hits(text, BEGINNER_MARKERS)
    advanced = _marker_hits(text, ADVANCED_MARKERS)
    if beginner > advanced:
        return STAGE_BEGINNER
    if advanced > beginner:
        return STAGE_ADVANCED
    if _marker_hits(text, ("基础", "基础知识", "foundation", "fundamentals", "basic")):
        return STAGE_FOUNDATION
    return STAGE_UNKNOWN


def _suitability_basis(text: str, analysis: ResourcesTermAnalysis) -> str:
    """仅用已读内容中的明确适用线索；无先修信息时不替用户猜测。"""
    if re.search(
        r"不(?:适合|适用|面向|建议)[^。；;\n]{0,12}(?:入门|初学|零基础|基础)"
        r"|(?:需要|要求|必须|先掌握|先学|先修)[^。；;\n]{0,40}"
        r"|not\s+(?:for|suitable)|prerequisites?|requires?\s|prior\s+knowledge",
        text, re.IGNORECASE,
    ):
        # 明确先修不能只凭“有点基础”等模糊自述放行，保留候选等待核对。
        return ""
    stage = _stage_from_markers(text)
    level = analysis.level
    if level is None and analysis.goal_kind is ResourcesGoalKind.QUICK_CONCEPT:
        level = ResourcesLevel.BEGINNER
    allowed = {
        ResourcesLevel.BEGINNER: (STAGE_BEGINNER,),
        ResourcesLevel.BASIC: (STAGE_BEGINNER, STAGE_FOUNDATION),
        ResourcesLevel.ADVANCED: (STAGE_BEGINNER, STAGE_FOUNDATION, STAGE_ADVANCED),
    }
    if level is None or stage not in allowed[level]:
        return ""
    if analysis.goal_kind is ResourcesGoalKind.EXAM_PREP and not _marker_hits(
        text, ("考试", "备考", "题型", "习题", "exam", "exercises")
    ):
        return ""
    return f"已读内容明确标为「{stage}」，与本轮层次相符" + (
        "；已读内容含考试或习题线索" if analysis.goal_kind is ResourcesGoalKind.EXAM_PREP else ""
    )


def _marker_hits(text: str, markers: tuple[str, ...]) -> int:
    """标记命中数：中文按子串，拉丁词按词边界（避免 Perspective 命中 pro）。"""
    lowered = text.lower()
    hits = 0
    for marker in markers:
        if any("\u4e00" <= char <= "\u9fff" for char in marker):
            if marker in text:
                hits += 1
            continue
        if re.search(rf"\b{re.escape(marker)}\b", lowered):
            hits += 1
    return hits


def _level_fit(level: ResourcesLevel | None, stage: str) -> int:
    """层次与阶段的契合度（只影响选哪些条目，不改变由浅入深的排列）。"""
    if level is None:
        return 0
    if level is ResourcesLevel.BEGINNER:
        return {STAGE_BEGINNER: 2, STAGE_FOUNDATION: 0, STAGE_ADVANCED: -3}.get(stage, 0)
    if level is ResourcesLevel.ADVANCED:
        return {STAGE_BEGINNER: 0, STAGE_FOUNDATION: 1, STAGE_ADVANCED: 2}.get(stage, 0)
    return {STAGE_BEGINNER: 1, STAGE_FOUNDATION: 1, STAGE_ADVANCED: 0}.get(stage, 0)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _covers(text: str, terms: tuple[str, ...]) -> bool:
    """文本是否覆盖某个术语：中文按子串，拉丁词按词元（避免 the 之类误命中）。"""
    normalized = _normalize(text)
    for term in terms:
        if not term:
            continue
        if any("\u4e00" <= char <= "\u9fff" for char in term):
            if term in text:
                return True
            continue
        tokens = _normalize(term).split()
        if tokens and all(token in normalized.split() for token in tokens):
            return True
    return False


def _normalize(value: str) -> str:
    lowered = value.lower()
    buffer = [char if (char.isalnum() or char.isspace()) else " " for char in lowered]
    return " ".join("".join(buffer).split())


def _dedupe_key(candidate: BookCandidate) -> str:
    creators = "、".join(sorted(candidate.creators[:2]))
    return f"{_normalize(candidate.title)}|{_normalize(creators)}"


def _source_notes(sources: Mapping[str, int]) -> list[str]:
    """逐来源如实说明取到多少条（未命中的来源也说明，不用其他来源冒充）。"""
    return [
        f"{SOURCE_LABELS.get(source, source)} 返回 {count} 条候选。"
        for source, count in sources.items()
    ]


__all__ = [
    "EvaluatedBook",
    "EvaluatedVideo",
    "MatchOutcome",
    "POPULAR_LIKE_THRESHOLD",
    "SOURCE_LABELS",
    "TITLE_ONLY_SCOPE",
    "cover_original_phrase",
    "match_resources",
    "match_terms",
]
