"""arXiv 触发、查询脱敏、中文结果编排与隐私审计。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from typing import Protocol

from bridges.arxiv_mcp.client import ArxivMcpError
from bridges.arxiv_mcp.contracts import (
    ArxivPaper,
    ArxivPaperProjection,
    ArxivSearchProjection,
    ArxivSearchStatus,
)
from bridges.arxiv_mcp.process import ArxivMcpProcessClient
from bridges.contracts.chat import ChatMode
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.observability.service import ObservabilityService
from bridges.routing import (
    CapabilityRoute,
    NaturalLanguageRouter,
    PaperSearchConstraints,
    RouteStatus,
)
from bridges.routing.contracts import PAPER_QUERY_VERSION, RemovedQueryCategory

ARXIV_RELEVANCE_VERSION = "2026.08.12"


@dataclass(frozen=True)
class ArxivSearchPlan:
    """本地提炼的最小论文查询计划。"""

    should_search: bool
    query: str
    reason: str
    max_results: int = 5
    constraints: PaperSearchConstraints | None = None
    route_version: str = PAPER_QUERY_VERSION
    removed_categories: tuple[RemovedQueryCategory, ...] = ()


class ArxivClient(Protocol):
    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Event | None = None,
    ) -> list[ArxivPaper]: ...


class ArxivQueryPlanner:
    """识别论文意图并删除身份、私密材料与凭据。"""

    def __init__(self, router: NaturalLanguageRouter | None = None) -> None:
        self._router = router or NaturalLanguageRouter()

    def route(self, content: str) -> CapabilityRoute:
        """只分类一次，供聊天入口持久化并在后续重放复用。"""
        return self._router.classify(content)

    def plan_from_route(self, route: CapabilityRoute) -> ArxivSearchPlan:
        """把已持久化的路由快照编译为论文 worker 计划，不重新分类。"""
        if route.status != RouteStatus.MATCHED or route.paper_search is None:
            return ArxivSearchPlan(False, "", "本轮未触发 arXiv 论文搜索")
        paper_plan = route.paper_search
        return ArxivSearchPlan(
            True,
            paper_plan.normalized_query,
            route.reason,
            paper_plan.constraints.max_results,
            paper_plan.constraints,
            paper_plan.version,
            tuple(paper_plan.removed_categories),
        )

    def plan(
        self,
        content: str,
        mode: ChatMode = ChatMode.COMPANION,
        *,
        force: bool = False,
    ) -> ArxivSearchPlan:
        route = self.route(content)
        if route.status == RouteStatus.MATCHED and route.paper_search is not None:
            paper_plan = route.paper_search
            return ArxivSearchPlan(
                True,
                paper_plan.normalized_query,
                route.reason,
                paper_plan.constraints.max_results,
                paper_plan.constraints,
                paper_plan.version,
                tuple(paper_plan.removed_categories),
            )
        if force:
            query = self._scrub(content)
            if not query:
                return ArxivSearchPlan(
                    False,
                    "",
                    "净化后没有可公开检索的论文主题",
                    route_version=PAPER_QUERY_VERSION,
                )
            constraints = PaperSearchConstraints(topic_terms=[query])
            return ArxivSearchPlan(
                True,
                query,
                "学习模式本地证据不足，自动补充 arXiv 论文",
                constraints.max_results,
                constraints,
                PAPER_QUERY_VERSION,
            )
        return ArxivSearchPlan(False, "", "本轮未触发 arXiv 论文搜索")

    def _scrub(self, content: str) -> str:
        return self._router.normalize_public_query(content)


class ArxivSearchService:
    """固定内置 arXiv MCP 的搜索编排服务。"""

    def __init__(
        self,
        *,
        client: ArxivClient | None = None,
        planner: ArxivQueryPlanner | None = None,
        observability: ObservabilityService | None = None,
    ) -> None:
        self._client = client or ArxivMcpProcessClient()
        self._planner = planner or ArxivQueryPlanner()
        self._observability = observability

    def plan(
        self,
        content: str,
        mode: ChatMode,
        *,
        force: bool = False,
    ) -> ArxivSearchPlan:
        return self._planner.plan(content, mode, force=force)

    def plan_from_route(self, route: CapabilityRoute) -> ArxivSearchPlan:
        return self._planner.plan_from_route(route)

    def close(self) -> None:
        """关闭受限 worker，避免应用重载后遗留子进程。"""
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def initial_projection(
        self, plan: ArxivSearchPlan, *, recovery: bool = False
    ) -> ArxivSearchProjection | None:
        if not plan.should_search:
            return None
        return ArxivSearchProjection(
            status=ArxivSearchStatus.RECOVERY if recovery else ArxivSearchStatus.LOADING,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            can_cancel=True,
        )

    def search(
        self,
        account_id: str,
        plan: ArxivSearchPlan,
        *,
        stop_event: Event | None = None,
    ) -> ArxivSearchProjection | None:
        if not plan.should_search:
            return None
        if stop_event is not None and stop_event.is_set():
            result = self._cancelled_projection(plan)
            self._audit(account_id, plan, result)
            return result
        if not 1 <= plan.max_results <= 10 or not plan.query.strip():
            result = ArxivSearchProjection(
                status=ArxivSearchStatus.ERROR,
                trigger_reason=plan.reason,
                query_summary=plan.query,
                searched_at=datetime.now(UTC),
                error_code="arxiv_request",
                error_message="论文搜索参数不合法，请调整主题、年份或结果数量后重试。",
                can_retry=False,
            )
            self._audit(account_id, plan, result)
            return result
        try:
            papers = self._client.search(
                plan.query, max_results=plan.max_results, stop_event=stop_event
            )
        except ArxivMcpError as exc:
            if exc.code == "arxiv_cancelled":
                # 搜索期间用户取消：投影为 cancelled，而不是折叠成启动失败
                result = self._cancelled_projection(
                    plan, with_timestamp=True, error_code=exc.code, error_message=exc.message
                )
            else:
                result = ArxivSearchProjection(
                    status=(
                        ArxivSearchStatus.PERMISSION
                        if exc.permission
                        else ArxivSearchStatus.ERROR
                    ),
                    trigger_reason=plan.reason,
                    query_summary=plan.query,
                    searched_at=datetime.now(UTC),
                    error_code=exc.code,
                    error_message=exc.message,
                    can_retry=True,
                )
            self._audit(account_id, plan, result)
            return result
        if stop_event is not None and stop_event.is_set():
            result = self._cancelled_projection(plan, with_timestamp=True)
            self._audit(account_id, plan, result)
            return result
        candidates = _deduplicate_papers(papers)
        relevant_papers = [
            paper for paper in candidates if _paper_matches_plan(paper, plan)
        ]
        projection = [
            self._project_paper(index, paper, plan.query, plan.constraints)
            for index, paper in enumerate(relevant_papers, 1)
        ]
        error_code = None
        error_message = None
        if not projection:
            if candidates:
                error_code = "arxiv_no_relevant_results"
                error_message = "没有找到与主题相关的 arXiv 论文，请调整领域或约束后重试。"
            else:
                error_code = "arxiv_no_results"
                error_message = "没有找到匹配的 arXiv 论文，请调整领域或约束后重试。"
        result = ArxivSearchProjection(
            status=ArxivSearchStatus.SUCCESS if projection else ArxivSearchStatus.EMPTY,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            papers=projection,
            searched_at=datetime.now(UTC),
            error_code=error_code,
            error_message=error_message,
            can_retry=not bool(projection),
        )
        self._audit(account_id, plan, result, candidate_count=len(candidates))
        return result

    @staticmethod
    def _cancelled_projection(
        plan: ArxivSearchPlan,
        *,
        with_timestamp: bool = False,
        error_code: str | None = None,
        error_message: str = "已取消本轮论文搜索。",
    ) -> ArxivSearchProjection:
        """构造取消投影（入口预检/搜索期间/后置检查三处共用）。"""
        return ArxivSearchProjection(
            status=ArxivSearchStatus.CANCELLED,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            searched_at=datetime.now(UTC) if with_timestamp else None,
            error_code=error_code,
            error_message=error_message,
        )

    def _audit(
        self,
        account_id: str,
        plan: ArxivSearchPlan,
        result: ArxivSearchProjection,
        *,
        candidate_count: int = 0,
    ) -> None:
        if self._observability is None:
            return
        audit_result = {
            ArxivSearchStatus.SUCCESS: AuditResult.SUCCESS,
            ArxivSearchStatus.EMPTY: AuditResult.DEGRADED,
            ArxivSearchStatus.PERMISSION: AuditResult.DENIED,
            ArxivSearchStatus.CANCELLED: AuditResult.BLOCKED,
        }.get(result.status, AuditResult.RETRYABLE_FAIL)
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.ARXIV_SEARCH,
            result=audit_result,
            reason="论文搜索路由",
            details={
                "data_categories": ["public_query_terms"],
                "permission_version": "2026.08.04",
                "route_version": plan.route_version,
                "relevance_rule_version": ARXIV_RELEVANCE_VERSION,
                "public_term_count": len(_topic_terms(plan.constraints)),
                "removed_categories": list(plan.removed_categories),
                "candidate_count": candidate_count,
                "relevant_result_count": len(result.papers),
                "removed_candidate_count": max(0, candidate_count - len(result.papers)),
                "terminal": result.status.value,
                "result_code": result.error_code,
            },
        )

    @staticmethod
    def _project_paper(
        index: int,
        paper: ArxivPaper,
        query: str,
        constraints: PaperSearchConstraints | None = None,
    ) -> ArxivPaperProjection:
        query_tokens = _topic_terms(constraints) or _query_tokens(query)
        matching = [
            token
            for token in query_tokens
            if token.lower() in f"{paper.title} {paper.abstract}".lower()
        ]
        matched_text = "、".join(matching[:4]) or "论文标题与摘要"
        return ArxivPaperProjection(
            citation_id=f"arxiv-{index}",
            arxiv_id=paper.arxiv_id,
            title=paper.title,
            authors=paper.authors,
            published_at=paper.published_at,
            abs_url=paper.abs_url,
            pdf_url=paper.pdf_url,
            abstract=paper.abstract,
            summary_zh=(
                f"论文摘要：这篇论文围绕《{paper.title}》展开研究。"
                f"以下原始摘要由 arXiv 返回，未补造摘要之外的结论：{paper.abstract}"
            ),
            relevance_basis=(
                f"与确认查询「{query}」的相关依据来自 arXiv 返回的标题和摘要，"
                f"匹配线索：{matched_text}。"
            ),
            learning_advice_zh="建议先阅读摘要和引言，随后核对方法、实验条件与局限；如需深入，再从该论文的参考文献和后续版本继续学习。",
        )


_TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "量子纠错": ("quantum error correction",),
    "量子": ("quantum",),
    "纠错": ("error correction",),
    "机器学习": ("machine learning",),
    "深度学习": ("deep learning",),
    "神经网络": ("neural network",),
    "自然语言处理": ("natural language processing",),
    "计算机视觉": ("computer vision",),
    "强化学习": ("reinforcement learning",),
}


def _paper_matches_plan(paper: ArxivPaper, plan: ArxivSearchPlan) -> bool:
    constraints = plan.constraints or _legacy_constraints(plan.query)
    searchable = _match_text(f"{paper.title} {paper.abstract}")
    for term in _topic_terms(constraints):
        if not _topic_matches(term, searchable):
            return False
    if constraints.author:
        authors = _match_text(" ".join(paper.authors))
        if _match_text(constraints.author) not in authors:
            return False
    if constraints.title and _match_text(constraints.title) not in _match_text(paper.title):
        return False
    if constraints.arxiv_id and not paper.arxiv_id.casefold().startswith(
        constraints.arxiv_id.casefold()
    ):
        return False
    if constraints.year_from is not None and paper.published_at.year < constraints.year_from:
        return False
    return not (
        constraints.year_to is not None
        and paper.published_at.year > constraints.year_to
    )


def _legacy_constraints(query: str) -> PaperSearchConstraints:
    terms = [
        token
        for token in _query_tokens(query)
        if token.casefold() not in {"author", "title", "id", "year"}
    ]
    return PaperSearchConstraints(topic_terms=terms[:12] or [query.strip()])


def _query_tokens(query: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", query)


def _topic_terms(constraints: PaperSearchConstraints | None) -> list[str]:
    if constraints is None:
        return []
    return [
        term
        for term in constraints.topic_terms
        if not re.match(r"^(?:author|title|id|year):", term, re.IGNORECASE)
    ]


def _topic_matches(term: str, searchable: str) -> bool:
    normalized_term = _match_text(term)
    if not normalized_term:
        return True
    base_variants = [normalized_term]
    for suffix in ("方向相关", "相关", "方向"):
        if term.endswith(suffix) and len(term) > len(suffix) + 1:
            base_variants.append(_match_text(term[: -len(suffix)]))
    variants = list(base_variants)
    for variant in base_variants:
        variants.extend(
            _match_text(alias)
            for alias in _TOPIC_ALIASES.get(variant, ())
        )
    return any(variant and variant in searchable for variant in variants)


def _match_text(value: str) -> str:
    return re.sub(r"[\s\-_.,:;，。；：!?！？()（）\[\]【】]+", "", value.casefold())


def _deduplicate_papers(papers: list[ArxivPaper]) -> list[ArxivPaper]:
    """按原始 arXiv 标识去重并保持供应商返回顺序。"""
    seen: set[str] = set()
    unique: list[ArxivPaper] = []
    for paper in papers:
        if paper.arxiv_id in seen:
            continue
        seen.add(paper.arxiv_id)
        unique.append(paper)
    return unique
