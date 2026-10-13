"""贴吧发言时间窗口与历史范围判定。"""

from __future__ import annotations

import calendar
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from bridges.tieba.contracts import (
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaTemporalScope,
    TiebaTimeFilter,
)
from bridges.tieba.lexicon import (
    HOLIDAY_ARRANGEMENT_TERMS,
)
from bridges.tieba.presenting import build_sections

#: 放假安排未指定年份时的说明：不假定年份，也不把历史通知当作本次安排。
HOLIDAY_YEAR_NOTE = (
    "问题没有指定年份：本轮不假定年份，也不把历史通知或旧帖当作本次安排；"
    "官方页面按原始名词与当前年份 {year} 优先挑选；已有该年份通知时，"
    "不混入标题注明其他年份的假期通知，"
    "并按原文摘录与取得时间呈现。"
)

_YEAR = re.compile(r"(20\d{2})")

DATE_PATTERN = re.compile(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?")


def summarize(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> tuple[TiebaTimeFilter, list[TiebaPostProjection], list[str]]:
    """按时间条件收敛已读楼层，并且只从收敛后的真实楼层里分段。"""
    time_filter, filtered = apply_time_condition(analysis, posts)
    return time_filter, filtered, build_sections(filtered)


def apply_time_condition(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> tuple[TiebaTimeFilter, list[TiebaPostProjection]]:
    """按实际发言时间标记近期、历史与未知，不丢弃老帖中的近期回复。"""
    anchor = max((post.retrieved_at for post in posts), default=datetime.now(UTC))
    anchor_date = anchor.date()
    since, until, supported = _requested_window(analysis, anchor_date)
    recent = historical = unknown = 0
    tagged: list[TiebaPostProjection] = []
    for post in posts:
        replies = []
        for reply in post.replies:
            moment = _parse_posted_date(reply.posted_at)
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
    requirement = analysis.time_requirement
    if requirement and not supported:
        note = (
            f"保留了时间条件「{requirement}」，但当前无法可靠换算成日历区间；"
            "本轮不会把默认近十二个月窗口冒充为该条件，相关发言的时间匹配状态标为未知。"
        )
    elif not applied:
        note = (
            f"保留了时间条件「{requirement or '默认近十二个月'}」；"
            "本轮没有读到带发帖时间的楼层，因此未能判断哪些发言落入时间范围。"
        )
    elif analysis.time_year is not None:
        note = (
            f"已按实际发言时间核对「{requirement or str(analysis.time_year)}」："
            f"匹配 {recent} 条、历史 {historical} 条、时间未知 {unknown} 条；"
            "历史内容仅作上下文。"
        )
    elif requirement:
        note = (
            f"已按实际时间区间 {since.isoformat()} 至 {until.isoformat()} 核对「{requirement}」："
            f"近期 {recent} 条、历史 {historical} 条、时间未知 {unknown} 条。"
        )
    else:
        note = (
            f"未指定时间：优先近十二个月（{since.isoformat()} 至 {until.isoformat()}），"
            f"近期 {recent} 条、历史 {historical} 条、时间未知 {unknown} 条；"
            "历史内容仅作上下文，时间未知的发言不标为近期。"
        )
    return (
        TiebaTimeFilter(
            requirement=requirement,
            year=analysis.time_year,
            applied=applied,
            since=(datetime.combine(since, datetime.min.time(), UTC) if supported else None),
            until=datetime.combine(until, datetime.min.time(), UTC) if supported else None,
            recent_count=recent,
            historical_count=historical,
            unknown_count=unknown,
            note=note,
        ),
        tagged,
    )


def time_filter_state(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> TiebaTimeFilter:
    """时间条件的执行状态：有没有可核对的时间、能不能真的过滤。"""
    state, _ = apply_time_condition(analysis, posts)
    return state


def _requested_window(analysis: TiebaQuestionAnalysis, anchor: date) -> tuple[date, date, bool]:
    """返回实际日历区间；无法可靠解释的显式条件不回退到默认窗口。"""
    requirement = re.sub(r"\s+", "", analysis.time_requirement or "")
    if not requirement:
        return _one_year_before(anchor), anchor, True

    date_range = re.fullmatch(
        r"(20\d{2})年(\d{1,2})月(?:(\d{1,2})日)?(?:至|到)"
        r"(?:(20\d{2})年)?(\d{1,2})月(?:(\d{1,2})日)?",
        requirement,
    )
    if date_range:
        (
            start_year_text,
            start_month_text,
            start_day_text,
            end_year_text,
            end_month_text,
            end_day_text,
        ) = date_range.groups()
        start_year, start_month = int(start_year_text), int(start_month_text)
        end_year = int(end_year_text) if end_year_text is not None else start_year
        end_month = int(end_month_text)
        start_day = int(start_day_text) if start_day_text is not None else 1
        _, _, start_valid = _month_window(start_year, start_month)
        _, end_month_end, end_valid = _month_window(end_year, end_month)
        if not start_valid or not end_valid:
            return anchor, anchor, False
        try:
            start = date(start_year, start_month, start_day)
            end = date(
                end_year,
                end_month,
                int(end_day_text) if end_day_text is not None else end_month_end.day,
            )
        except ValueError:
            return anchor, anchor, False
        if start > end:
            return anchor, anchor, False
        return start, end, True

    absolute = DATE_PATTERN.fullmatch(requirement)
    if absolute:
        year, month, day = (int(value) for value in absolute.groups())
        try:
            point = date(year, month, day)
        except ValueError:
            return anchor, anchor, False
        return point, point, True
    year_month = re.fullmatch(r"(20\d{2})年(\d{1,2})月", requirement)
    if year_month:
        year, month = (int(value) for value in year_month.groups())
        return _month_window(year, month)
    iso_month = re.fullmatch(r"(20\d{2})-(\d{1,2})", requirement)
    if iso_month:
        year, month = (int(value) for value in iso_month.groups())
        return _month_window(year, month)
    month_day = re.fullmatch(r"(\d{1,2})月(\d{1,2})日?", requirement)
    if month_day:
        month, day = (int(value) for value in month_day.groups())
        year = anchor.year if (month, day) <= (anchor.month, anchor.day) else anchor.year - 1
        try:
            point = date(year, month, day)
        except ValueError:
            return anchor, anchor, False
        return point, point, True
    month_only = re.fullmatch(r"(\d{1,2})月", requirement)
    if month_only:
        month = int(month_only.group(1))
        year = anchor.year if month <= anchor.month else anchor.year - 1
        return _month_window(year, month)
    year_only = re.fullmatch(r"(20\d{2})年", requirement)
    if year_only:
        year = int(year_only.group(1))
        return date(year, 1, 1), date(year, 12, 31), True
    if analysis.time_year is not None:
        return date(analysis.time_year, 1, 1), date(analysis.time_year, 12, 31), True

    if requirement in {"去年", "前年", "今年"}:
        offset = {"去年": 1, "前年": 2, "今年": 0}[requirement]
        year = anchor.year - offset
        return date(year, 1, 1), min(date(year, 12, 31), anchor), True
    if requirement in {"上个月", "这个月", "本月"}:
        year, month = anchor.year, anchor.month
        if requirement == "上个月":
            year, month = _shift_month(year, month, -1)
        start, end, valid = _month_window(year, month)
        return start, min(end, anchor), valid
    if requirement in {"上周", "本周", "这周"}:
        monday = anchor - timedelta(days=anchor.weekday())
        start = monday - timedelta(days=7) if requirement == "上周" else monday
        end = start + timedelta(days=6)
        return start, min(end, anchor), True
    if requirement in {"最近", "近期"}:
        return anchor - timedelta(days=29), anchor, True
    if requirement == "这几天":
        return anchor - timedelta(days=6), anchor, True
    if requirement == "这两天":
        return anchor - timedelta(days=1), anchor, True

    relative_days = re.fullmatch(
        r"(?:近|最近|过去)(\d+|一|两|三|五|七|十|十四|十五|二十|三十)天",
        requirement,
    )
    day_count = {
        "一": 1,
        "两": 2,
        "三": 3,
        "五": 5,
        "七": 7,
        "十": 10,
        "十四": 14,
        "十五": 15,
        "二十": 20,
        "三十": 30,
    }
    if relative_days:
        value = relative_days.group(1)
        count = int(value) if value.isdigit() else day_count[value]
        return anchor - timedelta(days=count - 1), anchor, True

    relative_weeks = re.fullmatch(
        r"(?:近|最近|过去)(\d+|一|两|三|四|五|六|七|八|九|十|十一|十二)(?:周|星期)",
        requirement,
    )
    week_count = {
        "一": 1,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "十一": 11,
        "十二": 12,
    }
    if relative_weeks:
        value = relative_weeks.group(1)
        count = int(value) if value.isdigit() else week_count[value]
        days = count * 7
        return anchor - timedelta(days=days - 1), anchor, True

    relative_months = re.fullmatch(r"(?:近|最近|过去)(\d+|一|两|三|六|十二)个?月", requirement)
    month_count = {"一": 1, "两": 2, "三": 3, "六": 6, "十二": 12}
    if relative_months:
        value = relative_months.group(1)
        count = int(value) if value.isdigit() else month_count[value]
        year, month = _shift_month(anchor.year, anchor.month, -count)
        day = min(anchor.day, calendar.monthrange(year, month)[1])
        return date(year, month, day), anchor, True
    return anchor, anchor, False


def _month_window(year: int, month: int) -> tuple[date, date, bool]:
    if not 1 <= month <= 12:
        return date(year, 1, 1), date(year, 1, 1), False
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]), True


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + offset
    shifted_year, shifted_month = divmod(index, 12)
    return shifted_year, shifted_month + 1


def _one_year_before(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _parse_posted_date(value: str | None) -> date | None:
    if not value:
        return None
    match = re.search(r"(?:(20\d{2})[-/.年])?(\d{1,2})[-/.月](\d{1,2})", value)
    if match is None:
        return None
    year_text, month_text, day_text = match.groups()
    # 页面省略年份时无法证明是哪一年的发言，不能猜成当前年的近期证据。
    if year_text is None:
        return None
    try:
        moment = date(int(year_text), int(month_text), int(day_text))
    except ValueError:
        return None
    return moment


def holiday_without_year(analysis: TiebaQuestionAnalysis) -> bool:
    """放假安排类问题没有指定年份：必须写明本轮不假定年份。"""
    if analysis.time_year is not None:
        return False
    return bool(set(analysis.topic_terms) & HOLIDAY_ARRANGEMENT_TERMS)


def year_of(text: str) -> int | None:
    match = _YEAR.search(text)
    return int(match.group(1)) if match is not None else None


def matches_requested_date(requirement: str, text: str, year: int | None) -> bool:
    """绝对年月日须逐项对应，年份出现不能证明另一个月份也适用。"""
    if year is None or str(year) not in text:
        return False
    month = re.search(r"(?:年|20\d{2}-)\s*(\d{1,2})(?:月|(?:-\d{1,2})?$)", requirement)
    day = re.search(r"(?:月|20\d{2}-\d{1,2}-)\s*(\d{1,2})(?:日|$)", requirement)
    if month is None:
        return True
    requested_month = int(month.group(1))
    if day is not None:
        return any(
            (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            == (year, requested_month, int(day.group(1)))
            for match in DATE_PATTERN.finditer(text)
        )
    return any(
        int(match.group(1)) == year and int(match.group(2)) == requested_month
        for match in re.finditer(r"(20\d{2})\s*(?:年|-)\s*(\d{1,2})(?:月|-)", text)
    )


def expired_notice(text: str, analysis: TiebaQuestionAnalysis, fetched_at: datetime) -> bool:
    """只对明确截止/受理区间判断过期；未写有效期不猜测废止。"""
    match = re.search(
        r"(20\d{2})年\d{1,2}月\d{1,2}日(?:至|到)(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日",
        text,
    )
    deadline = re.search(r"(?:截止|截至|有效期至)[：: ]*((20\d{2})年(\d{1,2})月(\d{1,2})日)", text)
    if match:
        end = (int(match.group(2) or match.group(1)), int(match.group(3)), int(match.group(4)))
    elif deadline:
        end = (int(deadline.group(2)), int(deadline.group(3)), int(deadline.group(4)))
    else:
        return False
    # 明确查询历史年月/日时保留该时期依据；只给年份不足以判定在受理期内。
    if (
        analysis.time_requirement
        and analysis.time_year
        and re.search(r"\d{1,2}月|20\d{2}-\d{1,2}", analysis.time_requirement)
    ):
        return False
    return end < (fetched_at.year, fetched_at.month, fetched_at.day)


def years_text(years: Sequence[int]) -> str:
    return "、".join(str(year) for year in sorted(set(years))) if years else "未标注"
