"""贴吧取证的确定性规则：检索、读取、官方核验、适用性与冲突裁决。

本模块不调用模型，全部判断都基于真实发生的调用与真实读到的文本；节点
内核只负责编排、门控与恢复。把它单独放在一处，是为了让「什么才算证据」
可以被直接单测，而不是散落在编排代码里。
"""

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
    ReadStatus,
    TiebaCandidateLink,
    TiebaConflict,
    TiebaConflictPostRef,
    TiebaConflictResolution,
    TiebaEvidenceRoute,
    TiebaOfficialApplicability,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaReadResult,
    TiebaRejectedCandidate,
    TiebaResearchProjection,
    TiebaResearchStatus,
    TiebaSearchHit,
    TiebaTimeFilter,
)
from bridges.tieba.lexicon import HOLIDAY_ARRANGEMENT_TERMS, TARGET_FORUM_NAME
from bridges.tieba.official import (
    OFFICIAL_FETCH_LIMIT,
    STATUS_VERIFIED,
    TiebaOfficialReader,
    is_official_url,
    official_fallback_query,
    official_query,
)
from bridges.tieba.presenting import build_sections
from bridges.tieba.reading import TiebaThreadReader
from bridges.tieba.searching import (
    REJECT_NOT_A_THREAD,
    HitDiagnostics,
    SearchOutcome,
    TiebaSearchPort,
    plan_queries,
)

#: 目标贴吧名称由词表单点定义。
FORUM_NAME = TARGET_FORUM_NAME

#: 各阶段的墙钟预算：搜索（允许搜索服务自身 8s 预算与收尾）、读取、官方核验。
SEARCH_DEADLINE_SECONDS = 25.0
READ_DEADLINE_SECONDS = 12.0
OFFICIAL_DEADLINE_SECONDS = 12.0

#: 单轮最多读取的帖子数（每个帖子一次真实公开读取）。
READ_POSTS_LIMIT = 3

#: 帖链降级最多给出的候选链接数。
CANDIDATE_LINKS_LIMIT = 6

#: 记为失败的查询状态（检索成功的空结果不算失败，它有自己的终态）。
FAILED_QUERY_STATUSES: frozenset[ModuleQueryStatus] = frozenset(
    {
        ModuleQueryStatus.ERROR,
        ModuleQueryStatus.TIMEOUT,
        ModuleQueryStatus.CANCELLED,
        ModuleQueryStatus.RATE_LIMITED,
    }
)

#: 逐轮查询的中文标签；有界计划固定为精确词 + 一条备用放宽词（见 ``plan_queries``）。
QUERY_ROUND_LABELS: tuple[str, str] = ("精确词查询", "备用放宽词查询")

#: 归零终止：这些查询状态下不再跑计划内的后续查询。
STOP_QUERY_STATUSES: frozenset[ModuleQueryStatus] = frozenset(
    {ModuleQueryStatus.CANCELLED}
)

#: 读取失败分类的中文短标签（仅帖链的「页面不可读」原因）。
READ_BLOCK_LABELS: dict[ReadStatus, str] = {
    ReadStatus.ACCESS_RESTRICTED: "访问受限，未绕过",
    ReadStatus.UNRECOGNIZED: "页面结构无法解析",
    ReadStatus.NOT_FOUND: "帖子不存在或已删除",
    ReadStatus.TIMEOUT: "读取超时",
    ReadStatus.ERROR: "读取失败",
    ReadStatus.CANCELLED: "已停止读取",
}

#: 官方核验触发时的中文说明。
OFFICIAL_TRIGGER_NOTE = (
    "本轮问题涉及校规／费用／开放时间／办事流程／放假安排，已追加学校官方页面核验。"
)

#: 放假安排未指定年份时的说明：不假定年份，也不把历史通知当作本次安排。
HOLIDAY_YEAR_NOTE = (
    "问题没有指定年份：本轮不假定年份，也不把历史通知或旧帖当作本次安排；"
    "官方页面按原始名词与当前年份 {year} 优先挑选（只用于排序），"
    "并按原文摘录与取得时间呈现。"
)

