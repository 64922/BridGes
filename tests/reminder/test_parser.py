"""提醒自然语言解析器测试（Issue 33，确定性规则矩阵）。

固定参考时刻：2026-08-05（周三）02:00 UTC = 北京 10:00。验证日锚/
时刻/重复规则/主题提取/时区换算/错误原因；解析器确定性（同一输入
两次解析结果一致，创建复核与预览一致）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bridges.reminder.parser import ReminderParseError, parse_reminder_text
from bridges.reminder.rules import ReminderRuleError

NOW = datetime(2026, 8, 5, 2, 0, tzinfo=UTC)  # 北京 2026-08-05 10:00


def _parse(text: str, tz: str = "Asia/Shanghai"):
    return parse_reminder_text(text, timezone=tz, now_utc=NOW)


def _local(schedule) -> str:
    return schedule.first_run_at.astimezone(UTC).isoformat()


def test_relative_day_and_period() -> None:
    """明天早上八点 → 次日 08:00（北京），UTC 时刻正确换算。"""
    schedule, subject = _parse("明天早上八点提醒我复习 transformer")
    assert _local(schedule) == "2026-08-06T00:00:00+00:00"
    assert schedule.first_run_local == "2026-08-06 08:00"
    assert schedule.timezone == "Asia/Shanghai"
    assert subject == "复习 transformer"


def test_explicit_today_past_rejected() -> None:
    """今天 9 点（已过）显式指定 → 明确中文错误。"""
    with pytest.raises(ReminderParseError) as exc:
        _parse("今天九点提醒我开会")
    assert "已经过去" in str(exc.value)


def test_implicit_time_past_advances() -> None:
    """无日锚且时刻已过（八点）→ 顺延到明天。"""
    schedule, subject = _parse("八点提醒我喝水")
    assert _local(schedule) == "2026-08-06T00:00:00+00:00"
    assert subject == "喝水"


def test_colon_clock() -> None:
    """半角冒号时刻 + 每日规则。"""
    schedule, subject = _parse("每天 8:30 提醒我背单词")
    assert schedule.repeat.value == "daily"
    assert schedule.first_run_local == "2026-08-06 08:30"
    assert subject == "背单词"


def test_chinese_number_and_half() -> None:
    """中文数字与「点半」。"""
    schedule, _ = _parse("下午三点半提醒我开会")
    assert schedule.first_run_local == "2026-08-05 15:30"


def test_evening_period_afternoon_mapping() -> None:
    """时段 → 24 小时制映射（晚上八点 = 20:00，下午三点 = 15:00）。"""
    evening, _ = _parse("今晚十点提醒我睡觉")
    assert evening.first_run_local == "2026-08-05 22:00"
    afternoon, _ = _parse("明天下午三点提醒我锻炼")
    assert afternoon.first_run_local == "2026-08-06 15:00"


def test_weekdays_repeat() -> None:
    """工作日规则。"""
    schedule, subject = _parse("工作日早上九点提醒我打卡")
    assert schedule.repeat.value == "weekdays"
    assert schedule.first_run_local == "2026-08-06 09:00"
    assert subject == "打卡"


def test_weekly_days_with_he_and_separators() -> None:
    """每周一和周三、顿号分隔。"""
    schedule, subject = _parse("每周一和周三下午3点半提醒我开会")
    assert schedule.repeat.value == "weekly_days"
    assert schedule.repeat_weekdays == [1, 3]
    assert schedule.first_run_local == "2026-08-10 15:30"
    assert subject == "开会"
    schedule2, _ = _parse("每周一、周五早上8点提醒我测血糖")
    assert schedule2.repeat_weekdays == [1, 5]


def test_monthly_day() -> None:
    """每月 N 号（中文与阿拉伯数字）。"""
    schedule, subject = _parse("每月15号晚上8点提醒我还信用卡")
    assert schedule.repeat.value == "monthly_day"
    assert schedule.repeat_month_day == 15
    assert schedule.first_run_local == "2026-08-15 20:00"
    assert subject == "还信用卡"
    schedule2, _ = _parse("每月一号早上9点提醒我交房租")
    assert schedule2.repeat_month_day == 1
    assert schedule2.first_run_local == "2026-09-01 09:00"


def test_next_week_anchor() -> None:
    """下周三 → 下周的周三（不是本周）。"""
    schedule, subject = _parse("下周三下午三点提醒我交报告")
    assert schedule.first_run_local == "2026-08-12 15:00"
    assert subject == "交报告"


def test_weekday_anchor_nearest() -> None:
    """周五（无周前缀）→ 最近一个周五（2026-08-07）。"""
    schedule, _ = _parse("周五晚上八点提醒我看电影")
    assert schedule.first_run_local == "2026-08-07 20:00"


def test_this_week_anchor() -> None:
    """本周日（已过？8 月 9 日未过）→ 本周日。"""
    schedule, _ = _parse("本周日上午十点提醒我做周报")
    assert schedule.first_run_local == "2026-08-09 10:00"


def test_day_after_tomorrow() -> None:
    """大后天。"""
    schedule, subject = _parse("大后天中午12点提醒我取快递")
    assert schedule.first_run_local == "2026-08-08 12:00"
    assert subject == "取快递"


def test_once_repeat_explicit() -> None:
    """显式「只提醒一次」在动词之后仍被识别且不残留主题。"""
    schedule, subject = _parse("明天晚上8点提醒我喝水，只提醒一次")
    assert schedule.repeat.value == "once"
    assert subject == "喝水"


def test_missing_subject_error() -> None:
    """无主题 → 明确中文原因。"""
    with pytest.raises(ReminderParseError) as exc:
        _parse("明天早上八点")
    assert "主题" in str(exc.value)


def test_missing_time_error() -> None:
    """无时间 → 明确中文原因。"""
    with pytest.raises(ReminderParseError) as exc:
        _parse("提醒我复习")
    assert "时间" in str(exc.value)


def test_empty_input_error() -> None:
    with pytest.raises(ReminderParseError):
        _parse("   ")


def test_invalid_timezone_error() -> None:
    with pytest.raises(ReminderRuleError):
        _parse("明天八点提醒我复习", tz="Mars/Olympus")


def test_other_timezone_conversion() -> None:
    """其他时区（UTC+8 以外）换算正确。

    NOW = 2026-08-05 02:00 UTC = 纽约 08-04 22:00 EDT；「明天」从纽约
    视角是 08-05，08:00 EDT = 12:00 UTC。
    """
    schedule, _ = parse_reminder_text(
        "明天早上八点提醒我复习", timezone="America/New_York", now_utc=NOW
    )
    assert _local(schedule) == "2026-08-05T12:00:00+00:00"


def test_deterministic_same_input_same_result() -> None:
    """同一输入两次解析结果一致（创建复核与预览一致的前提）。"""
    first = _parse("明天早上八点提醒我复习 transformer")
    second = _parse("明天早上八点提醒我复习 transformer")
    assert first[0].model_dump() == second[0].model_dump()
    assert first[1] == second[1]


def test_subject_with_keywords_preserved() -> None:
    """主题中的「每天」不被误剥（动词之后的日程词属于主题内容）。

    「每天」同时被识别为每日重复规则（规则短语全文本扫描），但主题
    保留原文，两种语义不互相破坏。
    """
    schedule, subject = _parse("明天早上八点提醒我每天背单词")
    assert subject == "每天背单词"
    assert schedule.repeat.value == "daily"


def test_no_period_midnight() -> None:
    """凌晨时段。"""
    schedule, _ = _parse("后天凌晨一点提醒我抢票")
    assert schedule.first_run_local == "2026-08-07 01:00"
