"""工单 28 独立验收发现的薪资边界回归。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from bridges.career_plan.collecting import parse_job_page
from bridges.career_plan.salary import aggregate_salary, parse_salary


@pytest.mark.parametrize(
    ("raw", "currency"),
    [("20000-30000港元/月", "HKD"), ("2000-3000欧元/月", "EUR"),
     ("200000-300000日元/月", "JPY"), ("CAD 2000-3000/月", "CAD"),
     ("20KUSD/月", "USD")],
)
def test_explicit_currencies_are_not_relabelled_as_yuan(raw: str, currency: str) -> None:
    band = parse_salary(raw)
    assert band.currency == currency
    intervals, _ = aggregate_salary([parse_salary("2000-3000元/月"), band])
    assert {item.currency for item in intervals} == {"CNY", currency}


def test_explicit_twelve_salary_months_are_preserved() -> None:
    assert parse_salary("15-25K·12薪").salary_months == 12


def test_foreign_k_without_period_is_not_assumed_monthly() -> None:
    assert not parse_salary("USD 80-100K").comparable


def test_even_sample_median_uses_both_middle_values() -> None:
    intervals, _ = aggregate_salary([parse_salary("10K"), parse_salary("20K")])
    assert intervals[0].amount_median == 15000


def test_structured_salary_retains_currency_from_the_actual_page() -> None:
    posting = {
        "@type": "JobPosting", "title": "Java后端开发工程师",
        "description": "岗位职责：开发后端接口", "jobLocation": {"name": "上海"},
        "baseSalary": {"currency": "USD", "value": {
            "minValue": 5000, "maxValue": 6000, "unitText": "MONTH"}},
    }
    html = '<script type="application/ld+json">' + json.dumps(posting) + '</script>'
    page = parse_job_page(html, reference=datetime.now(UTC))
    assert page.salary_raw is not None
    band = parse_salary(page.salary_raw)
    assert band.currency == "USD"
    assert band.unit == "美元/月"


def test_structured_salary_without_currency_is_not_assumed_yuan() -> None:
    posting = {
        "@type": "JobPosting", "title": "Java后端开发工程师",
        "description": "岗位职责：开发后端接口",
        "baseSalary": {"value": {"value": 5000, "unitText": "MONTH"}},
    }
    html = '<script type="application/ld+json">' + json.dumps(posting) + '</script>'
    page = parse_job_page(html, reference=datetime.now(UTC))
    band = parse_salary(page.salary_raw or "")
    assert band.currency == "UNKNOWN" and not band.comparable
