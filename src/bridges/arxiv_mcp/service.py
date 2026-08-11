"""arXiv 触发、查询脱敏、中文结果编排与隐私审计。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import Parameter, signature
from threading import Event
from typing import Any, Protocol, cast

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


@dataclass(frozen=True)
class ArxivSearchPlan:
    """本地提炼的最小论文查询计划。"""

    should_search: bool
    query: str
    reason: str
    max_results: int = 5
    constraints: PaperSearchConstraints | None = None
    route_version: str = "2026.08.09"


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
        )

    _ARXIV = re.compile(r"arxiv|论文|文献", re.IGNORECASE)
    _ACTION = re.compile(
        r"搜索|搜|查|找|检索|推荐|综述|论文搜索|最新|近[一二两三四五六七八九十0-9]+年"
    )
    _CODE = re.compile(r"```.*?```", re.DOTALL)
    _SECRET = re.compile(
        r"\b(?:password|passwd|pwd|credential|api[_ -]?key|authorization|secret|token)"
        r"\s*[:=]\s*(?:bearer\s+)?[^\s,，。！？；;]+",
        re.IGNORECASE,
    )
    _PRIVATE = re.compile(
        r"(?:私人|私密|个人)?(?:文档|文件|附件|资料|画像|档案|凭据|密码|密钥|"
        r"attachment|private\s*(?:document|file)|token|secret|api\s*key|apikey|qq|邮箱|用户名|"
        r"profile|account[_ ]?id|user[_ ]?id)[^。！？；;\n]*[。！？；;]?",
        re.IGNORECASE,
    )
    _EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
    _URL = re.compile(r"https?://\S+", re.IGNORECASE)
    _PERSONAL = re.compile(
        r"(?:^|(?<=[。！？；;\n]))(?=[^。！？；;\n]*(?:我的(?:姓名|名字|职业|背景|资料|画像|账户|账号|邮箱|"
        r"密码|密钥|附件|档案|个人信息)|本人|个人|我(?:是|叫|住在|来自|有|使用)))"
        r"[^。！？；;\n]+[。！？；;]?",
        re.IGNORECASE,
    )
    # 中文主题词保持连续，避免把“量子”拆成跨 token 的“量 子”。
    _TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}")
    _STOPWORDS = frozenset(
        {"请", "帮我", "一下", "看看", "告诉我", "论文", "文献", "搜索", "查找", "检索"}
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
            )
        if force:
            query = self._scrub(content)
            constraints = PaperSearchConstraints(topic_terms=[query or "公开论文主题"])
            return ArxivSearchPlan(
                True,
                query or "公开论文主题",
                "学习模式本地证据不足，自动补充 arXiv 论文",
                constraints.max_results,
                constraints,
            )
        return ArxivSearchPlan(False, "", "本轮未触发 arXiv 论文搜索")

    def _scrub(self, content: str) -> str:
        cleaned = self._CODE.sub(" ", content)
        cleaned = self._SECRET.sub(" ", cleaned)
        cleaned = self._PRIVATE.sub(" ", cleaned)
        cleaned = self._PERSONAL.sub(" ", cleaned)
        cleaned = self._EMAIL.sub(" ", cleaned)
        cleaned = self._URL.sub(" ", cleaned)
        tokens = [
            token
            for token in self._TOKEN.findall(cleaned)
            if token.lower() not in self._STOPWORDS
        ]
        return " ".join(tokens[:8])[:80].strip() or "公开论文主题"


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
        deadline: float | None = None,
    ) -> ArxivSearchProjection | None:
        if not plan.should_search:
            return None
        if _user_cancelled(stop_event):
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
            papers = _invoke_arxiv_client(
                self._client,
                plan.query,
                max_results=plan.max_results,
                stop_event=stop_event,
                deadline=deadline,
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
            self._audit(account_id, plan, result, deadline=deadline)
            return result
        if _user_cancelled(stop_event):
            result = self._cancelled_projection(plan, with_timestamp=True)
            self._audit(account_id, plan, result, deadline=deadline)
            return result
        if deadline is not None and time.monotonic() >= deadline:
            result = ArxivSearchProjection(
                status=ArxivSearchStatus.ERROR,
                trigger_reason=plan.reason,
                query_summary=plan.query,
                searched_at=datetime.now(UTC),
                error_code="arxiv_timeout",
                error_message="arXiv 搜索超时，请重试。",
                can_retry=True,
            )
            self._audit(account_id, plan, result, deadline=deadline)
            return result
        projection = [
            self._project_paper(index, paper, plan.query)
            for index, paper in enumerate(_deduplicate_papers(papers), 1)
        ]
        result = ArxivSearchProjection(
            status=ArxivSearchStatus.SUCCESS if projection else ArxivSearchStatus.EMPTY,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            papers=projection,
            searched_at=datetime.now(UTC),
            error_message=(
                None if projection else "没有找到匹配的 arXiv 论文，请调整领域或约束后重试。"
            ),
            can_retry=not bool(projection),
        )
        self._audit(account_id, plan, result, deadline=deadline)
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
        deadline: float | None = None,
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
            reason=plan.reason,
            details={
                "data_categories": ["public_query_terms"],
                "permission_version": "2026.08.04",
                "query_length": len(plan.query),
                "result_count": len(result.papers),
                "status": result.status.value,
                "error_code": result.error_code,
                "active_sources": ["arxiv"],
                "budget_source": "arxiv_search",
                "deadline_remaining_ms": (
                    max(0, int((deadline - time.monotonic()) * 1000))
                    if deadline is not None
                    else None
                ),
            },
        )

    @staticmethod
    def _project_paper(index: int, paper: ArxivPaper, query: str) -> ArxivPaperProjection:
        query_tokens = ArxivQueryPlanner._TOKEN.findall(query)
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


def _invoke_arxiv_client(
    client: Any,
    query: str,
    *,
    max_results: int,
    stop_event: Event | None,
    deadline: float | None,
) -> list[ArxivPaper]:
    """把截止时间传给新客户端，同时兼容旧的确定性测试替身。"""
    search = client.search
    try:
        parameters = tuple(signature(search).parameters.values())
    except (TypeError, ValueError):
        parameters = ()
        accepts_deadline = True
    else:
        accepts_deadline = any(
            parameter.name == "deadline" or parameter.kind == Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    kwargs: dict[str, Any] = {"max_results": max_results, "stop_event": stop_event}
    if deadline is not None and accepts_deadline:
        kwargs["deadline"] = deadline
    return cast(list[ArxivPaper], search(query, **kwargs))


def _user_cancelled(stop_event: Event | None) -> bool:
    """区分用户取消与编排器为截止时间发出的内部停止信号。"""
    if stop_event is None:
        return False
    marker = getattr(stop_event, "user_is_set", None)
    return bool(marker()) if callable(marker) else stop_event.is_set()
