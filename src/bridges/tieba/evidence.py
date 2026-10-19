"""贴吧候选帖的实际读取与归属筛选。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from bridges.tieba.contracts import (
    ReadStatus,
    TiebaCandidateLink,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaReadResult,
    TiebaRejectedCandidate,
    TiebaSearchHit,
)
from bridges.tieba.evidence_search import SearchAttempts
from bridges.tieba.lexicon import (
    CAMPUS_TERMS,
    PLACE_EVENT_TERMS,
)
from bridges.tieba.reading import TiebaThreadReader
from bridges.tieba.relevance import post_matches_topic, text_matches_topic
from bridges.tieba.searching import (
    plan_queries,
)

READ_DEADLINE_SECONDS = 12.0


#: 单轮最多读取的帖子数（每个帖子一次真实公开读取）。
READ_POSTS_LIMIT = 8


#: 帖链降级最多给出的候选链接数。
CANDIDATE_LINKS_LIMIT = 6


#: 读取失败分类的中文短标签（仅帖链的「页面不可读」原因）。
READ_BLOCK_LABELS: dict[ReadStatus, str] = {
    ReadStatus.ACCESS_RESTRICTED: "访问受限，未绕过",
    ReadStatus.UNRECOGNIZED: "页面结构无法解析",
    ReadStatus.NOT_FOUND: "帖子不存在或已删除",
    ReadStatus.TIMEOUT: "读取超时",
    ReadStatus.ERROR: "读取失败",
    ReadStatus.CANCELLED: "已停止读取",
}


@dataclass(frozen=True)
class ReadOutcome:
    """读取阶段的真实产出：确认帖、他吧帖、仅帖链与读取侧计数。"""

    confirmed: list[TiebaPostProjection]
    rejected: list[TiebaRejectedCandidate]
    unconfirmed: list[TiebaCandidateLink]
    #: 读过页面但没取得可确认归属的候选数，与超出读取上限、未尝试读取的候选数。
    unreadable: int = 0
    not_attempted: int = 0
    #: 未取得页面的候选总数（帖链列表按上限截断，计数不截断）。
    unconfirmed_total: int = 0
    #: 已经真实读取过页面的链接（恢复时跳过，不重复取证）。
    attempted_urls: tuple[str, ...] = ()
    #: 认证等待完成后必须重读的链接；失败尝试不能作为完成证据复用。
    retry_urls: tuple[str, ...] = ()
    partial: bool = False


def read_candidates(
    reader: TiebaThreadReader,
    attempts: SearchAttempts,
    *,
    analysis: TiebaQuestionAnalysis,
    stop_event: threading.Event | None,
    deadline_seconds: float,
    resume: dict[str, Any] | None = None,
) -> ReadOutcome:
    """读取候选：只有页面自身确认属于目标贴吧的帖子才算确认。

    ``resume`` 是上一轮的部分读取产物：已经真实读过的链接按原结果续用，
    不重复发起页面读取；只补还没读完的候选。
    """
    confirmed: list[TiebaPostProjection] = []
    rejected = list(attempts.rejected)
    links: list[TiebaCandidateLink] = []
    unreadable = 0
    attempted: set[str] = set()
    retry_urls: set[str] = set()
    prior_links: dict[str, TiebaCandidateLink] = {}
    if resume is not None:
        for raw in resume.get("confirmed", []):
            try:
                post = TiebaPostProjection.model_validate(raw)
            except ValueError:
                continue
            confirmed.append(post)
            attempted.add(post.url)
        for raw in resume.get("rejected", []):
            try:
                item = TiebaRejectedCandidate.model_validate(raw)
            except ValueError:
                continue
            if item.url not in {entry.url for entry in rejected}:
                rejected.append(item)
            attempted.add(item.url)
        for raw in resume.get("unconfirmed", []):
            try:
                link = TiebaCandidateLink.model_validate(raw)
            except ValueError:
                continue
            prior_links[link.url] = link
        attempted.update(str(url) for url in resume.get("attempted_urls", []))
        retry_urls.update(str(url) for url in resume.get("retry_urls", []))
        attempted.difference_update(retry_urls)
        unreadable += int(resume.get("unreadable") or 0)
    partial = False
    new_attempted: set[str] = set()
    browser_blocked_error: str | None = None
    stopped_index: int | None = None
    deadline = time.monotonic() + deadline_seconds
    # 回复命中往往来自标题不含话题的帖子；先给它们读取机会，避免被主题页
    # 的前几条候选永久挤在读取上限之外。
    ordered_hits = tuple(
        sorted(
            attempts.hits,
            key=lambda hit: 0 if hit.discovery_type == "reply" else 1,
        )
    )
    read_hits = ordered_hits[:READ_POSTS_LIMIT]
    for index, hit in enumerate(read_hits):
        if hit.url in attempted:
            prior = prior_links.get(hit.url)
            if prior is not None:
                links.append(prior)
            continue
        if stop_event is not None and stop_event.is_set():
            partial = True
            stopped_index = index - 1
            break
        result = reader.read(hit.url, stop_event=stop_event, deadline=deadline)
        deadline += max(0, result.excluded_wait_ms) / 1000
        new_attempted.add(hit.url)
        if result.error_code in {
            "tieba_browser_auth_required",
            "tieba_browser_auth_interrupted",
            "tieba_browser_auth_unverified",
        }:
            links.append(
                _unconfirmed_link(
                    hit,
                    _read_block_reason(result),
                    read_error_code=result.error_code,
                )
            )
            retry_urls.add(hit.url)
            browser_blocked_error = result.error_code
            partial = True
            stopped_index = index
            break
        retry_urls.discard(hit.url)
        if result.forum_matches_target:
            topic_terms = list(
                dict.fromkeys(
                    term
                    for query in plan_queries(analysis)
                    for term in query.split()[2:]
                    if term not in analysis.campus_terms and term != "贴吧"
                )
            )
            topic_analysis = analysis.model_copy(update={"topic_terms": topic_terms})
            result = _campus_scoped_result(result, topic_analysis)
            if not result.replies or not post_matches_topic(result, topic_analysis):
                rejected.append(
                    TiebaRejectedCandidate(
                        url=result.url,
                        title=result.title or hit.title,
                        evidence="已读取目标贴吧页面，但没有符合本轮话题及明确校区条件的原文。",
                    )
                )
                continue
            confirmed.append(confirmed_post(hit, result))
            continue
        if result.forum_name and result.status in {ReadStatus.READ, ReadStatus.PARTIAL}:
            rejected.append(
                TiebaRejectedCandidate(
                    url=result.url,
                    title=result.title or hit.title,
                    evidence=(
                        f"已读取帖子页面，页面声明所属贴吧为「{result.forum_name}」，不是目标贴吧"
                    ),
                )
            )
            continue
        unreadable += 1
        links.append(
            _unconfirmed_link(
                hit,
                _read_block_reason(result),
                read_error_code=result.error_code,
            )
        )
    not_attempted = 0
    remaining_hits = list(read_hits[stopped_index + 1 :] if stopped_index is not None else [])
    remaining_hits.extend(ordered_hits[READ_POSTS_LIMIT:])
    for hit in remaining_hits:
        if hit.url in attempted or hit.url in new_attempted:
            prior = prior_links.get(hit.url)
            if prior is not None:
                links.append(prior)
            continue
        not_attempted += 1
        if browser_blocked_error == "tieba_browser_auth_unverified":
            reason = "未读取：无法独立确认专用会话的认证状态"
        elif browser_blocked_error is not None:
            reason = "未读取：当前会话认证待完成，之后可继续"
        else:
            reason = "未读取：本轮停止或超出读取条数上限"
        links.append(_unconfirmed_link(hit, reason))
    visible_links = links[:CANDIDATE_LINKS_LIMIT]
    auth_link = next(
        (
            link
            for link in links
            if link.read_error_code
            in {
                "tieba_browser_auth_required",
                "tieba_browser_auth_interrupted",
                "tieba_browser_auth_unverified",
            }
        ),
        None,
    )
    if auth_link is not None and all(link.url != auth_link.url for link in visible_links):
        visible_links = [*visible_links[: CANDIDATE_LINKS_LIMIT - 1], auth_link]
    return ReadOutcome(
        confirmed=confirmed,
        rejected=rejected,
        unconfirmed=visible_links,
        unreadable=unreadable,
        not_attempted=not_attempted,
        unconfirmed_total=len(links),
        attempted_urls=tuple(sorted(attempted | new_attempted)),
        retry_urls=tuple(sorted(retry_urls)),
        partial=partial,
    )


def _campus_scoped_result(
    result: TiebaReadResult,
    analysis: TiebaQuestionAnalysis,
) -> TiebaReadResult:
    """校区必须由实际内容确认，保留匹配发言及其无冲突的关联上下文。"""
    if not analysis.campus_terms:
        return result
    requested = set(analysis.campus_terms)
    kept = []
    kept_ids: set[str] = set()
    pending = list(result.replies)
    while pending:
        next_pending = []
        for reply in pending:
            named = {term for term in CAMPUS_TERMS if term in reply.content}
            topic_match = text_matches_topic(reply.content, analysis.topic_terms)
            other_topic = not topic_match and _mentions_other_topic(reply.content, analysis)
            matches = requested.issubset(named) and topic_match
            context = not named and not other_topic and (
                topic_match or reply.parent_reply_id in kept_ids
            )
            if matches or context:
                kept.append(reply)
                if reply.reply_id:
                    kept_ids.add(reply.reply_id)
            elif not named and reply.parent_reply_id:
                next_pending.append(reply)
        if len(next_pending) == len(pending):
            break
        pending = next_pending
    identities = {id(reply) for reply in kept}
    by_id = {reply.reply_id: reply for reply in result.replies if reply.reply_id}
    # 楼中楼明确校区时，补上其未声明其他校区的父发言，防止纠正失去对象。
    pending_parents = [reply.parent_reply_id for reply in kept if reply.parent_reply_id]
    while pending_parents:
        parent = by_id.get(pending_parents.pop())
        if parent is None or id(parent) in identities:
            continue
        named = {term for term in CAMPUS_TERMS if term in parent.content}
        if named and not requested.issubset(named):
            continue
        if not text_matches_topic(parent.content, analysis.topic_terms) and _mentions_other_topic(
            parent.content, analysis
        ):
            continue
        identities.add(id(parent))
        if parent.parent_reply_id:
            pending_parents.append(parent.parent_reply_id)
    ordered = [reply for reply in result.replies if id(reply) in identities]
    excluded = len(result.replies) - len(ordered)
    if not excluded:
        return result
    return result.model_copy(
        update={
            "replies": ordered,
            "status": ReadStatus.PARTIAL,
            "error_message": "；".join(
                filter(
                    None,
                    [
                        result.error_message,
                        f"明确校区或话题条件下排除了 {excluded} 条不匹配的发言。",
                    ],
                )
            ),
        }
    )


def _mentions_other_topic(content: str, analysis: TiebaQuestionAnalysis) -> bool:
    """Reject an unrelated known campus topic from otherwise matching context."""
    requested = set(analysis.topic_terms) | set(analysis.campus_terms)
    return any(term in content for term in PLACE_EVENT_TERMS - requested)


def confirmed_post(hit: TiebaSearchHit, result: TiebaReadResult) -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id=result.thread_id,
        url=result.url,
        title=result.title or hit.title,
        affiliation_evidence=f"已读取帖子页面，页面声明所属贴吧为「{result.forum_name}」",
        read_status=result.status,
        pages_read=result.pages_read,
        pages_limit=result.pages_limit,
        pages_complete=result.pages_complete,
        page_evidence=list(result.page_evidence),
        scroll_rounds=result.scroll_rounds,
        scroll_rounds_limit=result.scroll_rounds_limit,
        total_pages=result.total_pages,
        total_replies=result.total_replies,
        floor_min=result.floor_min,
        floor_max=result.floor_max,
        replies_obtained=bool(result.replies),
        replies=list(result.replies),
        read_error_code=result.error_code,
        read_error_message=result.error_message,
        pagination_error_code=result.pagination_error_code,
        pagination_error_message=result.pagination_error_message,
        nested_complete=result.nested_complete,
        nested_incomplete_count=result.nested_incomplete_count,
        nested_error_code=result.nested_error_code,
        nested_error_message=result.nested_error_message,
        discoveries=list(hit.discoveries),
        retrieved_at=result.retrieved_at,
    )


def _unconfirmed_link(
    hit: TiebaSearchHit, reason: str, *, read_error_code: str | None = None
) -> TiebaCandidateLink:
    """仅帖链降级：来源标识与「为什么只有帖链」分开记，不把原因塞进来源字段。"""
    return TiebaCandidateLink(
        url=hit.url,
        title=hit.title,
        source=hit.source,
        unconfirmed_reason=reason,
        read_error_code=read_error_code,
        discoveries=list(hit.discoveries),
    )


def _read_block_reason(result: TiebaReadResult) -> str:
    """读取失败或页面未声明吧名时的中文短因（页面不可读一类）。"""
    if result.forum_name is None and result.status in {ReadStatus.READ, ReadStatus.PARTIAL}:
        return "页面不可读：页面未声明所属贴吧"
    label = READ_BLOCK_LABELS.get(result.status, result.status.value)
    return f"页面不可读：{label}"
