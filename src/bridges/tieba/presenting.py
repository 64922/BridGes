"""``tieba.summarize``：只消费已读文本的分区呈现。

分段只用确定性的标记词判断，并且每条都带引文与楼层／时间出处分；没有真实
回复文本时不产出任何个人经历或「吧友普遍认为」式表述，只保留帖链与
「未取得回复内容」的说明。证据分段由代码生成；独立的模型总结必须通过引用
与语义支持核验，否则只保留真实采集证据和失败说明。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from bridges.tieba.contracts import (
    KIND_LABELS,
    ReadStatus,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaResearchProjection,
    TiebaSearchDiscovery,
)
from bridges.tieba.lexicon import TARGET_FORUM_NAME
from bridges.tieba.official import STATUS_VERIFIED

#: 每段最多引用的楼层数。
MAX_QUOTES_PER_SECTION = 3

#: 单条引文最大字符数。
QUOTE_MAX_CHARS = 80

SECTION_EXPERIENCE = "可核验的个人经历"
SECTION_CONTRAST = "不同看法"
SECTION_HEDGE = "不确定点"

#: 第一人称经历标记：只有带这类标记的真实楼层才进入「个人经历」段。
EXPERIENCE_MARKERS: tuple[str, ...] = (
    "我",
    "本人",
    "我们",
    "咱",
    "亲测",
    "实测",
    "过来人",
    "去年",
    "当年",
    "室友",
    "舍友",
)

#: 相反意见标记。
CONTRAST_MARKERS: tuple[str, ...] = (
    "但是",
    "不过",
    "其实",
    "不一定",
    "反而",
    "不同意",
    "别听",
    "并非",
    "未必",
    "因人而异",
    "看情况",
)

#: 不确定标记。
HEDGE_MARKERS: tuple[str, ...] = (
    "听说",
    "据说",
    "好像",
    "可能",
    "大概",
    "不确定",
    "貌似",
    "应该是",
    "也许",
    "记不清",
)

#: 「未取得回复内容」的固定说明（拿不到回复时必须出现）。
REPLIES_NOT_OBTAINED = "未取得回复内容"


def build_sections(posts: list[TiebaPostProjection]) -> list[str]:
    """从真实读到的楼层里分出三段；没有任何已读文本时返回空列表。"""
    buckets: dict[str, list[str]] = {
        SECTION_EXPERIENCE: [],
        SECTION_CONTRAST: [],
        SECTION_HEDGE: [],
    }
    for post in posts:
        for reply in post.replies:
            quote = _quote(reply.content)
            if not quote:
                continue
            location = _location(post, reply.floor, reply.posted_at, reply.author)
            if _marked(reply.content, HEDGE_MARKERS):
                _append(buckets[SECTION_HEDGE], f"{quote}（{location}）")
            elif _marked(reply.content, CONTRAST_MARKERS):
                _append(buckets[SECTION_CONTRAST], f"{quote}（{location}）")
            elif _marked(reply.content, EXPERIENCE_MARKERS):
                _append(buckets[SECTION_EXPERIENCE], f"{quote}（{location}）")
    return [
        f"{name}：" + "；".join(quotes)
        for name, quotes in buckets.items()
        if quotes
    ]


def render_clarification_content(analysis: TiebaQuestionAnalysis) -> str:
    question = analysis.clarification.question if analysis.clarification else ""
    return (
        f"{TARGET_FORUM_NAME}信息搜集需要先确认一项：\n\n{question}\n\n"
        "直接回复这一条即可，我会沿用你原来的问题继续。"
    )


def render_waiting_auth_content(projection: TiebaResearchProjection) -> str:
    pending = projection.pending
    question = pending.question if pending is not None else "请完成专用贴吧会话的登录或安全验证。"
    recovery = "" if "继续" in question else "完成后回复“继续”即可接着原话题；"
    return (
        f"{TARGET_FORUM_NAME}信息搜集已暂停，尚未完成本轮采集。\n\n"
        f"{question}\n\n"
        f"{recovery}人工等待不计入本任务的五分钟工作预算。"
    )


def render_result_content(projection: TiebaResearchProjection) -> str:
    """把真实证据渲染成中文正文（零虚构：每个数字与引文都来自投影）。"""
    lines: list[str] = [
        f"{TARGET_FORUM_NAME}信息搜集（{KIND_LABELS[projection.question_kind]}）：{projection.topic}"
    ]
    lines.append("")
    lines.append(f"· 原问题：{projection.original_question}")
    if projection.topic_terms:
        lines.append(f"· 原始名词：{'、'.join(projection.topic_terms)}")
    if projection.place_or_event:
        lines.append(f"· 事件／地点：{'、'.join(projection.place_or_event)}")
    if projection.campus_terms:
        lines.append(f"· 点名校区：{'、'.join(projection.campus_terms)}")
    if projection.plan_rationale:
        lines.append(f"· 取证范围：{projection.plan_rationale}")
    plan_line = _query_plan_line(projection)
    if plan_line:
        lines.append(f"· 检索计划：{plan_line}")
    if projection.official_blocked_reason:
        lines.append(f"· 来源限制：{projection.official_blocked_reason}")
    lines.append(f"· 时间条件：{projection.time_filter.note}")
    lines.extend(_search_scope_lines(projection))
    if projection.summary:
        citations = "、".join(
            f"[{item.citation_id}]" for item in projection.summary_citations
        )
        summary = projection.summary + (f" 依据 {citations}" if citations else "")
        lines.extend(["", "【讨论总结】", summary])
    if projection.summary_note:
        lines.extend(["", f"总结说明：{projection.summary_note}"])
    for record in projection.queries:
        lines.append(
            f"· 实际查询词：{record.query}（来源：{record.source}，"
            f"取得 {record.evidence_count} 条候选，状态：{record.status}）"
        )

    lines.append("")
    if projection.confirmed_posts:
        lines.append(f"【确认属于「{TARGET_FORUM_NAME}」的公开帖子】")
        for index, post in enumerate(projection.confirmed_posts, start=1):
            lines.append("")
            lines.append(f"{index}. {post.title or '（页面未给出标题）'}")
            lines.append(f"   链接：{post.url}")
            lines.append(f"   归属依据：{post.affiliation_evidence}")
            lines.append(f"   已读范围：{_read_scope(post)}")
            discoveries = _discovery_text(post.discoveries)
            if discoveries:
                lines.append(f"   发现路径：{discoveries}")
            if post.replies:
                for reply in post.replies:
                    lines.append(
                        f"   第 {_floor(reply.floor)} 楼"
                        f"{_speaker(reply.author, reply.posted_at)}：{reply.content}"
                    )
            else:
                missing = post.read_error_message or "本轮未取得回复文本。"
                lines.append(f"   {REPLIES_NOT_OBTAINED}：{missing}")
    lines.extend(_candidate_link_lines(projection))

    if projection.sections:
        lines.append("")
        lines.append("【只依据真实读到的楼层】")
        for section in projection.sections:
            lines.append(f"· {section}")

    lines.extend(_rejected_lines(projection))
    lines.extend(_official_lines(projection))
    lines.extend(_conflict_lines(projection))

    if projection.evidence_boundary:
        lines.append("")
        lines.append("【证据边界】")
        for note in projection.evidence_boundary:
            lines.append(f"· {note}")
    return "\n".join(lines).rstrip()


def render_empty_content(projection: TiebaResearchProjection) -> str:
    lines = [
        f"{TARGET_FORUM_NAME}信息搜集（{KIND_LABELS[projection.question_kind]}）：{projection.topic}",
        "",
    ]
    if projection.plan_rationale:
        lines.append(f"· 取证范围：{projection.plan_rationale}")
    plan_line = _query_plan_line(projection)
    if plan_line:
        lines.append(f"· 检索计划：{plan_line}")
    if projection.official_blocked_reason:
        lines.append(f"· 来源限制：{projection.official_blocked_reason}")
    lines.extend(_search_scope_lines(projection))
    if projection.empty_reason:
        lines.append(projection.empty_reason)
    for record in projection.queries:
        lines.append(f"· 实际查询词：{record.query}（取得 {record.evidence_count} 条候选）")
    lines.extend(_candidate_link_lines(projection))
    lines.extend(_rejected_lines(projection))
    # 空态同样要给官方核验结果：贴吧没有可用帖子时，官方来源往往才是答案。
    lines.extend(_official_lines(projection))
    lines.extend(_conflict_lines(projection))
    if projection.evidence_boundary:
        lines.append("")
        lines.append("【证据边界】")
        for note in projection.evidence_boundary:
            lines.append(f"· {note}")
    return "\n".join(lines).rstrip()


def render_stopped_content(projection: TiebaResearchProjection) -> str:
    lines = [
        f"{TARGET_FORUM_NAME}信息搜集已停止。",
        "",
        f"· 原问题：{projection.original_question}",
    ]
    for record in projection.queries:
        lines.append(f"· 已发出的查询词：{record.query}（状态：{record.status}）")
    if projection.confirmed_posts:
        lines.append(f"· 停止前已读到 {len(projection.confirmed_posts)} 个确认属于该吧的帖子")
    lines.append("")
    lines.append("已停止：本轮没有继续读取页面，可以在同一会话里重试。")
    return "\n".join(lines).rstrip()


def _query_plan_line(projection: TiebaResearchProjection) -> str:
    """每轮检索目标与真实执行词（与浏览器参数、检索记录同源）。"""
    return "；".join(
        f"{item.goal}「{item.execution_terms}」" for item in projection.query_rounds
    )


def _candidate_link_lines(projection: TiebaResearchProjection) -> list[str]:
    """候选帖链段：只给链接与搜索标题，并逐条写明未确认归属的原因。"""
    if not projection.candidate_links:
        return []
    lines = [
        "",
        "【候选帖链（搜索摘要发现，贴吧归属未确认，未取得回复内容）】",
    ]
    for index, link in enumerate(projection.candidate_links, start=1):
        reason = f"{link.unconfirmed_reason}；" if link.unconfirmed_reason else ""
        discoveries = _discovery_text(link.discoveries)
        path = f"发现路径：{discoveries}；" if discoveries else ""
        lines.append(
            f"{index}. {link.title}｜{link.url}"
            f"（{link.source}；{path}{reason}归属未确认）"
        )
    return lines


def _search_scope_lines(projection: TiebaResearchProjection) -> list[str]:
    lines: list[str] = []
    for scope in projection.search_scope:
        label = "主题" if scope.tab == "thread" else "回复"
        pages = (
            f"第 {scope.visited_pages[0]}–{scope.visited_pages[-1]} 页"
            if scope.visited_pages
            else "未读到页面"
        )
        line = (
            f"· 搜索页范围：{label}页签「{scope.query}」已读 {pages}"
            f"（{scope.pages_read}/{scope.pages_limit} 页）"
        )
        if scope.has_more is True:
            line += "，页面声明还有后续结果"
        elif scope.has_more is False:
            line += "，页面声明没有更多结果"
        if scope.note:
            line += f"；{scope.note}"
        lines.append(line)
    return lines


def _discovery_text(discoveries: Sequence[TiebaSearchDiscovery]) -> str:
    parts: list[str] = []
    for discovery in discoveries:
        kind = "回复命中" if discovery.discovery_type == "reply" else "主题命中"
        query_text = f"「{discovery.query}」" if discovery.query else ""
        page_text = (
            f"第 {discovery.search_page} 页"
            if discovery.search_page is not None
            else "页码未知"
        )
        parts.append(f"{kind}{query_text} · {page_text}")
    return "；".join(parts)


def _official_lines(projection: TiebaResearchProjection) -> list[str]:
    """官方核验段：官方规定与吧友经历分列；成功、仅帖链与空态共用同一段渲染。"""
    if projection.official_checks:
        lines = ["", "【学校官方页面核验（官方规定与吧友经历分开看）】"]
        for check in projection.official_checks:
            lines.append("")
            lines.append(f"· {check.title}")
            lines.append(f"  链接：{check.url}")
            lines.append(f"  取得时间：{_fmt(check.fetched_at)}")
            if check.status == STATUS_VERIFIED and check.excerpt:
                lines.append(f"  官方原文摘录：{check.excerpt}")
                lines.append(f"  命中名词：{'、'.join(check.matched_terms)}")
                if check.applicability is not None:
                    suffix = "（适用）" if check.applicability.applicable else "（适用性未确认）"
                    lines.append(
                        f"  适用性核对：{check.applicability.note}{suffix}"
                    )
            else:
                lines.append(f"  {check.error_message or '未取得官方依据。'}")
        if projection.official_unverified_note:
            lines.append("")
            lines.append(projection.official_unverified_note)
        lines.append("")
        lines.append(
            "说明：以上为学校官方页面原文摘录；前文帖子内容是吧友个人经历，"
            "不能替代官方规定。"
        )
        return lines
    if projection.official_check_requested:
        note = (
            projection.official_unverified_note
            or "本轮涉及校规／费用／开放时间／流程／放假安排，但没有取得可用的学校官方页面，"
            "因此不给出官方结论。"
        )
        return ["", "【学校官方页面核验】", note]
    return []


def _conflict_lines(projection: TiebaResearchProjection) -> list[str]:
    """冲突段：官方与帖子按时间／适用范围核对后如实分列，不替任何一方下结论。"""
    if not projection.conflicts:
        return []
    lines = ["", "【官方与帖子经历的冲突（按时间与适用范围核对）】"]
    for index, conflict in enumerate(projection.conflicts, start=1):
        lines.append("")
        lines.append(f"{index}. 涉及：{'、'.join(conflict.topic_terms)}")
        lines.append(
            f"   官方（{conflict.official_title}｜{conflict.official_url}）："
            f"{conflict.official_statement}"
        )
        for ref in conflict.post_refs:
            floor = f"第 {ref.floor} 楼" if ref.floor is not None else "楼层未知"
            posted = f"｜{ref.posted_at}" if ref.posted_at else ""
            lines.append(f"   帖子（{ref.url}｜{floor}{posted}）：{ref.quote}")
        lines.append(f"   时间核对：{conflict.time_basis}")
        lines.append(f"   范围核对：{conflict.scope_basis}")
        lines.append(f"   处理：{conflict.note}")
    return lines


def _rejected_lines(projection: TiebaResearchProjection) -> list[str]:
    """被剔除的候选：剔除依据逐条写出（非帖子链接与他吧证据都在内），不静默丢弃。"""
    if not projection.rejected_candidates:
        return []
    lines = ["", "【已剔除的候选（每条附剔除依据）】"]
    for rejected in projection.rejected_candidates:
        lines.append(f"· {rejected.title}（{rejected.evidence}）｜{rejected.url}")
    return lines


def _read_scope(post: TiebaPostProjection) -> str:
    parts = [f"已读取 {post.pages_read}/{post.pages_limit} 页"]
    if post.scroll_rounds_limit:
        parts.append(f"向下加载 {post.scroll_rounds}/{post.scroll_rounds_limit} 轮")
    if post.total_pages is not None:
        parts.append(f"页面声明共 {post.total_pages} 页")
    if post.floor_min is not None and post.floor_max is not None:
        parts.append(f"楼层 {post.floor_min}–{post.floor_max}")
    times = [reply.posted_at for reply in post.replies if reply.posted_at]
    if times:
        parts.append(f"时间 {min(times)}–{max(times)}")
    if post.read_status != ReadStatus.READ and post.read_error_message:
        parts.append(post.read_error_message)
    return "；".join(parts)


def _quote(content: str) -> str:
    text = " ".join(content.split())
    if not text:
        return ""
    return text[:QUOTE_MAX_CHARS] + ("…" if len(text) > QUOTE_MAX_CHARS else "")


def _location(
    post: TiebaPostProjection,
    floor: int | None,
    posted_at: str | None,
    author: str | None,
) -> str:
    parts = [post.title or post.url]
    if floor is not None:
        parts.append(f"第 {floor} 楼")
    if author:
        parts.append(author)
    if posted_at:
        parts.append(posted_at)
    return " · ".join(parts)


def _marked(content: str, markers: tuple[str, ...]) -> bool:
    return any(marker in content for marker in markers)


def _append(bucket: list[str], value: str) -> None:
    if len(bucket) < MAX_QUOTES_PER_SECTION:
        bucket.append(value)


def _floor(floor: int | None) -> str:
    return str(floor) if floor is not None else "?"


def _speaker(author: str | None, posted_at: str | None) -> str:
    parts = [value for value in (author, posted_at) if value]
    return f"（{'，'.join(parts)}）" if parts else ""


def _fmt(moment: datetime) -> str:
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")