#: 用户限制只查贴吧时的说明：规定类问题保持「未核实」，不因缺官方页面而猜规定。
TIEBA_ONLY_NOTE = (
    "你明确只查贴吧：本轮未访问学校官方页面，规定相关内容保持未核实，"
    "只呈现吧友的真实经历与帖子原文。"
)

#: 官方页面里的「生效／修订」表述：只有这些明确依据才允许按新规定呈现。
OFFICIAL_SUPERSESSION_MARKERS: tuple[str, ...] = (
    "起执行",
    "施行",
    "生效",
    "修订",
    "新版",
    "最新",
    "调整为",
    "改为",
    "自20",
    "之日起",
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

_YEAR = re.compile(r"(20\d{2})")


@dataclass(frozen=True)
class SearchAttempts:
    """有界检索的合并结果：采用的候选、被剔除的候选、查询记录与逐轮诊断。"""

    hits: tuple[TiebaSearchHit, ...]
    rejected: tuple[TiebaRejectedCandidate, ...]
    records: list[ModuleQueryRecord]
    diagnostics: HitDiagnostics = HitDiagnostics()
    rounds: tuple[str, ...] = ()
    #: 是否因停止／预算／可重试失败而没有跑完计划（供恢复续作）。
    partial: bool = False


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
    partial: bool = False


def search_candidates(
    port: TiebaSearchPort,
    account_id: str,
    analysis: TiebaQuestionAnalysis,
    *,
    stop_event: threading.Event | None,
    deadline_seconds: float,
    resume: dict[str, Any] | None = None,
) -> SearchAttempts:
    """有界检索：先精确词，没有可用候选时再用一条放宽词。

    「检索成功但没有可用候选」与「请求失败」分开判断：前者按既有计划继续
    下一轮查询（这就是放宽词存在的意义），后者只在可重试时继续；取消、
    永久错误与超出搜索预算都在计划内终止，不扩大查询范围。
    ``resume`` 是上一轮的部分产物：已完成的查询与候选直接续用，只补缺口。
    """
    records: list[ModuleQueryRecord] = []
    rejected: list[TiebaRejectedCandidate] = []
    rejected_urls: set[str] = set()
    rounds: list[str] = []
    raw_hits = 0
    usable = 0
    completed: set[str] = set()
    seeded_hits: list[TiebaSearchHit] = []
    if resume is not None:
        for raw in resume.get("queries", []):
            try:
                record = ModuleQueryRecord.model_validate(raw)
            except ValueError:
                continue
            if (
                record.status in FAILED_QUERY_STATUSES
                or record.status is ModuleQueryStatus.SKIPPED
            ):
                continue
            records.append(record)
            completed.add(record.query)
        for raw in resume.get("rejected", []):
            try:
                item = TiebaRejectedCandidate.model_validate(raw)
            except ValueError:
                continue
            if item.url not in rejected_urls:
                rejected_urls.add(item.url)
                rejected.append(item)
        for raw in resume.get("candidates", []):
            try:
                seeded_hits.append(TiebaSearchHit.model_validate(raw))
            except ValueError:
                continue
        rounds.extend(str(note) for note in resume.get("rounds", []))
        raw_hits += int(resume.get("raw_hits") or 0)
    if seeded_hits:
        diagnostics = _deduped_diagnostics(
            rejected, raw_hits=raw_hits, usable=max(usable, len(seeded_hits))
        )
        return SearchAttempts(
            hits=tuple(seeded_hits),
            rejected=tuple(rejected),
            records=records,
            diagnostics=diagnostics,
            rounds=tuple(rounds),
            partial=False,
        )
    partial = False
    deadline = time.monotonic() + deadline_seconds
    for position, query in enumerate(plan_queries(analysis)):
        if query in completed:
            continue
        if position and _budget_exhausted(stop_event, deadline):
            rounds.append(_skipped_round_note(position, stop_event))
            partial = True
            break
        outcome = port.search_public(
            account_id,
            query=query,
            reason=f"{FORUM_NAME}信息搜集：只发送最小公开查询词",
            stop_event=stop_event,
            deadline=deadline,
        )
        records.append(outcome.record)
        raw_hits += outcome.diagnostics.raw_hits
        usable = outcome.diagnostics.usable
        # 去重跨轮生效：同一条链接在两轮里都出现时只留一条剔除记录，多出来的
        # 那次仍算进原始命中数（计入重复链接），正文里不会同一条链接列两遍。
        fresh = [item for item in outcome.rejected if item.url not in rejected_urls]
        rejected_urls.update(item.url for item in fresh)
        rejected.extend(fresh)
        rounds.append(_round_note(position, outcome))
        if outcome.candidates:
            return SearchAttempts(
                hits=outcome.candidates,
                rejected=tuple(rejected),
                records=records,
                diagnostics=_deduped_diagnostics(
                    rejected, raw_hits=raw_hits, usable=usable
                ),
                rounds=tuple(rounds),
            )
        if not _plan_continues(outcome.record):
            partial = outcome.record.retryable and outcome.record.status in FAILED_QUERY_STATUSES
            break
    return SearchAttempts(
        hits=(),
        rejected=tuple(rejected),
        records=records,
        diagnostics=_deduped_diagnostics(rejected, raw_hits=raw_hits, usable=usable),
        rounds=tuple(rounds),
        partial=partial,
    )


def read_candidates(
    reader: TiebaThreadReader,
    attempts: SearchAttempts,
    *,
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
        unreadable += int(resume.get("unreadable") or 0)
    partial = False
    new_attempted: set[str] = set()
    deadline = time.monotonic() + deadline_seconds
    for hit in attempts.hits[:READ_POSTS_LIMIT]:
        if hit.url in attempted:
            prior = prior_links.get(hit.url)
            if prior is not None:
                links.append(prior)
            continue
        if stop_event is not None and stop_event.is_set():
            partial = True
            break
        result = reader.read(hit.url, stop_event=stop_event, deadline=deadline)
        new_attempted.add(hit.url)
        if result.forum_matches_target:
            confirmed.append(_confirmed_post(hit, result))
            continue
        if result.forum_name and result.status in {ReadStatus.READ, ReadStatus.PARTIAL}:
            rejected.append(
                TiebaRejectedCandidate(
                    url=result.url,
                    title=result.title or hit.title,
                    evidence=(
                        f"已读取帖子页面，页面声明所属贴吧为「{result.forum_name}」，"
                        "不是目标贴吧"
                    ),
                )
            )
            continue
        unreadable += 1
        links.append(_unconfirmed_link(hit, _read_block_reason(result)))
    not_attempted = 0
    for hit in attempts.hits[READ_POSTS_LIMIT:]:
        if hit.url in attempted or hit.url in new_attempted:
            prior = prior_links.get(hit.url)
            if prior is not None:
                links.append(prior)
            continue
        not_attempted += 1
        links.append(_unconfirmed_link(hit, "未读取：超出本轮读取条数上限"))
    return ReadOutcome(
        confirmed=confirmed,
        rejected=rejected,
        unconfirmed=links[:CANDIDATE_LINKS_LIMIT],
        unreadable=unreadable,
        not_attempted=not_attempted,
        unconfirmed_total=len(links),
        attempted_urls=tuple(sorted(attempted | new_attempted)),
        partial=partial,
    )


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
) -> list[TiebaOfficialCheck]:
    """官网核验：先按官方域名检索，未命中再用一条去域名限定的查询。

    ``blocked`` 为用户硬条件（只查贴吧）阻止官方路径：不做任何外部调用。
    ``resume`` 是上一轮的部分核验产物：已取得的页面按原结果续用。
    """
    if blocked or reader is None:
        return []
    checks: list[TiebaOfficialCheck] = []
    fetched_urls: set[str] = set()
    if resume is not None:
        for raw in resume.get("checks", []):
            try:
                check = TiebaOfficialCheck.model_validate(raw)
            except ValueError:
                continue
            checks.append(check)
            fetched_urls.add(check.url)
    deadline = time.monotonic() + deadline_seconds
    terms = official_terms(analysis)
    # 问题自带年份时按用户年份挑页面，否则按当前年份（只影响挑选顺序）。
    preferred_year = analysis.time_year or datetime.now(UTC).year
    candidates: list[str] = []
    for query, reason in (
        (official_query(analysis), "校规／费用／开放时间／流程／放假安排核对学校官方页面"),
        (official_fallback_query(analysis), "官方域名未命中，改用校名与主题再找官方页面"),
    ):
        outcome = port.search_public(
            account_id,
            query=query,
            reason=reason,
            stop_event=stop_event,
            deadline=deadline,
        )
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
            check.model_copy(
                update={"applicability": assess_applicability(check, analysis)}
            )
        )
    return checks


