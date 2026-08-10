"""公网搜索编排：本地触发判断、查询脱敏、缓存、结果投影与审计。"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, RLock
from typing import Callable, Protocol

from bridges.contracts.chat import ChatMode
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.observability.service import ObservabilityService
from bridges.web_search.client import (
    DUCKDUCKGO_PROVIDER_VERSION,
    DuckDuckGoClient,
    WebSearchError,
)
from bridges.web_search.contracts import (
    WebSearchProjection,
    WebSearchResult,
    WebSearchStatus,
)

WEB_SEARCH_RULES_VERSION = "web-search-plan-v2"
DEFAULT_PROVIDER = "duckduckgo"
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
_FRESH_CACHE_TTL_SECONDS = 60 * 60
_CURRENT_CACHE_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class SearchPlan:
    """本地生成并可在网络请求前持久化的最小公网查询计划。"""

    should_search: bool
    query: str
    reason: str
    plan_id: str = ""
    provider: str = DEFAULT_PROVIDER
    provider_version: str = DUCKDUCKGO_PROVIDER_VERSION
    rules_version: str = WEB_SEARCH_RULES_VERSION
    query_hash: str = ""
    original_query_hash: str = ""
    freshness_window_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    deleted_categories: tuple[str, ...] = ()
    max_queries: int = 1
    max_results: int = 5
    max_retries: int = 1
    total_timeout_seconds: float = 8.0
    max_response_bytes: int = 1_000_000
    max_redirects: int = 0

    def __post_init__(self) -> None:
        query_hash = self.query_hash or hashlib.sha256(
            self.query.encode("utf-8")
        ).hexdigest()
        object.__setattr__(self, "query_hash", query_hash)
        if not self.original_query_hash:
            object.__setattr__(self, "original_query_hash", query_hash)
        if not self.plan_id:
            material = ":".join(
                (
                    self.provider,
                    self.provider_version,
                    self.rules_version,
                    query_hash,
                    str(self.freshness_window_seconds),
                )
            )
            object.__setattr__(
                self,
                "plan_id",
                hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
            )


class SearchClient(Protocol):
    def search(self, query: str) -> list[WebSearchResult]: ...


class WebSearchCache(Protocol):
    """按账户、查询、提供方和时间窗口隔离的可持久缓存边界。"""

    def get(
        self, account_id: str, plan: SearchPlan, now: datetime
    ) -> WebSearchProjection | None: ...

    def put(
        self,
        account_id: str,
        plan: SearchPlan,
        projection: WebSearchProjection,
        expires_at: datetime,
    ) -> None: ...


class InMemoryWebSearchCache:
    """测试与无持久化环境使用的账户隔离缓存实现。"""

    def __init__(self) -> None:
        self._entries: dict[
            tuple[str, str, str, int], tuple[datetime, WebSearchProjection]
        ] = {}
        self._lock = RLock()

    @staticmethod
    def _key(account_id: str, plan: SearchPlan) -> tuple[str, str, str, int]:
        return (
            account_id,
            plan.query_hash,
            plan.provider_version,
            plan.freshness_window_seconds,
        )

    def get(
        self, account_id: str, plan: SearchPlan, now: datetime
    ) -> WebSearchProjection | None:
        with self._lock:
            entry = self._entries.get(self._key(account_id, plan))
            if entry is None:
                return None
            expires_at, projection = entry
            if expires_at <= now:
                self._entries.pop(self._key(account_id, plan), None)
                return None
            return projection.model_copy(update={"cache_hit": True})

    def put(
        self,
        account_id: str,
        plan: SearchPlan,
        projection: WebSearchProjection,
        expires_at: datetime,
    ) -> None:
        with self._lock:
            self._entries[self._key(account_id, plan)] = (expires_at, projection)


class LocalQueryPlanner:
    """只根据本地消息判断是否搜索，并删除私有内容。"""

    _EXPLICIT = re.compile(
        r"联网|上网|网页|在线|搜索|查找|查一下|查查|查下|查一查|搜一下|帮我搜|帮我查"
    )
    _FRESHNESS = re.compile(
        r"最新|最近|近期|当前|截至|本周|本月|本季度|本年|最新消息|变化|"
        r"今天(?:新闻|消息|天气|股价|价格|汇率|比赛)|现在(?:新闻|消息|天气|股价|价格|汇率)|\b20\d{2}\b"
    )
    _CURRENT = re.compile(r"今天|现在|实时|当前|截至")
    _FACT_CHECK = re.compile(r"核实|查证|事实核查|属实|真假|可靠吗|是否正确|真的吗")
    _SECRET_ASSIGNMENT = re.compile(
        r"\b(?:password|passwd|pwd|credential|api[_ -]?key|authorization|secret|token)"
        r"\s*[:=]\s*(?:bearer\s+)?[^\s,，。！？；;]+",
        re.IGNORECASE,
    )
    _BEARER = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
    _PRIVATE_SEGMENT = re.compile(
        r"(?:私人|私密|个人)?(?:文档|文件|附件|资料|画像|档案|凭据|密码|密钥|"
        r"知识库|原文|证据|通信|聊天|attachment|private\s*(?:document|file)|token|secret|api\s*key|apikey|qq|邮箱|用户名|"
        r"profile|personal\s*profile|account[_ ]?id|user[_ ]?id)[^。！？；;\n]*[。！？；;]?",
        re.IGNORECASE,
    )
    _NAME_MARKER = re.compile(
        r"(?:姓名|名字|name)\s*(?:是|为|[:：=])?\s*[A-Za-z\u4e00-\u9fff]{2,20}",
        re.IGNORECASE,
    )
    _PRECISE_LOCATION = re.compile(
        r"(?:精确位置|具体地址|地址|住址|经纬度|坐标|GPS|location|address)"
        r"\s*(?:是|为|[:：=])?\s*[^。！？；;\n]+[。！？；;]?",
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
        # 论文/文献请求交给固定 arXiv 搜索，避免同一轮同时触发通用网页搜索。
        if not force and re.search(r"论文|文献|arxiv", content, re.IGNORECASE) and re.search(
            r"搜索|搜|查|找|检索|推荐|综述", content
        ):
            return SearchPlan(False, "", "论文请求由 arXiv 论文搜索处理")
        explicit = bool(self._EXPLICIT.search(content))
        fresh = bool(self._FRESHNESS.search(content))
        fact_check = bool(self._FACT_CHECK.search(content))
        should_search = force or (mode == ChatMode.COMPANION and (explicit or fresh or fact_check))
        reasons: list[str] = []
        if explicit:
            reasons.append("你明确要求联网搜索")
        if fresh:
            reasons.append("问题依赖最新信息")
        if fact_check:
            reasons.append("问题需要事实核查")
        if force and not reasons:
            reasons.append("学习模式本地证据不足，自动补充公开资料")
        reason = "、".join(reasons) if reasons else "本轮未触发公网搜索"
        query, deleted_categories = self._scrub_with_categories(content)
        freshness_window = self._freshness_window(content, fresh=fresh)
        return SearchPlan(
            should_search,
            query if should_search else "",
            reason,
            original_query_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            freshness_window_seconds=freshness_window,
            deleted_categories=tuple(deleted_categories),
        )

    def _scrub(self, content: str) -> str:
        return self._scrub_with_categories(content)[0]

    def _scrub_with_categories(self, content: str) -> tuple[str, list[str]]:
        deleted: list[str] = []

        def remove(pattern: re.Pattern[str], value: str, category: str) -> str:
            cleaned_value, count = pattern.subn(" ", value)
            if count and category not in deleted:
                deleted.append(category)
            return cleaned_value

        cleaned = remove(self._CODE, content, "code")
        cleaned = remove(self._SECRET_ASSIGNMENT, cleaned, "credentials")
        cleaned = remove(self._BEARER, cleaned, "credentials")
        cleaned = remove(self._PRIVATE_SEGMENT, cleaned, "private_context")
        cleaned = remove(self._PERSONAL_SENTENCE, cleaned, "identity")
        cleaned = remove(self._NAME_MARKER, cleaned, "identity")
        cleaned = remove(self._PRECISE_LOCATION, cleaned, "precise_location")
        cleaned = remove(self._EMAIL, cleaned, "contact")
        cleaned = remove(self._URL, cleaned, "urls")
        cleaned = remove(self._PHONE, cleaned, "identity")
        cleaned = remove(self._ID_CARD, cleaned, "identity")
        if re.search(r"知识库|原文|证据", content) and "knowledge_base" not in deleted:
            deleted.append("knowledge_base")
        if re.search(r"通信|聊天", content) and "private_communication" not in deleted:
            deleted.append("private_communication")
        tokens = [
            token
            for token in self._TOKEN.findall(cleaned)
            if token.lower() not in self._STOPWORDS
        ]
        return " ".join(tokens[:8])[:80].strip() or "公开信息", deleted

    @staticmethod
    def _freshness_window(content: str, *, fresh: bool) -> int:
        if not fresh:
            return DEFAULT_CACHE_TTL_SECONDS
        if LocalQueryPlanner._CURRENT.search(content):
            return _CURRENT_CACHE_TTL_SECONDS
        return _FRESH_CACHE_TTL_SECONDS


class WebSearchService:
    """固定 DuckDuckGo 的公网搜索服务。"""

    def __init__(
        self,
        *,
        client: SearchClient | None = None,
        planner: LocalQueryPlanner | None = None,
        observability: ObservabilityService | None = None,
        cache: WebSearchCache | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client or DuckDuckGoClient()
        self._planner = planner or LocalQueryPlanner()
        self._observability = observability
        self._cache = cache or InMemoryWebSearchCache()
        self._clock = clock or (lambda: datetime.now(UTC))

    def plan(
        self,
        content: str,
        mode: ChatMode,
        *,
        force: bool = False,
    ) -> SearchPlan:
        return self._planner.plan(content, mode, force=force)

    def initial_projection(
        self, plan: SearchPlan, *, recovery: bool = False
    ) -> WebSearchProjection | None:
        if not plan.should_search:
            return None
        return self._projection(
            plan,
            status=WebSearchStatus.RECOVERY if recovery else WebSearchStatus.LOADING,
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
        now = self._clock()
        cached = self._cache.get(account_id, plan, now)
        if cached is not None:
            cached = cached.model_copy(
                update={
                    "plan_id": plan.plan_id,
                    "query_hash": plan.query_hash,
                    "cache_hit": True,
                }
            )
            self._audit(account_id, plan, cached)
            return cached
        if stop_event is not None and stop_event.is_set():
            result = self._projection(
                plan,
                status=WebSearchStatus.CANCELLED,
                error_message="已取消本轮联网搜索。",
            )
            self._audit(account_id, plan, result)
            return result

        deadline = time.monotonic() + max(0.0, plan.total_timeout_seconds)
        attempts = 0
        while True:
            if time.monotonic() >= deadline:
                result = self._timeout_projection(plan, attempts)
                self._audit(account_id, plan, result)
                return result
            attempts += 1
            try:
                remaining = max(0.0, deadline - time.monotonic())
                if isinstance(self._client, DuckDuckGoClient):
                    results = self._client.search(plan.query, timeout=remaining)
                else:
                    results = self._client.search(plan.query)
                break
            except WebSearchError as exc:
                if (
                    exc.retryable
                    and attempts <= plan.max_retries
                    and time.monotonic() < deadline
                    and (stop_event is None or not stop_event.is_set())
                ):
                    continue
                result = self._projection(
                    plan,
                    status=self._error_status(exc.code, exc.permission),
                    searched_at=self._clock(),
                    error_code=exc.code,
                    error_message=exc.message,
                    can_retry=exc.retryable,
                    attempt_count=attempts,
                    query_count=1,
                )
                self._audit(account_id, plan, result)
                return result
        if time.monotonic() >= deadline:
            result = self._timeout_projection(plan, attempts)
            self._audit(account_id, plan, result)
            return result
        if stop_event is not None and stop_event.is_set():
            result = self._projection(
                plan,
                status=WebSearchStatus.CANCELLED,
                searched_at=self._clock(),
                error_message="已取消本轮联网搜索。",
                attempt_count=attempts,
                query_count=1,
            )
            self._audit(account_id, plan, result)
            return result

        result = self._project_results(plan, results, attempts)
        if result.status in {WebSearchStatus.SUCCESS, WebSearchStatus.PARTIAL}:
            expires_at = self._clock() + timedelta(
                seconds=plan.freshness_window_seconds
            )
            result = result.model_copy(update={"cache_expires_at": expires_at})
            self._cache.put(account_id, plan, result, expires_at)
        self._audit(account_id, plan, result)
        return result

    def _project_results(
        self,
        plan: SearchPlan,
        results: list[WebSearchResult],
        attempts: int,
    ) -> WebSearchProjection:
        conflicting = [
            result
            for result in results
            if getattr(result, "verification", "verified") == "conflicting"
        ]
        if conflicting:
            return self._projection(
                plan,
                status=WebSearchStatus.SOURCE_CONFLICT,
                results=results,
                searched_at=self._clock(),
                error_code="web_search_source_conflict",
                error_message="多个公开来源对当前事实给出冲突信息，暂不能形成确定结论。",
                can_retry=True,
                attempt_count=attempts,
                query_count=1,
            )
        verified = [
            result
            for result in results
            if getattr(result, "verification", "verified") in {"verified", "cross_verified"}
        ]
        failed = [
            result
            for result in results
            if getattr(result, "verification", "verified") == "fetch_failed"
        ]
        if not results:
            return self._projection(
                plan,
                status=WebSearchStatus.EMPTY,
                searched_at=self._clock(),
                error_code="web_search_no_results",
                error_message="没有找到可核实的公开网页结果。",
                can_retry=True,
                attempt_count=attempts,
                query_count=1,
            )
        if not verified:
            code = "web_search_page_fetch" if failed else "web_search_evidence_insufficient"
            message = (
                "搜索结果对应的页面无法抓取，尚未完成本次联网核验。"
                if failed
                else "当前只有搜索摘要，尚未获得可核验的来源页面。"
            )
            return self._projection(
                plan,
                status=(WebSearchStatus.FETCH_ERROR if failed else WebSearchStatus.EVIDENCE_INSUFFICIENT),
                results=results,
                searched_at=self._clock(),
                error_code=code,
                error_message=message,
                can_retry=True,
                attempt_count=attempts,
                query_count=1,
            )
        status = WebSearchStatus.PARTIAL if failed else WebSearchStatus.SUCCESS
        return self._projection(
            plan,
            status=status,
            results=results,
            searched_at=self._clock(),
            error_message=None,
            can_retry=False,
            attempt_count=attempts,
            query_count=1,
        )

    @staticmethod
    def _error_status(code: str, permission: bool) -> WebSearchStatus:
        if permission:
            return WebSearchStatus.PERMISSION
        if code.startswith("web_search_page_"):
            return WebSearchStatus.FETCH_ERROR
        return WebSearchStatus.ERROR

    def _timeout_projection(
        self, plan: SearchPlan, attempts: int
    ) -> WebSearchProjection:
        return self._projection(
            plan,
            status=WebSearchStatus.ERROR,
            searched_at=self._clock(),
            error_code="web_search_timeout",
            error_message="联网搜索超时，请重试。",
            can_retry=True,
            attempt_count=attempts,
            query_count=1 if attempts else 0,
        )

    @staticmethod
    def _projection(
        plan: SearchPlan,
        *,
        status: WebSearchStatus,
        results: list[WebSearchResult] | None = None,
        searched_at: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        can_retry: bool = False,
        can_cancel: bool = False,
        attempt_count: int = 0,
        query_count: int = 0,
        cache_expires_at: datetime | None = None,
    ) -> WebSearchProjection:
        return WebSearchProjection(
            status=status,
            trigger_reason=plan.reason,
            query_summary=plan.query,
            results=results or [],
            searched_at=searched_at,
            error_code=error_code,
            error_message=error_message,
            can_retry=can_retry,
            can_cancel=can_cancel,
            plan_id=plan.plan_id,
            provider=plan.provider,
            provider_version=plan.provider_version,
            rules_version=plan.rules_version,
            query_hash=plan.query_hash,
            original_query_hash=plan.original_query_hash,
            freshness_window_seconds=plan.freshness_window_seconds,
            cache_expires_at=cache_expires_at,
            attempt_count=attempt_count,
            query_count=query_count,
            deleted_categories=list(plan.deleted_categories),
        )

    def _audit(
        self, account_id: str, plan: SearchPlan, result: WebSearchProjection
    ) -> None:
        if self._observability is None:
            return
        if result.status in {WebSearchStatus.SUCCESS, WebSearchStatus.PARTIAL}:
            audit_result = AuditResult.SUCCESS
        elif result.status == WebSearchStatus.CANCELLED:
            audit_result = AuditResult.BLOCKED
        elif result.status == WebSearchStatus.PERMISSION:
            audit_result = AuditResult.DENIED
        elif result.status in {
            WebSearchStatus.EMPTY,
            WebSearchStatus.EVIDENCE_INSUFFICIENT,
            WebSearchStatus.FETCH_ERROR,
        }:
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
                "authorization_snapshot": "authz-1.0",
                "provider": plan.provider,
                "provider_version": plan.provider_version,
                "rules_version": plan.rules_version,
                "plan_id": plan.plan_id,
                "query_hash": plan.query_hash,
                "original_query_hash": plan.original_query_hash,
                "deleted_categories": list(plan.deleted_categories),
                "query_length": len(plan.query),
                "result_count": len(result.results),
                "attempt_count": result.attempt_count,
                "query_count": result.query_count,
                "cache_hit": result.cache_hit,
                "status": result.status.value,
            },
        )
