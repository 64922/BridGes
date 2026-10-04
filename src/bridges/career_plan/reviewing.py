"""个人交付的独立代码复核：从原始样本和背景重查，不采用生成者自评。"""

from __future__ import annotations

import re

from bridges.career_plan.contracts import CareerGapCategory, CareerPlanProjection
from bridges.career_plan.gap import evidence_polarity
from bridges.career_plan.lexicon import skill_present
from bridges.career_plan.parsing import detect_time_budget

_CLAUSES = re.compile(r"[。；;\n，,]+|(?:但是|但|不过)")
_SOURCE_ORDER = {"user_statement": 0, "task": 1, "resume": 2, "profile": 3}


def review_personal_projection(projection: CareerPlanProjection) -> str | None:
    """返回阻塞原因；只允许可重算分类和有原始依据的有限行动策略。"""
    background = projection.background
    items = [item for item in background.items if not item.overridden] if background else []
    for gap in projection.gaps:
        if gap.inference:
            return "新的个人能力综合推断缺少独立复核来源，保持阻塞。"
        samples = [
            sample
            for sample in projection.samples
            if any(skill_present(text, gap.term) for text in sample.requirements)
        ]
        requirements = {text.strip() for sample in samples for text in sample.requirements}
        urls = {sample.url for sample in samples}
        if not samples or not gap.job_evidence or not gap.job_sample_urls:
            return "差距的岗位要求不能定位到原始样本。"
        if any(
            text not in requirements or not skill_present(text, gap.term)
            for text in gap.job_evidence
        ):
            return "差距引用的岗位正文没有支持该技能。"
        if not set(gap.job_sample_urls).issubset(urls):
            return "差距引用的岗位链接没有支持该技能。"
        assertions = []
        for item in items:
            for clause in _CLAUSES.split(item.text):
                polarity = evidence_polarity(clause.strip(), gap.term)
                if polarity:
                    assertions.append(
                        (_SOURCE_ORDER[item.source.value], polarity, item, clause.strip())
                    )
        if assertions:
            rank = min(hit[0] for hit in assertions)
            assertions = [hit for hit in assertions if hit[0] == rank]
        polarities = {hit[1] for hit in assertions}
        expected = (
            CareerGapCategory.HAS_EVIDENCE
            if polarities == {"positive"}
            else CareerGapCategory.TO_IMPROVE
            if polarities == {"negative"}
            else CareerGapCategory.TO_CONFIRM
        )
        if gap.category is not CareerGapCategory.TO_CONFIRM and gap.category is not expected:
            return "个人分类与原始背景断言不一致，冲突或未知不能宣布为已确认。"
        if gap.category is not CareerGapCategory.TO_CONFIRM:
            refs = {hit[2].source_ref for hit in assertions}
            clauses = {hit[3] for hit in assertions}
            if not gap.background_refs or not set(gap.background_refs).issubset(refs):
                return "个人分类的背景引用没有定位到支持该断言的来源。"
            if not gap.background_evidence or any(
                line.partition("：")[2] not in clauses for line in gap.background_evidence
            ):
                return "个人分类引用的原话没有支持该断言。"
    for advice in projection.personal_advices:
        if not advice.inference:
            return "个人行动策略必须标注为建议推断，不能冒充用户直接事实。"
        if advice.kind in {"skill", "leverage", "confirm"}:
            matches = [gap for gap in projection.gaps if skill_present(advice.title, gap.term)]
            required = {
                "skill": CareerGapCategory.TO_IMPROVE,
                "leverage": CareerGapCategory.HAS_EVIDENCE,
                "confirm": CareerGapCategory.TO_CONFIRM,
            }[advice.kind]
            matches = [gap for gap in matches if gap.category is required]
            if not matches:
                return "个人行动与独立核验后的差距分类不一致。"
            supported = any(
                bool(advice.basis)
                and set(advice.basis).issubset(
                    {*gap.job_evidence, *[f"岗位样本：{url}" for url in gap.job_sample_urls]}
                )
                and (
                    advice.kind == "confirm"
                    or (
                        bool(advice.background_basis)
                        and set(advice.background_basis).issubset(gap.background_evidence)
                    )
                )
                for gap in matches
            )
            if not supported:
                return "个人行动策略缺少可定位的两侧原始支持。"
            if advice.kind == "confirm" and "不等于不足" not in advice.detail:
                return "待确认行动不得把未知写成不足。"
        elif advice.kind == "pace":
            if background is None or not any(
                item.text in advice.background_basis
                and detect_time_budget(item.text) == background.time_budget_minutes
                for item in items
            ):
                return "行动时段缺少可重算的背景原话。"
        elif advice.kind == "interest":
            if not any(
                item.relation == "interest"
                and item.source_ref in advice.background_basis
                and f"已记住信息：{item.text}" in advice.basis
                for item in items
            ):
                return "兴趣行动没有可定位的允许背景。"
        else:
            return "新的个人综合行动没有已登记的独立复核规则，保持阻塞。"
    for requirement in projection.combination_requirements:
        evidence = {text for gap in projection.gaps for text in gap.job_evidence}
        skills = {gap.term for gap in projection.gaps}
        if (
            not requirement.inference
            or not requirement.skills
            or not requirement.basis
            or not set(requirement.skills).issubset(skills)
            or not set(requirement.basis).issubset(evidence)
        ):
            return "组合需求缺少可定位的岗位依据或建议推断标注。"
    return None
