"""``tieba.summarize``：只消费已读文本的分区呈现。

分段只用确定性的标记词判断，并且每条都带引文与楼层／时间出处分；没有真实
回复文本时不产出任何个人经历或「吧友普遍认为」式表述，只保留帖链与
「未取得回复内容」的说明。正文完全由真实证据渲染，不依赖模型，因此不存在
用它填内容的空间。
"""

from __future__ import annotations

from datetime import datetime

from bridges.tieba.contracts import (
    ReadStatus,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaResearchProjection,
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
            location = _location(post, reply.floor, reply.posted_at)
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


def render_result_content(projection: TiebaResearchProjection) -> str:
    """把真实证据渲染成中文正文（零虚构：每个数字与引文都来自投影）。"""
    lines: list[str] = [f"{TARGET_FORUM_NAME}信息搜集：{projection.topic}"]
    lines.append("")
    lines.append(f"· 原问题：{projection.original_question}")
    if projection.topic_terms:
        lines.append(f"· 原始名词：{'、'.join(projection.topic_terms)}")
    if projection.place_or_event:
        lines.append(f"· 事件／地点：{'、'.join(projection.place_or_event)}")
    lines.append(f"· 时间条件：{projection.time_filter.note}")
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
            if post.replies:
                for reply in post.replies:
                    lines.append(
                        f"   第 {_floor(reply.floor)} 楼"
                        f"{_time(reply.posted_at)}：{reply.content}"
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

    if projection.official_checks:
        lines.append("")
        lines.append("【学校官方页面核验（官方规定与吧友经历分开看）】")
        for check in projection.official_checks:
            lines.append("")
            lines.append(f"· {check.title}")
            lines.append(f"  链接：{check.url}")
            lines.append(f"  取得时间：{_fmt(check.fetched_at)}")
            if check.status == STATUS_VERIFIED and check.excerpt:
                lines.append(f"  官方原文摘录：{check.excerpt}")
                lines.append(f"  命中名词：{'、'.join(check.matched_terms)}")
            else:
                lines.append(f"  {check.error_message or '未取得官方依据。'}")
        lines.append("")
        lines.append(
            "说明：以上为学校官方页面原文摘录；前文帖子内容是吧友个人经历，"
            "不能替代官方规定。"
        )
    elif projection.official_check_requested:
        lines.append("")
        lines.append("【学校官方页面核验】")
        lines.append(
            "本轮涉及校规／费用／开放时间／流程，但没有取得可用的学校官方页面，"
            "因此不给出官方结论。"
        )

    if projection.evidence_boundary:
        lines.append("")
        lines.append("【证据边界】")
        for note in projection.evidence_boundary:
            lines.append(f"· {note}")
    return "\n".join(lines).rstrip()


def render_empty_content(projection: TiebaResearchProjection) -> str:
    lines = [f"{TARGET_FORUM_NAME}信息搜集：{projection.topic}", ""]
    if projection.empty_reason:
        lines.append(projection.empty_reason)
    for record in projection.queries:
        lines.append(f"· 实际查询词：{record.query}（取得 {record.evidence_count} 条候选）")
    lines.extend(_candidate_link_lines(projection))
    lines.extend(_rejected_lines(projection))
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


def _candidate_link_lines(projection: TiebaResearchProjection) -> list[str]:
    """候选帖链段：只给链接与搜索标题，并写明归属未确认、未取得回复内容。"""
    if not projection.candidate_links:
        return []
    lines = [
        "",
        "【候选帖链（搜索摘要发现，贴吧归属未确认，未取得回复内容）】",
    ]
    for index, link in enumerate(projection.candidate_links, start=1):
        lines.append(f"{index}. {link.title}｜{link.url}")
    return lines


def _rejected_lines(projection: TiebaResearchProjection) -> list[str]:
    """被剔除的同名帖：剔除依据逐条写出，不静默丢弃。"""
    if not projection.rejected_candidates:
        return []
    lines = ["", "【已剔除的同名帖（证据指向其他贴吧）】"]
    for rejected in projection.rejected_candidates:
        lines.append(f"· {rejected.title}（{rejected.evidence}）｜{rejected.url}")
    return lines


def _read_scope(post: TiebaPostProjection) -> str:
    parts = [f"已读取 {post.pages_read}/{post.pages_limit} 页"]
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


def _location(post: TiebaPostProjection, floor: int | None, posted_at: str | None) -> str:
    parts = [post.title or post.url]
    if floor is not None:
        parts.append(f"第 {floor} 楼")
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


def _time(posted_at: str | None) -> str:
    return f"（{posted_at}）" if posted_at else ""


def _fmt(moment: datetime) -> str:
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")
