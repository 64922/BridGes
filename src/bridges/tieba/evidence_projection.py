"""将真实搜索、读取与核验结果构造成研究投影。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from bridges.contracts.modules import ModuleQueryRecord
from bridges.tieba.contracts import (
    TiebaCandidateLink,
    TiebaConflict,
    TiebaEvidenceRoute,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaQueryRound,
    TiebaQuestionAnalysis,
    TiebaRejectedCandidate,
    TiebaResearchProjection,
    TiebaResearchStatus,
    TiebaSearchScope,
    TiebaTimeFilter,
)
from bridges.tieba.evidence import ReadOutcome
from bridges.tieba.evidence_official import OFFICIAL_TRIGGER_NOTE
from bridges.tieba.evidence_search import FAILED_QUERY_STATUSES, FORUM_NAME, SearchAttempts
from bridges.tieba.evidence_time import HOLIDAY_YEAR_NOTE
from bridges.tieba.evidence_time_rules import holiday_without_year
from bridges.tieba.lexicon import (
    TARGET_FORUM_NAME,
    TARGET_FORUM_URL,
)
from bridges.tieba.searching import (
    HitDiagnostics,
)


def build_projection(
    *,
    analysis: TiebaQuestionAnalysis,
    records: Sequence[ModuleQueryRecord],
    confirmed: list[TiebaPostProjection],
    rejected: list[TiebaRejectedCandidate],
    unconfirmed: list[TiebaCandidateLink],
    sections: list[str],
    time_filter: TiebaTimeFilter,
    official_checks: list[TiebaOfficialCheck],
    conflicts: list[TiebaConflict] | None = None,
    attempts: SearchAttempts | None = None,
    reads: ReadOutcome | None = None,
    official_blocked_reason: str | None = None,
    plan_rationale: str | None = None,
    query_rounds: Sequence[TiebaQueryRound] = (),
    source_priority: Sequence[TiebaEvidenceRoute] = (),
    parallel_evidence: bool = False,
) -> TiebaResearchProjection:
    error_record = _first_error(records)
    if confirmed:
        status = TiebaResearchStatus.SUCCESS
    elif unconfirmed:
        status = TiebaResearchStatus.LINKS_ONLY
    elif not records or _only_failures(records):
        status = TiebaResearchStatus.ERROR
    else:
        status = TiebaResearchStatus.EMPTY
    boundary = evidence_boundary(
        confirmed=confirmed,
        unconfirmed=unconfirmed,
        rejected=rejected,
        attempts=attempts,
        reads=reads,
        time_filter=time_filter,
    )
    if analysis.needs_official_check:
        boundary.append(OFFICIAL_TRIGGER_NOTE)
    if official_blocked_reason:
        boundary.append(official_blocked_reason)
    if holiday_without_year(analysis):
        boundary.append(HOLIDAY_YEAR_NOTE.format(year=datetime.now(UTC).year))
    if status is TiebaResearchStatus.LINKS_ONLY:
        boundary.append(
            "候选帖的贴吧归属未能确认：只有真的读到帖子页面才会纳入确认结果，"
            "搜索摘要不足以确认归属。"
        )
    # 失败分类只描述本轮的真实落点：已经读到确认帖子的轮次不整体报失败，早先
    # 一次可重试的查询失败留在查询记录里；没有确认帖子时按可重试如实标注。
    ended_in_error = status is TiebaResearchStatus.ERROR
    retryable = bool(
        error_record is not None and error_record.retryable and (ended_in_error or not confirmed)
    )
    surface_error = ended_in_error or retryable
    return TiebaResearchProjection(
        status=status,
        search_scope=list(attempts.scope) if attempts is not None else [],
        topic=topic_of(analysis) or FORUM_NAME,
        original_question=analysis.original_question,
        topic_terms=list(analysis.topic_terms),
        place_or_event=list(analysis.place_or_event),
        question_kind=analysis.question_kind,
        campus_terms=list(analysis.campus_terms),
        query_rounds=list(query_rounds),
        source_priority=list(source_priority),
        parallel_evidence=parallel_evidence,
        plan_rationale=plan_rationale,
        official_blocked_reason=official_blocked_reason,
        time_filter=time_filter,
        queries=list(records),
        confirmed_posts=confirmed,
        candidate_links=unconfirmed,
        rejected_candidates=rejected,
        official_check_requested=analysis.needs_official_check,
        official_checks=official_checks,
        conflicts=list(conflicts or []),
        sections=sections,
        evidence_boundary=boundary,
        empty_reason=(
            _empty_reason(unconfirmed, rejected, attempts.diagnostics)
            if attempts is not None
            and status in {TiebaResearchStatus.LINKS_ONLY, TiebaResearchStatus.EMPTY}
            else None
        ),
        retryable=retryable,
        error_code=(
            error_record.error_code if error_record is not None and surface_error else None
        ),
        error_message=(
            error_record.error_message if error_record is not None and surface_error else None
        ),
    )


def topic_of(analysis: TiebaQuestionAnalysis) -> str:
    return " ".join(analysis.topic_terms)


def evidence_boundary(
    *,
    confirmed: list[TiebaPostProjection],
    unconfirmed: list[TiebaCandidateLink],
    rejected: list[TiebaRejectedCandidate],
    attempts: SearchAttempts | None,
    reads: ReadOutcome | None,
    time_filter: TiebaTimeFilter,
) -> list[str]:
    notes = [
        f"固定贴吧入口：[{TARGET_FORUM_NAME}]({TARGET_FORUM_URL})",
        f"只纳入有证据确认属于「{FORUM_NAME}」的帖子；确认依据是真的读到了帖子页面。",
    ]
    if attempts is not None:
        notes.extend(attempts.rounds)
        notes.extend(_search_scope_notes(attempts.scope))
    if unconfirmed and reads is not None:
        notes.append(
            f"另有 {reads.unconfirmed_total} 条候选帖没有取得页面（其中页面不可读 "
            f"{reads.unreadable} 条、超出读取上限未读取 {reads.not_attempted} 条），"
            f"因此只给出 {len(unconfirmed)} 条帖链：贴吧归属未确认，也未取得回复内容。"
        )
    if rejected:
        note = f"已剔除 {len(rejected)} 条候选（非帖子链接与他吧证据都在剔除依据里逐条留痕）"
        if attempts is not None and attempts.diagnostics.duplicate:
            note += (
                f"；含两轮重复在内的原始命中 {attempts.diagnostics.raw_hits} 条，重复链接只列一次"
            )
        notes.append(f"{note}。")
    if confirmed:
        unread = [post for post in confirmed if not post.replies_obtained]
        if unread:
            notes.append(
                f"其中 {len(unread)} 个帖子确认了归属但没有取得回复内容，未做任何内容推断。"
            )
        notes.append("不承诺完整抓取某帖全部回复，也不绕过登录或访问限制。")
    excluded = _exclusion_reasons(confirmed, time_filter)
    if excluded:
        detail = "、".join(
            f"{EXCLUSION_REASON_LABELS.get(reason, reason)} {count} 条"
            for reason, count in excluded.items()
        )
        notes.append(
            f"范围筛选：已读发言中 {sum(excluded.values())} 条未进入总结（{detail}）；"
            "仅上下文材料不能成为观点依据或引用。"
        )
    if confirmed and time_filter.eligible_count == 0:
        notes.append(
            "本轮已读原文中没有符合话题、校区和时间条件的合格证据；"
            "这只说明本轮未取得合格证据，不代表吧内不存在相关讨论。"
        )
    if attempts is not None and attempts.diagnostics.raw_hits:
        notes.append(
            f"本轮共取得 {attempts.diagnostics.raw_hits} 条原始搜索结果，"
            f"其中可用候选 {attempts.diagnostics.usable} 条："
            "原始命中数不等于确认帖子数。"
        )
    notes.append("总结仅基于实际取得的页面原文生成，引用指向本轮读取的发言；不补写未取得内容。")
    return notes


EXCLUSION_REASON_LABELS = {
    "campus_conflict": "明确其他校区",
    "campus_unconfirmed": "校区未确认",
    "other_topic": "其他话题",
    "topic_mismatch": "话题不相关",
    "out_of_time_range": "时间范围外",
    "unknown_time": "发言时间未知",
}


def _exclusion_reasons(
    confirmed: Sequence[TiebaPostProjection], time_filter: TiebaTimeFilter
) -> dict[str, int]:
    reasons = dict(time_filter.exclusion_reasons)
    for post in confirmed:
        for reason, count in post.scope_excluded_reasons.items():
            reasons[reason] = reasons.get(reason, 0) + count
    return reasons


def _search_scope_notes(scope: Sequence[TiebaSearchScope]) -> list[str]:
    notes: list[str] = []
    for item in scope:
        label = "主题" if item.tab == "thread" else "回复"
        pages = (
            f"第 {item.visited_pages[0]}–{item.visited_pages[-1]} 页"
            if item.visited_pages
            else "未读到页面"
        )
        note = (
            f"搜索页签「{label}」查询「{item.query}」："
            f"已读 {pages}（上限 {item.pages_limit} 页）"
        )
        if item.has_more is True:
            note += "，页面声明还有后续结果"
        elif item.has_more is False:
            note += "，页面声明没有更多结果"
        else:
            note += "，页面未声明是否还有后续结果"
        if item.note:
            note += f"；{item.note}"
        notes.append(note + "。")
    return notes


def _empty_reason(
    unconfirmed: list[TiebaCandidateLink],
    rejected: list[TiebaRejectedCandidate],
    diagnostics: HitDiagnostics,
) -> str:
    if unconfirmed:
        return (
            f"没有取得可确认属于「{FORUM_NAME}」的帖子页面，因此只给出候选帖链，"
            "并明确未取得回复内容。"
        )
    if rejected:
        return (
            f"{_hit_composition(diagnostics)}，没有可确认属于「{FORUM_NAME}」的帖子"
            "（已逐个列出剔除依据）。"
        )
    return f"本轮检索没有返回可确认属于「{FORUM_NAME}」的公开帖子。"


def _hit_composition(diagnostics: HitDiagnostics) -> str:
    """原始命中数的构成说明：命中条数不等于可用候选数，也不等于确认帖数。"""
    parts: list[str] = []
    if diagnostics.not_a_thread:
        parts.append(f"非帖子链接 {diagnostics.not_a_thread} 条")
    if diagnostics.other_forum:
        parts.append(f"带其他贴吧证据 {diagnostics.other_forum} 条")
    if diagnostics.duplicate:
        parts.append(f"重复链接 {diagnostics.duplicate} 条")
    if not parts:
        return f"共取得 {diagnostics.raw_hits} 条原始搜索结果"
    return f"共取得 {diagnostics.raw_hits} 条原始搜索结果（{'、'.join(parts)}）"


def _first_error(records: Sequence[ModuleQueryRecord]) -> ModuleQueryRecord | None:
    for record in records:
        if record.status in FAILED_QUERY_STATUSES:
            return record
    return None


def _only_failures(records: Sequence[ModuleQueryRecord]) -> bool:
    return bool(records) and all(record.status in FAILED_QUERY_STATUSES for record in records)