def assess_applicability(
    check: TiebaOfficialCheck, analysis: TiebaQuestionAnalysis
) -> TiebaOfficialApplicability:
    """核对官方页面与问题的主体／校区／用途／日期适用性（域名不自动放行）。"""
    text = f"{check.title}\n{check.excerpt or ''}"
    matched = set(check.matched_terms)
    subject_confirmed = check.status == STATUS_VERIFIED and bool(matched)
    campus = analysis.campus_terms[0] if analysis.campus_terms else None
    campus_confirmed: bool | None = None
    if campus:
        campus_confirmed = campus in text
    purpose_terms = [*analysis.topic_terms, *analysis.official_topics][:4]
    purpose_confirmed = bool(matched)
    date_requirement = analysis.time_requirement
    date_confirmed: bool | None = None
    if date_requirement:
        date_confirmed = (
            str(analysis.time_year) in text
            if analysis.time_year is not None
            else None
        )
    applicable = bool(
        subject_confirmed
        and purpose_confirmed
        and campus_confirmed is not False
        and date_confirmed is not False
    )
    parts: list[str] = []
    parts.append(
        "已在官方页面定位到与问题相关的段落。"
        if subject_confirmed
        else "尚未在官方页面定位到与问题相关的段落。"
    )
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
            parts.append(f"页面未出现你提到的年份 {analysis.time_year}，时效性未确认。")
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
        official_year = _year_of(check.title) or _year_of(excerpt)
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
                if not any(marker in content for marker in REPLY_NEGATION_MARKERS):
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
            for year in (_year_of(ref.posted_at or "") for ref in entry["refs"])
            if year is not None
        ]
        official_year = entry["official_year"]
        has_supersession = any(
            marker in excerpt for marker in OFFICIAL_SUPERSESSION_MARKERS
        )
        newer = has_supersession and (
            not reply_years
            or (official_year is not None and official_year >= max(reply_years))
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
                f"官方页面含生效／修订表述，日期线索 {official_year or '未标注'}，"
                f"帖子楼层时间为 {_years_text(reply_years)}：按新规定呈现，"
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
                f"帖子楼层时间为 {_years_text(reply_years)}：无法确认官方页面已替代帖中说法。"
            )
            note = (
                "官方规定与吧友经历不一致：已按时间与适用范围核对并双方分列，"
                "不替任何一方下结论。"
            )
            resolution = TiebaConflictResolution.KEPT_BOTH
        conflicts.append(
            TiebaConflict(
                topic_terms=list(entry["shared"]),
                official_title=official_check.title,
                official_url=official_check.url,
                official_date=str(official_year) if official_year is not None else None,
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


def summarize(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> tuple[TiebaTimeFilter, list[TiebaPostProjection], list[str]]:
    """按时间条件收敛已读楼层，并且只从收敛后的真实楼层里分段。"""
    time_filter, filtered = apply_time_condition(analysis, posts)
    return time_filter, filtered, build_sections(filtered)


def apply_time_condition(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> tuple[TiebaTimeFilter, list[TiebaPostProjection]]:
    """有时间条件且读到了带时间的楼层时，按条件过滤楼层（如实记录结果）。"""
    year = analysis.time_year
    if not analysis.time_requirement or year is None:
        return time_filter_state(analysis, posts), posts
    total = sum(len(post.replies) for post in posts)
    filtered: list[TiebaPostProjection] = []
    kept = 0
    for post in posts:
        replies = [
            reply
            for reply in post.replies
            if not reply.posted_at or str(year) in reply.posted_at
        ]
        kept += len(replies)
        filtered.append(
            post.model_copy(
                update={
                    "replies": replies,
                    "replies_obtained": bool(replies),
                    "read_error_message": post.read_error_message
                    if replies
                    else f"时间条件「{analysis.time_requirement}」内没有读到回复。",
                }
            )
        )
    state = TiebaTimeFilter(
        requirement=analysis.time_requirement,
        year=year,
        applied=True,
        note=(
            f"已按时间条件「{analysis.time_requirement}」核对楼层发帖时间："
            f"保留 {kept} 条、剔除 {total - kept} 条。"
        ),
    )
    return state, filtered


def time_filter_state(
    analysis: TiebaQuestionAnalysis, posts: list[TiebaPostProjection]
) -> TiebaTimeFilter:
    """时间条件的执行状态：有没有可核对的时间、能不能真的过滤。"""
    if not analysis.time_requirement:
        return TiebaTimeFilter(
            requirement=None,
            year=None,
            applied=False,
            note="本轮问题没有提出时间条件。",
        )
    has_times = any(reply.posted_at for post in posts for reply in post.replies)
    if not has_times:
        return TiebaTimeFilter(
            requirement=analysis.time_requirement,
            year=analysis.time_year,
            applied=False,
            note=(
                f"保留了你提出的时间条件「{analysis.time_requirement}」；"
                "本轮没有读到带发帖时间的楼层，因此没有按时间过滤。"
            ),
        )
    return TiebaTimeFilter(
        requirement=analysis.time_requirement,
        year=analysis.time_year,
        applied=False,
        note=f"已取得带时间的楼层，可核对你提出的时间条件「{analysis.time_requirement}」。",
    )


def official_terms(analysis: TiebaQuestionAnalysis) -> tuple[str, ...]:
    """官方候选排序用的原始名词（与官方查询词同源，不额外发明词）。"""
    terms = list(analysis.topic_terms)
    terms.extend(topic for topic in analysis.official_topics if topic not in terms)
    return tuple(terms)


def official_candidates(
    outcome: SearchOutcome, terms: Sequence[str], *, preferred_year: int
) -> list[str]:
    """官方页面按「点题且提到目标年份」优先排序，只作证据挑选顺序。

    真实取证里「放假」类查询的第一条官方命中可能是几年前的活动报道，排序
    把标题同时含原始名词与目标年份的通知类页面提到前面，避免旧帖顶在首位；
    目标年份取用户问题里的年份，问题没写年份时取当前年份。排序结果不影响
    任何结论，年份也不会写进用户问题或官方结论。
    """

    def rank(hit: TiebaSearchHit) -> int:
        title = hit.title or ""
        mentions_topic = any(term and term in title for term in terms)
        if mentions_topic and str(preferred_year) in title:
            return 0
        return 1 if mentions_topic else 2

    candidates = [hit for hit in outcome.hits if is_official_url(hit.url)]
    ordered = sorted(candidates, key=rank)  # 稳定排序：同档保持检索顺序
    seen: set[str] = set()
    urls: list[str] = []
    for hit in ordered:
        if hit.url in seen:
            continue
        seen.add(hit.url)
        urls.append(hit.url)
    return urls


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
    )
    if analysis.needs_official_check:
        boundary.append(OFFICIAL_TRIGGER_NOTE)
    if official_blocked_reason:
        boundary.append(official_blocked_reason)
    if _holiday_without_year(analysis):
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
        error_record is not None
        and error_record.retryable
        and (ended_in_error or not confirmed)
    )
    surface_error = ended_in_error or retryable
    return TiebaResearchProjection(
        status=status,
        topic=topic_of(analysis) or FORUM_NAME,
        original_question=analysis.original_question,
        topic_terms=list(analysis.topic_terms),
        place_or_event=list(analysis.place_or_event),
        question_kind=analysis.question_kind,
        campus_terms=list(analysis.campus_terms),
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
            error_record.error_message
            if error_record is not None and surface_error
            else None
        ),
    )


def topic_of(analysis: TiebaQuestionAnalysis) -> str:
    return " ".join(analysis.topic_terms)


def confirmed_post(hit: TiebaSearchHit, result: TiebaReadResult) -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id=result.thread_id,
        url=result.url,
        title=result.title or hit.title,
        affiliation_evidence=f"已读取帖子页面，页面声明所属贴吧为「{result.forum_name}」",
        read_status=result.status,
        pages_read=result.pages_read,
        pages_limit=result.pages_limit,
        total_pages=result.total_pages,
        floor_min=result.floor_min,
        floor_max=result.floor_max,
        replies_obtained=bool(result.replies),
        replies=list(result.replies),
        read_error_code=result.error_code,
        read_error_message=result.error_message,
        retrieved_at=result.retrieved_at,
    )


