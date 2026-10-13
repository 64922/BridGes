"""贴吧时间条件必须映射到真实日历区间或明确报告未执行。"""

from datetime import UTC, datetime

import pytest

from bridges.tieba.contracts import ReadStatus, TiebaPostProjection, TiebaReply
from bridges.tieba.evidence_time import apply_time_condition
from bridges.tieba.parsing import is_continue_request, parse_tieba_request


def _post(*dates: str) -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id="123",
        url="https://tieba.baidu.com/p/123",
        title="宿舍讨论",
        affiliation_evidence="已读取目标贴吧页面",
        read_status=ReadStatus.PARTIAL,
        pages_read=1,
        pages_limit=1,
        replies_obtained=bool(dates),
        replies=[
            TiebaReply(floor=index, posted_at=value, content="宿舍讨论")
            for index, value in enumerate(dates, start=1)
        ],
        retrieved_at=datetime(2026, 9, 25, tzinfo=UTC),
    )


def test_explicit_year_month_uses_only_the_requested_calendar_month() -> None:
    analysis = parse_tieba_request("华东交通大学吧 2025年9月 宿舍")
    state, posts = apply_time_condition(
        analysis, [_post("2025-09-01", "2025-09-30", "2025-08-31", "2025-10-01")]
    )

    assert state.since == datetime(2025, 9, 1, tzinfo=UTC)
    assert state.until == datetime(2025, 9, 30, tzinfo=UTC)
    assert state.recent_count == 2
    assert state.historical_count == 2
    assert [reply.temporal_scope.value for reply in posts[0].replies] == [
        "recent",
        "recent",
        "historical",
        "historical",
    ]


def test_relative_week_uses_calendar_week_boundaries() -> None:
    analysis = parse_tieba_request("华东交通大学吧 上周 宿舍")
    state, posts = apply_time_condition(analysis, [_post("2026-09-14", "2026-09-20", "2026-09-21")])

    assert state.since == datetime(2026, 9, 14, tzinfo=UTC)
    assert state.until == datetime(2026, 9, 20, tzinfo=UTC)
    assert [reply.temporal_scope.value for reply in posts[0].replies] == [
        "recent",
        "recent",
        "historical",
    ]


def test_explicit_month_range_keeps_both_ends() -> None:
    analysis = parse_tieba_request("华东交通大学吧 2025年9月至2026年2月 宿舍")
    state, _ = apply_time_condition(
        analysis,
        [_post("2025-08-31", "2025-09-01", "2026-02-28", "2026-03-01")],
    )

    assert analysis.time_requirement == "2025年9月至2026年2月"
    assert state.since == datetime(2025, 9, 1, tzinfo=UTC)
    assert state.until == datetime(2026, 2, 28, tzinfo=UTC)
    assert state.recent_count == 2 and state.historical_count == 2


def test_month_range_can_omit_the_repeated_end_year() -> None:
    analysis = parse_tieba_request("华东交通大学吧 2025年9月至12月 宿舍")
    state, _ = apply_time_condition(
        analysis, [_post("2025-08-31", "2025-09-01", "2025-12-31", "2026-01-01")]
    )

    assert analysis.time_requirement == "2025年9月至12月"
    assert state.since == datetime(2025, 9, 1, tzinfo=UTC)
    assert state.until == datetime(2025, 12, 31, tzinfo=UTC)
    assert state.recent_count == 2 and state.historical_count == 2


@pytest.mark.parametrize("window", ("近三个月", "最近三个月", "过去三个月"))
def test_recent_n_months_preserves_the_complete_explicit_interval(window: str) -> None:
    analysis = parse_tieba_request(f"华东交通大学吧 {window} 宿舍")
    state, _ = apply_time_condition(analysis, [_post("2026-06-24", "2026-06-25", "2026-09-25")])

    assert analysis.time_requirement == window
    assert state.since == datetime(2026, 6, 25, tzinfo=UTC)
    assert state.until == datetime(2026, 9, 25, tzinfo=UTC)
    assert state.recent_count == 2 and state.historical_count == 1


def test_recent_seven_days_preserves_the_complete_explicit_interval() -> None:
    analysis = parse_tieba_request("华东交通大学吧 最近7天 宿舍")
    state, _ = apply_time_condition(
        analysis, [_post("2026-09-18", "2026-09-19", "2026-09-25", "2026-09-26")]
    )

    assert analysis.time_requirement == "最近7天"
    assert state.since == datetime(2026, 9, 19, tzinfo=UTC)
    assert state.until == datetime(2026, 9, 25, tzinfo=UTC)
    assert state.recent_count == 2 and state.historical_count == 2


def test_recent_two_weeks_preserves_the_complete_explicit_interval() -> None:
    analysis = parse_tieba_request("华东交通大学吧 最近两周 宿舍")
    state, _ = apply_time_condition(
        analysis, [_post("2026-09-11", "2026-09-12", "2026-09-25", "2026-09-26")]
    )

    assert analysis.time_requirement == "最近两周"
    assert state.since == datetime(2026, 9, 12, tzinfo=UTC)
    assert state.until == datetime(2026, 9, 25, tzinfo=UTC)
    assert state.recent_count == 2 and state.historical_count == 2


def test_unmappable_semester_condition_is_not_silently_widened() -> None:
    analysis = parse_tieba_request("华东交通大学吧 上学期 宿舍")
    state, posts = apply_time_condition(analysis, [_post("2026-09-01")])

    assert state.applied is False
    assert state.since is None and state.until is None
    assert posts[0].replies[0].temporal_scope.value == "unknown"
    assert "无法可靠换算成日历区间" in state.note


def test_only_standalone_continue_phrases_resume_a_waiting_tieba_task() -> None:
    assert is_continue_request("继续")
    assert is_continue_request("恢复刚才")
    assert not is_continue_request("继续查宿舍条件")
