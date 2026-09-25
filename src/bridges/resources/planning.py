"""``resources.plan``：把解析结果变成真实可发送的最小查询词。

两类查询分开构造，但共用同一个主词（原词永远在查询里，扩展词只作补充）：

- 图书查询发给书目来源（``resources.search_books``），保持短而居中；
- 视频查询发给公网搜索（``resources.search_videos``），在层次后缀的帮助下
  让结果落在与本轮层次相符的公开讲解页，再逐条核对哔哩哔哩直达页。

候选请求量高于目标数量（默认目标 2 本书 + 3 条视频），因为核对会淘汰条目；
目标数量不因候选不足而降低——不足时如实报数。
"""

from __future__ import annotations

from bridges.resources.contracts import ResourcesQueryPlan, ResourcesTermAnalysis
from bridges.resources.lexicon import VIDEO_QUERY_SUFFIX

#: 默认目标数量（编排合同：两本书与三条哔哩哔哩视频）。
DEFAULT_TARGET_BOOKS = 2
DEFAULT_TARGET_VIDEOS = 3

#: 候选请求上限（高于目标，核对淘汰后仍有机会凑到目标）。
BOOK_CANDIDATE_LIMIT = 8
VIDEO_CANDIDATE_LIMIT = 8

#: 视频查询没有层次时的兜底后缀。
DEFAULT_VIDEO_SUFFIX = "教程"


def plan_resources(analysis: ResourcesTermAnalysis) -> ResourcesQueryPlan:
    """生成本轮检索计划（图书查询与视频查询各一条）。"""
    term = (analysis.normalized_term or analysis.original_phrase).strip()
    expansions = list(analysis.expansions[:1])
    book_query = _join([term, *expansions])
    suffix = (
        VIDEO_QUERY_SUFFIX.get(analysis.level, DEFAULT_VIDEO_SUFFIX)
        if analysis.level is not None
        else DEFAULT_VIDEO_SUFFIX
    )
    video_query = _join([term, suffix])
    rationale = (
        f"主题词保持你给的原始说法「{term}」"
        + (f"，英文写法「{expansions[0]}」只作为补充" if expansions else "")
        + f"；图书查询只发送主题词，视频查询额外带上层次提示（{'、'.join(suffix.split())}）"
        "以便找到与本轮层次相符的公开讲解。"
    )
    return ResourcesQueryPlan(
        term=term,
        book_query=book_query,
        video_query=video_query,
        target_books=DEFAULT_TARGET_BOOKS,
        target_videos=DEFAULT_TARGET_VIDEOS,
        book_candidate_limit=BOOK_CANDIDATE_LIMIT,
        video_candidate_limit=VIDEO_CANDIDATE_LIMIT,
        expansions_used=expansions,
        rationale=rationale,
    )


def _join(parts: list[str]) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())