def evidence_boundary(
    *,
    confirmed: list[TiebaPostProjection],
    unconfirmed: list[TiebaCandidateLink],
    rejected: list[TiebaRejectedCandidate],
    attempts: SearchAttempts | None,
    reads: ReadOutcome | None,
) -> list[str]:
    notes = [
        f"只纳入有证据确认属于「{FORUM_NAME}」的帖子；确认依据是真的读到了帖子页面。",
    ]
    if attempts is not None:
        notes.extend(attempts.rounds)
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
                f"；含两轮重复在内的原始命中 {attempts.diagnostics.raw_hits} 条，"
                "重复链接只列一次"
            )
        notes.append(f"{note}。")
    if confirmed:
        unread = [post for post in confirmed if not post.replies_obtained]
        if unread:
            notes.append(
                f"其中 {len(unread)} 个帖子确认了归属但没有取得回复内容，未做任何内容推断。"
            )
        notes.append("不承诺完整抓取某帖全部回复，也不绕过登录或访问限制。")
    if attempts is not None and attempts.diagnostics.raw_hits:
        notes.append(
            f"本轮共取得 {attempts.diagnostics.raw_hits} 条原始搜索结果，"
            f"其中可用候选 {attempts.diagnostics.usable} 条："
            "原始命中数不等于确认帖子数。"
        )
    notes.append("本模块不调用模型生成内容，正文与引文都来自实际取得的页面文本。")
    return notes


