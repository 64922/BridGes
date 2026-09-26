"""``career.analyze``：只从主样本归纳技能、岗位分布与薪资区间。

每条结论都带样本量、日期、地区与计薪单位；薪资按计薪单位分别归并，不可比较
的写法只留原因、不并入区间。**样本不足时停止总体推断**：产出的是「本轮检索
所得的公开岗位样本」口径，绝不称为全国市场均值。
"""

from __future__ import annotations

from bridges.career_plan.contracts import (
    CareerAnalysis,
    CityCount,
    JobSample,
    SalaryInterval,
    SkillStat,
)
from bridges.career_plan.salary import aggregate_salary, parse_salary

#: 样本量低于此值时不支撑总体推断（也不称为市场均值）。
SMALL_SAMPLE_MIN = 5

#: 技能统计最多列出的关键词数。
MAX_SKILL_STATS = 20

#: 城市未给出时的归类名。
UNKNOWN_CITY = "页面未给出城市"


def analyze_samples(samples: list[JobSample]) -> CareerAnalysis:
    """从主样本归纳；样本为空时如实给出零样本口径。"""
    if not samples:
        return CareerAnalysis(
            sample_count=0,
            sample_scope_note="本轮没有公开可读且岗位与城市都匹配的岗位样本。",
            small_sample=True,
            overall_inference_stopped=True,
        )
    cities = _city_composition(samples)
    span = _published_span(samples)
    intervals, notes = _salary_intervals(samples)
    return CareerAnalysis(
        sample_count=len(samples),
        city_composition=cities,
        published_span=span,
        skill_stats=_skill_stats(samples),
        salary_intervals=intervals,
        incomparable_notes=notes,
        sample_scope_note=_scope_note(len(samples), cities, span),
        small_sample=len(samples) < SMALL_SAMPLE_MIN,
        overall_inference_stopped=len(samples) < SMALL_SAMPLE_MIN,
    )


def _city_composition(samples: list[JobSample]) -> list[CityCount]:
    counts: dict[str, int] = {}
    for sample in samples:
        key = (sample.city or "").strip() or UNKNOWN_CITY
        counts[key] = counts.get(key, 0) + 1
    return [
        CityCount(city=city, count=count)
        for city, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _published_span(samples: list[JobSample]) -> str | None:
    dates = sorted(
        sample.published_date for sample in samples if sample.published_date is not None
    )
    if not dates:
        return None
    if dates[0] == dates[-1]:
        return dates[0].isoformat()
    return f"{dates[0].isoformat()}–{dates[-1].isoformat()}"


def _skill_stats(samples: list[JobSample]) -> list[SkillStat]:
    counts: dict[str, list[str]] = {}
    for sample in samples:
        for skill in sample.skills:
            counts.setdefault(skill, []).append(sample.url)
    ordered = sorted(counts.items(), key=lambda item: (-len(item[1]), item[0]))
    return [
        SkillStat(term=term, count=len(urls), urls=urls)
        for term, urls in ordered[:MAX_SKILL_STATS]
    ]


def _salary_intervals(
    samples: list[JobSample],
) -> tuple[list[SalaryInterval], list[str]]:
    """按计薪单位归并薪资；不可比较的原文只留原因。"""
    bands = [parse_salary(sample.salary_raw or "") for sample in samples]
    aggregates, notes = aggregate_salary(bands)
    city_by_raw: dict[str, list[str]] = {}
    for sample in samples:
        raw = (sample.salary_raw or "").strip()
        if raw and sample.city:
            city_by_raw.setdefault(raw, []).append(sample.city)
    intervals: list[SalaryInterval] = []
    for aggregate in aggregates:
        cities: list[str] = []
        for raw in aggregate.raws:
            for city in city_by_raw.get(raw, []):
                if city not in cities:
                    cities.append(city)
        intervals.append(
            SalaryInterval(
                unit=aggregate.unit,
                sample_count=aggregate.sample_count,
                amount_min=aggregate.amount_min,
                amount_max=aggregate.amount_max,
                amount_median=aggregate.amount_median,
                cities=cities,
                raws=list(aggregate.raws),
                small_sample=aggregate.sample_count < SMALL_SAMPLE_MIN,
            )
        )
    missing = [sample for sample in samples if not (sample.salary_raw or "").strip()]
    if missing:
        notes.append(f"有 {len(missing)} 个岗位页没有给出薪资原文，未纳入任何薪资区间。")
    return intervals, notes


def _scope_note(count: int, cities: list[CityCount], span: str | None) -> str:
    """样本口径：样本量、地区构成与日期范围三件必须写清。"""
    city_part = "、".join(f"{item.city} {item.count} 个" for item in cities)
    parts = [f"本轮共取得 {count} 个公开可读且匹配的岗位样本（地区：{city_part}）"]
    parts.append(f"发布日期范围：{span}" if span else "发布日期范围：页面未给出可用日期")
    parts.append("结论只代表本轮检索所得的公开岗位样本，不是全国市场均值。")
    return "；".join(parts)
