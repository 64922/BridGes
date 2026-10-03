"""``resources.plan``：把解析结果变成真实可发送的最小查询词与目标数量。

三类学习目的决定默认数量与主线/补充比例，用户明确的数量或媒介是硬条件
（在计划阶段就被冻结），不默认凑 2 本 + 3 条：

- 快速理解概念：一本直接相关的图书加一条讲解视频，先建立正确轮廓；
- 备考复习：围绕考试范围给一条主线加视频讲解，便于按薄弱点回看；
- 系统学习：主线教材与补充讲解并行，按先修顺序推进。

两类查询分开构造，但共用同一个主词（原词永远在查询里，扩展词只作补充）。
候选请求量高于目标数量，因为核对与证据门会淘汰条目；目标数量不因候选不足
而降低——不足时如实报数。
"""

from __future__ import annotations

from bridges.resources.contracts import (
    ResourceMedia,
    ResourcesGoalKind,
    ResourcesQueryPlan,
    ResourcesTermAnalysis,
)
from bridges.resources.lexicon import VIDEO_QUERY_SUFFIX

#: 三类目的 → 默认（图书, 视频）目标；目标未知时按适度覆盖处理。
GOAL_TARGETS: dict[ResourcesGoalKind | None, tuple[int, int]] = {
    ResourcesGoalKind.QUICK_CONCEPT: (1, 1),
    ResourcesGoalKind.EXAM_PREP: (1, 2),
    ResourcesGoalKind.SYSTEMATIC: (2, 2),
    None: (2, 2),
}

#: 主线所需内容与先修要求（按目的声明，供证据门核对覆盖）。
GOAL_REQUIREMENTS: dict[ResourcesGoalKind | None, tuple[str, ...]] = {
    ResourcesGoalKind.QUICK_CONCEPT: ("概念覆盖", "低门槛（少前置要求）"),
    ResourcesGoalKind.EXAM_PREP: ("课程范围覆盖", "面向考试的要点与题型"),
    ResourcesGoalKind.SYSTEMATIC: ("先修递进", "主线教材", "配套讲解"),
    None: ("内容覆盖与先修适配",),
}

#: 候选请求上限（高于目标，核对与证据门淘汰后仍有机会凑到目标）。
BOOK_CANDIDATE_LIMIT = 8
VIDEO_CANDIDATE_LIMIT = 8

#: 实际深读（书目目录/简介、视频页面）的数量上限（运行预算内）。
READ_LIMIT_BOOKS = 3
READ_LIMIT_VIDEOS = 3

#: 视频查询没有层次时的兜底后缀。
DEFAULT_VIDEO_SUFFIX = "教程"

#: 书与视频两路检索的并行上限（共享运行预算；orchestration §10 初值）。
SEARCH_PARALLEL_LIMIT = 2


def plan_resources(analysis: ResourcesTermAnalysis) -> ResourcesQueryPlan:
    """生成本轮检索计划（图书查询与视频查询各一条，数量随目标/媒介/明确要求）。"""
    term = (analysis.normalized_term or analysis.original_phrase).strip()
    expansions = list(analysis.expansions[:1])
    book_query = _join([term, *expansions])
    suffix = (
        VIDEO_QUERY_SUFFIX.get(analysis.level, DEFAULT_VIDEO_SUFFIX)
        if analysis.level is not None
        else DEFAULT_VIDEO_SUFFIX
    )
    video_query = _join([term, suffix])
    media = analysis.media or ResourceMedia.BOTH
    target_books, target_videos = GOAL_TARGETS.get(analysis.goal_kind, (2, 2))
    if media is ResourceMedia.BOOKS:
        target_videos = 0
    elif media is ResourceMedia.VIDEOS:
        target_books = 0
    # 用户明确的数量是硬条件：覆盖目标推导与媒介默认。
    if analysis.requested_books is not None:
        target_books = analysis.requested_books
    if analysis.requested_videos is not None:
        target_videos = analysis.requested_videos
    rationale = (
        f"主题词保持你给的原始说法「{term}」"
        + (f"，英文写法「{expansions[0]}」只作为补充" if expansions else "")
        + f"；图书查询只发送主题词，视频查询额外带上层次提示（{'、'.join(suffix.split())}）"
        "以便找到与本轮层次相符的公开讲解。"
        + (
            f"目标数量按本轮目的取 {target_books} 本书 + {target_videos} 条视频"
            + (
                "（你明确的数量优先）"
                if analysis.requested_books is not None
                or analysis.requested_videos is not None
                else ""
            )
            + "。"
        )
    )
    return ResourcesQueryPlan(
        term=term,
        book_query=book_query,
        video_query=video_query,
        target_books=target_books,
        target_videos=target_videos,
        book_candidate_limit=BOOK_CANDIDATE_LIMIT,
        video_candidate_limit=VIDEO_CANDIDATE_LIMIT,
        expansions_used=expansions,
        rationale=rationale,
        goal_kind=analysis.goal_kind,
        media=media,
        main_requirements=list(
            GOAL_REQUIREMENTS.get(analysis.goal_kind, GOAL_REQUIREMENTS[None])
        ),
        parallel_limit=SEARCH_PARALLEL_LIMIT,
        read_limit_books=READ_LIMIT_BOOKS,
        read_limit_videos=READ_LIMIT_VIDEOS,
    )


def _join(parts: list[str]) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())
