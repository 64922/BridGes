"""``career.plan``：生成并展示实际查询词与筛选条件。

分别对三类来源各发一条有界查询：公开招聘职位页（BOSS 等岗位页）、企业招聘
页、校招页。查询词只用岗位锚点原话、城市及由阶段确定的招聘口径，加上固定字面量；
筛选条件逐条列出，用户看到的就是将要执行的东西。
"""

from __future__ import annotations

from bridges.career_plan.contracts import CareerQueryPlanItem, CareerRequestAnalysis
from bridges.career_plan.lexicon import (
    SOURCE_BOSS,
    SOURCE_CAMPUS,
    SOURCE_CORPORATE,
    SOURCE_LABELS,
    family_for,
)

#: 公开招聘限定到岗位详情路径，避免列表页占满结果。
BOSS_HOST = "site:zhipin.com/job_detail/"

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
            query=_join(jobs, city_part, "校园招聘", _recruitment_stage(analysis)),
            reason=(
                "最后找校招页，补充面向在校生与应届生的岗位。"
                if not stage_part
                else f"最后找校招与实习详情页；你的阶段是「{stage_part}」，"
                f"实际检索口径为「{_recruitment_stage(analysis)}」。"
            ),
            filters=[*filters, "校园招聘/应届生口径"],
        ),
    )


def _query_jobs(analysis: CareerRequestAnalysis) -> tuple[str, ...]:
    """检索锚点：用户原话岗位词（最多两条，避免查询过窄）。"""
    return tuple(analysis.job_terms[:MAX_QUERY_JOBS])


def _recruitment_stage(analysis: CareerRequestAnalysis) -> str:
    """招聘页使用实习／届别口径，年级原话保留在解析结果里。"""
    if analysis.graduation_year is not None:
        return f"{analysis.graduation_year}届"
    if analysis.stage in {"大一", "大二", "大三", "研一", "研二"}:
        return "实习"
    return analysis.stage or ""


def build_recovery_plan(analysis: CareerRequestAnalysis) -> tuple[CareerQueryPlanItem, ...]:
    """Agent 详情全部不可用时，同岗位方向最多补搜两次，不放宽匹配条件。"""
    if not any(
        family is not None and family.key == "agent"
        for family in (family_for(term) for term in analysis.job_terms)
    ):
        return ()
    city = analysis.cities[0] if analysis.cities else None
    stage = _recruitment_stage(analysis)
    return tuple(
        CareerQueryPlanItem(
            source=source, source_label=SOURCE_LABELS[source],
            query=_join(words, city or "", stage, "招聘", "岗位职责"),
            reason="原查询没有取得可用详情，按同一 Agent 方向的招聘说法补搜；取得样本即结束。",
            filters=_filters(analysis, city),
        )
        for source, words in (
            (SOURCE_CORPORATE, "Agent 大模型 算法"),
            (SOURCE_CAMPUS, "Agent 智能体 开发"),
        )
    )


def _stage_part(analysis: CareerRequestAnalysis) -> str:
    if analysis.graduation_year is not None:
        return f"{analysis.graduation_year}届"
    return analysis.stage or ""


def _filters(analysis: CareerRequestAnalysis, city: str | None) -> list[str]:
    """筛选条件只列**实际执行**的判定。

    阶段只进查询词（见 ``build_plan`` 的 reason 与证据边界）；城市与经验
    条件都在 ``career.filter`` 里逐条代码核对，因此写进筛选条件。
    """
    filters = ["岗位名或职责原文必须命中目标岗位及其同义职责"]
    if city:
        filters.append(f"城市必须是「{city}」")
        others = [item for item in analysis.cities if item != city]
        if others:
            filters.append(
                f"你给了多个城市，本轮只按第一个城市「{city}」检索与过滤；"
                f"「{'、'.join(others)}」本轮未检索"
            )
    else:
        filters.append("未给出城市：不按城市过滤，但逐条标注岗位实际城市")
    if analysis.experience_hint:
        filters.append(
            f"经验要求必须核对为「{analysis.experience_hint}」；页面未给出经验时不进入样本"
        )
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