def _confirmed_post(hit: TiebaSearchHit, result: TiebaReadResult) -> TiebaPostProjection:
    return confirmed_post(hit, result)


def _unconfirmed_link(hit: TiebaSearchHit, reason: str) -> TiebaCandidateLink:
    """仅帖链降级：来源标识与「为什么只有帖链」分开记，不把原因塞进来源字段。"""
    return TiebaCandidateLink(
        url=hit.url,
        title=hit.title,
        source="tavily",
        unconfirmed_reason=reason,
    )


def _read_block_reason(result: TiebaReadResult) -> str:
    """读取失败或页面未声明吧名时的中文短因（页面不可读一类）。"""
    if result.forum_name is None and result.status in {ReadStatus.READ, ReadStatus.PARTIAL}:
        return "页面不可读：页面未声明所属贴吧"
    label = READ_BLOCK_LABELS.get(result.status, result.status.value)
    return f"页面不可读：{label}"


def _budget_exhausted(
    stop_event: threading.Event | None, deadline: float
) -> bool:
    """计划内是否还有继续下一轮查询的余地（停止与预算都在此判定）。"""
    if stop_event is not None and stop_event.is_set():
        return True
    return time.monotonic() >= deadline


def _deduped_diagnostics(
    rejected: Sequence[TiebaRejectedCandidate], *, raw_hits: int, usable: int
) -> HitDiagnostics:
    """剔除记录跨轮去重后的计数：多出来的命中归入重复链接。

    各计数之和始终等于原始命中数（``duplicate`` 是去重后的差额，含轮内重复与
    跨轮重复），因此正文里的「原始命中构成」与剔除记录条目数能对上。
    """
    not_a_thread = sum(1 for item in rejected if item.evidence == REJECT_NOT_A_THREAD)
    other_forum = len(rejected) - not_a_thread
    return HitDiagnostics(
        raw_hits=raw_hits,
        not_a_thread=not_a_thread,
        other_forum=other_forum,
        duplicate=raw_hits - not_a_thread - other_forum - usable,
        usable=usable,
    )


