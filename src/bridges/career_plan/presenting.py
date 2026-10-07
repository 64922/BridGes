"""职业规划模块的中文正文渲染（``career.analyze`` 与 ``career.advise`` 的呈现）。

正文全部由真实证据渲染：查询词、样本字段、统计口径、建议依据都直接来自投影，
本模块不调用模型，因此不存在用模型记忆填内容的空间。每个数字都标出样本量、
日期、地区或计薪单位；小样本只按「本轮检索所得」表述，绝不写成全国市场均值。
"""

from __future__ import annotations

from datetime import datetime

from bridges.career_plan.contracts import (
    CareerBranch,
    CareerGapCategory,
    CareerPlanProjection,
    CareerRequestAnalysis,
    JobSample,
)
from bridges.career_plan.filtering import (
    KIND_ADJACENT,
    KIND_CITY,
    KIND_CITY_UNVERIFIED,
    KIND_DUPLICATE,
    KIND_EXPERIENCE,
    KIND_EXPERIENCE_UNVERIFIED,
    KIND_EXPIRED,
    KIND_NOT_JOB,
    KIND_TITLE_MISMATCH,
)

#: 单个样本最多列出的要求原文条数。
MAX_REQUIREMENT_LINES = 6

#: 统一查询记录状态 → 中文（正文一律用中文，不回显英文枚举值）。
QUERY_STATUS_LABELS: dict[str, str] = {
    "success": "成功",
    "empty": "无结果",
    "skipped": "未执行",
    "timeout": "超时",
    "cancelled": "已取消",
    "rate_limited": "被上游限流",
    "error": "失败",
}

#: 剔除分类 → 中文小标题（不静默丢弃，逐类写明）。
REJECTION_LABELS: dict[str, str] = {
    KIND_ADJACENT: "相邻岗位（单列，不并入样本）",
    KIND_CITY: "城市不符",
    KIND_CITY_UNVERIFIED: "城市无法核对",
    KIND_EXPERIENCE: "经验要求不符",
    KIND_EXPERIENCE_UNVERIFIED: "经验要求无法核对",
    KIND_EXPIRED: "已过期或已下线",
    KIND_DUPLICATE: "重复岗位",
    KIND_NOT_JOB: "不是岗位详情页",
    KIND_TITLE_MISMATCH: "岗位名不匹配",
}

#: 个人差距分类 → 中文标题（未知不是弱项）。
GAP_CATEGORY_LABELS: dict[str, str] = {
    CareerGapCategory.HAS_EVIDENCE.value: "已有依据",
    CareerGapCategory.TO_IMPROVE.value: "明确待提升",
    CareerGapCategory.TO_CONFIRM.value: "待确认（不等于不足）",
}


def render_clarification_content(analysis: CareerRequestAnalysis) -> str:
    question = analysis.clarification.question if analysis.clarification else ""
    parts = ["职业规划需要先确认一项：", "", question]
    if analysis.cities:
        parts.append("")
        parts.append(f"（已记下你的城市：{'、'.join(analysis.cities)}）")
    if analysis.stage:
        parts.append(f"（已记下你的阶段：{analysis.stage}）")
    parts.append("")
    parts.append("直接回复这一条即可，我会沿用你原来的要求继续。")
    return "\n".join(parts).rstrip()


def render_result_content(projection: CareerPlanProjection) -> str:
    """有主样本时先交付分析与建议，再展示岗位证据和查询记录。"""
    lines: list[str] = [f"职业规划分析：{projection.topic}", ""]
    if projection.analysis is not None:
        lines.extend(_analysis_lines(projection))

    if projection.advices:
        lines.extend(["", "【可执行建议（区分证据与推断）】"])
        for advice in projection.advices:
            tag = "推断" if advice.inference else "证据"
            lines.append(f"· [{tag}] {advice.title}：{advice.detail}")
            for basis in advice.basis:
                lines.append(f"    依据：{basis}")

    lines.extend(_personal_lines(projection))
    lines.extend(["", f"【公开可读且匹配的岗位样本（{len(projection.samples)} 个）】"])
    for index, sample in enumerate(projection.samples, start=1):
        lines.append("")
        lines.extend(_sample_lines(index, sample))
    lines.extend(_requirement_lines_block(projection))
    lines.extend(_plan_lines(projection))
    lines.extend(_query_lines(projection))
    lines.extend(_adjacent_lines(projection))
    lines.extend(_rejected_lines(projection))
    lines.extend(_boundary_lines(projection))
    return "\n".join(lines).rstrip()


