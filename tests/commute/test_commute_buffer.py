"""``route.buffer`` 单元合同：八个课间点、±10 分钟边界、+5 分钟与规则声明。

Issue 12 验收项要求「按 ``Asia/Shanghai`` 判断八个课间时间点前后十分钟的边界及
五分钟规则缓冲，并明确这不是实时人流数据」。用例固定边界（恰好 10 分钟含在窗口
内）、时区换算与「不是实时人流数据」的措辞。
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from bridges.commute.buffer import evaluate_break_buffer
from bridges.commute.lexicon import BREAK_TIMES, CAMPUS_TIMEZONE

SHANGHAI = ZoneInfo(CAMPUS_TIMEZONE)


def _at(hour: int, minute: int, *, day: int = 25) -> datetime:
    """按 Asia/Shanghai 构造时刻（内部再转 UTC，验证时区换算真的生效）。"""
    return datetime(2026, 9, day, hour, minute, tzinfo=SHANGHAI).astimezone(UTC)


def test_all_eight_break_times_are_exactly_in_window() -> None:
    assert len(BREAK_TIMES) == 8
    for label in BREAK_TIMES:
        hour, minute = (int(part) for part in label.split(":"))
        buffer = evaluate_break_buffer(now=_at(hour, minute))
        assert buffer.in_window is True
        assert buffer.matched_break_time == label
        assert buffer.minutes_away == 0
        assert buffer.added_minutes == 5
        assert buffer.timezone == CAMPUS_TIMEZONE


def test_ten_minute_boundaries_are_inclusive() -> None:
    before = evaluate_break_buffer(now=_at(11, 50))
    after = evaluate_break_buffer(now=_at(12, 10))

    assert before.in_window is True
    assert before.matched_break_time == "12:00"
    assert before.minutes_away == 10
    assert after.in_window is True
    assert after.matched_break_time == "12:00"
    assert after.minutes_away == 10


def test_outside_the_window_adds_no_buffer() -> None:
    early = evaluate_break_buffer(now=_at(11, 49))
    late = evaluate_break_buffer(now=_at(12, 11))

    assert early.in_window is False
    assert early.added_minutes == 0
    assert early.matched_break_time is None
    assert late.in_window is False
    assert late.added_minutes == 0


def test_equal_distance_picks_the_earlier_break_time() -> None:
    """09:40 距 09:30 与 10:00 都是 10 分钟：取更早的时间点（确定性）。"""
    buffer = evaluate_break_buffer(now=_at(9, 40))

    assert buffer.in_window is True
    assert buffer.matched_break_time == "09:30"
    assert buffer.minutes_away == 10


def test_seconds_are_ignored_at_minute_granularity() -> None:
    moment = datetime(2026, 9, 25, 12, 10, 59, tzinfo=SHANGHAI).astimezone(UTC)

    buffer = evaluate_break_buffer(now=moment)

    assert buffer.in_window is True
    assert buffer.minutes_away == 10


def test_notes_say_this_is_a_rule_not_realtime_crowd_data() -> None:
    inside = evaluate_break_buffer(now=_at(12, 0))
    outside = evaluate_break_buffer(now=_at(3, 0))

    for buffer in (inside, outside):
        assert "不是实时人流数据" in buffer.rule_note
        assert "规则估计" in buffer.rule_note
    assert "可能人多" in inside.rule_note
    assert "加 5 分钟" in inside.rule_note
    assert "不加课间缓冲" in outside.rule_note


def test_checked_at_is_reported_in_shanghai_time() -> None:
    buffer = evaluate_break_buffer(now=_at(12, 0))

    assert buffer.checked_at.astimezone(SHANGHAI).hour == 12
    assert buffer.checked_at.astimezone(SHANGHAI).minute == 0
