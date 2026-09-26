"""``career.plan``：生成并展示实际查询词与筛选条件。

分别对三类来源各发一条有界查询：公开招聘职位页（BOSS 等岗位页）、企业招聘
页、校招页。查询词只用用户说出的岗位锚点原话、城市与阶段，加上固定字面量；
筛选条件逐条列出，用户看到的就是将要执行的东西。
"""

from __future__ import annotations

from bridges.career_plan.contracts import CareerQueryPlanItem, CareerRequestAnalysis
from bridges.career_plan.lexicon import (
    SOURCE_BOSS,
    SOURCE_CAMPUS,
    SOURCE_CORPORATE,
    SOURCE_LABELS,
)

#: 各来源的固定查询字面量（域名字面量必须完整发送才能被召回）。
BOSS_HOST = "zhipin.com"
CAMPUS_HOST = "yingjiesheng.com"

#: 每条查询最多使用的岗位锚点数。
MAX_QUERY_JOBS = 2


def build_plan(
    analysis: CareerRequestAnalysis,
    *,
    cities: list[str] | None = None,
) -> tuple[CareerQueryPlanItem, ...]:
    """生成三条来源查询；返回的顺序即执行顺序。"""
    jobs = _query_jobs(analysis)
    if not jobs:
        return ()
    city_list = cities or analysis.cities
    city = city_list[0] if city_list else None
    city_part = city or ""
    stage_part = _stage_part(analysis)
    filters = _filters(analysis, city)
    return (
        CareerQueryPlanItem(
            source=SOURCE_BOSS,
            source_label=SOURCE_LABELS[SOURCE_BOSS],
            query=_join(BOSS_HOST, jobs, city_part, "招聘"),
            reason="先在公开招聘职位页找在招的岗位，拿到岗位名、城市与薪资原文。",
            filters=list(filters),
        ),
        CareerQueryPlanItem(
            source=SOURCE_CORPORATE,
            source_label=SOURCE_LABELS[SOURCE_CORPORATE],
            query=_join(jobs, city_part, "招聘", "官网"),
            reason="再找企业官网的招聘栏目，核对公司自己发布的岗位与要求。",
            filters=list(filters),
        ),
        CareerQueryPlanItem(
            source=SOURCE_CAMPUS,
            source_label=SOURCE_LABELS[SOURCE_CAMPUS],
            query=_join(CAMPUS_HOST, jobs, city_part, "校园招聘", stage_part),
            reason="最后找校招页，补充面向在校生与应届生的岗位。",
            filters=[*filters, "校园招聘/应届生口径"],
        ),
    )


def _query_jobs(analysis: CareerRequestAnalysis) -> tuple[str, ...]:
    """检索锚点：用户原话岗位词（最多两条，避免查询过窄）。"""
    return tuple(analysis.job_terms[:MAX_QUERY_JOBS])


def _stage_part(analysis: CareerRequestAnalysis) -> str:
    if analysis.graduation_year is not None:
        return f"{analysis.graduation_year}届"
    return analysis.stage or ""


def _filters(analysis: CareerRequestAnalysis, city: str | None) -> list[str]:
    filters = ["岗位名必须命中目标岗位或其同义名"]
    if city:
        filters.append(f"城市必须是「{city}」")
    else:
        filters.append("未给出城市：不按城市过滤，但逐条标注岗位实际城市")
    stage = _stage_part(analysis)
    if stage:
        filters.append(f"阶段：{stage}")
    if analysis.experience_hint:
        filters.append(f"经验要求：{analysis.experience_hint}")
    filters.append("排除相邻岗位、已过期与重复岗位")
    return filters


def _join(*parts: str | tuple[str, ...]) -> str:
    """拼装查询词：跳过空片段，单空格分隔（逐字保留原词，不做同义替换）。"""
    flat: list[str] = []
    for part in parts:
        if isinstance(part, tuple):
            flat.extend(item for item in part if item and item.strip())
        elif part and part.strip():
            flat.append(part.strip())
    return " ".join(flat)