def render_links_only_content(projection: CareerPlanProjection) -> str:
    """只拿到候选链接时的正文：如实说明为什么没有样本。"""
    lines: list[str] = [f"职业规划分析：{projection.topic}", ""]
    if projection.empty_reason:
        lines.append(projection.empty_reason)
    lines.extend(_recovery_lines(projection))
    lines.extend(_requirement_lines_block(projection))
    lines.extend(_plan_lines(projection))
    lines.extend(_query_lines(projection))
    lines.extend(_candidate_lines(projection))
    lines.extend(_rejected_lines(projection))
    lines.extend(_personal_lines(projection))
    lines.extend(_boundary_lines(projection))
    return "\n".join(lines).rstrip()


def render_empty_content(projection: CareerPlanProjection) -> str:
    """没有可用岗位（或失败）时的正文：只给实际查询与证据缺口。"""
    lines: list[str] = [f"职业规划分析：{projection.topic}", ""]
    if projection.empty_reason:
        lines.append(projection.empty_reason)
    lines.extend(_recovery_lines(projection))
    lines.extend(_requirement_lines_block(projection))
    lines.extend(_plan_lines(projection))
    lines.extend(_query_lines(projection))
    lines.extend(_candidate_lines(projection))
    lines.extend(_rejected_lines(projection))
    lines.extend(_personal_lines(projection))
    lines.extend(_boundary_lines(projection))
    return "\n".join(lines).rstrip()


def render_stopped_content(projection: CareerPlanProjection) -> str:
    lines = [
        f"职业规划分析已停止：{projection.topic}",
        "",
        f"· 原请求：{projection.original_request}",
    ]
    for record in projection.queries:
        status = QUERY_STATUS_LABELS.get(str(record.status), str(record.status))
        lines.append(f"· 已发出的查询词：{record.query}（状态：{status}）")
    if projection.samples:
        lines.append(f"· 停止前已读到 {len(projection.samples)} 个匹配的岗位样本")
    lines.append("")
    lines.append("已停止：本轮没有继续读取岗位页，可以在同一会话里重试。")
    return "\n".join(lines).rstrip()


def _recovery_lines(projection: CareerPlanProjection) -> list[str]:
    """没有样本时仍交付恢复动作；不凭空生成技能或薪资结论。"""
    job = projection.topic or "目标岗位"
    return [
        "", "【接下来可以怎么推进】",
        f"· 保留「{job}」目标，在职业规划模块重试相同请求，可重新读取公开岗位。",
        "· 下方候选链接只作为查找入口；打开后确认是否有单个职位的职责、"
        "工作地点和投递条件，再用岗位详情链接继续核对。",
        "· 如需个人准备路线，可补充已经做过的编程或项目经历；"
        "在取得岗位要求前，不把你的能力判为不足，也不据此估计薪资。",
    ]


def _requirement_lines_block(projection: CareerPlanProjection) -> list[str]:
    lines = [f"· 原始请求：{projection.original_request}"]
    if projection.job_terms:
        lines.append(f"· 岗位锚点（原话）：{'、'.join(projection.job_terms)}")
    if projection.family_title:
        lines.append(f"· 识别的岗位方向：{projection.family_title}")
    if projection.stage:
        lines.append(f"· 毕业阶段：{projection.stage}")
    if projection.graduation_year is not None:
        lines.append(f"· 届别：{projection.graduation_year} 届")
    lines.append(
        f"· 期望城市：{'、'.join(projection.cities)}" if projection.cities else "· 期望城市：未给出"
    )
    if projection.experience_hint:
        lines.append(f"· 经验条件：{projection.experience_hint}（逐条核对，未知不进入统计）")
    if projection.constraints:
        lines.append(f"· 其他约束：{'、'.join(projection.constraints)}")
    return lines


def _plan_lines(projection: CareerPlanProjection) -> list[str]:
    if not projection.plan:
        return []
    lines = ["", "【检索计划（实际执行的查询与筛选条件）】"]
    for index, item in enumerate(projection.plan, start=1):
        lines.append(f"{index}. {item.source_label}")
        lines.append(f"   查询词：{item.query}")
        lines.append(f"   筛选条件：{'；'.join(item.filters)}")
        lines.append(f"   理由：{item.reason}")
    return lines


