"""``resources.rank``：主题门 → 阶段判定 → 由浅入深的单一清单。

排序是确定性的，规则全部来自真实元数据：

1. **主题门**：条目标题里必须出现本轮原词或它的英文扩展词，否则排除并计数；
   一条都没覆盖时判定为主题不匹配，停止推荐并请用户澄清，绝不凑数量。
2. **阶段**：标题里的入门／进阶标记决定条目落在入门、打基础还是进阶；视频
   在标记相同时用真实时长兜底。
3. **层次对齐**：本轮学习层次影响「选哪些条目」——零基础不会被推进阶书，
   进阶不会只拿到科普视频；清单本身仍按由浅入深排列。
4. **数量**：默认两本书 + 三条视频；不足时按实际数量收敛并如实说明。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bridges.resources.contracts import (
    LEVEL_LABELS,
    STAGE_ADVANCED,
    STAGE_BEGINNER,
    STAGE_FOUNDATION,
    STAGE_ORDER,
    ResourceItem,
    ResourceKind,
    ResourcesLevel,
    ResourcesQueryPlan,
    ResourcesTermAnalysis,
    format_duration,
)
from bridges.resources.lexicon import ADVANCED_MARKERS, BEGINNER_MARKERS
from bridges.resources.sources import (
    BILIBILI_SOURCE,
    OPENALEX_SOURCE,
    OPENLIBRARY_SOURCE,
    BookCandidate,
    VideoCandidate,
)

#: 视频时长门槛（秒）：标记相同时按「先短后长、长讲解更靠后」判定阶段。
SHORT_VIDEO_SECONDS = 600
LONG_VIDEO_SECONDS = 1800

#: 点赞过此数视为「有公开反馈」（口碑证据的弱信号，只用于同阶段取舍）。
POPULAR_LIKE_THRESHOLD = 100

#: 同类条目的阶段内排序（图书是结构化主干，同阶段排在视频前）。
_KIND_ORDER: dict[str, int] = {ResourceKind.BOOK: 0, ResourceKind.VIDEO: 1}

#: 书目来源优先级（同一书目重复出现时保留书目信息更完整的那条）。
_SOURCE_ORDER: dict[str, int] = {OPENLIBRARY_SOURCE: 0, OPENALEX_SOURCE: 1}

#: 来源 → 中文名（正文与证据说明共用一份；未列出的来源原样显示）。
SOURCE_LABELS: dict[str, str] = {
    OPENLIBRARY_SOURCE: "Open Library 书目",
    OPENALEX_SOURCE: "OpenAlex 图书记录",
    BILIBILI_SOURCE: "哔哩哔哩公开视频页",
}


@dataclass(frozen=True)
class _Scored:
    """一条候选的排序中间结果（分数只用于选择，不呈现给用户）。"""

    stage: str
    score: int
    item: ResourceItem


@dataclass(frozen=True)
class RankOutcome:
    """排名结果：有序条目、证据说明与主题不匹配标记。"""

    items: list[ResourceItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    topic_mismatch: bool = False


def rank_resources(
    analysis: ResourcesTermAnalysis,
    plan: ResourcesQueryPlan,
    books: list[BookCandidate],
    videos: list[VideoCandidate],
    *,
    book_sources: dict[str, int] | None = None,
    rejected_videos: int = 0,
) -> RankOutcome:
    """把两个来源的图书与已核对的视频排成一条由浅入深的学习路径。"""
    notes: list[str] = []
    terms = _match_terms(analysis)
    book_matches, book_excluded = _match_books(books, terms)
    video_matches, video_excluded = _match_videos(videos, terms)
    if not book_matches and not video_matches:
        return RankOutcome(
            items=[],
            notes=[
                "检索回来的图书与视频标题都没有覆盖你的原始说法「"
                f"{analysis.original_phrase}」。",
                *_source_notes(book_sources or {}),
            ],
            topic_mismatch=True,
        )
    selected_books = _select(
        [_scored_book(candidate, analysis) for candidate in book_matches],
        plan.target_books,
    )
    selected_videos = _select(
        [_scored_video(candidate, analysis) for candidate in video_matches],
        plan.target_videos,
    )
    items = _order([*selected_books, *selected_videos])
    notes.extend(_count_notes(len(selected_books), len(selected_videos), plan, analysis))
    notes.extend(_exclusion_notes(book_excluded, video_excluded, rejected_videos))
    notes.extend(_source_notes(book_sources or {}))
    notes.extend(_level_conflict_notes(analysis, items))
    if analysis.level_basis:
        notes.append(f"学习层次依据：{analysis.level_basis}。")
    return RankOutcome(items=items, notes=notes)


def cover_original_phrase(analysis: ResourcesTermAnalysis, items: list[ResourceItem]) -> bool:
    """条目里是否至少有一条真正覆盖了本轮原词（终态前的最后一道门）。"""
    terms = _match_terms(analysis)
    return any(_covers(item.title, terms) for item in items)


# ---------------------------------------------------------------------------
# 选择与排序
# ---------------------------------------------------------------------------


def _select(scored: list[_Scored], limit: int) -> list[_Scored]:
    """按分数取前若干条，但保持由浅入深的顺序供最终排列使用。"""
    if limit <= 0:
        return []
    ordered = sorted(
        scored,
        key=lambda entry: (-entry.score, _KIND_ORDER[entry.item.kind], entry.item.title),
    )
    chosen = ordered[:limit]
    return sorted(
        chosen,
        key=lambda entry: (
            STAGE_ORDER.index(entry.stage),
            _KIND_ORDER[entry.item.kind],
            entry.item.title,
        ),
    )


def _order(entries: list[_Scored]) -> list[ResourceItem]:
    """把所有选中条目排成单一的由浅入深清单，并重编号 ``order``。"""
    ordered = sorted(
        entries,
        key=lambda entry: (
            STAGE_ORDER.index(entry.stage),
            _KIND_ORDER[entry.item.kind],
            entry.item.title,
        ),
    )
    items: list[ResourceItem] = []
    for index, entry in enumerate(ordered, start=1):
        items.append(entry.item.model_copy(update={"order": index}))
    return items


# ---------------------------------------------------------------------------
# 图书
# ---------------------------------------------------------------------------


def _match_books(
    books: list[BookCandidate], terms: tuple[str, ...]
) -> tuple[list[BookCandidate], int]:
    """主题门 + 同一本书去重（保留书目信息更完整的那条）。"""
    kept: dict[str, BookCandidate] = {}
    excluded = 0
    for candidate in books:
        if not _covers(candidate.title, terms):
            excluded += 1
            continue
        key = _title_key(candidate.title)
        existing = kept.get(key)
        if existing is None or _book_rank(candidate) < _book_rank(existing):
            if existing is not None:
                excluded += 1
            kept[key] = candidate
        else:
            excluded += 1
    return list(kept.values()), excluded


def _book_rank(candidate: BookCandidate) -> tuple[int, int, int]:
    """去重时保留更完整的书目：先看来源优先级，再看 ISBN 与出版社。"""
    return (
        _SOURCE_ORDER.get(candidate.source, 9),
        0 if candidate.isbn else 1,
        0 if candidate.publisher else 1,
    )


def _scored_book(candidate: BookCandidate, analysis: ResourcesTermAnalysis) -> _Scored:
    terms = _match_terms(analysis)
    stage = _stage_from_markers(candidate.title, video_seconds=None)
    score = 0
    if analysis.original_phrase and _covers(candidate.title, (analysis.original_phrase,)):
        score += 3
    elif analysis.expansions and _covers(candidate.title, tuple(analysis.expansions)):
        score += 2
    if candidate.isbn:
        score += 2
    if candidate.publisher:
        score += 1
    if candidate.source == OPENLIBRARY_SOURCE:
        score += 1
    score += _level_fit(analysis.level, stage)
    return _Scored(
        stage=stage,
        score=score,
        item=ResourceItem(
            order=1,
            kind=ResourceKind.BOOK,
            title=candidate.title,
            creator="、".join(candidate.creators) if candidate.creators else None,
            year=candidate.year,
            source=candidate.source,
            url=candidate.url,
            stage=stage,
            reason_zh=_book_reason(candidate, analysis, stage),
            match_basis=_book_match_basis(candidate, terms),
            publisher=candidate.publisher,
            isbn=candidate.isbn,
            unverified=_book_unverified(candidate),
        ),
    )


def _book_reason(
    candidate: BookCandidate, analysis: ResourcesTermAnalysis, stage: str
) -> str:
    parts = [f"作为「{stage}」阶段的读书主干"]
    if candidate.publisher:
        parts.append(f"由 {candidate.publisher} 出版")
    if candidate.year:
        parts.append(f"{candidate.year} 年版")
    if candidate.isbn:
        parts.append(f"书目信息可核对（ISBN {candidate.isbn}）")
    else:
        parts.append("来源没有给出 ISBN，书目信息只到标题与年份")
    if analysis.goal:
        parts.append(f"与你的目的（{analysis.goal}）一致")
    parts.append("未阅读正文，只依据来源返回的书目元数据推荐")
    return "；".join(parts) + "。"


def _book_match_basis(candidate: BookCandidate, terms: tuple[str, ...]) -> str:
    hits = [term for term in terms if term and _covers(candidate.title, (term,))]
    source_label = SOURCE_LABELS.get(candidate.source, candidate.source)
    return f"命中「{'、'.join(hits)}」；来源：{source_label}。"


def _book_unverified(candidate: BookCandidate) -> list[str]:
    notes: list[str] = ["未阅读正文，难度与写法只按书目信息判断"]
    if not candidate.isbn:
        notes.append("来源未给出 ISBN")
    if not candidate.publisher:
        notes.append("来源未给出出版社")
    return notes


# ---------------------------------------------------------------------------
# 视频
# ---------------------------------------------------------------------------


def _match_videos(
    videos: list[VideoCandidate], terms: tuple[str, ...]
) -> tuple[list[VideoCandidate], int]:
    """主题门 + 同一视频去重（与图书同一条规则：**标题**必须覆盖主题词）。

    简介同样来自公开接口，但它不能替代标题：清单里每条视频都要能凭标题
    看出与本轮主题的关系（否则终态门与排序门会互相矛盾）。
    """
    kept: list[VideoCandidate] = []
    excluded = 0
    seen: set[str] = set()
    for candidate in videos:
        if candidate.video_id in seen:
            excluded += 1
            continue
        if not _covers(candidate.title, terms):
            excluded += 1
            continue
        seen.add(candidate.video_id)
        kept.append(candidate)
    return kept, excluded


def _scored_video(candidate: VideoCandidate, analysis: ResourcesTermAnalysis) -> _Scored:
    stage = _stage_from_markers(
        candidate.title, video_seconds=candidate.duration_seconds
    )
    score = 0
    if analysis.original_phrase and _covers(candidate.title, (analysis.original_phrase,)):
        score += 3
    elif analysis.expansions and _covers(candidate.title, tuple(analysis.expansions)):
        score += 2
    if candidate.uploader:
        score += 1
    if candidate.duration_seconds:
        score += 1
    if candidate.like_count is not None and candidate.like_count >= POPULAR_LIKE_THRESHOLD:
        # 公开反馈的弱信号（口碑证据）：只在同阶段内用于取舍，绝不当作质量结论。
        score += 1
    score += _level_fit(analysis.level, stage)
    return _Scored(
        stage=stage,
        score=score,
        item=ResourceItem(
            order=1,
            kind=ResourceKind.VIDEO,
            title=candidate.title,
            creator=candidate.uploader,
            year=candidate.published_at.year if candidate.published_at else None,
            source=BILIBILI_SOURCE,
            url=candidate.url,
            stage=stage,
            reason_zh=_video_reason(candidate, stage, analysis),
            match_basis=_video_match_basis(candidate, analysis),
            duration_seconds=candidate.duration_seconds,
            published_at=candidate.published_at,
            view_count=candidate.view_count,
            like_count=candidate.like_count,
            unverified=["未观看视频，只核对公开元数据（标题、作者、发布时间、时长、简介、公开计数）"],
        ),
    )


def _video_reason(
    candidate: VideoCandidate, stage: str, analysis: ResourcesTermAnalysis
) -> str:
    parts = [f"「{stage}」阶段的视频讲解，元数据取自哔哩哔哩公开接口"]
    if candidate.uploader:
        parts.append(f"UP 主：{candidate.uploader}")
    if candidate.duration_seconds:
        parts.append(f"时长 {format_duration(candidate.duration_seconds)}")
    if candidate.published_at:
        parts.append(f"发布于 {candidate.published_at.date().isoformat()}")
    counters = _counters_text(candidate)
    if counters:
        parts.append(f"{counters}（平台计数，不代表质量结论）")
    if analysis.goal:
        parts.append(f"与你的目的（{analysis.goal}）一致")
    parts.append("未观看，不对讲授质量下结论")
    return "；".join(parts) + "。"


def _counters_text(candidate: VideoCandidate) -> str | None:
    """公开计数的中文写法（万位取约数，明确标注这是平台计数）。"""
    parts: list[str] = []
    if candidate.view_count is not None:
        parts.append(f"播放 {_count_text(candidate.view_count)} 次")
    if candidate.like_count is not None:
        parts.append(f"点赞 {_count_text(candidate.like_count)} 次")
    return "、".join(parts) if parts else None


def _count_text(value: int) -> str:
    if value >= 10000:
        return f"约 {value / 10000:.1f} 万"
    return str(value)


def _video_match_basis(candidate: VideoCandidate, analysis: ResourcesTermAnalysis) -> str:
    hits = [term for term in _match_terms(analysis) if term and _covers(candidate.title, (term,))]
    return (
        f"标题命中「{'、'.join(hits)}」；来源：{SOURCE_LABELS[BILIBILI_SOURCE]}"
        f"（已用公开接口核对标题、作者、发布时间、时长与公开计数）。"
    )


# ---------------------------------------------------------------------------
# 阶段与层次
# ---------------------------------------------------------------------------


def _stage_from_markers(title: str, *, video_seconds: int | None) -> str:
    beginner = _marker_hits(title, BEGINNER_MARKERS)
    advanced = _marker_hits(title, ADVANCED_MARKERS)
    if beginner > advanced:
        return STAGE_BEGINNER
    if advanced > beginner:
        return STAGE_ADVANCED
    if video_seconds is not None:
        if video_seconds <= SHORT_VIDEO_SECONDS:
            return STAGE_BEGINNER
        if video_seconds >= LONG_VIDEO_SECONDS:
            return STAGE_ADVANCED
    return STAGE_FOUNDATION


def _marker_hits(title: str, markers: tuple[str, ...]) -> int:
    """标记命中数：中文按子串，拉丁词按词边界（避免 Perspective 命中 pro）。"""
    lowered = title.lower()
    hits = 0
    for marker in markers:
        if any("\u4e00" <= char <= "\u9fff" for char in marker):
            if marker in title:
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
        return {STAGE_BEGINNER: 2, STAGE_FOUNDATION: 0, STAGE_ADVANCED: -3}[stage]
    if level is ResourcesLevel.ADVANCED:
        return {STAGE_BEGINNER: 0, STAGE_FOUNDATION: 1, STAGE_ADVANCED: 2}[stage]
    return {STAGE_BEGINNER: 1, STAGE_FOUNDATION: 1, STAGE_ADVANCED: 0}[stage]


# ---------------------------------------------------------------------------
# 证据说明
# ---------------------------------------------------------------------------


def _count_notes(
    books: int,
    videos: int,
    plan: ResourcesQueryPlan,
    analysis: ResourcesTermAnalysis,
) -> list[str]:
    notes = [
        f"本轮实际取得 {books} 本书、{videos} 条视频"
        f"（目标 {plan.target_books} 本 + {plan.target_videos} 条）。"
    ]
    if books < plan.target_books or videos < plan.target_videos:
        missing: list[str] = []
        if books < plan.target_books:
            missing.append(f"图书还差 {plan.target_books - books} 本")
        if videos < plan.target_videos:
            missing.append(f"视频还差 {plan.target_videos - videos} 条")
        notes.append(
            "、".join(missing)
            + "：来源没有返回同时满足主题与层次的条目，我没有虚构补足。"
        )
    if analysis.goal:
        notes.append(f"本轮按你的学习目的（{analysis.goal}）搭配。")
    return notes


def _exclusion_notes(
    book_excluded: int, video_excluded: int, rejected_videos: int
) -> list[str]:
    notes: list[str] = []
    if book_excluded:
        notes.append(f"有 {book_excluded} 条书目因主题不匹配或重复被排除。")
    if video_excluded:
        notes.append(f"有 {video_excluded} 条视频因主题不匹配或重复被排除。")
    if rejected_videos:
        notes.append(
            f"有 {rejected_videos} 条搜索结果未通过哔哩哔哩元数据核对"
            "（不可见或取不到元数据），已移除。"
        )
    return notes


def _source_notes(sources: dict[str, int]) -> list[str]:
    """逐来源如实说明取到多少条（未命中的来源也说明，不用其他来源冒充）。"""
    return [
        f"{SOURCE_LABELS.get(source, source)} 返回 {count} 条候选。"
        for source, count in sources.items()
    ]


def _level_conflict_notes(
    analysis: ResourcesTermAnalysis, items: list[ResourceItem]
) -> list[str]:
    """层次与阶段明显冲突时如实说明（候选不足时仍给出，但绝不假装匹配）。"""
    level = analysis.level
    if level is None:
        return []
    conflicting = [item for item in items if _conflicts(level, item.stage)]
    if not conflicting:
        return []
    label = LEVEL_LABELS.get(level, "当前层次")
    stages = "、".join(sorted({item.stage for item in conflicting}))
    return [
        f"其中 {len(conflicting)} 条（{stages}）与你的层次（{label}）不完全匹配："
        "本主题下符合层次的条目不足，我没有因此少给条目，请按理由自行取舍。"
    ]


def _conflicts(level: ResourcesLevel, stage: str) -> bool:
    if level is ResourcesLevel.BEGINNER:
        return stage == STAGE_ADVANCED
    if level is ResourcesLevel.ADVANCED:
        return stage == STAGE_BEGINNER
    return False


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _match_terms(analysis: ResourcesTermAnalysis) -> tuple[str, ...]:
    """参与主题匹配的词：原词优先，扩展词只作补充。"""
    terms = [analysis.original_phrase.strip(), analysis.normalized_term.strip()]
    terms.extend(analysis.expansions)
    return tuple(dict.fromkeys(term for term in terms if term))


def _covers(text: str, terms: tuple[str, ...]) -> bool:
    """标题是否覆盖某个术语：中文按子串，拉丁词按词元（避免 the 之类误命中）。"""
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


def _title_key(title: str) -> str:
    return _normalize(title)


