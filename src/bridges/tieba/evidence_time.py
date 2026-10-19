"""贴吧发言时间窗口与历史范围判定。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

from bridges.tieba.contracts import (
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaReply,
    TiebaTemporalScope,
    TiebaTimeFilter,
    TiebaTimeScopeMode,
)
from bridges.tieba.evidence_time_rules import parse_posted_date, requested_window
from bridges.tieba.presenting import build_sections

#: 放假安排未指定年份时的说明：不假定年份，也不把历史通知当作本次安排。
HOLIDAY_YEAR_NOTE = (
    "问题没有指定年份：本轮不假定年份，也不把历史通知或旧帖当作本次安排；"
    "官方页面按原始名词与当前年份 {year} 优先挑选；已有该年份通知时，"
    "不混入标题注明其他年份的假期通知，"
    "并按原文摘录与取得时间呈现。"
)

#: 默认先看近十二个月；近期证据少于该数量时明确补充历史与未知时间。
DEFAULT_RECENT_WINDOW_MONTHS = 12
MIN_RECENT_EVIDENCE = 3


def summarize(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> tuple[
    TiebaTimeFilter,
    list[TiebaPostProjection],
    list[TiebaPostProjection],
    list[str],
]:
    """返回全部时间标注、总结范围内证据与只由范围内原文生成的分段。"""
    time_filter, tagged = apply_time_condition(analysis, posts)
    scoped, eligible, context_only = _select_scoped_posts(tagged, time_filter.mode)
    return (
        _with_selection_counts(time_filter, tagged, scoped, eligible, context_only),
        tagged,
        scoped,
        build_sections(scoped),
    )


def apply_time_condition(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> tuple[TiebaTimeFilter, list[TiebaPostProjection]]:
    """按实际发言时间标记近期、历史与未知，不丢弃老帖中的近期回复。"""
    anchor = max((post.retrieved_at for post in posts), default=datetime.now(UTC))
    anchor_date = anchor.date()
    since, until, supported = requested_window(analysis, anchor_date)
    requirement = analysis.time_requirement
    recent = historical = unknown = 0
    tagged: list[TiebaPostProjection] = []
    for post in posts:
        replies = []
        for reply in post.replies:
            moment = parse_posted_date(reply.posted_at, anchor_date)
            if moment is None or not supported:
                scope = TiebaTemporalScope.UNKNOWN
                unknown += 1
            elif since <= moment <= until:
                scope = TiebaTemporalScope.RECENT
                recent += 1
            else:
                scope = TiebaTemporalScope.HISTORICAL
                historical += 1
            replies.append(reply.model_copy(update={"temporal_scope": scope}))
        tagged.append(post.model_copy(update={"replies": replies}))
    applied = recent + historical > 0
    mode = _time_mode(requirement, supported, recent)
    exclusion_reasons = _time_exclusion_reasons(mode, historical, unknown)
    eligible = _eligible_by_time(tagged, mode)
    eligible_citable = sum(
        1 for post in eligible for reply in post.replies if not reply.context_only
    )
    context_only = sum(1 for post in tagged for reply in post.replies if reply.context_only)
    note = _time_note(
        analysis,
        mode=mode,
        requirement=requirement,
        supported=supported,
        since=since,
        until=until,
        recent=recent,
        historical=historical,
        unknown=unknown,
    )
    return (
        TiebaTimeFilter(
            requirement=requirement,
            year=analysis.time_year,
            applied=applied,
            mode=mode,
            since=(datetime.combine(since, datetime.min.time(), UTC) if supported else None),
            until=datetime.combine(until, datetime.min.time(), UTC) if supported else None,
            recent_count=recent,
            historical_count=historical,
            unknown_count=unknown,
            eligible_count=eligible_citable,
            context_only_count=context_only,
            excluded_count=max(
                0, recent + historical + unknown - eligible_citable - context_only
            ),
            history_supplemented=mode is TiebaTimeScopeMode.DEFAULT_SUPPLEMENT and historical > 0,
            exclusion_reasons=exclusion_reasons,
            note=note,
        ),
        tagged,
    )


def summary_scope(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> tuple[TiebaTimeFilter, list[TiebaPostProjection]]:
    """返回总结可用的证据：合格发言与仅上下文父回复分开保留。

    默认先只取近十二个月；近期证据不足时才明确补充历史与未知时间。显式
    时间范围只取范围内发言，范围外的历史父回复只作理解上下文，不能引用。
    """
    time_filter, tagged = apply_time_condition(analysis, posts)
    scoped, eligible, context_only = _select_scoped_posts(tagged, time_filter.mode)
    return (
        _with_selection_counts(time_filter, tagged, scoped, eligible, context_only),
        scoped,
    )


def _select_scoped_posts(
    tagged: Sequence[TiebaPostProjection], mode: TiebaTimeScopeMode
) -> tuple[list[TiebaPostProjection], int, int]:
    scopes = _summary_scopes(mode)
    selected_posts: list[TiebaPostProjection] = []
    eligible = context_only = 0
    for post in tagged:
        direct_keys: set[int] = set()
        for index, reply in enumerate(post.replies):
            if not reply.context_only and reply.temporal_scope in scopes:
                direct_keys.add(index)
                eligible += 1
        parent_keys = _parent_context_keys(post, direct_keys)
        replacement: dict[int, TiebaReply] = {}
        for index in parent_keys - direct_keys:
            parent = post.replies[index]
            replacement[index] = parent.model_copy(update={"context_only": True})
            context_only += 1
        keys = direct_keys | parent_keys
        ordered = [
            replacement.get(index, reply)
            for index, reply in enumerate(post.replies)
            if index in keys
        ]
        if ordered:
            selected_posts.append(post.model_copy(update={"replies": ordered}))
    return selected_posts, eligible, context_only


def _with_selection_counts(
    time_filter: TiebaTimeFilter,
    tagged: Sequence[TiebaPostProjection],
    scoped: Sequence[TiebaPostProjection],
    eligible: int,
    context_only: int,
) -> TiebaTimeFilter:
    total_historical = sum(
        1
        for post in tagged
        for reply in post.replies
        if reply.temporal_scope is TiebaTemporalScope.HISTORICAL
    )
    total_unknown = sum(
        1
        for post in tagged
        for reply in post.replies
        if reply.temporal_scope is TiebaTemporalScope.UNKNOWN
    )
    selected_historical = sum(
        1
        for post in scoped
        for reply in post.replies
        if reply.temporal_scope is TiebaTemporalScope.HISTORICAL
        and not reply.context_only
    )
    selected_unknown = sum(
        1
        for post in scoped
        for reply in post.replies
        if reply.temporal_scope is TiebaTemporalScope.UNKNOWN and not reply.context_only
    )
    context_historical = sum(
        1
        for post in scoped
        for reply in post.replies
        if reply.temporal_scope is TiebaTemporalScope.HISTORICAL and reply.context_only
    )
    context_unknown = sum(
        1
        for post in scoped
        for reply in post.replies
        if reply.temporal_scope is TiebaTemporalScope.UNKNOWN and reply.context_only
    )
    reasons: dict[str, int] = {}
    if time_filter.mode is not TiebaTimeScopeMode.DEFAULT_SUPPLEMENT:
        excluded_historical = max(
            0, total_historical - selected_historical - context_historical
        )
        excluded_unknown = max(0, total_unknown - selected_unknown - context_unknown)
        if excluded_historical:
            reasons["out_of_time_range"] = excluded_historical
        if excluded_unknown:
            reasons["unknown_time"] = excluded_unknown
    return time_filter.model_copy(
        update={
            "eligible_count": eligible,
            "context_only_count": context_only,
            "excluded_count": sum(reasons.values()),
            "exclusion_reasons": reasons,
        }
    )


def _parent_context_keys(post: TiebaPostProjection, direct_keys: set[int]) -> set[int]:
    """补齐合格楼中楼所回应的父发言；父发言即使超时也只作上下文。"""
    by_id = {
        reply.reply_id: index
        for index, reply in enumerate(post.replies)
        if reply.reply_id
    }
    keys = set(direct_keys)
    pending = [
        post.replies[index].parent_reply_id
        for index in direct_keys
        if post.replies[index].parent_reply_id
    ]
    while pending:
        parent_id = pending.pop()
        index = by_id.get(parent_id or "")
        if index is None or index in keys:
            continue
        keys.add(index)
        parent = post.replies[index]
        if parent.parent_reply_id:
            pending.append(parent.parent_reply_id)
    return keys


def _summary_scopes(mode: TiebaTimeScopeMode) -> frozenset[TiebaTemporalScope]:
    if mode is TiebaTimeScopeMode.UNAVAILABLE:
        return frozenset()
    if mode is TiebaTimeScopeMode.DEFAULT_SUPPLEMENT:
        return frozenset(
            {
                TiebaTemporalScope.RECENT,
                TiebaTemporalScope.HISTORICAL,
                TiebaTemporalScope.UNKNOWN,
            }
        )
    return frozenset({TiebaTemporalScope.RECENT})


def _eligible_by_time(
    posts: Sequence[TiebaPostProjection], mode: TiebaTimeScopeMode
) -> list[TiebaPostProjection]:
    scopes = _summary_scopes(mode)
    result: list[TiebaPostProjection] = []
    for post in posts:
        replies = [
            reply
            for reply in post.replies
            if not reply.context_only and reply.temporal_scope in scopes
        ]
        if replies:
            result.append(post.model_copy(update={"replies": replies}))
    return result


def _time_mode(
    requirement: str | None, supported: bool, recent: int
) -> TiebaTimeScopeMode:
    if requirement and not supported:
        return TiebaTimeScopeMode.UNAVAILABLE
    if requirement:
        return TiebaTimeScopeMode.EXPLICIT
    if recent >= MIN_RECENT_EVIDENCE:
        return TiebaTimeScopeMode.DEFAULT_RECENT
    return TiebaTimeScopeMode.DEFAULT_SUPPLEMENT


def _time_exclusion_reasons(
    mode: TiebaTimeScopeMode, historical: int, unknown: int
) -> dict[str, int]:
    if mode is TiebaTimeScopeMode.DEFAULT_SUPPLEMENT:
        return {}
    reasons: dict[str, int] = {}
    if historical:
        reasons["out_of_time_range"] = historical
    if unknown:
        reasons["unknown_time"] = unknown
    return reasons


def _time_note(
    analysis: TiebaQuestionAnalysis,
    *,
    mode: TiebaTimeScopeMode,
    requirement: str | None,
    supported: bool,
    since: date,
    until: date,
    recent: int,
    historical: int,
    unknown: int,
) -> str:
    if recent + historical + unknown == 0:
        return "没有读到带发帖时间的楼层；本轮时间条件没有参与过滤，也不据此推断近期或历史。"
    if requirement and not supported:
        return (
            f"保留了时间条件「{requirement}」，但当前无法可靠换算成日历区间；"
            "本轮不会把默认近十二个月窗口冒充为该条件，相关发言的时间匹配状态标为未知。"
        )
    if requirement:
        if analysis.time_year is not None:
            return (
                f"已按实际发言时间核对「{requirement or str(analysis.time_year)}」："
                f"匹配 {recent} 条、历史 {historical} 条、时间未知 {unknown} 条；"
                "范围外内容不进入总结，历史父回复仅作理解上下文。"
            )
        return (
            f"已按实际时间区间 {since.isoformat()} 至 {until.isoformat()} 核对「{requirement}」："
            f"近期 {recent} 条、历史 {historical} 条、时间未知 {unknown} 条；"
            "范围外内容不进入总结，历史父回复仅作理解上下文。"
        )
    if mode is TiebaTimeScopeMode.DEFAULT_SUPPLEMENT:
        return (
            f"未指定时间：优先近十二个月（{since.isoformat()} 至 {until.isoformat()}），"
            f"近期证据不足（{recent} 条），已明确补充历史 {historical} 条、"
            f"时间未知 {unknown} 条；历史与未知时间发言会按实际时间标注，不标为近期。"
        )
    return (
        f"未指定时间：优先近十二个月（{since.isoformat()} 至 {until.isoformat()}），"
        f"近期 {recent} 条；历史 {historical} 条与时间未知 {unknown} 条不进入总结，"
        "时间未知的发言不标为近期。"
    )


def time_filter_state(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> TiebaTimeFilter:
    """时间条件的执行状态：有没有可核对的时间、能不能真的过滤。"""
    return summary_scope(analysis, posts)[0]