def _query_lines(projection: CareerPlanProjection) -> list[str]:
    if not projection.queries:
        return []
    lines = ["", "【每次外部调用记录】"]
    for record in projection.queries:
        status = QUERY_STATUS_LABELS.get(str(record.status), str(record.status))
        head = (
            f"· 提供方 {record.source}｜{record.query}｜状态：{status}｜"
            f"取得 {record.evidence_count} 条"
        )
        if record.detail:
            head += f"｜{record.detail}"
        lines.append(head)
        if record.error_message:
            lines.append(f"    失败原因：{record.error_message}")
    return lines


def _sample_lines(index: int, sample: JobSample) -> list[str]:
    lines = [f"{index}. {sample.title}"]
    if sample.company:
        lines.append(f"   公司：{sample.company}")
    lines.append(f"   城市：{sample.city or '页面未给出'}")
    lines.append(f"   薪资原文：{sample.salary_raw or '页面未给出'}")
    lines.append(f"   发布日期：{sample.published_raw or '页面未给出'}")
    if sample.published_date is not None:
        lines.append(f"   换算日期：{sample.published_date.isoformat()}")
    if sample.experience:
        lines.append(f"   经验要求：{sample.experience}")
    if sample.education:
        lines.append(f"   学历要求：{sample.education}")
    if sample.skills:
        lines.append(f"   命中的技能关键词：{'、'.join(sample.skills)}")
    lines.append(f"   直达链接：{sample.url}")
    lines.append(f"   抓取时间：{_fmt(sample.retrieved_at)}（来源：{sample.source_label}）")
    lines.append(f"   岗位匹配依据：{sample.title_evidence}")
    lines.append(f"   城市核对：{sample.city_evidence}")
    if sample.experience_evidence:
        lines.append(f"   经验核对：{sample.experience_evidence}")
    for requirement in sample.requirements[:MAX_REQUIREMENT_LINES]:
        lines.append(f"   要求原文：{requirement}")
    if len(sample.requirements) > MAX_REQUIREMENT_LINES:
        lines.append(
            f"   （另有 {len(sample.requirements) - MAX_REQUIREMENT_LINES} 条要求原文未在本条列出）"
        )
    return lines


def _analysis_lines(projection: CareerPlanProjection) -> list[str]:
    analysis = projection.analysis
    assert analysis is not None  # 调用点已判定
    lines = ["", "【技能与薪资分析（只基于上面的样本）】"]
    lines.append(f"· 样本口径：{analysis.sample_scope_note}")
    lines.append(
        "· 城市构成："
        + "、".join(f"{item.city} {item.count} 个" for item in analysis.city_composition)
    )
    lines.append(
        f"· 发布日期范围：{analysis.published_span or '页面未给出可用日期'}"
    )
    if analysis.skill_stats:
        lines.append("· 技能关键词（出现该词的样本数）：")
        for stat in analysis.skill_stats:
            lines.append(f"    {stat.term}：{stat.count}/{analysis.sample_count}")
    else:
        lines.append("· 技能关键词：样本要求原文里没有命中词表内的技能词。")
    if analysis.salary_intervals:
        lines.append("· 薪资区间（按币种与计薪单位分别统计，不同币种／单位不混算）：")
        for interval in analysis.salary_intervals:
            lines.append(
                f"    {interval.unit}：{interval.amount_min:,}–{interval.amount_max:,}"
                f"（中位 {interval.amount_median:,}；样本 {interval.sample_count} 个；"
                f"地区：{'、'.join(interval.cities) or '页面未给出'}）"
            )
            if interval.salary_months:
                months = "、".join(f"{item} 薪" for item in interval.salary_months)
                lines.append(f"      发薪月数：{months}（只记录原文，未并入区间金额）")
            if interval.small_sample:
                lines.append(
                    f"      样本量 {interval.sample_count} 个偏少，只按本区间读数，"
                    "不作为市场均值。"
                )
            lines.append(f"      用到的薪资原文：{'｜'.join(interval.raws)}")
    else:
        lines.append("· 薪资区间：没有可比较的薪资原文，未形成任何区间。")
    for note in analysis.incomparable_notes:
        lines.append(f"· 未并入区间：{note}")
    if analysis.overall_inference_stopped:
        lines.append(
            "· 样本不足，已停止总体推断：以上只是本轮检索所得，不代表总体市场情况。"
        )
    return lines


def _adjacent_lines(projection: CareerPlanProjection) -> list[str]:
    if not projection.adjacent_suggestions:
        return []
    lines = ["", "【相邻岗位建议（单列，未并入上面的样本统计）】"]
    for item in projection.adjacent_suggestions:
        lines.append(f"· {item.title}：{item.reason}")
        if item.sample_count:
            lines.append(f"    本轮检索到该岗位的页面 {item.sample_count} 个（已按相邻岗位剔除）")
    return lines


