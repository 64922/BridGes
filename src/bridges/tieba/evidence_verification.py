"""贴吧取证计划与证据一致性验证规则。"""

from __future__ import annotations

from collections.abc import Sequence

from bridges.tieba.contracts import (
    TiebaEvidencePlan,
    TiebaEvidenceRoute,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaQuestionKind,
    TiebaResearchProjection,
    TiebaVerification,
)
from bridges.tieba.evidence_official import (
    assess_applicability,
    detect_conflicts,
)
from bridges.tieba.evidence_time import apply_time_condition
from bridges.tieba.lexicon import TARGET_FORUM_NAME
from bridges.tieba.official import (
    STATUS_VERIFIED,
    official_fallback_query,
    official_query,
)
from bridges.tieba.presenting import build_sections
from bridges.tieba.recipe import CONSENSUS_PHRASES, OFFICIAL_UNVERIFIED_NOTE
from bridges.tieba.searching import (
    plan_query_rounds,
)


def build_evidence_plan(
    analysis: TiebaQuestionAnalysis, *, official_blocked_reason: str | None
) -> TiebaEvidencePlan:
    """按问题类型安排来源顺序：规定官方优先，体验只查贴吧，混合两路独立。"""
    if analysis.question_kind is TiebaQuestionKind.EXPERIENCE:
        return TiebaEvidencePlan(
            question_kind=analysis.question_kind,
            routes=[TiebaEvidenceRoute.TIEBA],
            source_priority=[TiebaEvidenceRoute.TIEBA],
            official_required=False,
            official_blocked_reason=None,
            check_requirements=["以目标贴吧的真实帖子与回复呈现实际经历与不同看法。"],
            query_rounds=list(plan_query_rounds(analysis)),
            official_queries=[],
            rationale="体验类问题：以目标贴吧实际帖子与回复为主，只使用真实读到的楼层。",
        )
    blocked = official_blocked_reason
    if analysis.question_kind is TiebaQuestionKind.POLICY:
        rationale = (
            "规定类问题：先核对与任务相关的学校官方主体/校区/用途/日期，"
            "再由贴吧帖子与回复提供实际办理经历；两路独立、共享同一运行预算。"
        )
    else:
        rationale = (
            "混合问题：官方页面核对规定，贴吧帖子与回复给实际经历；"
            "两路独立取证、共享同一运行预算，冲突按时间与适用范围分列。"
        )
    requirements = ["核对官方主体、点名校区、用途与日期（版本）是否适用。"]
    if analysis.campus_terms:
        requirements.append(f"官方页面需点名校区「{analysis.campus_terms[0]}」或标注未确认。")
    if analysis.time_requirement:
        requirements.append(f"核对时间条件「{analysis.time_requirement}」的时效。")
    requirements.append("贴吧路径提供真实办理经历与不同看法，不替代官方规定。")
    return TiebaEvidencePlan(
        question_kind=analysis.question_kind,
        routes=[TiebaEvidenceRoute.OFFICIAL, TiebaEvidenceRoute.TIEBA],
        source_priority=[TiebaEvidenceRoute.OFFICIAL, TiebaEvidenceRoute.TIEBA],
        official_required=not blocked,
        official_blocked_reason=blocked,
        check_requirements=requirements,
        query_rounds=list(plan_query_rounds(analysis)),
        official_queries=(
            [] if blocked else [official_query(analysis), official_fallback_query(analysis)]
        ),
        rationale=rationale,
    )


def run_verification(
    analysis: TiebaQuestionAnalysis,
    plan: TiebaEvidencePlan,
    projection: TiebaResearchProjection,
    checks: Sequence[TiebaOfficialCheck],
    posts: Sequence[TiebaPostProjection],
) -> TiebaVerification:
    """结构化核验：归属、读取范围、未读声明、官方范围与冲突披露。"""
    read_by_url = {post.url: post for post in posts}
    attribution = all(
        post.url in read_by_url
        and TARGET_FORUM_NAME in post.affiliation_evidence
        and post.affiliation_evidence.startswith("已读取帖子页面")
        for post in projection.confirmed_posts
    )
    _, filtered_posts = apply_time_condition(analysis, list(posts))
    read_scope = _read_scope_consistent(projection.confirmed_posts, filtered_posts)
    unread_absent = _quotes_traceable(projection.sections, filtered_posts) and _summary_traceable(
        projection, filtered_posts
    )
    consensus_absent = not _has_consensus_phrases(projection)
    official_scope = _official_scope_consistent(analysis, plan, projection, checks)
    recomputed = detect_conflicts(analysis, checks, posts)
    conflicts_disclosed = projection.conflicts == recomputed
    unconfirmed: list[str] = []
    if not attribution:
        unconfirmed.append("存在与读取页面不一致的帖归属引用。")
    if not read_scope:
        unconfirmed.append("存在超出实际读取范围的楼层引用。")
    if not unread_absent:
        unconfirmed.append("存在无法回溯到已读回复的摘录。")
    if not official_scope:
        unconfirmed.append("官方说法超出已取得且适用的页面范围。")
    if not conflicts_disclosed:
        unconfirmed.append("官方与帖子的冲突没有如实分列。")
    passed = (
        attribution
        and read_scope
        and unread_absent
        and consensus_absent
        and official_scope
        and conflicts_disclosed
    )
    return TiebaVerification(
        attribution_consistent=attribution,
        read_scope_consistent=read_scope,
        unread_claims_absent=unread_absent,
        consensus_claims_absent=consensus_absent,
        official_scope_consistent=official_scope,
        conflicts_disclosed=conflicts_disclosed,
        summary=(
            "核验通过：归属、读取范围、官方适用范围与冲突披露都与真实产物一致。"
            if passed
            else "核验未通过：" + "；".join(unconfirmed)
        ),
        unconfirmed=unconfirmed,
    )


