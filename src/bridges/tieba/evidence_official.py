"""学校官方证据的发现、适用性与冲突核验。"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.tieba.contracts import (
    TiebaConflict,
    TiebaConflictPostRef,
    TiebaConflictResolution,
    TiebaOfficialApplicability,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaSearchHit,
)
from bridges.tieba.evidence_search import (
    AUTH_ERROR_CODES,
    BUDGET_ERROR_CODES,
    FAILED_QUERY_STATUSES,
    budget_exhausted,
)
from bridges.tieba.evidence_time_rules import (
    DATE_PATTERN,
    expired_notice,
    holiday_without_year,
    matches_requested_date,
    year_of,
    years_text,
)
from bridges.tieba.lexicon import (
    HOLIDAY_ARRANGEMENT_TERMS,
)
from bridges.tieba.official import (
    OFFICIAL_FETCH_LIMIT,
    STATUS_VERIFIED,
    TiebaOfficialReader,
    is_official_url,
    official_fallback_query,
    official_query,
)
from bridges.tieba.searching import (
    SearchOutcome,
    TiebaSearchPort,
)

OFFICIAL_DEADLINE_SECONDS = 12.0


#: 官方核验触发时的中文说明。
OFFICIAL_TRIGGER_NOTE = (
    "本轮问题涉及校规／费用／开放时间／办事流程／放假安排，已追加学校官方页面核验。"
)


#: 用户限制只查贴吧时的说明：规定类问题保持「未核实」，不因缺官方页面而猜规定。
TIEBA_ONLY_NOTE = (
    "你明确只查贴吧：本轮未访问学校官方页面，规定相关内容保持未核实，"
    "只呈现吧友的真实经历与帖子原文。"
)


#: 官方页面里的「生效／修订」表述：只有这些明确依据才允许按新规定呈现。
OFFICIAL_SUPERSESSION_MARKERS: tuple[str, ...] = (
    "调整为",
    "改为",
    "替代",
    "代替",
    "废止",
)


#: 回复里的相反说法标记：与官方摘录共享名词且出现这些词时才构成冲突候选。
REPLY_NEGATION_MARKERS: tuple[str, ...] = (
    "不是",
    "没有",
    "不用",
    "取消",
    "改了",
    "不再",
    "废了",
    "早就不",
    "没这回事",
)

_EFFECTIVE_DATE = re.compile(
    r"(?:自|从)((20\d{2})年(\d{1,2})月(\d{1,2})日)(?:起|之日起)(?:执行|施行|生效)"
)

_POLICY_PURPOSE = re.compile(r"规定|办法|通知|申请|审批|办理|缴费|收费|开放|放假|实施|执行|施行")


@dataclass(frozen=True)
class OfficialOutcome:
    """官方路径的页面和发现记录；恢复只补未完成调用。"""

    checks: list[TiebaOfficialCheck]
    records: list[ModuleQueryRecord]
    candidates: list[str]
    partial: bool = False
    closeout_reason: str = "completed"


def verify_official(
    port: TiebaSearchPort,
    reader: TiebaOfficialReader | None,
    account_id: str,
    analysis: TiebaQuestionAnalysis,
    *,
    stop_event: threading.Event | None,
    deadline_seconds: float,
    blocked: bool = False,
    resume: dict[str, Any] | None = None,
) -> OfficialOutcome:
    """官网核验：两轮均按官方域名检索，未命中时只减少主题条件。

    ``blocked`` 为用户硬条件（只查贴吧）阻止官方路径：不做任何外部调用。
    ``resume`` 是上一轮的部分核验产物：已取得的页面按原结果续用。
    """
    if blocked or reader is None:
        return OfficialOutcome([], [], [], closeout_reason="skipped")
    checks: list[TiebaOfficialCheck] = []
    records: list[ModuleQueryRecord] = []
    candidates: list[str] = []
    completed: set[str] = set()
    fetched_urls: set[str] = set()
    if resume is not None:
        for raw in resume.get("checks", []):
            try:
                check = TiebaOfficialCheck.model_validate(raw)
            except ValueError:
                continue
            checks.append(check)
            fetched_urls.add(check.url)
        for raw in resume.get("records", []):
            record = ModuleQueryRecord.model_validate(raw)
            if (
                record.status not in FAILED_QUERY_STATUSES
                and record.status is not ModuleQueryStatus.SKIPPED
            ):
                records.append(record)
                completed.add(record.query)
        candidates = list(resume.get("candidates", []))
    deadline = time.monotonic() + deadline_seconds
    terms = official_terms(analysis)
    # 问题自带年份时按用户年份挑页面，否则按当前年份（只影响挑选顺序）。
    preferred_year = analysis.time_year or datetime.now(UTC).year
    for query, reason in (
        (official_query(analysis), "校规／费用／开放时间／流程／放假安排核对学校官方页面"),
        (official_fallback_query(analysis), "官方域名未命中，改用校名与主题再找官方页面"),
    ):
        if candidates:
            break
        if query in completed:
            continue
        if budget_exhausted(stop_event, deadline):
            return OfficialOutcome(
                checks,
                records,
                candidates,
                partial=True,
                closeout_reason=(
                    "stopped"
                    if stop_event is not None and stop_event.is_set()
                    else "deadline"
                ),
            )
        outcome = port.search_public(
            account_id,
            query=query,
            reason=reason,
            stop_event=stop_event,
            deadline=deadline,
        )
        records.append(outcome.record)
        candidates = official_candidates(outcome, terms, preferred_year=preferred_year)
        if candidates:
            break
    for url in candidates[:OFFICIAL_FETCH_LIMIT]:
        if url in fetched_urls:
            continue
        if stop_event is not None and stop_event.is_set():
            break
        check = reader.fetch(
            url,
            terms=tuple(analysis.topic_terms),
            stop_event=stop_event,
            deadline=deadline,
        )
        checks.append(
            check.model_copy(update={"applicability": assess_applicability(check, analysis)})
        )
    partial = bool(
        budget_exhausted(stop_event, deadline)
        or any(record.retryable for record in records if record.status in FAILED_QUERY_STATUSES)
    )
    failed = next(
        (
            record
            for record in reversed(records)
            if record.status in FAILED_QUERY_STATUSES
        ),
        None,
    )
    closeout_reason = "completed"
    if stop_event is not None and stop_event.is_set():
        closeout_reason = "stopped"
    elif failed is not None and failed.error_code in AUTH_ERROR_CODES:
        closeout_reason = "auth_required"
    elif failed is not None and failed.error_code in BUDGET_ERROR_CODES:
        closeout_reason = "budget_exhausted"
    elif failed is not None and failed.retryable:
        closeout_reason = "transient_error"
    elif failed is not None:
        closeout_reason = "permanent_error"
    return OfficialOutcome(
        checks, records, candidates, partial=partial, closeout_reason=closeout_reason
    )


def assess_applicability(
    check: TiebaOfficialCheck, analysis: TiebaQuestionAnalysis
) -> TiebaOfficialApplicability:
    """核对官方页面与问题的主体／校区／用途／日期适用性（域名不自动放行）。"""
    text = f"{check.title}\n{check.excerpt or ''}"
    matched = set(check.matched_terms)
    # 域名只是候选边界；页面还须说明本校或主管部门，转载他校材料不作本校规定。
    named_schools = re.findall(r"[\u4e00-\u9fff]{2,12}大学", text)
    foreign_subject = any("华东交通大学" not in school for school in named_schools)
    subject_confirmed = bool(
        check.status == STATUS_VERIFIED
        and is_official_url(check.url)
        and not foreign_subject
        and any(
            name in text
            for name in ("华东交通大学", "华东交大", "教务处", "学生工作处", "后勤", "图书馆")
        )
    )
    campus = analysis.campus_terms[0] if analysis.campus_terms else None
    campus_confirmed: bool | None = None
    if campus:
        campus_confirmed = all(named in text for named in analysis.campus_terms)
    purpose_terms = [*analysis.topic_terms, *analysis.official_topics][:4]
    purpose_confirmed = bool(
        check.excerpt
        and any(term in check.excerpt for term in purpose_terms if term in matched)
        and _POLICY_PURPOSE.search(check.excerpt)
        and all(
            audience in text
            for audience in ("本科生", "研究生", "新生", "毕业生")
            if audience in analysis.original_question
        )
    )
    date_requirement = analysis.time_requirement
    notice_year = year_of(check.title) or year_of(check.excerpt or "")
    historical_holiday = bool(
        holiday_without_year(analysis)
        and notice_year is not None
        and notice_year != check.fetched_at.year
    )
    date_confirmed: bool | None = None
    if date_requirement:
        date_confirmed = (
            matches_requested_date(date_requirement, text, analysis.time_year)
            if analysis.time_year is not None
            else None
        )
    applicable = bool(
        subject_confirmed
        and purpose_confirmed
        and campus_confirmed is not False
        and (not date_requirement or date_confirmed is True)
        and not expired_notice(text, analysis, check.fetched_at)
        and not historical_holiday
    )
    parts: list[str] = []
    parts.append(
        "页面说明了本校或主管部门主体。"
        if subject_confirmed
        else "页面主体未确认，不能仅凭官方域名判定适用。"
    )
    if expired_notice(text, analysis, check.fetched_at):
        parts.append("页面明确受理期限已过，不能作为当前适用规定。")
    if historical_holiday:
        parts.append(f"这是 {notice_year} 年的历史假期通知，不能作为当前安排。")
    parts.append(
        "已定位到相关规定用途。"
        if purpose_confirmed
        else "未定位到相关规定用途，不能把新闻或经历当作规定。"
    )
    date_basis = _date_basis_note(check, text)
    if date_basis:
        parts.append(date_basis)
    if campus:
        parts.append(
            f"页面点名了「{campus}」。"
            if campus_confirmed
            else f"页面未点名「{campus}」，该校区适用性未确认。"
        )
    if date_requirement:
        if date_confirmed is True:
            parts.append(f"页面出现你提到的年份 {analysis.time_year}。")
        elif date_confirmed is False:
            parts.append(f"页面日期未对应时间条件「{date_requirement}」，时效性未确认。")
        else:
            parts.append(f"时间条件「{date_requirement}」为相对说法，页面无法直接核对。")
    return TiebaOfficialApplicability(
        subject=f"{check.host}（学校官方域名页面）",
        subject_confirmed=subject_confirmed,
        campus=campus,
        campus_confirmed=campus_confirmed,
        purpose_terms=purpose_terms,
        purpose_confirmed=purpose_confirmed,
        date_requirement=date_requirement,
        date_confirmed=date_confirmed,
        applicable=applicable,
        note="".join(parts) if parts else "未核对到可确认的适用性依据。",
    )


def _date_basis_note(check: TiebaOfficialCheck, text: str) -> str:
    """把页面的发布日期／生效范围写进适用性说明；没有时如实说明。"""
    if check.status != STATUS_VERIFIED:
        return ""
    effective = _EFFECTIVE_DATE.search(text)
    if effective:
        return f"页面注明自 {effective.group(1)} 起执行（生效依据）。"
    absolute = DATE_PATTERN.search(text)
    if absolute:
        return f"页面日期线索为 {absolute.group(0)}，未找到更明确的生效表述。"
    year = year_of(text)
    if year is not None:
        return f"页面日期线索为 {year} 年（仅年份），未找到更明确的生效日期。"
    return "页面未标注发布日期或生效范围，已按已取得原文核对。"


def detect_conflicts(
    analysis: TiebaQuestionAnalysis,
    checks: Sequence[TiebaOfficialCheck],
    posts: Sequence[TiebaPostProjection],
) -> list[TiebaConflict]:
    """官方摘录与真实回复共享名词且说法相反时，按时间／范围核对并分列。"""
    grouped: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for check in checks:
        if check.status != STATUS_VERIFIED or not check.excerpt:
            continue
        excerpt = check.excerpt
        official_year = year_of(check.title) or year_of(excerpt)
        for post in posts:
            for reply in post.replies:
                content = reply.content or ""
                shared = tuple(
                    term
                    for term in _conflict_terms(analysis)
                    if term in excerpt and term in content
                )
                if not shared:
                    continue
                if content.strip().rstrip("。！! ") == excerpt.strip().rstrip("。！! "):
                    continue
                if not any(
                    marker in content or marker in excerpt for marker in REPLY_NEGATION_MARKERS
                ):
                    continue
                key = (check.url, shared)
                entry = grouped.setdefault(
                    key,
                    {
                        "check": check,
                        "shared": shared,
                        "official_year": official_year,
                        "refs": [],
                    },
                )
                ref = TiebaConflictPostRef(
                    url=post.url,
                    title=post.title,
                    floor=reply.floor,
                    posted_at=reply.posted_at,
                    quote=_clip(reply.content),
                )
                if len(entry["refs"]) < 3 and not _same_ref(entry["refs"], ref):
                    entry["refs"].append(ref)
    conflicts: list[TiebaConflict] = []
    for entry in grouped.values():
        official_check: TiebaOfficialCheck = entry["check"]
        excerpt = official_check.excerpt or ""
        reply_years = [
            year
            for year in (year_of(ref.posted_at or "") for ref in entry["refs"])
            if year is not None
        ]
        official_year = entry["official_year"]
        has_supersession = any(marker in excerpt for marker in OFFICIAL_SUPERSESSION_MARKERS)
        effective = _EFFECTIVE_DATE.search(excerpt)
        effective_date = (
            tuple(int(effective.group(index)) for index in (2, 3, 4)) if effective else None
        )
        post_dates = [DATE_PATTERN.search(ref.posted_at or "") for ref in entry["refs"]]
        scope = assess_applicability(official_check, analysis)
        newer = bool(
            has_supersession
            and effective_date
            and scope.applicable
            and post_dates
            and all(
                date is not None
                and effective_date > tuple(int(date.group(index)) for index in (1, 2, 3))
                for date in post_dates
            )
        )
        campus_note = ""
        if analysis.campus_terms:
            named = analysis.campus_terms[0]
            campus_note = (
                f"页面与问题都涉及「{named}」，适用范围一致。"
                if named in f"{official_check.title}\n{excerpt}"
                else f"页面未点名问题中的「{named}」，适用范围未确认。"
            )
        else:
            campus_note = "问题未点名校区，按页面整体规定核对。"
        if newer:
            time_basis = (
                "官方页面明确调整或替代原规定，"
                f"自 {effective.group(1) if effective else '未标注'} 生效，"
                f"帖子楼层时间为 {years_text(reply_years)}：按新规定呈现，"
                "同时保留帖子经历。"
            )
            note = (
                "官方规定与吧友经历不一致：官方页面有更晚的生效依据，"
                "按新规定呈现，原始经历一并保留。"
            )
            resolution = TiebaConflictResolution.OFFICIAL_NEWER
        else:
            time_basis = (
                f"官方页面日期线索 {official_year or '未标注'}，"
                f"帖子楼层时间为 {years_text(reply_years)}：无法确认官方页面已替代帖中说法。"
            )
            note = (
                "官方规定与吧友经历不一致：已按时间与适用范围核对并双方分列，不替任何一方下结论。"
            )
            resolution = TiebaConflictResolution.KEPT_BOTH
        conflicts.append(
            TiebaConflict(
                topic_terms=list(entry["shared"]),
                official_title=official_check.title,
                official_url=official_check.url,
                official_date=effective.group(1)
                if effective
                else (str(official_year) if official_year is not None else None),
                official_statement=excerpt,
                post_refs=list(entry["refs"]),
                time_basis=time_basis,
                scope_basis=campus_note,
                resolution=resolution,
                note=note,
            )
        )
    conflicts.sort(key=lambda item: (item.official_url, tuple(item.topic_terms)))
    return conflicts


def official_terms(analysis: TiebaQuestionAnalysis) -> tuple[str, ...]:
    """官方候选排序用的原始名词（与官方查询词同源，不额外发明词）。"""
    terms = list(analysis.topic_terms)
    terms.extend(topic for topic in analysis.official_topics if topic not in terms)
    return tuple(terms)


def official_candidates(
    outcome: SearchOutcome, terms: Sequence[str], *, preferred_year: int
) -> list[str]:
    """官方页面按「点题且提到目标年份」优先挑选。

    真实取证里「放假」类查询的第一条官方命中可能是几年前的活动报道，排序
    把标题同时含原始名词与目标年份的通知类页面提到前面，避免旧帖顶在首位；
    目标年份取用户问题里的年份，问题没写年份时取当前年份。已取得目标年份
    假期通知时不混入其他已注明年份的通知；不把选择年份写进用户问题。
    """

    def rank(hit: TiebaSearchHit) -> int:
        title = hit.title or ""
        mentions_topic = any(term and term in title for term in terms)
        if mentions_topic and str(preferred_year) in title:
            return 0
        return 1 if mentions_topic else 2

    candidates = [hit for hit in outcome.hits if is_official_url(hit.url)]
    ordered = sorted(candidates, key=rank)  # 稳定排序：同档保持检索顺序
    if set(terms) & HOLIDAY_ARRANGEMENT_TERMS and any(rank(hit) == 0 for hit in ordered):
        ordered = [hit for hit in ordered if year_of(hit.title) in {None, preferred_year}]
    seen: set[str] = set()
    urls: list[str] = []
    for hit in ordered:
        if hit.url in seen:
            continue
        seen.add(hit.url)
        urls.append(hit.url)
    return urls


def _conflict_terms(analysis: TiebaQuestionAnalysis) -> list[str]:
    terms = list(analysis.topic_terms)
    terms.extend(topic for topic in analysis.official_topics if topic not in terms)
    return terms


def _same_ref(refs: Sequence[TiebaConflictPostRef], ref: TiebaConflictPostRef) -> bool:
    return any(
        item.url == ref.url and item.floor == ref.floor and item.quote == ref.quote for item in refs
    )


def _clip(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:120] + ("…" if len(collapsed) > 120 else "")
