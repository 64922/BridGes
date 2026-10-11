"""并行论文来源检索节点与真实调用记录。"""

from __future__ import annotations

import inspect
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.kernel.contracts import NodeExecution, NodeInvocation
from bridges.paper.artifact_codec import _record_payload
from bridges.paper.candidate_codec import candidate_payload
from bridges.paper.contracts import PaperQueryPlan
from bridges.paper.identity import merge_candidates
from bridges.paper.planning import DEFAULT_TARGET_COUNT
from bridges.paper.recipe import NODE_PARSE, NODE_PLAN
from bridges.paper.screening import screen_candidates
from bridges.paper.source_types import CandidateSearchOutcome, PaperCandidate

if TYPE_CHECKING:
    from bridges.paper.kernel import PaperNodeFlow



def search_node(self: PaperNodeFlow, invocation: NodeInvocation) -> NodeExecution:
    analysis = self._analysis(invocation, NODE_PARSE)
    plans = [
        PaperQueryPlan.model_validate(item)
        for item in invocation.dependencies[NODE_PLAN].payload["plans"]
    ]
    records: list[ModuleQueryRecord] = []
    attempts: list[dict[str, Any]] = []
    all_candidates: list[PaperCandidate] = []
    outcomes: list[str] = []
    budget_blocked = False
    jobs: list[tuple[int, PaperQueryPlan, Any, str, bool, dict[str, Any]]] = []
    for index, plan in enumerate(plans):
        if self._stop_event is not None and self._stop_event.is_set():
            break
        source = self._sources.get(plan.source)
        if source is None:
            continue
        call_key = f"paper.search:{plan.source}:{index}"
        registered = False
        if self._budget is not None:
            registered = self._budget.register_external(call_key, purpose="academic_search")
            if not registered:
                budget_blocked = True
                break
        search_kwargs: dict[str, Any] = {
            "max_results": plan.max_results,
            "stop_event": self._stop_event,
            "deadline": time.monotonic() + self._attempt_seconds(),
        }
        signature = inspect.signature(source.search)
        if "year_from" in signature.parameters:
            search_kwargs["year_from"] = analysis.constraints.year_from
            search_kwargs["year_to"] = analysis.constraints.year_to
        jobs.append((index, plan, source, call_key, self._budget is not None, search_kwargs))

    results_by_index: dict[int, CandidateSearchOutcome] = {}
    if jobs:
        with ThreadPoolExecutor(max_workers=min(3, len(jobs))) as executor:
            futures = {
                executor.submit(
                    source.search,
                    invocation.account_id,
                    plan.query,
                    **search_kwargs,
                ): (index, plan, call_key, registered)
                for index, plan, source, call_key, registered, search_kwargs in jobs
            }
            for future in as_completed(futures):
                index, plan, call_key, registered = futures[future]
                try:
                    result = future.result()
                except Exception as error:  # noqa: BLE001 - 单一来源故障不丢其他来源
                    result = CandidateSearchOutcome(
                        query=plan.query,
                        candidates=[],
                        record=ModuleQueryRecord(
                            source=plan.source,
                            query=plan.query,
                            status=ModuleQueryStatus.ERROR,
                            retrieved_at=self._clock(),
                            error_code=f"{plan.source}_search_error",
                            error_message="该学术来源本轮检索失败，其他来源仍可继续。",
                            retryable=True,
                            detail=type(error).__name__,
                        ),
                    )
                if self._budget is not None and registered:
                    self._budget.release_external(
                        call_key, outcome_code=result.record.status.value
                    )
                results_by_index[index] = result

    for index, plan, _source, _call_key, _registered, _kwargs in jobs:
        result = results_by_index[index]
        records.append(result.record)
        self._search_records.append(result.record)
        outcomes.append(result.record.status.value)
        all_candidates.extend(result.candidates)
        relevant_count = len(screen_candidates(analysis, result.candidates).screened)
        attempts.append(
            {
                "index": index,
                "source": plan.source,
                "query": result.record.query,
                "status": result.record.status.value,
                "candidate_count": len(result.candidates),
                "relevant_count": relevant_count,
            }
        )
    if not records:
        stopped = self._stop_event is not None and self._stop_event.is_set()
        return self._failure(
            invocation,
            payload={
                "candidates": [],
                "records": [],
                "query": "",
                "status": (
                    "cancelled"
                    if stopped
                    else "budget_exhausted"
                    if budget_blocked
                    else "unavailable"
                ),
                "attempts": list(attempts),
            },
            code=(
                "paper_cancelled"
                if stopped
                else "run_budget_exhausted"
                if budget_blocked
                else "paper_source_unavailable"
            ),
            message=(
                "本轮检索已停止。"
                if stopped
                else "本轮运行预算已用尽，未发起新的论文检索。"
                if budget_blocked
                else "当前没有可执行的论文检索来源。"
            ),
            retryable=stopped,
        )
    merged = merge_candidates(all_candidates)
    failed_statuses = {
        ModuleQueryStatus.ERROR.value,
        ModuleQueryStatus.TIMEOUT.value,
        ModuleQueryStatus.RATE_LIMITED.value,
        ModuleQueryStatus.CANCELLED.value,
    }
    all_failed = all(status in failed_statuses for status in outcomes)
    notes = [
        f"{record.source} 本轮失败：{record.error_message or record.error_code}。"
        for record in records
        if record.status in failed_statuses
    ]
    if budget_blocked:
        notes.append("共享运行预算不足，未完成所有已选来源的检索。")
    payload = {
        "candidates": [candidate_payload(item) for item in merged],
        "records": [_record_payload(item) for item in records],
        "query": "；".join(dict.fromkeys(item.query for item in records)),
        "status": "error" if all_failed else "success" if merged else "empty",
        "target_count": int(
            invocation.dependencies[NODE_PLAN].payload.get("target_count")
            or DEFAULT_TARGET_COUNT
        ),
        "attempts": attempts,
        "notes": notes,
        "adjustment": {
            "used": False,
            "reason": None,
            "outcome": None,
        },
    }
    if all_failed and not merged:
        error_record = next((item for item in records if item.error_code), records[0])
        return self._failure(
            invocation,
            payload=payload,
            code=error_record.error_code or "paper_search_failed",
            message=error_record.error_message or "论文检索失败，请稍后重试。",
            retryable=error_record.retryable,
        )
    if outcomes and all(status == ModuleQueryStatus.CANCELLED.value for status in outcomes):
        return self._failure(
            invocation,
            payload=payload,
            code="paper_cancelled",
            message="本轮检索已停止。",
            retryable=True,
        )
    return self._completed(invocation, payload)
