"""``route.buffer``：按 ``Asia/Shanghai`` 判断课间规则缓冲（V2 Issue 12）。

规则（``docs/v2/workflows.md`` 第 3 节）：抵达时间落在八个课间时间点
（07:50、09:30、10:00、12:00、14:20、16:00、16:30、18:10）前后 **10 分钟**
内时提示「可能人多」，并在高德基础耗时之外增加 **5 分钟**。

两点必须在结果里写清：

- 这是**规则估计**，不是实时人流数据（本模块没有、也不声称有人流数据源）；
- 时间判定按固定时区 ``Asia/Shanghai`` 的分钟粒度（秒以下忽略），因此
  「前后 10 分钟」的边界（恰好 10 分钟）含在窗口内。
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from bridges.commute.contracts import CommuteBreakBuffer
from bridges.commute.lexicon import (
    BREAK_BUFFER_MINUTES,
    BREAK_RULE_NOTE,
    BREAK_TIMES,
    BREAK_WINDOW_MINUTES,
    CAMPUS_TIMEZONE,
)

#: 命中窗口时的提示（明确这是规则估计，不是实时人流数据）。
IN_WINDOW_NOTE = (
    "抵达时间落在课间点前后 10 分钟内，可能人多；"
    f"建议在高德耗时之外增加 {BREAK_BUFFER_MINUTES} 分钟。"
    "这是规则估计，不是实时人流数据。"
)

#: 未命中窗口时的说明（同样明示不是实时数据）。
OUT_OF_WINDOW_NOTE = (
    "当前时间不在课间点前后 10 分钟内，不加课间缓冲。"
    "课间提示按规则估计，不是实时人流数据。"
)


def evaluate_break_buffer(*, now: datetime | None = None) -> CommuteBreakBuffer:
    """判定当前时刻是否命中课间窗口；命中时给出建议增加的分钟数。"""
    moment = (now or datetime.now(UTC)).astimezone(ZoneInfo(CAMPUS_TIMEZONE))
    # 分钟粒度：秒与微秒忽略，边界（恰好 10 分钟）含在窗口内。
    floored = moment.replace(second=0, microsecond=0)
    matched: tuple[int, str] | None = None
    for label in BREAK_TIMES:
        hour, minute = (int(part) for part in label.split(":"))
        target = floored.replace(hour=hour, minute=minute)
        away = abs(int((target - floored).total_seconds() // 60))
        if away > BREAK_WINDOW_MINUTES:
            continue
        # 平局取更早的时间点：窗口只有 10 分钟，相邻课间点相隔 30 分钟以上时
        # 不会同时命中；此规则只是防御性顺序，保证判定与候选顺序无关。
        if matched is None or (away, label) < matched:
            matched = (away, label)
    if matched is None:
        return CommuteBreakBuffer(
            in_window=False,
            matched_break_time=None,
            minutes_away=None,
            added_minutes=0,
            checked_at=moment,
            timezone=CAMPUS_TIMEZONE,
            rule_note=f"{OUT_OF_WINDOW_NOTE}（{BREAK_RULE_NOTE}）",
        )
    away, label = matched
    return CommuteBreakBuffer(
        in_window=True,
        matched_break_time=label,
        minutes_away=away,
        added_minutes=BREAK_BUFFER_MINUTES,
        checked_at=moment,
        timezone=CAMPUS_TIMEZONE,
        rule_note=f"{IN_WINDOW_NOTE}（{BREAK_RULE_NOTE}）",
    )
