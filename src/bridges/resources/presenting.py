"""``resources`` 的正文渲染：由真实证据渲染的中文清单，顺序与边界都写清楚。

正文完全由真实证据渲染（原词、实际查询词、每条的角色/用途、来源链接、
选择理由与实际读取范围），**不调用模型**：图书只写到已读取的目录/简介与
书目元数据，视频只写到公开接口给出的标题、作者、发布时间、时长、简介与
公开计数。未阅读的图书、未观看的视频一律不下结论，因此这份正文天然零虚构。
"""

from __future__ import annotations

from bridges.resources.contracts import (
    GOAL_KIND_LABELS,
    MEDIA_LABELS,
    LearningResourcesProjection,
    ResourceEvidenceLevel,
    ResourceItem,
    ResourceKind,
    ResourceRole,
    ResourcesQueryPlan,
    ResourcesTermAnalysis,
    format_duration,
)

#: 条目类型的中文标签（清单与正文共用一份）。
KIND_LABELS: dict[str, str] = {
    ResourceKind.BOOK: "图书",
    ResourceKind.VIDEO: "视频",
}

#: 证据层次的中文标签（正文如实标注条目凭什么获批）。
EVIDENCE_LABELS: dict[str, str] = {
    ResourceEvidenceLevel.CATALOG: "已读目录/主题",
    ResourceEvidenceLevel.INTRO: "已读简介",
    ResourceEvidenceLevel.TITLE: "仅标题/公开计数（弱信号）",
}


def render_clarification_content(analysis: ResourcesTermAnalysis) -> str:
    clarification = analysis.clarification
    if clarification is None:
        return ""
    return clarification.question


def render_result_content(
    analysis: ResourcesTermAnalysis,
    plan: ResourcesQueryPlan,
    projection: LearningResourcesProjection,
) -> str:
    """成功结果的正文：原词、查询词、主线/补充清单、逐条证据与边界。"""
    lines: list[str] = []
    term_note = ""
    if analysis.original_phrase and analysis.original_phrase != plan.book_query:
        term_note = f"（原始说法：{analysis.original_phrase}）"
    lines.append(f"已按学习资料推荐检索{term_note}，图书查询词：{plan.book_query}。")
    lines.append(f"视频发现查询词：{plan.video_query}。")
    if projection.goal_kind:
        goal_label = GOAL_KIND_LABELS.get(projection.goal_kind, "本轮目标")
        suffix = f"（{analysis.goal}）" if analysis.goal else ""
        lines.append(f"学习目的：{goal_label}{suffix}。")
    if projection.media is not None and plan.media.value != "both":
        lines.append(f"媒介条件：{MEDIA_LABELS.get(plan.media, '图书与视频')}。")
    if projection.level_label:
        lines.append(f"学习层次：{projection.level_label}。")
    if projection.parallel_limit:
        lines.append(
            f"书与视频两路并行检索（并发上限 {projection.parallel_limit}），共用本轮预算。"
        )
    lines.append("")
    main_items = [item for item in projection.items if item.role is ResourceRole.MAIN]
    supplement_items = [
        item for item in projection.items if item.role is ResourceRole.SUPPLEMENT
    ]
    lines.append(
        f"共 {len(projection.items)} 条，按由浅入深的顺序"
        f"（主线 {len(main_items)} 条、补充 {len(supplement_items)} 条）："
    )
    if main_items:
        lines.append("")
        lines.append("主线（先看这些）：")
        lines.extend(_item_lines(main_items))
    if supplement_items:
        lines.append("")
        lines.append("补充/候选（按需查看）：")
        lines.extend(_item_lines(supplement_items))
    lines.append("")
    if projection.path_verified:
        lines.append(
            "主线条目均有已读取的来源证据支持（主题覆盖与适用阶段来自目录/简介 "
            "或公开页面信息）；未读内容不作教学效果断言。"
        )
    else:
        lines.append(
            "主线所需的先修/覆盖证据未确认：以上不构成已核实的完整路径，"
            "请按链接自行核对后再投入时间。"
        )
    if projection.assumptions:
        lines.append("")
        lines.append("本轮假设（可随时纠正）：")
        lines.extend(f"- {note}" for note in projection.assumptions)
    if projection.evidence_notes:
        lines.append("")
        lines.append("证据边界：")
        lines.extend(f"- {note}" for note in projection.evidence_notes)
    return "\n".join(lines)