def _candidate_lines(projection: CareerPlanProjection) -> list[str]:
    if not projection.candidate_links:
        return []
    lines = ["", "【未核实的候选链接（未纳入样本）】"]
    for index, link in enumerate(projection.candidate_links, start=1):
        lines.append(f"{index}. {link.title}｜{link.url}")
        lines.append(f"   {link.note}")
    return lines


def _rejected_lines(projection: CareerPlanProjection) -> list[str]:
    if not projection.rejected:
        return []
    grouped: dict[str, list[str]] = {}
    for item in projection.rejected:
        label = REJECTION_LABELS.get(item.kind, item.kind)
        grouped.setdefault(label, []).append(f"{item.title}（{item.evidence}）｜{item.url}")
    lines = ["", f"【已剔除的候选（{len(projection.rejected)} 条，逐条留痕）】"]
    for label, entries in grouped.items():
        lines.append(f"· {label}：共 {len(entries)} 条")
        for entry in entries:
            lines.append(f"    {entry}")
    return lines


def _personal_lines(projection: CareerPlanProjection) -> list[str]:
    """个人准备分支：背景、差距（三分类）、优先行动与关键问题。"""

    if projection.branch is not CareerBranch.PERSONAL_PLANNING:
        return []
    lines: list[str] = ["", "【个人准备（只基于你明确给出的背景与岗位要求）】"]
    background = projection.background
    if background is None:
        lines.append("· 本轮没有读取个人背景；以下结论只来自当前陈述。")
    else:
        if background.used_profile:
            lines.append(
                "· 背景来源：当前陈述与已记住信息最小切片"
                f"（采用 {len(background.items)} 条；切片已检查版本与来源）。"
            )
        else:
            lines.append("· 背景来源：当前陈述。")
        if background.unavailable_reason:
            lines.append(f"· 长期背景说明：{background.unavailable_reason}")
        if background.time_budget_minutes is not None:
            source = (
                "当前陈述"
                if background.time_budget_source == "statement"
                else "已记住信息"
            )
            lines.append(
                f"· 每天可用时间：{background.time_budget_minutes} 分钟（来源：{source}）"
            )

    if projection.gaps:
        grouped: dict[str, list[str]] = {}
        for gap in projection.gaps:
            label = GAP_CATEGORY_LABELS.get(str(gap.category), str(gap.category))
            grouped.setdefault(label, []).append(
                f"· {gap.term}（岗位 {gap.requirement_count}/{gap.requirement_total}）：{gap.note}"
            )
            for evidence in gap.background_evidence:
                grouped[label].append(f"    你的依据：{evidence}")
        lines.append("· 逐项对照：")
        for label, entries in grouped.items():
            lines.append(f"  {label}")
            lines.extend(f"  {entry}" for entry in entries)
    else:
        lines.append("· 逐项对照：本轮没有形成可交付的个人差距结论。")

    if projection.personal_advices:
        lines.append("· 优先行动（按上面的顺序）：")
        for advice in projection.personal_advices:
            tag = "推断" if advice.inference else "证据"
            serial = f"{advice.priority}. " if advice.priority else "· "
            lines.append(f"  {serial}[{tag}] {advice.title}：{advice.detail}")
            for basis in advice.background_basis:
                lines.append(f"      你的依据：{basis}")
            if advice.feasibility:
                lines.append(f"      可执行性：{advice.feasibility}")

    if projection.combination_requirements:
        lines.append("· 可交给资料／项目模块继续的最小需求：")
        for requirement in projection.combination_requirements:
            kind = "学习资料" if requirement.kind == "resources" else "实践项目"
            tag = "推断" if requirement.inference else "证据"
            lines.append(
                f"  [{tag}] {kind}：{requirement.goal}"
                f"（涉及：{'、'.join(requirement.skills) or '见岗位要求'}）"
            )

    if projection.follow_up_question:
        lines.extend(["", f"关键问题（只问这一项）：{projection.follow_up_question}"])
    return lines


def _boundary_lines(projection: CareerPlanProjection) -> list[str]:
    if not projection.evidence_boundary:
        return []
    lines = ["", "【证据边界】"]
    for note in projection.evidence_boundary:
        lines.append(f"· {note}")
    return lines


def _fmt(moment: datetime) -> str:
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")
