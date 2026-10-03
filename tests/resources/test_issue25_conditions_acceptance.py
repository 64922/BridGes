"""工单 25：显式选择条件不能被解析或默认计划静默丢弃。"""

import pytest

from bridges.resources.contracts import ResourceMedia, ResourcesGoalKind, ResourcesTermAnalysis
from bridges.resources.parsing import read_conditions
from bridges.resources.planning import plan_resources


def analysis(**conditions: object) -> ResourcesTermAnalysis:
    return ResourcesTermAnalysis.model_validate({
        "original_phrase": "机器学习", "normalized_term": "机器学习",
        "confidence": 1.0, "goal_kind": ResourcesGoalKind.SYSTEMATIC, **conditions,
    })


@pytest.mark.parametrize("text,books,videos", [
    ("0本书，25条视频", 0, 25), ("零本书，零条视频", 0, 0), ("二十五本书", 25, None),
])
def test_explicit_counts_are_preserved(text: str, books: int, videos: int | None) -> None:
    parsed = read_conditions(text)
    assert (parsed.requested_books, parsed.requested_videos) == (books, videos)


@pytest.mark.parametrize("time", ["每天半小时", "一天", "每天30分钟", "每日1小时"])
def test_time_phrase_is_complete_and_limits_default_path(time: str) -> None:
    parsed = read_conditions(f"系统学习机器学习，{time}，我学过线性代数")
    assert parsed.time_budget == time
    assert parsed.basis_evidence == "我学过线性代数"
    plan = plan_resources(analysis(time_budget=parsed.time_budget))
    assert (plan.target_books, plan.target_videos) == (1, 1)
    assert "不保证" in plan.rationale


def test_language_is_part_of_public_query_without_private_basis() -> None:
    plan = plan_resources(analysis(language="中文", basis_evidence="我学过线性代数"))
    assert "中文" in plan.book_query and "中文" in plan.video_query
    assert "线性代数" not in plan.book_query + plan.video_query


def test_explicit_count_has_priority_over_time_but_cannot_override_media() -> None:
    plan = plan_resources(analysis(media=ResourceMedia.BOOKS, requested_books=25,
                                   requested_videos=3, time_budget="一天"))
    assert (plan.target_books, plan.target_videos) == (25, 0)
    assert "媒介" in plan.rationale


def test_longer_daily_time_does_not_shorten_default_path() -> None:
    plan = plan_resources(analysis(time_budget="每天2小时"))
    assert (plan.target_books, plan.target_videos) == (2, 2)
