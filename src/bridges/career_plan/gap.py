"""工单 29：岗位要求 × 用户证据的逐项对照与个人优先行动。

判定只使用两侧的真实文本，不调用模型：

- **岗位侧**：工单 28 主样本页面上的要求原文（技能统计里的词条）。
- **用户侧**：当前陈述、当前任务原话与允许使用的 19 切片采用条目。

三种分类的语义边界：

- ``has_evidence``：背景里有明确依据（会/熟悉/学过/正在学等，注明确切程度）。
- ``to_improve``：背景里**明确**说需要补或尚未掌握；
- ``to_confirm``：没有提供相关背景。**未知不等于不足**，绝不写成弱项。

个人行动按「明确待提升 → 已有依据可转化 → 待确认先自测」排序，并把当前
陈述的时间约束转成可执行的推进说明；兴趣只调整练习题材，不替换岗位目标。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from bridges.career_plan.contracts import (
    CareerAdviceItem,
    CareerAnalysis,
    CareerBackgroundItem,
    CareerBackgroundSnapshot,
    CareerCombinationRequirement,
    CareerGapCategory,
    CareerGapItem,
    CareerRequestAnalysis,
    JobSample,
)
from bridges.career_plan.lexicon import detect_skills, skill_present
from bridges.career_plan.parsing import detect_time_budget

#: 逐项对照最多覆盖的要求词条数。
MAX_GAP_ITEMS = 8

#: 单条差距最多保留的岗位侧要求原文与样本链接。
MAX_JOB_EVIDENCE_LINES = 2

#: 用户侧证据最多保留的原话句数。
MAX_BACKGROUND_EVIDENCE_LINES = 2

#: 背景原文里「已有依据」的表述特征（「实习」单独出现不算证据，避免求职原话误判）。
_POSITIVE_RE = re.compile(
    r"会|熟悉|熟练|掌握|精通|了解|擅长|用过|做过|写过|搭建过|学过|修过|"
    r"项目中|实习过|实习中|在实习|有.{0,6}经验|正在学|在学|开始学|刚学"
)

#: 背景原文里「明确待提升」的表述特征（用户明确说出的缺口或补学打算）。
_NEGATIVE_RE = re.compile(
    r"不会|不熟悉|不熟练|没学过|没有学过|没接触过|没做过|没写过|没有.{0,6}经验|"
    r"欠缺|缺乏|薄弱|零基础|不懂|不太会|不怎么会|不擅长|待补|"
    r"要补习|补一下|还没学|没系统学过"
)

#: 当前目标里包含学习资料或实践项目时，输出供工单 37 组合的最小需求产物。
_RESOURCES_RE = re.compile(r"资料|学习|课程|书|教程|视频|资源|教材|网课|刷题")
_PROJECT_RE = re.compile(r"项目|练手|实践|github|开源|作品", re.IGNORECASE)

_SENTENCE_SPLIT_RE = re.compile(r"[。；;\n，,]+|(?:但是|但|不过)")
# 引用、第三方、疑问和愿望不能当作用户已经具备/缺少能力的断言。
_NON_ASSERTION_RE = re.compile(
    r"[？?吗呢]|是否|如果|假如|岗位|招聘|要求|不确定|可能|也许|能否|不是不会|并非不会|不会只|朋友|同学|同事|他|她|你|"
    r"[‘’“”\"「」]|想学|打算学|准备学|需要学|学会"
)

_SOURCE_LABELS: dict[str, str] = {
    "user_statement": "本轮自述",
    "task": "任务原话",
    "profile": "已记住信息",
    "resume": "你提供的材料",
}


def wants_learning_resources(text: str) -> bool:
    """用户目标是否明确包含学习资料。"""

    return _RESOURCES_RE.search(text or "") is not None


def wants_practice_project(text: str) -> bool:
    """用户目标是否明确包含实践项目。"""

    return _PROJECT_RE.search(text or "") is not None


def build_gaps(
    *,
    analysis: CareerRequestAnalysis,
    report: CareerAnalysis | None,
    samples: Sequence[JobSample],
    background: CareerBackgroundSnapshot | None,
) -> list[CareerGapItem]:
    """逐项对照岗位要求与用户证据；未知一律待确认。"""

    if report is None or report.sample_count == 0 or not report.skill_stats:
        return []
    items = list(background.items) if background is not None else []
    gaps: list[CareerGapItem] = []
    for stat in report.skill_stats[:MAX_GAP_ITEMS]:
        job_evidence, urls = _job_evidence(samples, stat.term)
        positive: list[tuple[CareerBackgroundItem, str]] = []
        negative: list[tuple[CareerBackgroundItem, str]] = []
        for item in items:
            if item.overridden:
                continue
            for sentence in _sentences_with(item.text, stat.term):
                polarity = evidence_polarity(sentence, stat.term)
                if polarity == "negative":
                    negative.append((item, sentence))
                elif polarity == "positive":
                    positive.append((item, sentence))
        # 本轮明确自述覆盖任务原话与长期背景；同层冲突不强行选一侧。
        all_hits = [*positive, *negative]
        if all_hits:
            rank = min(_source_rank(item) for item, _ in all_hits)
            positive = [(item, text) for item, text in positive if _source_rank(item) == rank]
            negative = [(item, text) for item, text in negative if _source_rank(item) == rank]
        if positive and negative:
            category = CareerGapCategory.TO_CONFIRM
            note = (
                f"岗位样本里有 {stat.count}/{report.sample_count} 个要求「{stat.term}」；"
                "相关背景存在冲突，需要确认当前情况；未知不等于不足。"
            )
            hits = [*positive, *negative]
        elif negative:
            category = CareerGapCategory.TO_IMPROVE
            note = (
                f"岗位样本里有 {stat.count}/{report.sample_count} 个要求「{stat.term}」；"
                "你明确提到尚未掌握或需要补学。这是按你的原话标记的明确待提升，"
                "不是系统对你的能力判断。"
            )
            hits = negative
        elif positive:
            category = CareerGapCategory.HAS_EVIDENCE
            note = (
                f"岗位样本里有 {stat.count}/{report.sample_count} 个要求「{stat.term}」；"
                "你的背景里有对应依据（具体掌握程度仍以你原话为准，不推断等级）。"
            )
            hits = positive
        else:
            category = CareerGapCategory.TO_CONFIRM
            note = (
                f"岗位样本里有 {stat.count}/{report.sample_count} 个要求「{stat.term}」；"
                "你目前没有提供相关背景。未知不等于不足，先列为待确认。"
            )
            hits = []
        evidence_lines, refs = _background_evidence(hits)
        if category is not CareerGapCategory.TO_CONFIRM and (
            not evidence_lines or not job_evidence or not urls
        ):
            # 防御：分类必须有用户侧原文支持，否则退回待确认，绝不凭空写差距。
            category = CareerGapCategory.TO_CONFIRM
            note = (
                f"岗位样本里有 {stat.count}/{report.sample_count} 个要求「{stat.term}」；"
                "没有取得可定位的用户侧依据，未知不等于不足，保持待确认。"
            )
        gaps.append(
            CareerGapItem(
                term=stat.term,
                category=category,
                requirement_count=stat.count,
                requirement_total=report.sample_count,
                job_evidence=job_evidence,
                job_sample_urls=urls,
                background_evidence=evidence_lines,
                background_refs=refs,
                note=note,
                inference=False,
            )
        )
    return gaps


def build_personal_advice(
    *,
    analysis: CareerRequestAnalysis,
    report: CareerAnalysis | None,
    gaps: Sequence[CareerGapItem],
    background: CareerBackgroundSnapshot | None,
) -> tuple[list[CareerAdviceItem], list[CareerCombinationRequirement], str | None]:
    """按差距与约束编排优先行动，并给出唯一的背景关键问题。"""

    if report is None or not gaps:
        return [], [], None
    ordered = sorted(
        gaps,
        key=lambda gap: (_category_rank(gap.category), -gap.requirement_count),
    )
    budget = background.time_budget_minutes if background is not None else None
    budget_note = _budget_note(background)
    advices: list[CareerAdviceItem] = []
    priority = 0
    for gap in ordered:
        priority += 1
        feasibility = budget_note
        step = _feasible_step(budget, background)
        if gap.category is CareerGapCategory.TO_IMPROVE:
            advices.append(
                CareerAdviceItem(
                    kind="skill",
                    title=f"优先补强 {gap.term}",
                    detail=(
                        f"岗位样本里有 {gap.requirement_count}/{gap.requirement_total} 个岗位"
                        f"要求「{gap.term}」，你也明确提到需要补；{step}"
                        "再回到岗位要求原文逐条核对。"
                    ),
                    basis=[*gap.job_evidence, *[f"岗位样本：{url}" for url in gap.job_sample_urls]],
                    background_basis=list(gap.background_evidence),
                    inference=True,
                    priority=priority,
                    feasibility=feasibility,
                )
            )
        elif gap.category is CareerGapCategory.HAS_EVIDENCE:
            advices.append(
                CareerAdviceItem(
                    kind="leverage",
                    title=f"把 {gap.term} 的现有基础转成可核验材料",
                    detail=(
                        f"你的背景里有「{gap.term}」的对应依据；{step}"
                        "将实际完成的例子整理成材料，不把正在学习当成项目经验。"
                    ),
                    basis=[*gap.job_evidence, *[f"岗位样本：{url}" for url in gap.job_sample_urls]],
                    background_basis=list(gap.background_evidence),
                    inference=True,
                    priority=priority,
                    feasibility=feasibility,
                )
            )
        else:
            advices.append(
                CareerAdviceItem(
                    kind="confirm",
                    title=f"先确认 {gap.term} 的实际掌握程度",
                    detail=(
                        f"岗位样本里有 {gap.requirement_count}/{gap.requirement_total} 个岗位"
                        f"要求「{gap.term}」，但你还没有提供相关背景；这不等于不足。"
                        f"{step}确认后再决定它排在哪一档。"
                    ),
                    basis=[*gap.job_evidence, *[f"岗位样本：{url}" for url in gap.job_sample_urls]],
                    inference=True,
                    priority=priority,
                    feasibility=feasibility,
                )
            )
    interest = _interest_note(analysis, background)
    if interest is not None:
        priority += 1
        interest.priority = priority
        advices.append(interest)
    if budget is not None and background is not None:
        priority += 1
        budget_source = (
            "当前陈述" if background.time_budget_source == "statement" else "已记住信息"
        )
        advices.append(
            CareerAdviceItem(
                kind="pace",
                title=f"按每天 {budget} 分钟推进上面的顺序",
                detail=(
                    "你给的可执行时段是每天 "
                    f"{budget} 分钟（来源：{budget_source}）；"
                    "一次只推进一个最小步骤，上面的优先级就是推进顺序。"
                ),
                basis=["时间约束来自你明确给出的原话，不是系统估计。"],
                background_basis=[
                    item.text
                    for item in _budget_source_items(background)
                ],
                inference=True,
                priority=priority,
                feasibility=budget_note,
            )
        )
    requirements = _combination_requirements(analysis, gaps)
    question = build_follow_up_question(gaps, background)
    return advices, requirements, question


def build_follow_up_question(
    gaps: Sequence[CareerGapItem], background: CareerBackgroundSnapshot | None
) -> str | None:
    """背景不足时只问一个影响建议的关键问题。"""

    if not gaps:
        return None
    unknown = [gap for gap in gaps if gap.category is CareerGapCategory.TO_CONFIRM]
    if unknown:
        top = max(unknown, key=lambda gap: gap.requirement_count)
        return (
            f"为了把优先级排准：你目前接触过「{top.term}」吗（学过、用过或做过相关项目都算）？"
            "它现在只列为待确认，不当作不足。"
        )
    if background is None or background.time_budget_minutes is None:
        return (
            "你每天大概能安排多少时间准备这个岗位？"
            "我会按这个时段把上面的行动拆成可执行的步骤。"
        )
    return None


def personal_boundary_notes(
    *,
    branch_is_personal: bool,
    background: CareerBackgroundSnapshot | None,
    gaps: Sequence[CareerGapItem],
) -> list[str]:
    """个人判断的证据边界（只写本轮真实发生的事）。"""

    if not branch_is_personal:
        return []
    notes = [
        "个人差距只用你明确给出的背景与公开岗位样本的要求原文对照；"
        "没有背景依据的能力一律列为待确认，不判为不足。",
    ]
    if background is None or not any(gap.background_evidence for gap in gaps):
        notes.append(
            "本轮没有取得可定位的个人背景证据，差距清单全部保持待确认；"
            "公开岗位部分不受影响。"
        )
    if background is not None and background.unavailable_reason:
        notes.append(background.unavailable_reason)
    if background is not None and background.used_profile:
        notes.append("本轮采用了允许使用的已记住信息最小切片，来源与版本随轮次记录。")
    if background is not None and background.time_budget_minutes is not None:
        source = "当前陈述" if background.time_budget_source == "statement" else "已记住信息"
        notes.append(
            f"每天可用时间 {background.time_budget_minutes} 分钟来自{source}，"
            "当前陈述优先于长期默认值。"
        )
    notes.append("本轮没有把岗位结果写回个人画像，也没有向公网发送简历或私人原文。")
    return notes


def _category_rank(category: CareerGapCategory) -> int:
    if category is CareerGapCategory.TO_IMPROVE:
        return 0
    if category is CareerGapCategory.HAS_EVIDENCE:
        return 1
    return 2


def _job_evidence(
    samples: Sequence[JobSample], term: str
) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    urls: list[str] = []
    for sample in samples:
        for requirement in sample.requirements:
            text = requirement.strip()
            if skill_present(text, term) and text not in lines:
                lines.append(text)
        if any(
            skill_present(requirement, term) for requirement in sample.requirements
        ) and sample.url not in urls:
            urls.append(sample.url)
    return lines[:MAX_JOB_EVIDENCE_LINES], urls[:MAX_JOB_EVIDENCE_LINES]


def _source_rank(item: CareerBackgroundItem) -> int:
    return {"user_statement": 0, "task": 1, "resume": 2, "profile": 3}[item.source.value]


def evidence_polarity(text: str, term: str) -> str | None:
    """只接受同一分句中绑定该技能的能力断言；不明确则待确认。"""
    if (
        _NON_ASSERTION_RE.search(text)
        or not skill_present(text, term)
        or not re.match(r"^(?:我|本人|用户|会|熟悉|熟练|掌握|精通|了解|擅长|用过|做过|写过|"
                        r"学过|正在学|在学|不会|不熟|没|没有|还没|零基础)", text.strip())
    ):
        return None
    markers = [(match.start(), match.end(), "negative")
               for match in _NEGATIVE_RE.finditer(text)]
    markers += [(match.start(), match.end(), "positive")
                for match in _POSITIVE_RE.finditer(text)
                if not any(start <= match.start() < end for start, end, _ in markers)]
    skills = detect_skills(text)
    lowered = text.casefold()
    position = lowered.find(term.casefold())
    if position < 0:
        return None
    before = [(start, end, value) for start, end, value in markers if end <= position]
    after = [(start, end, value) for start, end, value in markers
             if start >= position + len(term)]
    if before:
        return max(before)[2]
    if after:
        start, _end, value = min(after)
        # 「Java、Redis不会」共享谓词；「Java和会Redis」不把Redis谓词归给Java。
        between = text[position + len(term):start]
        if not any(skill_present(text[start:], skill) for skill in skills if skill != term):
            return value
        if not between.strip():
            return None
    return None


def _sentences_with(text: str, term: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in _SENTENCE_SPLIT_RE.split(text)
        if sentence.strip() and skill_present(sentence, term)
    ]


def _background_evidence(
    hits: Sequence[tuple[CareerBackgroundItem, str]],
) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    refs: list[str] = []
    for item, sentence in hits:
        label = _SOURCE_LABELS.get(item.source.value, item.source.value)
        line = f"{label}：{sentence}"
        if line not in lines:
            lines.append(line)
        if item.source_ref and item.source_ref not in refs:
            refs.append(item.source_ref)
    return lines[:MAX_BACKGROUND_EVIDENCE_LINES], refs[:MAX_BACKGROUND_EVIDENCE_LINES]


def _feasible_step(budget: int | None, background: CareerBackgroundSnapshot | None) -> str:
    texts = " ".join(item.text for item in background.items) if background else ""
    if re.search(r"没有电脑|没电脑|只有手机|只能用手机", texts):
        return "先用手机阅读一个小例子并写下思路，暂不安排需要电脑运行的项目。"
    if budget is not None and budget <= 15:
        return "本时段先阅读一个例子并记录一个问题，暂不安排完整项目。"
    if budget is not None and budget < 60:
        return "本时段先完成一个单功能练习，将较大项目拆到后续时段。"
    return "先做一个最小可展示的练习或小项目。"


def _budget_note(background: CareerBackgroundSnapshot | None) -> str:
    if background is not None and background.time_budget_minutes is not None:
        source = "当前陈述" if background.time_budget_source == "statement" else "已记住信息"
        return (
            f"按你给出的每天 {background.time_budget_minutes} 分钟（来源：{source}）"
            "安排最小步骤；时间不足时先推进优先级最高的一项。"
        )
    return "本轮没有确认每天可用时间：先按最小步骤推进，并补充可用时段以便调整强度。"


def _budget_source_items(
    background: CareerBackgroundSnapshot | None,
) -> list[CareerBackgroundItem]:
    """找出携带本轮时间约束的采用条目（用于证据回显）。

    当前陈述优先：本轮原话里给过时段时回显该条；否则在画像条目里找
    允许改变计划时间且真的能解析出同一个时段的那一条。
    """

    if background is None or background.time_budget_minutes is None:
        return []
    source = background.time_budget_source
    for item in background.items:
        if source == "profile" and "plan_time_budget" not in item.applicable_to:
            continue
        if detect_time_budget(item.text) == background.time_budget_minutes:
            return [item]
    return []


def _interest_note(
    analysis: CareerRequestAnalysis, background: CareerBackgroundSnapshot | None
) -> CareerAdviceItem | None:
    """兴趣只调整建议题材，绝不替换本轮岗位目标。"""

    if background is None:
        return None
    interests = [item for item in background.items if item.relation == "interest"]
    if not interests:
        return None
    job = analysis.job_title or (analysis.job_terms[0] if analysis.job_terms else "目标岗位")
    return CareerAdviceItem(
        kind="interest",
        title="兴趣只用于调整练习题材",
        detail=(
            f"你提到的兴趣只会用于选择练习或小项目的题材；本轮岗位目标仍是「{job}」，"
            "不会因为兴趣替换成相邻岗位。"
        ),
        basis=[
            *[f"已记住信息：{item.text}" for item in interests[:1]],
        ],
        background_basis=[item.source_ref for item in interests[:1]],
        inference=True,
    )


def _combination_requirements(
    analysis: CareerRequestAnalysis, gaps: Sequence[CareerGapItem]
) -> list[CareerCombinationRequirement]:
    """用户目标含资料/实践项目时，输出供 37 组合的最小需求产物。"""

    goal_text = analysis.original_request
    job = analysis.job_title or (analysis.job_terms[0] if analysis.job_terms else "目标岗位")
    needs = [gap for gap in gaps if gap.category is not CareerGapCategory.HAS_EVIDENCE]
    requirements: list[CareerCombinationRequirement] = []
    if wants_learning_resources(goal_text):
        requirements.append(
            CareerCombinationRequirement(
                kind="resources",
                topic=job,
                goal=f"围绕「{job}」岗位要求推荐学习资料；待确认技能先核实基础，不预设不足。",
                skills=[gap.term for gap in (needs or gaps)],
                basis=[group for gap in (needs or gaps) for group in gap.job_evidence][:4],
                inference=True,
            )
        )
    if wants_practice_project(goal_text):
        evidenced = [
            gap.term for gap in gaps if gap.category is CareerGapCategory.HAS_EVIDENCE
        ]
        skills = evidenced or [gap.term for gap in needs]
        requirements.append(
            CareerCombinationRequirement(
                kind="github",
                topic=job,
                goal="用一个小项目串起岗位高频要求，形成可写进材料的具体例子。",
                skills=skills,
                basis=[group for gap in gaps for group in gap.job_evidence][:4],
                inference=True,
            )
        )
    return requirements


__all__ = [
    "MAX_GAP_ITEMS",
    "build_follow_up_question",
    "build_gaps",
    "build_personal_advice",
    "personal_boundary_notes",
    "wants_learning_resources",
    "wants_practice_project",
]
