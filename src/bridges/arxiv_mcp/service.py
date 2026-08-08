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


@dataclass(frozen=True)
class ArxivSearchPlan:
    """本地提炼的最小论文查询计划。"""

    should_search: bool
    query: str
    reason: str


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
        explicit = bool(re.search(r"论文搜索|搜索论文|查论文|找论文", content, re.IGNORECASE))
        natural = bool(self._ARXIV.search(content) and self._ACTION.search(content))
        should_search = force or explicit or natural
        reason = (
            "学习模式本地证据不足，自动补充 arXiv 论文"
            if force and not (explicit or natural)
            else "用户明确要求搜索论文"
            if should_search
            else "本轮未触发 arXiv 论文搜索"
        )
        return ArxivSearchPlan(should_search, self._scrub(content) if should_search else "", reason)

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
        try:
            papers = self._client.search(plan.query, max_results=5, stop_event=stop_event)
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
        projection = [
            self._project_paper(index, paper, plan.query)
            for index, paper in enumerate(papers, 1)
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
        self._audit(account_id, plan, result)
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

    def _audit(self, account_id: str, plan: ArxivSearchPlan, result: ArxivSearchProjection) -> None:
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
