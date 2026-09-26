"""``career.advise``：把岗位要求与用户已明确的背景联系起来。

建议只由两样东西生成：**主样本里真实出现的要求原文**，以及**用户在请求里
说过的阶段／城市／约束**。凡是没有样本原文直接支持的内容（例如「做一个小
项目来练手」这种方向性建议），一律标记为推断并写明依据范围。

样本不足时不产出总体结论型的建议，只给取证动作（扩大城市或岗位范围再跑）。
"""

from __future__ import annotations

from bridges.career_plan.analyzing import SMALL_SAMPLE_MIN
from bridges.career_plan.contracts import (
    AdjacentJobSuggestion,
    CareerAdviceItem,
    CareerAnalysis,
    CareerRequestAnalysis,
    JobSample,
)

#: 技能建议最多列出的条数。
MAX_SKILL_ADVICES = 5

#: 单条建议里最多引用的要求原文条数。
MAX_BASIS_LINES = 2

#: 单条依据原文的最大字符数。
BASIS_MAX_CHARS = 120

#: 项目类建议需要的技能条目数下限。
MIN_SKILLS_FOR_PROJECT = 2


def build_advice(
    analysis: CareerRequestAnalysis,
    report: CareerAnalysis | None,
    samples: list[JobSample],
    *,
    adjacent_counts: dict[str, int] | None = None,
) -> tuple[list[CareerAdviceItem], list[AdjacentJobSuggestion]]:
    """生成建议与相邻岗位单列。零样本时不给内容型建议。"""
    adjacent = _adjacent_suggestions(analysis, adjacent_counts or {})
    if not samples or report is None or report.sample_count == 0:
        return [], adjacent
    advices = [
        *_skill_advices(report, samples),
        *_project_advice(analysis, report, samples),
        *_action_advices(analysis, report, samples, adjacent),
    ]
    return advices, adjacent


def _skill_advices(
    report: CareerAnalysis, samples: list[JobSample]
) -> list[CareerAdviceItem]:
    """按样本里出现的频次给补强顺序（依据是要求原文，不是推断）。"""
    advices: list[CareerAdviceItem] = []
    for stat in report.skill_stats[:MAX_SKILL_ADVICES]:
        basis = _basis_for_skill(samples, stat.term)
        advices.append(
            CareerAdviceItem(
                kind="skill",
                title=f"补强 {stat.term}",
                detail=(
                    f"{report.sample_count} 个岗位样本里有 {stat.count} 个在要求原文中"
                    f"提到「{stat.term}」。"
                ),
                basis=basis,
                inference=False,
            )
        )
    return advices


def _project_advice(
    analysis: CareerRequestAnalysis,
    report: CareerAnalysis,
    samples: list[JobSample],
) -> list[CareerAdviceItem]:
    """用高频要求组合出一个可写进简历的小项目（明确标注为推断）。"""
    terms = [stat.term for stat in report.skill_stats[:3]]
    if len(terms) < MIN_SKILLS_FOR_PROJECT:
        return []
    job = analysis.job_title or (analysis.job_terms[0] if analysis.job_terms else "目标岗位")
    city_part = f"，面向 {analysis.cities[0]}" if analysis.cities else ""
    return [
        CareerAdviceItem(
            kind="project",
            title=f"做一个小项目串起 {'、'.join(terms)}",
            detail=(
                f"把最常被要求的能力放进同一个可展示的项目里（{job}{city_part}）；"
                "这条建议是依据岗位要求推断出来的方向，不代表招聘方对项目的具体要求。"
            ),
            basis=[
                f"依据样本要求里的高频词：{'、'.join(terms)}",
                *[
                    f"样本链接：{sample.url}"
                    for sample in samples[:MAX_BASIS_LINES]
                ],
            ],
            inference=True,
        )
    ]


