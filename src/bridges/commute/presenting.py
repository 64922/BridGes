"""``route.present``：带证据的中文路线正文（V2 Issue 12）。

正文完全由**真实证据渲染**（高德返回的地点、距离、耗时、路段与缓冲判定），
不调用模型，也就不存在用模型记忆补路线的可能：

- 地点缺失或冲突时只写那一句澄清问题；
- 成功时写起终点、方式、距离、高德基础耗时、课间缓冲、建议总时间与文字路段；
- 未取得路径点时明确写「未绘制路线」，并给出已证实的最近可定位点；
- 失败与停止都在同一消息里显示真实状态（含实际检索词与失败分类）。
"""

from __future__ import annotations

from bridges.commute.contracts import (
    MODE_LABELS,
    PLACE_ROLE_LABELS,
    CommuteClarification,
    CommuteMode,
    CommutePlace,
    CommuteRouteProjection,
    CommuteRouteStatus,
)


def format_distance(meters: int | None) -> str:
    if meters is None:
        return "未知"
    if meters < 1000:
        return f"{meters} 米"
    return f"{meters / 1000:.1f} 公里"


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "未知"
    if seconds < 60:
        return f"{seconds} 秒"
    total_minutes = max(1, round(seconds / 60))
    if total_minutes < 60:
        return f"{total_minutes} 分钟"
    hours, rest = divmod(total_minutes, 60)
    return f"{hours} 小时 {rest} 分钟" if rest else f"{hours} 小时"


def render_clarification_content(clarification: CommuteClarification) -> str:
    """澄清正文：只写那一句话，卡片负责把候选列成可选项。"""
    return clarification.question


def render_result_content(projection: CommuteRouteProjection) -> str:
    """成功或未验证结果的正文（顺序与交互规格 §4.1 一致）。"""
    mode_label = projection.mode_label or _mode_label(projection.mode)
    lines: list[str] = [f"已按校园通勤模块规划{ mode_label }路线（高德路线规划 2.0）。"]
    if projection.origin is not None:
        lines.append(f"起点：{_place_line(projection.origin)}")
    if projection.destination is not None:
        lines.append(f"终点：{_place_line(projection.destination)}")
    if projection.mode is not None and projection.mode_phrase:
        lines.append(f"方式：{mode_label}（你说的是「{projection.mode_phrase}」）。")
    lines.append(f"距离：{format_distance(projection.distance_m)}")
    lines.append(f"高德基础耗时：{format_duration(projection.base_duration_seconds)}")
    if projection.buffer is not None:
        buffer = projection.buffer
        if buffer.in_window and buffer.matched_break_time:
            lines.append(
                f"课间缓冲：可能人多（命中课间点 {buffer.matched_break_time} 前后 "
                f"{buffer.minutes_away} 分钟），建议加 {buffer.added_minutes} 分钟。"
            )
        else:
            lines.append("课间缓冲：当前不在课间高峰窗口内，不加缓冲。")
        lines.append(f"建议总时间：{format_duration(projection.suggested_total_seconds)}")
    if projection.path_verified:
        lines.append(
            f"地图：已取得高德返回的 {len(projection.polyline)} 个路径点，"
            "可在下面路线卡中缩放查看，并适合截图保存。"
        )
    else:
        lines.append(
            "地图：本轮没有取得可核验的路径点，因此没有绘制任何路线线；"
            "只保留高德已证实的地点、距离与耗时。"
        )
    if projection.steps:
        lines.append("")
        lines.append("路线文字：")
        for step in projection.steps:
            road = f"（{step.road_name}）" if step.road_name else ""
            distance = f"，{format_distance(step.distance_m)}" if step.distance_m else ""
            lines.append(f"{step.index}. {step.instruction}{road}{distance}")
    if projection.evidence_notes:
        lines.append("")
        lines.append("证据边界：")
        lines.extend(f"- {note}" for note in projection.evidence_notes)
    lines.append("")
    lines.append(
        f"课间提示按规则估计：{projection.buffer.rule_note}"
        if projection.buffer is not None
        else "课间提示按规则估计，不是实时人流数据。"
    )
    return "\n".join(lines)


def render_error_content(projection: CommuteRouteProjection) -> str:
    """失败正文：写清实际使用的地点、方式与失败分类，绝不改写成「没有结果」。"""
    lines = ["校园通勤本轮没有完成，下面是真实状态。"]
    if projection.origin is not None:
        lines.append(f"起点：{_place_line(projection.origin)}")
    if projection.destination is not None:
        lines.append(f"终点：{_place_line(projection.destination)}")
    if projection.mode is not None:
        lines.append(f"方式：{projection.mode_label or _mode_label(projection.mode)}")
    if projection.error_message:
        lines.append(projection.error_message)
    else:
        lines.append("路线查询失败，请稍后重试。")
    if projection.retryable:
        lines.append("可以在下面重试同一条请求。")
    return "\n".join(lines)


def render_stopped_content(projection: CommuteRouteProjection) -> str:
    queries = "、".join(record.query for record in projection.queries) or "（尚未发出外部查询）"
    return f"本轮校园通勤已停止。已完成的外部查询：{queries}。未生成的步骤不会补做。"


def _place_line(place: CommutePlace) -> str:
    label = PLACE_ROLE_LABELS[place.role]
    address = f"，{place.address}" if place.address else ""
    scope = "校内" if place.campus_verified else "未确认校内"
    return f"{place.name}{address}（坐标 {place.location}，{label}·{scope}·高德 POI）"


def _mode_label(mode: CommuteMode | None) -> str:
    return MODE_LABELS.get(mode, "出行") if mode is not None else "出行"


def status_content(projection: CommuteRouteProjection) -> str:
    """按状态选择正文（成功与未验证共用结果正文）。"""
    if projection.status in {CommuteRouteStatus.SUCCESS, CommuteRouteStatus.UNVERIFIED}:
        return render_result_content(projection)
    if projection.status is CommuteRouteStatus.ERROR:
        return render_error_content(projection)
    if projection.status is CommuteRouteStatus.STOPPED:
        return render_stopped_content(projection)
    if projection.pending is not None:
        return projection.pending.question
    return "校园通勤等待你的补充信息。"
