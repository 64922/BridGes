"""提醒重复规则的计算（Issue 33）。

规则计算始终在账户时区的本地时间进行（DST 安全），结果转回 UTC 保存；
ADR-0019 要求同时保存账户时区规则与 UTC 执行时间。规则：

- ``once``：无下一次（投递后自然完成）；
- ``daily``：下一个自然日同一本地时刻；
- ``weekdays``：下一个工作日（周一至周五）同一本地时刻；
- ``weekly_days``：下一周内属于给定星期集合的日子同一本地时刻；
- ``monthly_day``：下个月指定日期同一本地时刻，超出当月天数自动
  收敛到月末（2 月 30 日 → 2 月最后一天）。

``next_occurrence`` 是纯函数：给定规则、参考 UTC 时刻与账户时区，
返回严格大于参考时刻的下一次执行的 UTC 时刻；规则参数非法时抛
:class:`ReminderRuleError`（中文原因）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bridges.contracts.reminder import ReminderRepeatRule

#: ISO 星期编号（1=周一 … 7=周日）。
_WEEKDAY_MIN = 1
_WEEKDAY_MAX = 7


class ReminderRuleError(Exception):
    """重复规则参数非法；message 为面向用户的中文说明。"""


def validate_timezone(timezone: str) -> str:
    """校验并返回 IANA 时区标识；非法时抛中文错误。"""
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ReminderRuleError(f"未知时区：{timezone}，请选择有效的 IANA 时区。") from exc
    return timezone


def localize(local_time: datetime, timezone: str) -> datetime:
    """把账户时区的本地时间转换为 UTC 时刻（DST 安全）。"""
    zone = ZoneInfo(timezone)
    if local_time.tzinfo is None:
        local_time = local_time.replace(tzinfo=zone)
    return local_time.astimezone(ZoneInfo("UTC"))


def utc_to_local(utc_time: datetime, timezone: str) -> datetime:
    """把 UTC 时刻转换为账户时区的本地时间（naive，用于展示与规则计算）。"""
    return utc_time.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)


def _weekday_of(value: datetime) -> int:
    """本地 naive 时间的星期编号（1=周一 … 7=周日）。"""
    return value.isoweekday()


def _days_until_weekday(from_local: datetime, target: int) -> int:
    """从 from_local（naive 本地）到下一个 target 星期的天数（1-7）。"""
    current = _weekday_of(from_local)
    delta = target - current
    if delta <= 0:
        delta += 7
    return delta


def _month_end(year: int, month: int) -> int:
    """给定年月当月的天数。"""
    next_month = (
        datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    )
    return (next_month - timedelta(days=1)).day


def next_occurrence(
    rule: ReminderRepeatRule,
    *,
    reference_utc: datetime,
    timezone: str,
    weekdays: list[int] | None = None,
    month_day: int | None = None,
) -> datetime | None:
    """返回严格大于 reference_utc 的下一次执行的 UTC 时刻。

    ``once`` 返回 None（无下一次）；其余规则按账户时区本地时刻推进。
    规则参数非法时抛 :class:`ReminderRuleError`。
    """
    validate_timezone(timezone)
    local = utc_to_local(reference_utc, timezone)

    if rule is ReminderRepeatRule.ONCE:
        return None
    if rule is ReminderRepeatRule.DAILY:
        target = local + timedelta(days=1)
        return localize(target, timezone)
    if rule is ReminderRepeatRule.WEEKDAYS:
        current = _weekday_of(local)  # 1=周一 … 7=周日
        # 周一至周五 → 下一个工作日；周六 → 周一(+2)，周日 → 周一(+1)
        days = 1 if current <= 5 else 8 - current
        return localize(local + timedelta(days=days), timezone)
    if rule is ReminderRepeatRule.WEEKLY_DAYS:
        if not weekdays or not all(_WEEKDAY_MIN <= d <= _WEEKDAY_MAX for d in weekdays):
            raise ReminderRuleError("每周规则必须提供 1-7 的星期集合（1=周一）。")
        unique = sorted(set(weekdays))
        next_local: datetime | None = None
        for day in unique:
            days = _days_until_weekday(local, day)
            candidate = local + timedelta(days=days)
            if next_local is None or candidate < next_local:
                next_local = candidate
        assert next_local is not None
        return localize(next_local, timezone)
    if rule is ReminderRepeatRule.MONTHLY_DAY:
        if month_day is None or not 1 <= month_day <= 31:
            raise ReminderRuleError("每月规则必须提供 1-31 的日期。")

        def _month_target(year: int, month: int) -> datetime:
            return datetime(
                year,
                month,
                min(month_day, _month_end(year, month)),
                hour=local.hour,
                minute=local.minute,
                second=local.second,
                microsecond=local.microsecond,
            )

        # 本月指定日仍在参考时刻之后 → 本月；否则下月（跨年安全）
        this_month = _month_target(local.year, local.month)
        if this_month > local:
            return localize(this_month, timezone)
        next_year = local.year + (1 if local.month == 12 else 0)
        next_month = (local.month % 12) + 1
        return localize(_month_target(next_year, next_month), timezone)
    raise ReminderRuleError(f"未知重复规则：{rule.value}。")


__all__ = [
    "ReminderRuleError",
    "localize",
    "next_occurrence",
    "utc_to_local",
    "validate_timezone",
]
