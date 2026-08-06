"""公网搜索编排：本地触发判断、查询脱敏、结果投影与审计。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from typing import Protocol

from bridges.contracts.chat import ChatMode
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.observability.service import ObservabilityService
from bridges.web_search.client import DuckDuckGoClient, WebSearchError
from bridges.web_search.contracts import (
    WebSearchProjection,
    WebSearchResult,
    WebSearchStatus,
)


@dataclass(frozen=True)
class SearchPlan:
    """本地生成的最小公网查询计划。"""

    should_search: bool
    query: str
    reason: str


class SearchClient(Protocol):
    def search(self, query: str) -> list[WebSearchResult]: ...


class LocalQueryPlanner:
    """只根据当前用户消息判断是否搜索，并删除私有内容。"""

    _EXPLICIT = re.compile(
        r"联网|上网|网页|在线|搜索|查找|查一下|查查|查下|查一查|搜一下|帮我搜|帮我查"
    )
    _FRESHNESS = re.compile(
        r"最新|最近|近期|当前|截至|本周|本月|最新消息|"
        r"今天(?:新闻|消息|天气|股价|价格|汇率|比赛)|现在(?:新闻|消息|天气|股价|价格|汇率)|\b20\d{2}\b"
    )
    _FACT_CHECK = re.compile(r"核实|查证|事实核查|属实|真假|可靠吗|是否正确|真的吗")
    _SECRET_ASSIGNMENT = re.compile(
        r"\b(?:password|passwd|pwd|credential|api[_ -]?key|authorization|secret|token)"
        r"\s*[:=]\s*(?:bearer\s+)?[^\s,，。！？；;]+",
        re.IGNORECASE,
    )
    _BEARER = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
    _PRIVATE_SEGMENT = re.compile(
        r"(?:私人|私密|个人)?(?:文档|文件|附件|资料|画像|档案|凭据|密码|密钥|"
        r"attachment|private\s*(?:document|file)|token|secret|api\s*key|apikey|qq|邮箱|用户名|"
        r"profile|personal\s*profile|"
        r"account[_ ]?id|user[_ ]?id)[^。！？；;\n]*[。！？；;]?",
        re.IGNORECASE,
    )
    _PERSONAL_SENTENCE = re.compile(
        r"(?:^|(?<=[。！？；;\n]))"
        r"(?=[^。！？；;\n]*(?:我的(?:姓名|名字|职业|背景|资料|画像|账户|账号|邮箱|"
        r"密码|密钥|附件|档案|个人信息|研究方向)|本人|个人|我(?:是|叫|住在|来自|有|使用)))"
        r"[^。！？；;\n]+[。！？；;]?",
        re.IGNORECASE,
    )
    _EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
    _URL = re.compile(r"https?://\S+", re.IGNORECASE)
    # Issue 39 AC6：结构化 PII（手机号/身份证号）同样去掉身份；
    # QQ 号交由 _PERSONAL_SENTENCE / _EMAIL（QQ 邮箱）覆盖，避免
    # 误伤合法数字查询（统计数字、年份等）。
    _PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
    _ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
    _CODE = re.compile(r"```.*?```", re.DOTALL)
    _TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,8}")
    _STOPWORDS = frozenset(
        {"请", "帮我", "一下", "看看", "告诉我", "搜索", "查找", "联网", "上网", "网页", "在线"}
    )

    def plan(
        self,
        content: str,
        mode: ChatMode = ChatMode.COMPANION,
        *,
        force: bool = False,
    ) -> SearchPlan:
        # 论文/文献请求交给 Issue 22 的固定 arXiv MCP，避免同一轮同时触发
        # 通用网页搜索并把普通网页误呈现为论文证据。
        if not force and re.search(r"论文|文献|arxiv", content, re.IGNORECASE) and re.search(
            r"搜索|搜|查|找|检索|推荐|综述", content
        ):
            return SearchPlan(False, "", "论文请求由 arXiv 论文搜索处理")
        explicit = bool(self._EXPLICIT.search(content))
        fresh = bool(self._FRESHNESS.search(content))
        fact_check = bool(self._FACT_CHECK.search(content))
        should_search = force or (mode == ChatMode.COMPANION and (explicit or fresh or fact_check))
        reasons = []
        if explicit:
            reasons.append("你明确要求联网搜索")
        if fresh:
            reasons.append("问题依赖最新信息")
        if fact_check:
            reasons.append("问题需要事实核查")
        if force and not reasons:
            reasons.append("学习模式本地证据不足，自动补充公开资料")
        reason = "、".join(reasons) if reasons else "本轮未触发公网搜索"
        return SearchPlan(should_search, self._scrub(content) if should_search else "", reason)

    def _scrub(self, content: str) -> str:
        cleaned = self._CODE.sub(" ", content)
        cleaned = self._SECRET_ASSIGNMENT.sub(" ", cleaned)
        cleaned = self._BEARER.sub(" ", cleaned)
        cleaned = self._PRIVATE_SEGMENT.sub(" ", cleaned)
        cleaned = self._PERSONAL_SENTENCE.sub(" ", cleaned)
        cleaned = self._EMAIL.sub(" ", cleaned)
        cleaned = self._URL.sub(" ", cleaned)
        cleaned = self._PHONE.sub(" ", cleaned)
        cleaned = self._ID_CARD.sub(" ", cleaned)
        tokens = [
            token for token in self._TOKEN.findall(cleaned)
            if token.lower() not in self._STOPWORDS
        ]
        return " ".join(tokens[:8])[:80].strip() or "公开信息"


class WebSearchService:
    """固定 DuckDuckGo 的公网搜索服务。"""

    def __init__(
        self,
        *,
        client: SearchClient | None = None,
        planner: LocalQueryPlanner | None = None,
        observability: ObservabilityService | None = None,
    ) -> None:
        self._client = client or DuckDuckGoClient()
        self._planner = planner or LocalQueryPlanner()
        self._observability = observability

    def plan(
        self,
        content: str,
        mode: ChatMode,
        *,
        force: bool = False,
    ) -> SearchPlan:
        return self._planner.plan(content, mode, force=force)

    def initial_projection(self, plan: SearchPlan) -> WebSearchProjection | None:
        if not plan.should_search:
            return None
        return WebSearchProjection(
            status=WebSearchStatus.LOADING,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            can_cancel=True,
        )

    def search(
        self,
        account_id: str,
        plan: SearchPlan,
        *,
        stop_event: Event | None = None,
    ) -> WebSearchProjection | None:
        if not plan.should_search:
            return None
        if stop_event is not None and stop_event.is_set():
            result = WebSearchProjection(
                status=WebSearchStatus.CANCELLED,
                trigger_reason=plan.reason,
                query_summary=plan.query,
                error_message="已取消本轮联网搜索。",
            )
            self._audit(account_id, plan, result)
            return result
        try:
            results = self._client.search(plan.query)
        except WebSearchError as exc:
            status = WebSearchStatus.PERMISSION if exc.permission else WebSearchStatus.ERROR
            result = WebSearchProjection(
                status=status,
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
            result = WebSearchProjection(
                status=WebSearchStatus.CANCELLED,
                trigger_reason=plan.reason,
                query_summary=plan.query,
                searched_at=datetime.now(UTC),
                error_message="已取消本轮联网搜索。",
            )
            self._audit(account_id, plan, result)
            return result
        status = WebSearchStatus.SUCCESS if results else WebSearchStatus.EMPTY
        result = WebSearchProjection(
            status=status,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            results=results,
            searched_at=datetime.now(UTC),
            error_message=None if results else "没有找到可核实的公开网页结果。",
            can_retry=not bool(results),
        )
        self._audit(account_id, plan, result)
        return result

    def _audit(
        self, account_id: str, plan: SearchPlan, result: WebSearchProjection
    ) -> None:
        if self._observability is None:
            return
        if result.status == WebSearchStatus.SUCCESS:
            audit_result = AuditResult.SUCCESS
        elif result.status == WebSearchStatus.CANCELLED:
            audit_result = AuditResult.BLOCKED
        elif result.status == WebSearchStatus.PERMISSION:
            audit_result = AuditResult.DENIED
        elif result.status == WebSearchStatus.EMPTY:
            audit_result = AuditResult.DEGRADED
        else:
            audit_result = AuditResult.RETRYABLE_FAIL
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.WEB_SEARCH,
            result=audit_result,
            reason=plan.reason,
            details={
                "data_categories": ["public_query_terms"],
                # Issue 39 AC6：与画像切片审计一致的授权快照版本，
                # 保证每次云披露都可审计类别与授权来源。
                "authorization_snapshot": "authz-1.0",
                "query_length": len(plan.query),
                "result_count": len(result.results),
                "status": result.status.value,
            },
        )