def _plan_continues(record: ModuleQueryRecord) -> bool:
    """这一轮查询之后是否继续计划内的下一轮。

    检索本身完成（成功／零结果）但没给出可用候选时继续——这正是备用放宽词
    存在的原因；查询失败只在可重试时继续，取消与永久错误都在此终止。
    """
    if record.status in STOP_QUERY_STATUSES:
        return False
    if record.status in FAILED_QUERY_STATUSES:
        return record.retryable
    return True


def _round_note(position: int, outcome: SearchOutcome) -> str:
    """逐轮诊断：查询词、原始结果数、可用候选数与剔除数（脱敏，只记计数）。"""
    label = QUERY_ROUND_LABELS[position]
    if outcome.record.status in FAILED_QUERY_STATUSES:
        reason = outcome.record.error_message or outcome.record.status.value
        return (
            f"{label}「{outcome.record.query}」未完成（{reason}），"
            f"取得 {len(outcome.hits)} 条原始结果。"
        )
    return (
        f"{label}「{outcome.record.query}」取得 {len(outcome.hits)} 条原始结果："
        f"可用候选 {len(outcome.candidates)} 条，"
        f"剔除 {len(outcome.rejected)} 条（其中非帖子链接 {outcome.diagnostics.not_a_thread} 条、"
        f"他吧证据 {outcome.diagnostics.other_forum} 条）。"
    )


