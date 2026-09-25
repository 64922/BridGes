"""``resources.present``：由真实证据渲染的中文清单，顺序与边界都写清楚。

正文完全由真实证据渲染（原词、实际查询词、每条的来源链接与选择理由、数量与
证据边界），**不调用模型**：图书只写到书目元数据，视频只写到公开接口给出的
标题、作者、发布时间、时长与简介。未阅读的图书、未观看的视频一律不下结论，
因此这份正文天然零虚构，也不需要「模型概述」这一层。
"""

from __future__ import annotations

from bridges.resources.contracts import (
    LearningResourcesProjection,
    ResourceItem,
    ResourceKind,
    ResourcesQueryPlan,
    ResourcesTermAnalysis,
    format_duration,
)

#: 条目类型的中文标签（清单与正文共用一份）。
KIND_LABELS: dict[str, str] = {
    ResourceKind.BOOK: "图书",
    ResourceKind.VIDEO: "视频",
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
    """成功结果的正文：原词、实际查询词、由浅入深的清单、每条依据与边界。"""
    lines: list[str] = []
    term_note = ""
    if analysis.original_phrase and analysis.original_phrase != plan.book_query:
        term_note = f"（原始说法：{analysis.original_phrase}）"
    lines.append(f"已按学习资料推荐检索{term_note}，图书查询词：{plan.book_query}。")
    lines.append(f"视频发现查询词：{plan.video_query}。")
    if projection.level_label:
        lines.append(f"学习层次：{projection.level_label}。")
    if analysis.goal:
        lines.append(f"学习目的：{analysis.goal}。")
    lines.append("")
    lines.append(f"共 {len(projection.items)} 条，按由浅入深的顺序：")
    for item in projection.items:
        lines.append("")
        lines.append(f"{item.order}. {_item_heading(item)}")
        lines.append(f"   适用阶段：{item.stage}")
        lines.append(f"   选择理由：{item.reason_zh}")
        lines.append(f"   核对依据：{item.match_basis}")
        lines.append(f"   链接：{item.url}")
        if item.unverified:
            lines.append(f"   未核实项：{'；'.join(item.unverified)}")
    if projection.evidence_notes:
        lines.append("")
        lines.append("证据边界：")
        lines.extend(f"- {note}" for note in projection.evidence_notes)
    return "\n".join(lines)


def render_empty_content(
    analysis: ResourcesTermAnalysis, plan: ResourcesQueryPlan, notes: list[str]
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
    analysis: ResourcesTermAnalysis, plan: ResourcesQueryPlan, notes: list[str]
) -> str:
    lines = [
        f"图书查询词：{plan.book_query}；视频发现查询词：{plan.video_query}"
        f"（原始说法：{analysis.original_phrase}）。",
        "检索回来的图书与视频标题都没有覆盖你的原始说法，因此我停止推荐，没有凑数量。",
    ]
    lines.extend(f"- {note}" for note in notes)
    lines.append("请补充你要的领域或纠正术语（例如说明是机器学习还是电力系统），我再检索。")
    return "\n".join(lines)


def render_stopped_content(plan: ResourcesQueryPlan) -> str:
    """停止正文：如实写明停止时已经确定的图书查询词，不补做未生成的步骤。"""
    return (
        f"学习资料检索已停止，图书查询词：{plan.book_query}。"
        "已完成的步骤保留在本条消息内。"
    )


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

