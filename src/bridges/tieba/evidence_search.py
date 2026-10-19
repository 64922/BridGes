"""贴吧搜索候选与逐轮检索记录。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.tieba.contracts import (
    TiebaQuestionAnalysis,
    TiebaRejectedCandidate,
    TiebaSearchHit,
    TiebaSearchScope,
)
from bridges.tieba.lexicon import (
    TARGET_FORUM_NAME,
)
from bridges.tieba.search_merge import merge_search_hit
from bridges.tieba.searching import (
    REJECT_NOT_A_THREAD,
    HitDiagnostics,
    SearchOutcome,
    TiebaSearchPort,
    plan_query_rounds,
)

#: 目标贴吧名称由词表单点定义。
FORUM_NAME = TARGET_FORUM_NAME


#: 各阶段的墙钟预算：搜索（允许搜索服务自身 8s 预算与收尾）、读取、官方核验。
SEARCH_DEADLINE_SECONDS = 50.0


#: 记为失败的查询状态（检索成功的空结果不算失败，它有自己的终态）。
FAILED_QUERY_STATUSES: frozenset[ModuleQueryStatus] = frozenset(
    {
        ModuleQueryStatus.ERROR,
        ModuleQueryStatus.TIMEOUT,
        ModuleQueryStatus.CANCELLED,
        ModuleQueryStatus.RATE_LIMITED,
    }
)


#: 归零终止：这些查询状态下不再跑计划内的后续查询。
STOP_QUERY_STATUSES: frozenset[ModuleQueryStatus] = frozenset(
    {ModuleQueryStatus.CANCELLED, ModuleQueryStatus.RATE_LIMITED}
)

AUTH_ERROR_CODES: frozenset[str] = frozenset(
    {
        "tieba_browser_auth_required",
        "tieba_browser_auth_interrupted",
        "tieba_browser_auth_unverified",
        "tieba_browser_auth_status_unknown",
    }
)

BUDGET_ERROR_CODES: frozenset[str] = frozenset(
    {
        "run_budget_exhausted",
        "tieba_browser_work_time_exhausted",
        "tieba_browser_request_queue_full",
    }
)


@dataclass(frozen=True)
class SearchAttempts:
    """有界检索的合并结果：采用的候选、被剔除的候选、查询记录与逐轮诊断。"""

    hits: tuple[TiebaSearchHit, ...]
    rejected: tuple[TiebaRejectedCandidate, ...]
    records: list[ModuleQueryRecord]
    diagnostics: HitDiagnostics = HitDiagnostics()
    rounds: tuple[str, ...] = ()
    scope: tuple[TiebaSearchScope, ...] = ()
    #: 是否因停止／预算／可重试失败而没有跑完计划（供恢复续作）。
    partial: bool = False
    closeout_reason: str = "completed"


def search_candidates(
    port: TiebaSearchPort,
    account_id: str,
    analysis: TiebaQuestionAnalysis,
    *,
    stop_event: threading.Event | None,
    deadline_seconds: float,
    resume: dict[str, Any] | None = None,
    checkpoint: Callable[[SearchAttempts], None] | None = None,
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
    scopes: dict[tuple[str, str], TiebaSearchScope] = {}
    raw_hits = 0
    usable = 0
    completed: set[str] = set()
    seeded_hits: list[TiebaSearchHit] = []
    if resume is not None:
        for raw in resume.get("records", resume.get("queries", [])):
            try:
                record = ModuleQueryRecord.model_validate(raw)
            except ValueError:
                continue
            if record.status in FAILED_QUERY_STATUSES or record.status is ModuleQueryStatus.SKIPPED:
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
        for raw in resume.get("search_scope", []):
            try:
                scope = TiebaSearchScope.model_validate(raw)
            except ValueError:
                continue
            scopes[(scope.tab, scope.query)] = scope
        rounds.extend(str(note) for note in resume.get("rounds", []))
        raw_hits += int(resume.get("raw_hits") or 0)
        usable += int(resume.get("usable", len(seeded_hits)) or 0)
    partial = False
    closeout_reason = "completed"
    found_by_url: dict[str, TiebaSearchHit] = {}
    for hit in seeded_hits:
        if hit.url in found_by_url:
            found_by_url[hit.url] = merge_search_hit(found_by_url[hit.url], hit)
        else:
            found_by_url[hit.url] = hit
    query_rounds = plan_query_rounds(analysis)
    executed_expressions = {
        item.execution_terms for item in query_rounds if item.query in completed
    }
    deadline = time.monotonic() + deadline_seconds

    def snapshot(
        *, checkpoint_partial: bool, checkpoint_reason: str
    ) -> SearchAttempts:
        return SearchAttempts(
            hits=tuple(found_by_url.values()),
            rejected=tuple(rejected),
            records=list(records),
            diagnostics=_deduped_diagnostics(
                rejected, raw_hits=raw_hits, usable=usable
            ),
            rounds=tuple(rounds),
            scope=tuple(scopes.values()),
            partial=checkpoint_partial
            or any(
                record.retryable
                for record in records
                if record.status in FAILED_QUERY_STATUSES
            ),
            closeout_reason=checkpoint_reason,
        )

    for position, query_round in enumerate(query_rounds):
        if query_round.query in completed or query_round.execution_terms in executed_expressions:
            continue
        if position and budget_exhausted(stop_event, deadline):
            rounds.append(_skipped_round_note(query_round.goal, stop_event))
            partial = True
            closeout_reason = (
                "stopped"
                if stop_event is not None and stop_event.is_set()
                else "deadline"
            )
            break
        outcome = port.search_public(
            account_id,
            query=query_round.query,
            reason=f"{FORUM_NAME}信息搜集：只发送最小公开查询词",
            stop_event=stop_event,
            deadline=deadline,
        )
        executed_expressions.add(query_round.execution_terms)
        deadline += max(0, outcome.excluded_wait_ms) / 1000
        records.extend(outcome.prior_records)
        records.append(outcome.record)
        raw_hits += outcome.diagnostics.raw_hits
        usable += outcome.diagnostics.usable
        # 去重跨轮生效：同一条链接在两轮里都出现时只留一条剔除记录，多出来的
        # 那次仍算进原始命中数（计入重复链接），正文里不会同一条链接列两遍。
        fresh = [item for item in outcome.rejected if item.url not in rejected_urls]
        rejected_urls.update(item.url for item in fresh)
        rejected.extend(fresh)
        rounds.append(_round_note(query_round.goal, outcome))
        for scope in outcome.scope:
            scopes[(scope.tab, scope.query)] = scope
        for hit in outcome.candidates:
            if hit.url in found_by_url:
                found_by_url[hit.url] = merge_search_hit(found_by_url[hit.url], hit)
            else:
                found_by_url[hit.url] = hit
        if checkpoint is not None:
            checkpoint(snapshot(checkpoint_partial=True, checkpoint_reason="checkpoint"))
        if not _plan_continues(outcome.record):
            browser_blocked = outcome.record.error_code in AUTH_ERROR_CODES
            partial = browser_blocked or (
                outcome.record.retryable and outcome.record.status in FAILED_QUERY_STATUSES
            )
            closeout_reason = _closeout_reason(
                outcome.record, stop_event=stop_event, deadline=deadline
            )
            break
    if len(records) < len(query_rounds) and not any(
        record.status in STOP_QUERY_STATUSES for record in records
    ):
        partial = partial or len(found_by_url) > 0
    if closeout_reason == "completed" and not query_rounds:
        closeout_reason = "no_planned_queries"
    return snapshot(
        checkpoint_partial=partial, checkpoint_reason=closeout_reason
    )


def budget_exhausted(stop_event: threading.Event | None, deadline: float) -> bool:
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


def _closeout_reason(
    record: ModuleQueryRecord,
    *,
    stop_event: threading.Event | None,
    deadline: float,
) -> str:
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    if time.monotonic() >= deadline:
        return "deadline"
    if record.status is ModuleQueryStatus.RATE_LIMITED:
        return "rate_limited"
    if record.error_code in AUTH_ERROR_CODES:
        return "auth_required"
    if record.error_code in BUDGET_ERROR_CODES:
        return "budget_exhausted"
    if record.status is ModuleQueryStatus.CANCELLED:
        return "cancelled"
    if record.retryable:
        return "transient_error"
    return "permanent_error"


def _round_note(goal: str, outcome: SearchOutcome) -> str:
    """逐轮诊断：查询词、原始结果数、可用候选数与剔除数（脱敏，只记计数）。"""
    if outcome.record.status in FAILED_QUERY_STATUSES:
        reason = outcome.record.error_message or outcome.record.status.value
        return (
            f"{goal}「{outcome.record.query}」未完成（{reason}），"
            f"取得 {len(outcome.hits)} 条原始结果。"
        )
    note = (
        f"{goal}「{outcome.record.query}」取得 {len(outcome.hits)} 条原始结果："
        f"可用候选 {len(outcome.candidates)} 条，"
        f"剔除 {len(outcome.rejected)} 条（其中非帖子链接 {outcome.diagnostics.not_a_thread} 条、"
        f"他吧证据 {outcome.diagnostics.other_forum} 条）。"
    )
    if outcome.scope:
        pages = "、".join(
            f"{'主题' if item.tab == 'thread' else '回复'} {item.pages_read}/{item.pages_limit} 页"
            for item in outcome.scope
        )
        note += f" 页范围：{pages}。"
    return note


def _skipped_round_note(goal: str, stop_event: threading.Event | None) -> str:
    """备用查询没有执行时的原因：停止还是预算耗尽。"""
    if stop_event is not None and stop_event.is_set():
        return f"{goal}因你已停止而未执行。"
    return f"{goal}因超出本轮检索预算而未执行。"