def _action_advices(
    analysis: CareerRequestAnalysis,
    report: CareerAnalysis,
    samples: list[JobSample],
    adjacent: list[AdjacentJobSuggestion],
) -> list[CareerAdviceItem]:
    """取证与投递动作（可直接执行，不依赖对用户背景的猜测）。"""
    job = analysis.job_title or (analysis.job_terms[0] if analysis.job_terms else "目标岗位")
    city_part = f"，城市限定 {'、'.join(analysis.cities)}" if analysis.cities else "，暂未限定城市"
    stage_part = f"，阶段 {analysis.stage}" if analysis.stage else ""
    advices = [
        CareerAdviceItem(
            kind="action",
            title=f"按「{job}」继续跟进新岗位",
            detail=f"用同样的岗位名与筛选条件（{city_part}{stage_part}）再跑一轮，"
            "把新出现的岗位与这几条样本对比。",
            basis=[f"本轮实际查询词可在检索计划中逐条查看（共 {report.sample_count} 个样本）。"],
            inference=False,
        ),
        CareerAdviceItem(
            kind="action",
            title="逐条核对岗位页上的城市与经验要求",
            detail=(
                "样本里已经记录了每个岗位的城市核对依据与经验要求原文；"
                "投递前按岗位页原文复核，不要用本轮的汇总区间代替岗位页。"
            ),
            basis=[
                f"样本链接：{sample.url}" for sample in samples[:MAX_BASIS_LINES]
            ],
            inference=False,
        ),
    ]
    if report.overall_inference_stopped:
        advices.append(
            CareerAdviceItem(
                kind="action",
                title="先扩大范围再下结论",
                detail=(
                    f"本轮只有 {report.sample_count} 个样本（不足 "
                    f"{SMALL_SAMPLE_MIN} 个），因此没有给出市场层面的结论；"
                    "可以放宽城市或加上同义岗位名再跑一轮。"
                ),
                basis=[report.sample_scope_note],
                inference=False,
            )
        )
    if adjacent:
        titles = "、".join(item.title for item in adjacent[:3])
        advices.append(
            CareerAdviceItem(
                kind="action",
                title=f"相邻岗位可单独考虑：{titles}",
                detail=(
                    "这些岗位与目标岗位相邻，本轮检索到的相邻岗位没有并入上面的技能与"
                    "薪资统计；如果愿意放宽目标，可以单独跑一轮。"
                ),
                basis=[item.reason for item in adjacent[:MAX_BASIS_LINES]],
                inference=False,
            )
        )
    return advices


def _adjacent_suggestions(
    analysis: CareerRequestAnalysis, counts: dict[str, int]
) -> list[AdjacentJobSuggestion]:
    """相邻岗位单列建议（绝不并入主样本的统计）。"""
    job = analysis.job_title or (analysis.job_terms[0] if analysis.job_terms else "")
    return [
        AdjacentJobSuggestion(
            title=title,
            reason=(
                f"「{title}」与目标岗位「{job}」相邻但不是同一个岗位；"
                "按编排合同单列，不混入主样本。"
            ),
            sample_count=counts.get(title, 0),
        )
        for title in analysis.adjacent_jobs[:5]
    ]


def _basis_for_skill(samples: list[JobSample], term: str) -> list[str]:
    """技能词的依据：命中该词的要求原文与来源链接。"""
    lines: list[str] = []
    urls: list[str] = []
    for sample in samples:
        for requirement in sample.requirements:
            if term in requirement and len(lines) < MAX_BASIS_LINES:
                text = requirement.strip()
                lines.append(
                    text[:BASIS_MAX_CHARS] + ("…" if len(text) > BASIS_MAX_CHARS else "")
                )
        if term in " ".join(sample.requirements) and sample.url not in urls:
            urls.append(sample.url)
    if not lines:
        lines.append(f"命中「{term}」的要求原文未能在样本里保留，仅按技能词计数。")
    for url in urls[:MAX_BASIS_LINES]:
        lines.append(f"样本链接：{url}")
    return lines
