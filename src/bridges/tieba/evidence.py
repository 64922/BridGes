"""贴吧候选帖的实际读取与归属筛选。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
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
from bridges.tieba.reading import READ_PAGES_LIMIT, TiebaThreadReader
from bridges.tieba.reading_scope import scope_scoped_result
from bridges.tieba.relevance import post_matches_topic
from bridges.tieba.searching import (
    plan_queries,
)

READ_DEADLINE_SECONDS = 60.0

#: 首轮每个帖子只取一页，先让多个候选都获得真实页面证据。
READ_FIRST_PASS_PAGES = 1

#: 单帖首轮或深入读取的墙钟上限；总读取截止仍由阶段预算收紧。
READ_POST_DEADLINE_SECONDS = 15.0

#: 首轮后最多深入的帖子数，避免单个长帖独占资源。
DEEP_READ_POSTS_LIMIT = 3

#: 首轮已有这么多确认帖且各自取得原文时，停止追加采集。
SUFFICIENT_CONFIRMED_POSTS = 3


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
    #: 首轮实际取得页面的帖子数、首轮后成功深入的帖子数。
    first_pass_read_count: int = 0
    deepened_post_count: int = 0
    closeout_reason: str = "completed"


def read_candidates(
    reader: TiebaThreadReader,
    attempts: SearchAttempts,
    *,
    analysis: TiebaQuestionAnalysis,
    stop_event: threading.Event | None,
    deadline_seconds: float,
    resume: dict[str, Any] | None = None,
    checkpoint: Callable[[ReadOutcome], None] | None = None,
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
                resumed_post = TiebaPostProjection.model_validate(raw)
            except ValueError:
                continue
            confirmed.append(resumed_post)
            attempted.add(resumed_post.url)
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
    closeout_reason = "completed"
    first_pass_read_count = 0
    deepened_post_count = 0
    deep_failed = False
    deadline = time.monotonic() + deadline_seconds
    excluded_wait_seconds = 0.0

    def notify() -> None:
        if checkpoint is None:
            return
        checkpoint(
            ReadOutcome(
                confirmed=list(confirmed),
                rejected=list(rejected),
                unconfirmed=list(links),
                unreadable=unreadable,
                not_attempted=0,
                unconfirmed_total=len(links),
                attempted_urls=tuple(sorted(attempted | new_attempted)),
                retry_urls=tuple(sorted(retry_urls)),
                partial=True,
                first_pass_read_count=first_pass_read_count,
                deepened_post_count=deepened_post_count,
                closeout_reason="checkpoint",
            )
        )
    # 回复命中往往来自标题不含话题的帖子；先给它们读取机会，避免被主题词
    # 的前几条候选永久挤在读取上限之外。
    ordered_hits = tuple(
        sorted(
            attempts.hits,
            key=lambda hit: 0 if hit.discovery_type == "reply" else 1,
        )
    )
    read_hits = ordered_hits[:READ_POSTS_LIMIT]
    topic_terms = list(
        dict.fromkeys(
            term
            for query in plan_queries(analysis)
            for term in query.split()[2:]
            if term not in analysis.campus_terms and term != "贴吧"
        )
    )
    topic_analysis = analysis.model_copy(update={"topic_terms": topic_terms})
    for index, hit in enumerate(read_hits):
        if hit.url in attempted:
            prior = prior_links.get(hit.url)
            if prior is not None:
                links.append(prior)
            continue
        if stop_event is not None and stop_event.is_set():
            partial = True
            closeout_reason = "stopped"
            stopped_index = index - 1
            break
        if time.monotonic() >= deadline:
            partial = True
            closeout_reason = "deadline"
            stopped_index = index - 1
            break
        result = reader.read(
            hit.url,
            pages_limit=READ_FIRST_PASS_PAGES,
            stop_event=stop_event,
            deadline=_slice_deadline(
                deadline,
                len(read_hits) - index,
                extra_seconds=excluded_wait_seconds,
            ),
        )
        wait_seconds = max(0, result.excluded_wait_ms) / 1000
        deadline += wait_seconds
        excluded_wait_seconds += wait_seconds
        new_attempted.add(hit.url)
        if _auth_blocked(result.error_code):
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
            closeout_reason = "auth_required"
            stopped_index = index
            notify()
            break
        if _budget_blocked(result.error_code):
            unreadable += 1
            links.append(
                _unconfirmed_link(
                    hit,
                    "未读取：本轮工作预算已耗尽",
                    read_error_code=result.error_code,
                )
            )
            partial = True
            closeout_reason = "budget_exhausted"
            stopped_index = index
            notify()
            break
        retry_urls.discard(hit.url)
        action, post, rejected_item = _classify_read(hit, result, topic_analysis)
        if action == "confirmed" and post is not None:
            confirmed.append(post)
            first_pass_read_count += 1
        elif action == "rejected" and rejected_item is not None:
            rejected.append(rejected_item)
        else:
            unreadable += 1
            links.append(
                _unconfirmed_link(
                    hit,
                    _read_block_reason(result),
                    read_error_code=result.error_code,
                )
            )
        notify()
    if closeout_reason == "completed":
        if len(confirmed) >= SUFFICIENT_CONFIRMED_POSTS and all(
            post.replies_obtained for post in confirmed
        ):
            closeout_reason = "evidence_sufficient"
        else:
            deep_candidates = [
                post
                for post in confirmed
                if post.url not in attempted and _needs_deepening(post)
            ][:DEEP_READ_POSTS_LIMIT]
            for index, post in enumerate(deep_candidates):
                if stop_event is not None and stop_event.is_set():
                    partial = True
                    closeout_reason = "stopped"
                    break
                if time.monotonic() >= deadline:
                    partial = True
                    closeout_reason = "deadline"
                    break
                deep_hit = next(
                    (item for item in read_hits if item.url == post.url), None
                )
                if deep_hit is None:
                    continue
                result = reader.read(
                    post.url,
                    pages_limit=READ_PAGES_LIMIT,
                    stop_event=stop_event,
                    deadline=_slice_deadline(
                        deadline,
                        len(deep_candidates) - index,
                        extra_seconds=excluded_wait_seconds,
                    ),
                )
                wait_seconds = max(0, result.excluded_wait_ms) / 1000
                deadline += wait_seconds
                excluded_wait_seconds += wait_seconds
                if _auth_blocked(result.error_code):
                    deep_failed = True
                    closeout_reason = "deepen_incomplete"
                    break
                if _budget_blocked(result.error_code):
                    partial = True
                    closeout_reason = "budget_exhausted"
                    break
                action, deeper, _ = _classify_read(deep_hit, result, topic_analysis)
                if action == "confirmed" and deeper is not None:
                    if _improved(post, deeper):
                        confirmed[confirmed.index(post)] = deeper
                        deepened_post_count += 1
                        notify()
                else:
                    deep_failed = True
            if closeout_reason == "completed" and deep_failed:
                closeout_reason = "deepen_incomplete"
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
        first_pass_read_count=first_pass_read_count,
        deepened_post_count=deepened_post_count,
        closeout_reason=closeout_reason,
    )


def _slice_deadline(
    global_deadline: float, slots: int, *, extra_seconds: float = 0.0
) -> float:
    """给当前帖子一个有界时间片，避免慢帖吃光后续首轮覆盖。

    人工登录等待已从总预算中排除，因此给剩余帖子的工作时间上限也按等待时长
    顺延，但仍不能越过已经顺延的总截止。
    """
    now = time.monotonic()
    remaining = global_deadline - now
    if remaining <= 0:
        return global_deadline
    cap = min(READ_POST_DEADLINE_SECONDS + max(0.0, extra_seconds), remaining)
    share = remaining / max(1, slots)
    return now + min(cap, share)


def _auth_blocked(code: str | None) -> bool:
    return code in {
        "tieba_browser_auth_required",
        "tieba_browser_auth_interrupted",
        "tieba_browser_auth_unverified",
    }


def _budget_blocked(code: str | None) -> bool:
    return code in {
        "run_budget_exhausted",
        "tieba_browser_work_time_exhausted",
        "tieba_browser_request_queue_full",
    }


def _classify_read(
    hit: TiebaSearchHit,
    result: TiebaReadResult,
    analysis: TiebaQuestionAnalysis,
) -> tuple[str, TiebaPostProjection | None, TiebaRejectedCandidate | None]:
    if result.forum_matches_target:
        scoped = scope_scoped_result(result, analysis)
        if not scoped.replies or not post_matches_topic(scoped, analysis):
            return (
                "rejected",
                None,
                TiebaRejectedCandidate(
                    url=scoped.url,
                    title=scoped.title or hit.title,
                    evidence="已读取目标贴吧页面，但没有符合本轮话题及明确校区条件的原文。",
                ),
            )
        return "confirmed", confirmed_post(hit, scoped), None
    if result.forum_name and result.status in {ReadStatus.READ, ReadStatus.PARTIAL}:
        return (
            "rejected",
            None,
            TiebaRejectedCandidate(
                url=result.url,
                title=result.title or hit.title,
                evidence=(
                    f"已读取帖子页面，页面声明所属贴吧为「{result.forum_name}」，不是目标贴吧"
                ),
            ),
        )
    return "unreadable", None, None


def _needs_deepening(post: TiebaPostProjection) -> bool:
    if not post.replies_obtained:
        return False
    if post.pages_read <= 0:
        return post.nested_incomplete_count > 0
    if post.total_pages is not None and post.pages_read < post.total_pages:
        return True
    if post.pages_read < READ_PAGES_LIMIT and not post.pages_complete:
        return True
    return post.nested_incomplete_count > 0


def _improved(previous: TiebaPostProjection, current: TiebaPostProjection) -> bool:
    return (
        current.pages_read > previous.pages_read
        or len(current.replies) > len(previous.replies)
        or (current.nested_complete and not previous.nested_complete)
    )


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
        scope_excluded_reasons=dict(result.scope_excluded_reasons),
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
