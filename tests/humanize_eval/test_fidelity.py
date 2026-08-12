"""最小保真检查（Test plan 3：编造经历跑红；缺失检查失败关闭）。"""

from __future__ import annotations

import pytest
from conftest import ARTICLE_FAITHFUL_OUTPUT

from bridges.humanize_eval.cases import HUMANIZE_CASES, HumanizeCaseKind
from bridges.humanize_eval.fidelity import (
    FidelityCheckError,
    run_fidelity_check,
)

ARTICLE = next(
    c for c in HUMANIZE_CASES if c.case_id == "article-time-management-v1"
)
CHAT = next(c for c in HUMANIZE_CASES if c.kind is HumanizeCaseKind.CHAT)

FAITHFUL_OUTPUT = ARTICLE_FAITHFUL_OUTPUT


def test_faithful_output_passes():
    report = run_fidelity_check(ARTICLE, FAITHFUL_OUTPUT)
    assert report.passed, [c.reason for c in report.checks if not c.passed]
    assert report.missing_checks == []


def test_fabricated_first_person_fails_critical():
    for fabricated in (
        "我以前试过这个方法，效果很好。",
        "上周和朋友聊起，朋友说时间块很管用。",
        "半小时前我还用它规划了日程。",
        "刷短视频时无意间发现这个方法。",
    ):
        output = FAITHFUL_OUTPUT + fabricated
        report = run_fidelity_check(ARTICLE, output)
        assert not report.passed, fabricated
        first_person = next(
            c for c in report.checks if c.check_id == "first_person"
        )
        assert not first_person.passed
        assert first_person.severity.value == "critical"


def test_fabricated_unsourced_data_fails():
    output = FAITHFUL_OUTPUT + "一份 5000 例用户的调研显示时间块更有效。"
    report = run_fidelity_check(ARTICLE, output)
    assert not report.passed
    numbers = next(c for c in report.checks if c.check_id == "numbers")
    assert not numbers.passed
    assert "新增无来源数字" in numbers.reason


def test_lost_protected_fact_fails():
    output = FAITHFUL_OUTPUT.replace("90 分钟", "长时间")
    report = run_fidelity_check(ARTICLE, output)
    assert not report.passed
    numbers = next(c for c in report.checks if c.check_id == "numbers")
    assert not numbers.passed
    assert "丢失" in numbers.reason


def test_lost_negation_boundary_fails():
    output = FAITHFUL_OUTPUT.replace("不鼓励把任务切得过碎", "任务切碎也没关系")
    report = run_fidelity_check(ARTICLE, output)
    assert not report.passed
    negations = next(c for c in report.checks if c.check_id == "negations")
    assert not negations.passed
    assert "丢失" in negations.reason


def test_lost_quote_fails():
    output = FAITHFUL_OUTPUT.replace("“计划赶不上变化”", "计划赶不上变化")
    report = run_fidelity_check(ARTICLE, output)
    quotes = next(c for c in report.checks if c.check_id == "quotes")
    assert not quotes.passed
    assert "丢失" in quotes.reason


def test_empty_output_fails_closed():
    with pytest.raises(FidelityCheckError):
        run_fidelity_check(ARTICLE, "   ")


def test_chat_case_protected_numbers_checked():
    """无原文的聊天案例：保护项数字（25/5）必须保留。"""
    report = run_fidelity_check(CHAT, "番茄工作法：专注 25 分钟，休息 5 分钟，交替进行。")
    assert report.passed
    numbers = next(c for c in report.checks if c.check_id == "numbers")
    assert not numbers.not_applicable
    bad = run_fidelity_check(CHAT, "番茄工作法就是专注一段时间，然后休息。")
    assert not bad.passed
    numbers_bad = next(c for c in bad.checks if c.check_id == "numbers")
    assert not numbers_bad.passed


def test_not_applicable_checks_are_recorded_not_missing():
    """chat 案例无原文 URL/引语：检查如实记录 not_applicable，不算缺失。"""
    report = run_fidelity_check(CHAT, "番茄工作法：专注 25 分钟，休息 5 分钟，交替进行。")
    na = [c for c in report.checks if c.not_applicable]
    assert na, "无原文案例应有 not_applicable 检查记录"
    assert report.missing_checks == []
    assert report.passed
