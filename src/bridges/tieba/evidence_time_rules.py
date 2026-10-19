"""贴吧发言时间与官方日期的解析规则。"""

from __future__ import annotations

import calendar
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone

from bridges.tieba.contracts import TiebaQuestionAnalysis
from bridges.tieba.lexicon import HOLIDAY_ARRANGEMENT_TERMS

_YEAR = re.compile(r"(20\d{2})")

DATE_PATTERN = re.compile(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?")

_TIEBA_EARLIEST_YEAR = 2003

_CHINA_TZ = timezone(timedelta(hours=8))

_AWARE_DATETIME = re.compile(
    r"(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{2})(?::\d{2})?"
    r"\s*(Z|[+-]\d{2}:?\d{2}|UTC|GMT)"
)


def requested_window(analysis: TiebaQuestionAnalysis, anchor: date) -> tuple[date, date, bool]:
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


def parse_posted_date(value: str | None, anchor: date | None = None) -> date | None:
    if not value:
        return None
    aware = _AWARE_DATETIME.search(value)
    if aware is not None:
        return _aware_date(aware, anchor)
    # 出现无法解释的时区缩写时保留未知，不按页面本地时间猜测。
    if re.search(r"(?<=\d)\s*[A-Za-z]{2,5}\b", value):
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
    return _plausible_date(moment, anchor)


def _aware_date(match: re.Match[str], anchor: date | None) -> date | None:
    """显式时区先换算到贴吧页面所在时区；无法识别的时区不猜日期。"""
    year, month, day, hour, minute = (int(value) for value in match.groups()[:5])
    zone = match.group(6)
    try:
        if zone in {"UTC", "GMT", "Z"}:
            offset = timedelta(0)
        else:
            sign = 1 if zone[0] == "+" else -1
            digits = zone[1:].replace(":", "")
            offset = sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        moment = datetime(
            year, month, day, hour, minute, tzinfo=timezone(offset)
        ).astimezone(_CHINA_TZ)
    except (ValueError, IndexError):
        return None
    return _plausible_date(moment.date(), anchor)


def _plausible_date(moment: date, anchor: date | None) -> date | None:
    """未来日期或早于贴吧上线的年份不可信，不能据此标为近期或历史。"""
    if moment.year < _TIEBA_EARLIEST_YEAR:
        return None
    if anchor is not None and moment > anchor + timedelta(days=1):
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
