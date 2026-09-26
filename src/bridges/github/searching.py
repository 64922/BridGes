"""``github.search``：有界查询词、公开仓库检索的窄接口与候选归一。

查询词只用用户原文里的场景与要点（逐字保留），按「先整体、后组件」的顺序
发出：第一条是整体功能相近的查询，召回不足时才逐条查关键要点，因此每个候选
都能说出它由哪条查询召回、覆盖的是整体还是某一块。

技术词**不拼进查询**：GitHub 检索把空格分隔的词按 AND 处理，把技术词拼进去
会把召回压到零；技术词只用于排序时的软性匹配（见 ``ranking``）。

检索结果只作候选：仓库是否真的覆盖 idea 由 ``github.inspect`` 的真实证据与
``github.rank`` 的判定决定，检索命中数不计入结论。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from typing import Any, Protocol

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.github.client import GithubApiClient
from bridges.github.contracts import GithubIdeaAnalysis, GithubRepositoryCandidate
from bridges.github.lexicon import SEARCH_SOURCE, compact_scenario

#: 单条查询最多取回的候选数（有界；排序按上游 best match）。
SEARCH_RESULTS_PER_QUERY = 10

#: 元数据简介的字符上限：上游简介可以任意长，超出即截断（正文与卡片都只显示截断后的原文）。
MAX_DESCRIPTION_CHARS = 300

#: 单轮最多发出的查询数（上游检索接口按分钟计量，取数有界）。
MAX_QUERIES = 3

#: 整体查询已得到该数量以上候选时不再补组件查询（够用即止）。
MIN_WHOLE_RESULTS = 3


@dataclass(frozen=True)
class SearchOutcome:
    """一次检索调用的结果：统一查询记录与候选。"""

    query: str
    candidates: list[GithubRepositoryCandidate]
    record: ModuleQueryRecord


class GithubSearchPort(Protocol):
    """公开仓库检索的窄接口（由 ``GithubApiSearchAdapter`` 实现）。"""

    def search_repositories(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: Event | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome: ...


def plan_queries(analysis: GithubIdeaAnalysis) -> tuple[str, ...]:
    """生成有界查询词：第一条整体，其后是逐条要点（不足时才会用到）。

    整体查询用**剥掉末尾品类词**的场景（``compact_scenario``）：上游对中文按字
    模糊匹配，带上「平台／系统」这类词会把无关仓库一起召回；要点查询逐字保留
    用户原词。要点就是场景本身（整句没有分段）时不重复发一遍原句。
    """
    raw = analysis.scenario.strip()
    ordered: list[str] = []
    for index, term in enumerate([compact_scenario(analysis.scenario), *analysis.features]):
        cleaned = " ".join(term.split()).strip()
        if not cleaned or cleaned in ordered:
            continue
        if index and cleaned == raw:
            continue
        ordered.append(cleaned)
    return tuple(ordered[:MAX_QUERIES])


def query_record(
    *,
    query: str,
    status: ModuleQueryStatus,
    evidence_count: int,
    retrieved_at: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    retryable: bool = False,
    detail: str | None = None,
    source: str = SEARCH_SOURCE,
) -> ModuleQueryRecord:
    """统一查询记录（沿用 V2 Issue 11 建立的证据合同）。"""
    return ModuleQueryRecord(
        source=source,
        query=query,
        status=status,
        evidence_count=evidence_count,
        retrieved_at=retrieved_at or datetime.now(UTC),
        error_code=error_code,
        error_message=error_message,
        retryable=retryable,
        detail=detail,
    )


class GithubApiSearchAdapter:
    """把公开 GitHub REST API 适配成模块的检索边界。"""

    def __init__(
        self,
        client: GithubApiClient,
        *,
        per_page: int = SEARCH_RESULTS_PER_QUERY,
    ) -> None:
        self._client = client
        self._per_page = max(1, min(30, per_page))

    def search_repositories(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: Event | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome:
        if stop_event is not None and stop_event.is_set():
            return SearchOutcome(
                query=query,
                candidates=[],
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.CANCELLED,
                    evidence_count=0,
                    error_code="github_cancelled",
                    error_message="本轮检索已取消。",
                ),
            )
        if deadline is not None and time.monotonic() >= deadline:
            return SearchOutcome(
                query=query,
                candidates=[],
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.SKIPPED,
                    evidence_count=0,
                    detail="本轮检索达到时间预算，未发出该查询。",
                ),
            )
        response = self._client.get(
            "/search/repositories",
            params={
                "q": query,
                "per_page": str(self._per_page),
            },
            account_id=account_id,
            reason=reason,
        )
        if not response.ok:
            return SearchOutcome(
                query=query,
                candidates=[],
                record=query_record(
                    query=query,
                    status=_status_from_error(response),
                    evidence_count=0,
                    retrieved_at=response.requested_at,
                    error_code=response.error_code,
                    error_message=response.error_message,
                    retryable=response.retryable,
                ),
            )
        items = (response.payload or {}).get("items") or []
        candidates = [
            candidate
            for item in items
            if (candidate := _candidate_from_item(item, query=query)) is not None
        ]
        return SearchOutcome(
            query=query,
            candidates=candidates,
            record=query_record(
                query=query,
                status=(
                    ModuleQueryStatus.SUCCESS if candidates else ModuleQueryStatus.EMPTY
                ),
                evidence_count=len(candidates),
                retrieved_at=response.requested_at,
            ),
        )


def _status_from_error(response: Any) -> ModuleQueryStatus:
    code = getattr(response, "error_code", None)
    if code == "github_rate_limit":
        return ModuleQueryStatus.RATE_LIMITED
    if code == "github_timeout":
        return ModuleQueryStatus.TIMEOUT
    if code == "github_unavailable":
        return ModuleQueryStatus.ERROR
    return ModuleQueryStatus.ERROR


def _candidate_from_item(item: Any, *, query: str) -> GithubRepositoryCandidate | None:
    """把上游返回的一条仓库记录归一成候选；结构不符的记录直接跳过。"""
    if not isinstance(item, dict):
        return None
    full_name = str(item.get("full_name") or "").strip()
    html_url = str(item.get("html_url") or "").strip()
    if not full_name or not html_url:
        return None
    license_payload = item.get("license")
    license_info = license_payload if isinstance(license_payload, dict) else {}
    topics = item.get("topics")
    return GithubRepositoryCandidate(
        full_name=full_name,
        html_url=html_url,
        description=_bounded(item.get("description")),
        topics=[str(topic) for topic in topics][:20] if isinstance(topics, list) else [],
        language=_clean_text(item.get("language")),
        stars=_as_int(item.get("stargazers_count")) or 0,
        forks=_as_int(item.get("forks_count")) or 0,
        open_issues=_as_int(item.get("open_issues_count")) or 0,
        pushed_at=_as_datetime(item.get("pushed_at")),
        created_at=_as_datetime(item.get("created_at")),
        archived=bool(item.get("archived")),
        is_fork=bool(item.get("fork")),
        default_branch=_clean_text(item.get("default_branch")),
        license_spdx_id=_clean_text(license_info.get("spdx_id")),
        license_name=_clean_text(license_info.get("name")),
        matched_query=query,
        source=SEARCH_SOURCE,
    )


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _bounded(value: Any, *, limit: int = MAX_DESCRIPTION_CHARS) -> str | None:
    """归一简介并截断：上游简介长度不受控，截断后仍是原文（只去首尾空白）。"""
    text = _clean_text(value)
    if text is None or len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}…"


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _as_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
