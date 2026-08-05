"""中文自然语言提醒解析器（Issue 33，确定性实现）。

把「明天早上八点提醒我复习 transformer」这类输入解析为带时区的结构化
日程（ADR-0019：同时保存账户时区规则与 UTC 执行时间）。解析器是确定性
规则实现，不调用模型：同一输入在解析预览与创建复核两个时刻必然产生
同一日程，保证「所见即所存」。

支持的语法（未命中时给出明确中文原因）：

- 时刻：``凌晨/早上/上午/中午/下午/傍晚/晚上/今晚`` + 数字点[分]，
  支持中文数字、半角冒号（``8:30``）与「点半」；未带日锚时默认为
  今天，时刻已过则按规则推进（一次性 → 明天同一时刻）；
- 日锚：``今天/明天/后天/大后天``、``本周X/这周X``、``下X``、
  ``X点``（X=一…日，最近的一个；今天同星期视为今天）；
- 重复规则：``一次``（默认）、``每天/每日``、``工作日``、
  ``每周X[,X...]``、``每月N(日|号)``；
- 主题：动词前缀 ``提醒我/记得提醒我/别忘了/不要忘记`` 之后的文本。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from bridges.contracts.reminder import (
    ReminderRepeatRule,
    ReminderSchedule,
)
from bridges.reminder.rules import (
    localize,
    next_occurrence,
    utc_to_local,
    validate_timezone,
)

#: 动词前缀（主题提取边界；在全文任意位置查找，长短语优先）。
_PREFIX_RE = re.compile(r"(?:记得提醒我|提醒我|别忘了|不要忘记|别忘|提醒|记得)")

#: 中文数字（一…十、两）。
_CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

#: 星期中文名 → ISO 编号（1=周一 … 7=周日）。
_WEEKDAY_CN = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7,
}

#: 重复规则短语（星期可分隔或「和」连接）。
_REPEAT_DAILY_RE = re.compile(r"(?:每天|每日)")
_REPEAT_WEEKDAYS_RE = re.compile(r"工作日")
_REPEAT_WEEKLY_RE = re.compile(
    r"(?:每周|每星期)"
    r"([一二三四五六日天](?:[、,，和]?(?:周|星期)?[一二三四五六日天])*)"
)
_REPEAT_MONTHLY_RE = re.compile(r"每月([0-9]{1,2}|[一二两三四五六七八九十]{1,3})(?:日|号)")
_REPEAT_ONCE_RE = re.compile(r"(?:一次|只提醒一次|仅此一次)")

#: 日锚短语（主题剥离用；日锚语义解析在 _anchor_day 中独立实现）。
_DAY_ANCHOR_RE = re.compile(
    r"(?:大后天|后天|明天|今天|今日"
    r"|(?:下(?:周|星期)?|[本周这]周?|周|星期)([一二三四五六日天]))"
)

#: 时刻片段（时段词 + 数字点…）。
_CLOCK_RE = re.compile(
    r"(凌晨|早上|上午|中午|下午|傍晚|晚上|今晚)?\s*"
    r"([0-9]{1,2}|一|二|两|三|四|五|六|七|八|九|十|十一|十二|"
    r"十三|十四|十五|十六|十七|十八|十九|二十|二十一|二十二|二十三|二十四)"
    r"(?:点)(?:(?P<minutes>[0-9]{1,2}|[一二两三四五六七八九十]{1,2})分?|(?P<half>半))?"
)

#: 半角冒号时刻（``8:30``）。
_COLON_RE = re.compile(r"(凌晨|早上|上午|中午|下午|傍晚|晚上|今晚)?\s*(\d{1,2}):(\d{2})")


class ReminderParseError(Exception):
    """解析失败；message 为面向用户的中文原因。"""


def _cn_to_int(text: str) -> int | None:
    """解析中文/阿拉伯数字（1-31）；无法解析返回 None。"""
    if text.isdigit():
        value = int(text)
        return value if 1 <= value <= 31 else None
    if text == "十":
        return 10
    if text.startswith("十"):
        tail = _CN_NUM.get(text[1:])
        return 10 + tail if tail is not None else None
    if text.endswith("十") and len(text) > 1:
        head = _CN_NUM.get(text[0])
        return head * 10 if head is not None else None
    if len(text) == 2:
        tens = _CN_NUM.get(text[0])
        ones = _CN_NUM.get(text[1])
        if tens is not None and ones is not None and 2 <= tens <= 9:
            return tens * 10 + ones
    if len(text) == 1:
        return _CN_NUM.get(text)
    return None


def _period_adjust(period: str | None, hour: int) -> int:
    """按时段词调整 12 小时制小时，返回 24 小时制小时。

    - 凌晨/早上/上午：原样（1-12）；
    - 中午：12；
    - 下午：12 点 → 12，其余 +12；
    - 晚上/傍晚/今晚：12 点 → 0（次日 0 点，由调用方按 24 处理）。
    """
    if period is None:
        return hour
    if period == "中午":
        return 12
    if period in {"下午", "晚上", "傍晚", "今晚"}:
        if hour == 12:
            return 12 if period == "下午" else 0
        return hour + 12
    # 凌晨 / 早上 / 上午
    return hour


def _find_clock(text: str) -> tuple[int, int] | None:
    """扫描文本中的第一处时刻并返回 (时, 分)；未命中返回 None。"""
    colon = _COLON_RE.search(text)
    if colon:
        raw_hour = int(colon.group(2))
        if not 0 <= raw_hour <= 23:
            return None
        hour = _period_adjust(colon.group(1), raw_hour)
        return hour, int(colon.group(3))
    match = _CLOCK_RE.search(text)
    if match:
        cn_hour = _cn_to_int(match.group(2))
        if cn_hour is None or not 1 <= cn_hour <= 24:
            return None
        hour = _period_adjust(match.group(1), cn_hour)
        minute = 0
        if match.group("half") is not None:
            minute = 30
        elif match.group("minutes"):
            cn_minute = _cn_to_int(match.group("minutes"))
            if cn_minute is None or not 0 <= cn_minute <= 59:
                return None
            minute = cn_minute
        if hour == 24:
            return 0, minute
        return hour, minute
    return None


def _repeat_of(text: str) -> tuple[ReminderRepeatRule, list[int] | None, int | None]:
    """提取重复规则短语；未命中返回 (once, None, None)。"""
    weekly = _REPEAT_WEEKLY_RE.search(text)
    if weekly:
        days = [_WEEKDAY_CN[char] for char in weekly.group(1) if char in _WEEKDAY_CN]
        if not days:
            raise ReminderParseError("「每周」后请指定星期，如「每周一」。")
        return ReminderRepeatRule.WEEKLY_DAYS, sorted(set(days)), None
    monthly = _REPEAT_MONTHLY_RE.search(text)
    if monthly:
        day = _cn_to_int(monthly.group(1))
        if day is None or not 1 <= day <= 31:
            raise ReminderParseError("「每月」后请指定 1-31 的日期，如「每月15号」。")
        return ReminderRepeatRule.MONTHLY_DAY, None, day
    if _REPEAT_WEEKDAYS_RE.search(text):
        return ReminderRepeatRule.WEEKDAYS, None, None
    if _REPEAT_DAILY_RE.search(text):
        return ReminderRepeatRule.DAILY, None, None
    if _REPEAT_ONCE_RE.search(text):
        return ReminderRepeatRule.ONCE, None, None
    return ReminderRepeatRule.ONCE, None, None


_SCHEDULE_STRIP_RE = (
    _CLOCK_RE,
    _COLON_RE,
    _REPEAT_DAILY_RE,
    _REPEAT_WEEKDAYS_RE,
    _REPEAT_WEEKLY_RE,
    _REPEAT_MONTHLY_RE,
    _DAY_ANCHOR_RE,
)


def _subject_of(text: str) -> str:
    """剥离日程短语（时刻/重复/日锚）与动词前缀后得到主题。

    动词前缀在全文任意位置识别：前缀之前是「日程区」（时刻/重复/日锚
    在此剥离），前缀之后是主题区。主题区中的「每天」「周一」等日程词
    不会被误剥（如「提醒我每天背单词」的「每天」属于主题内容）；显式
    一次性标记（一次/只提醒一次/仅此一次）仍会从主题区移除。没有动词
    前缀时把整段当作日程区剥离（无主题则返回空，由调用方报错）。
    """
    prefix = _PREFIX_RE.search(text)
    if prefix is None:
        stripped = text
        for pattern in _SCHEDULE_STRIP_RE:
            stripped = pattern.sub("", stripped)
        subject = _REPEAT_ONCE_RE.sub("", stripped)
    else:
        head = text[: prefix.start()]
        for pattern in _SCHEDULE_STRIP_RE:
            head = pattern.sub("", head)
        tail = _REPEAT_ONCE_RE.sub("", text[prefix.end() :])
        subject = head + tail
    subject = re.sub(r"^[\s，。、,.;:：]+", "", subject)
    subject = re.sub(r"[\s，。、,.;:：]+$", "", subject)
    return subject


def _anchor_day(text: str, today_local: datetime) -> tuple[datetime, bool]:
    """解析日锚并返回 (目标日 0 点, 是否显式指定)。

    未命中 → (今天, False)；「本周X」在当周且已过时顺延到下周同星期；
    「周X」取最近一个（今天同星期视为今天）。
    """
    today = today_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if "大后天" in text:
        return today + timedelta(days=3), True
    if "后天" in text:
        return today + timedelta(days=2), True
    if "明天" in text:
        return today + timedelta(days=1), True
    if "今天" in text or "今日" in text:
        return today, True
    explicit_next = re.search(r"下(?:周|星期)?([一二三四五六日天])", text)
    if explicit_next:
        target = _WEEKDAY_CN[explicit_next.group(1)]
        days = (target - today.isoweekday()) % 7
        if days == 0:
            days = 7
        return today + timedelta(days=days), True
    for prefix in ("本周", "这周"):
        match = re.search(rf"{prefix}([一二三四五六日天])", text)
        if match:
            target = _WEEKDAY_CN[match.group(1)]
            days = (target - today.isoweekday()) % 7
            result = today + timedelta(days=days)
            if result <= today_local:
                result = result + timedelta(days=7)
            return result, True
    nearest = re.search(r"(?:周|星期)([一二三四五六日天])", text)
    if nearest:
        target = _WEEKDAY_CN[nearest.group(1)]
        days = (target - today.isoweekday()) % 7
        return today + timedelta(days=days), True
    return today, False


def _is_occurrence(
    local: datetime,
    rule: ReminderRepeatRule,
    *,
    weekdays: list[int] | None,
    month_day: int | None,
) -> bool:
    """候选本地时刻本身是否是规则的合法执行时刻。"""
    if rule is ReminderRepeatRule.DAILY:
        return True
    if rule is ReminderRepeatRule.WEEKDAYS:
        return local.isoweekday() <= 5
    if rule is ReminderRepeatRule.WEEKLY_DAYS:
        return bool(weekdays) and local.isoweekday() in (weekdays or [])
    if rule is ReminderRepeatRule.MONTHLY_DAY:
        return month_day is not None and local.day == month_day
    return True  # once：任意时刻都合法


def _first_run(
    *,
    day: datetime,
    hour: int,
    minute: int,
    timezone: str,
    rule: ReminderRepeatRule,
    weekdays: list[int] | None,
    month_day: int | None,
    explicit_day: bool,
    now_utc: datetime,
) -> datetime:
    """由 (日锚, 时刻, 规则) 计算首次执行 UTC 时刻。

    候选时刻是规则的合法执行时刻且在未来 → 直接采用（如「每周三下午
    三点」说在周三上午，首跑就是今天）；否则取规则的下一次。候选已过
    且显式指定日期（如「今天八点」）时抛中文错误。
    """
    candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate_utc = localize(candidate, timezone)
    if candidate_utc > now_utc:
        if _is_occurrence(candidate, rule, weekdays=weekdays, month_day=month_day):
            return candidate_utc
        following = next_occurrence(
            rule,
            reference_utc=candidate_utc,
            timezone=timezone,
            weekdays=weekdays,
            month_day=month_day,
        )
        assert following is not None and following > now_utc
        return following
    if explicit_day:
        raise ReminderParseError("该日期与时刻已经过去，请选择未来的时间。")
    if rule is ReminderRepeatRule.ONCE:
        return localize(candidate + timedelta(days=1), timezone)
    following = next_occurrence(
        rule,
        reference_utc=candidate_utc,
        timezone=timezone,
        weekdays=weekdays,
        month_day=month_day,
    )
    assert following is not None and following > now_utc
    return following


def parse_reminder_text(
    raw_text: str,
    *,
    timezone: str,
    now_utc: datetime | None = None,
) -> tuple[ReminderSchedule, str]:
    """解析自然语言为 (结构化日程, 主题)；失败抛 :class:`ReminderParseError`。

    ``now_utc`` 供测试注入可控时钟；缺省使用当前 UTC 时间。
    """
    if now_utc is None:
        now_utc = datetime.now(UTC)
    now_utc = now_utc.astimezone(UTC)
    text = raw_text.strip()
    if not text:
        raise ReminderParseError(
            "请输入提醒内容，例如「明天早上八点提醒我复习 transformer」。"
        )

    validate_timezone(timezone)

    subject = _subject_of(text)
    if not subject:
        raise ReminderParseError(
            "没有识别到提醒主题，请在时间后说明要提醒的内容。"
        )

    clock = _find_clock(text)
    if clock is None:
        raise ReminderParseError(
            "没有识别到时间，请使用「早上八点 / 8:30 / 明天下午三点」等说法。"
        )
    hour, minute = clock

    rule, weekdays, month_day = _repeat_of(text)

    zone_local = utc_to_local(now_utc, timezone)
    day, explicit = _anchor_day(text, zone_local)

    first_run = _first_run(
        day=day,
        hour=hour,
        minute=minute,
        timezone=timezone,
        rule=rule,
        weekdays=weekdays,
        month_day=month_day,
        explicit_day=explicit,
        now_utc=now_utc,
    )
    first_local = utc_to_local(first_run, timezone)
    schedule = ReminderSchedule(
        timezone=timezone,
        first_run_at=first_run.replace(tzinfo=UTC),
        first_run_local=first_local.strftime("%Y-%m-%d %H:%M"),
        repeat=rule,
        repeat_weekdays=weekdays or [],
        repeat_month_day=month_day,
    )
    return schedule, subject


__all__ = [
    "ReminderParseError",
    "parse_reminder_text",
]
