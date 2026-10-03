"""``resources.organize``：按目的把已匹配候选组织成主线与补充。

组织规则（与 ``docs/workflow/daily-workflows.md`` 的学习资料段落一致）：

1. **目标决定比例**：快速理解概念只立 1 条主线；备考按「教材主线 + 视频
   讲解」立 2 条；系统学习立 3 条（两书一视频优先，按证据补足）。目标未知
   时按 2 条主线保守组织，绝不默认凑数量。
2. **证据门决定角色**：读取到目录/简介（``catalog``/``intro``）的候选才能
   进主线；只有标题、时长、点赞等弱信号的条目一律放补充/候选，并如实标注。
3. **数量条件**：用户明确的数量与媒介在计划里已成硬条件，这里只按目标数
   收敛；不足时如实报差多少，不虚构补足。
4. **主线未确认**：没有候选能过证据门时，``path_verified=False``，输出只
   列补充/候选，正文不称「完整路径已核实」。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from bridges.resources.contracts import (
    GOAL_KIND_LABELS,
    STAGE_ORDER,
    ResourceEvidenceLevel,
    ResourceItem,
    ResourceKind,
    ResourceRole,
    ResourcesGoalKind,
    ResourcesQueryPlan,
    ResourcesTermAnalysis,
    format_duration,
)
from bridges.resources.matching import (
    SOURCE_LABELS,
    EvaluatedBook,
    EvaluatedVideo,
    MatchOutcome,
)
from bridges.resources.sources import BILIBILI_SOURCE

#: 证据层次的排序权重（主线选择时目录 > 简介 > 标题）。
_EVIDENCE_ORDER: dict[ResourceEvidenceLevel, int] = {
    ResourceEvidenceLevel.CATALOG: 0,
    ResourceEvidenceLevel.INTRO: 1,
    ResourceEvidenceLevel.TITLE: 2,
}

#: 同类条目的阶段内排序（图书是结构化主干，同阶段排在视频前）。
_KIND_ORDER: dict[ResourceKind, int] = {ResourceKind.BOOK: 0, ResourceKind.VIDEO: 1}

#: 硬失败分类（与 sources 的记录状态一致）。
_HARD_FAILURES = frozenset({"error", "timeout", "rate_limited"})


@dataclass(frozen=True)
class OrganizeOutcome:
    """组织结果：条目、证据说明、主线是否确认与整轮失败描述。"""

    items: list[ResourceItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    path_verified: bool = False
    topic_mismatch: bool = False
    failure: dict[str, Any] | None = None


def organize_resources(
    analysis: ResourcesTermAnalysis,
    plan: ResourcesQueryPlan,
    match: MatchOutcome,
    *,
    book_records: list[Mapping[str, Any]],
    video_records: list[Mapping[str, Any]],
) -> OrganizeOutcome:
    """把匹配结果组织成含角色与顺序的清单（空结果与硬失败也如实收敛）。"""
    failure = detect_hard_failure(
        book_records=book_records,
        video_records=video_records,
        has_candidates=bool(match.books or match.videos),
    )
    if failure is not None:
        return OrganizeOutcome(
            notes=match.notes,
            failure=failure,
        )
    items, path_verified = select_items(analysis, plan, match)
    notes = list(match.notes)
    notes.extend(_record_gap_notes(book_records, video_records))
    if items:
        counts = _counts(items)
        notes.append(
            f"本轮按「{_goal_label(analysis.goal_kind)}」目标："
            f"主线 {counts['main']} 条、补充 {counts['supplement']} 条"
            f"（目标 {plan.target_books} 本书 + {plan.target_videos} 条视频，"
            f"实际给出 {counts['book']} 本 + {counts['video']} 条）。"
        )
        notes.extend(_shortfall_notes(counts, plan))
        if not path_verified:
            notes.append(
                "主线所需的先修/覆盖证据未在本轮候选中确认："
                "以下条目按补充/候选列出，不称完整路径已核实。"
            )
    if analysis.level_basis:
        notes.append(f"学习层次依据：{analysis.level_basis}。")
    for assumption in analysis.assumptions:
        notes.append(f"本轮假设：{assumption}")
    return OrganizeOutcome(
        items=items,
        notes=notes,
        path_verified=path_verified,
        topic_mismatch=match.topic_mismatch,
    )


def select_items(
    analysis: ResourcesTermAnalysis,
    plan: ResourcesQueryPlan,
    match: MatchOutcome,
) -> tuple[list[ResourceItem], bool]:
    """按目标与证据选主线/补充并排出由浅入深的顺序。"""
    eligible_books = [book for book in match.books if _eligible(book)]
    eligible_videos = [video for video in match.videos if _eligible(video)]
    main_books, main_videos = _pick_main(
        analysis.goal_kind,
        eligible_books,
        eligible_videos,
        plan=plan,
    )
    chosen_books = {id(book) for book in main_books}
    chosen_videos = {id(video) for video in main_videos}
    supplement_books = [
        *[book for book in eligible_books if id(book) not in chosen_books],
        *[book for book in match.books if not _eligible(book)],
    ]
    supplement_videos = [
        *[video for video in eligible_videos if id(video) not in chosen_videos],
        *[video for video in match.videos if not _eligible(video)],
    ]
    book_slots = max(0, plan.target_books - len(main_books))
    video_slots = max(0, plan.target_videos - len(main_videos))
    supplements = _merge_supplements(
        supplement_books[:book_slots],
        supplement_videos[:video_slots],
    )
    entries: list[tuple[ResourceRole, EvaluatedBook | EvaluatedVideo]] = [
        *[(ResourceRole.MAIN, book) for book in main_books],
        *[(ResourceRole.MAIN, video) for video in main_videos],
        *[(ResourceRole.SUPPLEMENT, entry) for entry in supplements],
    ]
    ordered = sorted(entries, key=_entry_order)
    items = [
        _to_item(analysis, role, entry, order=index)
        for index, (role, entry) in enumerate(ordered, start=1)
    ]
    path_verified = bool(main_books or main_videos)
    return items, path_verified


def detect_hard_failure(
    *,
    book_records: list[Mapping[str, Any]],
    video_records: list[Mapping[str, Any]],
    has_candidates: bool,
) -> dict[str, Any] | None:
    """两条检索都没有可用答复、且至少一处硬失败时的整轮失败描述。

    「可用答复」指某条来源真的跑完并如实给出了结果（成功或确实没有）；
    只要存在这样一条记录，整轮就不判失败——失败只作为证据缺口呈现。
    """
    if has_candidates:
        return None
    records = [*book_records, *video_records]
    if not records or any(
        str(record.get("status")) in {"success", "empty"} for record in records
    ):
        return None
    for node, group in (
        ("resources.search_books", book_records),
        ("resources.search_videos", video_records),
    ):
        for record in group:
            if str(record.get("status")) in _HARD_FAILURES:
                return {
                    "node": node,
                    "code": str(record.get("error_code") or "resources_search_failed"),
                    "message": str(
                        record.get("error_message") or "学习资料检索失败，请稍后重试。"
                    ),
                    "retryable": bool(record.get("retryable", True)),
                }
    return None


# ---------------------------------------------------------------------------
# 主线与补充
# ---------------------------------------------------------------------------


def _pick_main(
    goal_kind: ResourcesGoalKind | None,
    books: list[EvaluatedBook],
    videos: list[EvaluatedVideo],
    *,
    plan: ResourcesQueryPlan,
) -> tuple[list[EvaluatedBook], list[EvaluatedVideo]]:
    best_books = sorted(books, key=_book_sort_key)
    best_videos = sorted(videos, key=_video_sort_key)
    allow_books = plan.target_books > 0
    allow_videos = plan.target_videos > 0
    main_books: list[EvaluatedBook] = []
    main_videos: list[EvaluatedVideo] = []
    if goal_kind is ResourcesGoalKind.QUICK_CONCEPT:
        # 快速理解概念：证据最强的单条即可，视频更快建立轮廓。
        if allow_videos and best_videos:
            main_videos.append(best_videos[0])
        elif allow_books and best_books:
            main_books.append(best_books[0])
        return main_books, main_videos
    if allow_books and best_books:
        main_books.append(best_books[0])
    if allow_videos and best_videos:
        main_videos.append(best_videos[0])
    if goal_kind is ResourcesGoalKind.SYSTEMATIC:
        remaining_books = best_books[len(main_books):]
        remaining_videos = best_videos[len(main_videos):]
        extra_book = remaining_books[0] if allow_books and remaining_books else None
        extra_video = remaining_videos[0] if allow_videos and remaining_videos else None
        if extra_book is not None and (
            extra_video is None or extra_book.score >= extra_video.score
        ):
            main_books.append(extra_book)
        elif extra_video is not None:
            main_videos.append(extra_video)
    return main_books, main_videos


def _merge_supplements(
    books: list[EvaluatedBook], videos: list[EvaluatedVideo]
) -> list[EvaluatedBook | EvaluatedVideo]:
    merged: list[EvaluatedBook | EvaluatedVideo] = [*books, *videos]
    return sorted(
        merged,
        key=lambda entry: (
            _EVIDENCE_ORDER[entry.evidence_level],
            -entry.score,
        ),
    )


def _eligible(entry: EvaluatedBook | EvaluatedVideo) -> bool:
    return entry.covered and entry.evidence_level is not ResourceEvidenceLevel.TITLE


def _book_sort_key(book: EvaluatedBook) -> tuple[int, int, str]:
    return (
        _EVIDENCE_ORDER[book.evidence_level],
        -book.score,
        book.candidate.title,
    )


def _video_sort_key(video: EvaluatedVideo) -> tuple[int, int, str]:
    return (
        _EVIDENCE_ORDER[video.evidence_level],
        -video.score,
        video.candidate.title,
    )


def _entry_order(
    entry: tuple[ResourceRole, EvaluatedBook | EvaluatedVideo]
) -> tuple[int, int, int, str]:
    role, evaluated = entry
    kind = (
        ResourceKind.BOOK
        if isinstance(evaluated, EvaluatedBook)
        else ResourceKind.VIDEO
    )
    return (
        STAGE_ORDER.index(evaluated.stage),
        _KIND_ORDER[kind],
        0 if role is ResourceRole.MAIN else 1,
        evaluated.candidate.title,
    )


# ---------------------------------------------------------------------------
# 条目构造
# ---------------------------------------------------------------------------


def _to_item(
    analysis: ResourcesTermAnalysis,
    role: ResourceRole,
    entry: EvaluatedBook | EvaluatedVideo,
    *,
    order: int,
) -> ResourceItem:
    if isinstance(entry, EvaluatedBook):
        return _book_item(analysis, role, entry, order=order)
    return _video_item(analysis, role, entry, order=order)


def _book_item(
    analysis: ResourcesTermAnalysis,
    role: ResourceRole,
    entry: EvaluatedBook,
    *,
    order: int,
) -> ResourceItem:
    candidate = entry.candidate
    purpose = _purpose(role, analysis.goal_kind, ResourceKind.BOOK, entry)
    reason_parts = [f"作为「{entry.stage}」阶段的{_role_label(role)}图书"]
    if candidate.publisher:
        reason_parts.append(f"由 {candidate.publisher} 出版")
    if candidate.year:
        reason_parts.append(f"{candidate.year} 年版")
    reason_parts.append(f"实际读取：{entry.read_scope}")
    if analysis.goal:
        reason_parts.append(f"与你的目的（{analysis.goal}）一致")
    reason_parts.append("未阅读正文，只依据目录/简介与书目信息判断")
    unverified = ["未阅读正文，难度与写法只按已读内容与书目信息判断"]
    if entry.evidence_level is ResourceEvidenceLevel.TITLE:
        unverified.append("仅有标题与书目元数据，未读取目录/简介，不能证明先修与覆盖")
    if not candidate.isbn:
        unverified.append("来源未给出 ISBN")
    if not candidate.publisher:
        unverified.append("来源未给出出版社")
    return ResourceItem(
        order=order,
        kind=ResourceKind.BOOK,
        title=candidate.title,
        creator="、".join(candidate.creators) if candidate.creators else None,
        year=candidate.year,
        source=candidate.source,
        url=candidate.url,
        stage=entry.stage,
        reason_zh="；".join(reason_parts) + f"；本轮角色：{purpose}。",
        match_basis=entry.match_basis,
        role=role,
        purpose_zh=purpose,
        evidence_level=entry.evidence_level,
        read_scope=entry.read_scope,
        publisher=candidate.publisher,
        isbn=candidate.isbn,
        unverified=unverified,
    )


def _video_item(
    analysis: ResourcesTermAnalysis,
    role: ResourceRole,
    entry: EvaluatedVideo,
    *,
    order: int,
) -> ResourceItem:
    candidate = entry.candidate
    purpose = _purpose(role, analysis.goal_kind, ResourceKind.VIDEO, entry)
    reason_parts = [
        f"「{entry.stage}」阶段的{_role_label(role)}视频，元数据取自哔哩哔哩公开接口"
    ]
    if candidate.uploader:
        reason_parts.append(f"UP 主：{candidate.uploader}")
    if candidate.duration_seconds:
        reason_parts.append(f"时长 {format_duration(candidate.duration_seconds)}")
    if candidate.published_at:
        reason_parts.append(f"发布于 {candidate.published_at.date().isoformat()}")
    counters = _counters_text(candidate)
    if counters:
        reason_parts.append(f"{counters}（平台计数，不代表质量结论）")
    if analysis.goal:
        reason_parts.append(f"与你的目的（{analysis.goal}）一致")
    reason_parts.append("未观看，不对讲授质量下结论")
    unverified = [
        "未观看视频，只核对公开元数据（标题、作者、发布时间、时长、简介、公开计数）"
    ]
    if entry.evidence_level is ResourceEvidenceLevel.TITLE:
        unverified.append("该页未提供简介，只有标题/时长/公开计数等弱信号")
    return ResourceItem(
        order=order,
        kind=ResourceKind.VIDEO,
        title=candidate.title,
        creator=candidate.uploader,
        year=candidate.published_at.year if candidate.published_at else None,
        source=BILIBILI_SOURCE,
        url=candidate.url,
        stage=entry.stage,
        reason_zh="；".join(reason_parts) + f"；本轮角色：{purpose}。",
        match_basis=entry.match_basis,
        role=role,
        purpose_zh=purpose,
        evidence_level=entry.evidence_level,
        read_scope=entry.read_scope,
        duration_seconds=candidate.duration_seconds,
        published_at=candidate.published_at,
        view_count=candidate.view_count,
        like_count=candidate.like_count,
        unverified=unverified,
    )


def _purpose(
    role: ResourceRole,
    goal_kind: ResourcesGoalKind | None,
    kind: ResourceKind,
    entry: EvaluatedBook | EvaluatedVideo,
) -> str:
    if role is ResourceRole.MAIN:
        if goal_kind is ResourcesGoalKind.QUICK_CONCEPT:
            return "快速建立概念轮廓：先看这一条，再按需要深入"
        if goal_kind is ResourcesGoalKind.EXAM_PREP:
            return "备考主线：覆盖考试范围的要点与题型"
        if goal_kind is ResourcesGoalKind.SYSTEMATIC:
            if kind is ResourceKind.BOOK:
                return "系统学习主线教材：按先修顺序建立知识框架"
            return "系统学习主线讲解：配合教材巩固关键概念（未观看，按公开信息判断）"
        return "本轮主线：先建立可直接使用的知识框架"
    if entry.evidence_level is ResourceEvidenceLevel.TITLE:
        return "候选：仅有标题/时长等弱信号，未读取目录或简介，不能证明先修与覆盖"
    return "补充：主线之外的备选，用于查漏或换一种讲法"


def _role_label(role: ResourceRole) -> str:
    return "主线" if role is ResourceRole.MAIN else "补充"


def _counters_text(candidate: Any) -> str | None:
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


# ---------------------------------------------------------------------------
# 说明
# ---------------------------------------------------------------------------


def _record_gap_notes(
    book_records: list[Mapping[str, Any]], video_records: list[Mapping[str, Any]]
) -> list[str]:
    """逐来源如实标注硬失败（部分交付时也说明哪一侧缺口）。"""
    notes: list[str] = []
    for record in [*book_records, *video_records]:
        status = str(record.get("status") or "")
        if status not in _HARD_FAILURES:
            continue
        source = str(record.get("source") or "")
        label = SOURCE_LABELS.get(source, source)
        detail = str(record.get("error_message") or "").strip()
        notes.append(
            f"{label} 本轮状态：{status}" + (f"（{detail}）" if detail else "。")
        )
    return notes


def _counts(items: list[ResourceItem]) -> dict[str, int]:
    return {
        "main": sum(1 for item in items if item.role is ResourceRole.MAIN),
        "supplement": sum(
            1 for item in items if item.role is ResourceRole.SUPPLEMENT
        ),
        "book": sum(1 for item in items if item.kind is ResourceKind.BOOK),
        "video": sum(1 for item in items if item.kind is ResourceKind.VIDEO),
    }


def _shortfall_notes(counts: dict[str, int], plan: ResourcesQueryPlan) -> list[str]:
    missing: list[str] = []
    if counts["book"] < plan.target_books:
        missing.append(f"图书还差 {plan.target_books - counts['book']} 本")
    if counts["video"] < plan.target_videos:
        missing.append(f"视频还差 {plan.target_videos - counts['video']} 条")
    if not missing:
        return []
    return [
        "、".join(missing)
        + "：来源没有返回同时满足主题与证据要求的条目，我没有虚构补足。"
    ]


def _goal_label(goal_kind: ResourcesGoalKind | None) -> str:
    if goal_kind is None:
        return "目标未知（保守搭配）"
    return GOAL_KIND_LABELS.get(goal_kind, "本轮目标")


__all__ = [
    "OrganizeOutcome",
    "detect_hard_failure",
    "organize_resources",
    "select_items",
]