def _skipped_round_note(position: int, stop_event: threading.Event | None) -> str:
    """备用查询没有执行时的原因：停止还是预算耗尽。"""
    label = QUERY_ROUND_LABELS[position]
    if stop_event is not None and stop_event.is_set():
        return f"{label}因你已停止而未执行。"
    return f"{label}因超出本轮检索预算而未执行。"


def _holiday_without_year(analysis: TiebaQuestionAnalysis) -> bool:
    """放假安排类问题没有指定年份：必须写明本轮不假定年份。"""
    if analysis.time_year is not None:
        return False
    return bool(set(analysis.topic_terms) & HOLIDAY_ARRANGEMENT_TERMS)


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
    return bool(records) and all(
        record.status in FAILED_QUERY_STATUSES for record in records
    )


def _conflict_terms(analysis: TiebaQuestionAnalysis) -> list[str]:
    terms = list(analysis.topic_terms)
    terms.extend(topic for topic in analysis.official_topics if topic not in terms)
    return terms


def _same_ref(refs: Sequence[TiebaConflictPostRef], ref: TiebaConflictPostRef) -> bool:
    return any(
        item.url == ref.url and item.floor == ref.floor and item.quote == ref.quote
        for item in refs
    )


def _clip(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:120] + ("…" if len(collapsed) > 120 else "")


def _year_of(text: str) -> int | None:
    match = _YEAR.search(text)
    return int(match.group(1)) if match is not None else None


def _years_text(years: Sequence[int]) -> str:
    return "、".join(str(year) for year in sorted(set(years))) if years else "未标注"