def _read_scope_consistent(
    projected: Sequence[TiebaPostProjection], posts: Sequence[TiebaPostProjection]
) -> bool:
    read_by_url = {post.url: post for post in posts}
    for item in projected:
        source = read_by_url.get(item.url)
        if source is None:
            return False
        if (
            item.pages_read != source.pages_read
            or item.pages_limit != source.pages_limit
            or item.pages_complete != source.pages_complete
            or item.page_evidence != source.page_evidence
            or item.scroll_rounds != source.scroll_rounds
            or item.scroll_rounds_limit != source.scroll_rounds_limit
            or item.total_pages != source.total_pages
            or item.read_status != source.read_status
            or item.pagination_error_code != source.pagination_error_code
            or item.pagination_error_message != source.pagination_error_message
            or item.retrieved_at != source.retrieved_at
            or item.title != source.title
            or item.thread_id != source.thread_id
            or item.affiliation_evidence != source.affiliation_evidence
            or item.replies_obtained != bool(item.replies)
        ):
            return False
        if item.floor_min != source.floor_min or item.floor_max != source.floor_max:
            return False
        for reply in item.replies:
            if reply not in source.replies:
                return False
    return True


def _quotes_traceable(sections: Sequence[str], posts: Sequence[TiebaPostProjection]) -> bool:
    """每个分段里的引文都必须能回溯到真实读到的回复原文。"""
    return list(sections) == build_sections(list(posts))


def _has_consensus_phrases(projection: TiebaResearchProjection) -> bool:
    """检查我们自己生成的文字：引文之外的模板不得出现共识式表述。"""
    generated = [
        projection.plan_rationale or "",
        projection.empty_reason or "",
        projection.official_unverified_note or "",
        projection.official_blocked_reason or "",
        *(item.note for item in projection.conflicts),
        *(item.time_basis for item in projection.conflicts),
        *(item.scope_basis for item in projection.conflicts),
        *(claim.text for claim in projection.summary_claims),
    ]
    return any(phrase in text for phrase in CONSENSUS_PHRASES for text in generated)


def _summary_traceable(
    projection: TiebaResearchProjection, posts: Sequence[TiebaPostProjection]
) -> bool:
    citations = {item.citation_id: item for item in projection.summary_citations}
    if len(citations) != len(projection.summary_citations):
        return False
    source_posts = {post.url: post for post in posts}
    for citation in projection.summary_citations:
        post = source_posts.get(citation.url)
        if post is None:
            return False
        matches = [
            reply
            for reply in post.replies
            if reply.reply_id == citation.reply_id
            and reply.parent_reply_id == citation.parent_reply_id
            and reply.nested == citation.nested
            and reply.floor == citation.floor
            and reply.posted_at == citation.posted_at
            and reply.author == citation.author
            and citation.quote
            and citation.quote in reply.content
        ]
        if len(matches) != 1:
            return False
    for claim in projection.summary_claims:
        if not claim.text.strip() or not claim.citation_ids:
            return False
        if any(item not in citations for item in claim.citation_ids):
            return False
    if projection.summary:
        if not projection.summary_claims or not citations:
            return False
        claim_citation_ids = {
            citation_id for claim in projection.summary_claims for citation_id in claim.citation_ids
        }
        if not claim_citation_ids.issubset(citations):
            return False
    return True


def _official_scope_consistent(
    analysis: TiebaQuestionAnalysis,
    plan: TiebaEvidencePlan,
    projection: TiebaResearchProjection,
    checks: Sequence[TiebaOfficialCheck],
) -> bool:
    if not analysis.needs_official_check:
        return not projection.official_checks
    if plan.official_blocked_reason:
        return (
            projection.official_blocked_reason == plan.official_blocked_reason
            and not projection.official_checks
        )
    if list(projection.official_checks) != list(checks):
        return False
    for check in checks:
        if check.applicability != assess_applicability(check, analysis):
            return False
        if check.status == STATUS_VERIFIED and (
            not check.excerpt or not check.matched_terms or check.applicability is None
        ):
            return False
    applicable = [
        check
        for check in checks
        if check.status == STATUS_VERIFIED
        and check.applicability is not None
        and check.applicability.applicable
    ]
    return bool(applicable or projection.official_unverified_note == OFFICIAL_UNVERIFIED_NOTE)