def _item_lines(items: list[ResourceItem]) -> list[str]:
    lines: list[str] = []
    for item in items:
        lines.append("")
        lines.append(
            f"{item.order}. {_item_heading(item)}"
            + ("【主线】" if item.role is ResourceRole.MAIN else "【补充】")
        )
        lines.append(f"   用途：{item.purpose_zh}")
        lines.append(f"   适用阶段：{item.stage}")
        lines.append(f"   选择理由：{item.reason_zh}")
        lines.append(f"   核对依据：{item.match_basis}")
        lines.append(
            f"   实际读取：{item.read_scope}"
            f"（证据层次：{EVIDENCE_LABELS.get(item.evidence_level, '弱信号')}）"
        )
        lines.append(f"   链接：{item.url}")
        if item.unverified:
            lines.append(f"   未核实项：{'；'.join(item.unverified)}")
    return lines


def render_empty_content(
    analysis: ResourcesTermAnalysis,
    plan: ResourcesQueryPlan,
    notes: list[str],
) -> str:
    lines = [
        f"已按主题「{analysis.original_phrase or plan.term}」检索：图书查询词 {plan.book_query}，"
        f"视频发现查询词 {plan.video_query}。",
        "来源没有返回可用的图书或视频，我没有可推荐的条目，也不会用记忆补造。",
    ]
    lines.extend(f"- {note}" for note in notes)
    lines.append("可以换一个更常见的说法或补充领域后重试。")
    return "\n".join(lines)


def render_mismatch_content(
    analysis: ResourcesTermAnalysis,
    plan: ResourcesQueryPlan,
    notes: list[str],
) -> str:
    lines = [
        f"图书查询词：{plan.book_query}；视频发现查询词：{plan.video_query}"
        f"（原始说法：{analysis.original_phrase}）。",
        "检索回来的图书与视频标题、简介或目录都没有覆盖你的原始说法，因此我停止推荐，没有凑数量。",
    ]
    lines.extend(f"- {note}" for note in notes)
    lines.append("请补充你要的领域或纠正术语（例如说明是机器学习还是电力系统），我再检索。")
    return "\n".join(lines)


def render_stopped_content(plan: ResourcesQueryPlan | None = None) -> str:
    """停止正文：如实写明停止时已经确定的图书查询词，不补做未生成的步骤。"""
    if plan is None:
        return "学习资料检索已停止，已完成的步骤保留在本条消息内。"
    return (
        f"学习资料检索已停止，图书查询词：{plan.book_query}。"
        "已完成的步骤保留在本条消息内。"
    )


def render_error_content(projection: LearningResourcesProjection) -> str:
    """失败正文：如实写明错误说明与已发生的查询，不把失败伪装成空结果。"""
    lines = [f"学习资料检索失败：{projection.error_message or '请稍后重试。'}"]
    if projection.final_query:
        lines.append(f"图书查询词：{projection.final_query}。")
    if projection.evidence_notes:
        lines.append("已发生的检索记录：")
        lines.extend(f"- {note}" for note in projection.evidence_notes)
    return "\n".join(lines)


def _item_heading(item: ResourceItem) -> str:
    label = KIND_LABELS.get(item.kind, "资料")
    parts = [f"{label}《{item.title}》"]
    if item.creator:
        parts.append(f"（{item.creator}）")
    details: list[str] = []
    if item.publisher:
        details.append(item.publisher)
    if item.year:
        details.append(f"{item.year} 年")
    if item.isbn:
        details.append(f"ISBN {item.isbn}")
    if item.duration_seconds:
        details.append(f"时长 {format_duration(item.duration_seconds)}")
    if details:
        parts.append("，" + "，".join(details))
    return "".join(parts)
