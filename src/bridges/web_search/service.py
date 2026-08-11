"""公网搜索编排：本地触发判断、查询脱敏、缓存、结果投影与审计。"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from inspect import Parameter, signature
from threading import Event, RLock
from typing import Any, Protocol, cast

from bridges.contracts.chat import ChatMode
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.observability.service import ObservabilityService
from bridges.web_search.client import (
    DEFAULT_PROVIDER_COOLDOWN_SECONDS,
    DUCKDUCKGO_REQUEST_PROFILE_VERSION,
    DUCKDUCKGO_PROVIDER_VERSION,
    DuckDuckGoClient,
    WebSearchError,
)
from bridges.web_search.contracts import (
    WebSearchHealth,
    WebSearchHealthStatus,
    WebSearchHealthSummary,
    WebSearchProjection,
    WebSearchPageClassification,
    WebSearchProviderAttempt,
    WebSearchResult,
    WebSearchStatus,
    aggregate_public_search_health,
)

WEB_SEARCH_RULES_VERSION = "web-search-plan-v2"
DEFAULT_PROVIDER = "duckduckgo"
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
_FRESH_CACHE_TTL_SECONDS = 60 * 60
_CURRENT_CACHE_TTL_SECONDS = 15 * 60
FALLBACK_RESERVE_SECONDS = 2.0
FALLBACK_MIN_BUDGET_SECONDS = 0.25
_PRIMARY_FAILOVER_ERROR_CODES = frozenset(
    {
        "web_search_provider_challenge",
        "web_search_rate_limit",
        "web_search_dns",
        "web_search_offline",
        "web_search_connect",
        "web_search_timeout",
        "web_search_parse",
        "web_search_response_too_large",
        "web_search_redirect",
        "web_search_provider",
    }
)


class _SearchStopSignal:
    """搜索内部停止信号：不把预算到期误写成用户取消。"""

    def __init__(self, parent: Event | None) -> None:
        self._parent = parent
        self._local = Event()

    def is_set(self) -> bool:
        return self._local.is_set() or bool(
            self._parent is not None and self._parent.is_set()
        )

    def user_is_set(self) -> bool:
        return bool(self._parent is not None and self._parent.is_set())

    def set(self) -> None:
        self._local.set()


@dataclass(frozen=True)
class SearchPlan:
    """本地生成并可在网络请求前持久化的最小公网查询计划。"""

    should_search: bool
    query: str
    reason: str
    queries: tuple[str, ...] = ()
    plan_id: str = ""
    provider: str = DEFAULT_PROVIDER
    provider_version: str = DUCKDUCKGO_PROVIDER_VERSION
    rules_version: str = WEB_SEARCH_RULES_VERSION
    query_hash: str = ""
    original_query_hash: str = ""
    freshness_window_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    deleted_categories: tuple[str, ...] = ()
    max_queries: int = 3
    max_results: int = 5
    max_retries: int = 1
    total_timeout_seconds: float = 8.0
    max_response_bytes: int = 1_000_000
    max_redirects: int = 2

    def __post_init__(self) -> None:
        max_queries = max(1, min(self.max_queries, 4))
        raw_queries = tuple(query.strip() for query in self.queries if query.strip())
        if not raw_queries and self.query.strip():
            raw_queries = (self.query.strip(),)
        bounded_queries = raw_queries[:max_queries]
        primary_query = bounded_queries[0] if bounded_queries else ""
        object.__setattr__(self, "max_queries", max_queries)
        object.__setattr__(self, "queries", bounded_queries)
        object.__setattr__(self, "query", primary_query)
        query_material = "\x1f".join(bounded_queries)
        query_hash = self.query_hash or hashlib.sha256(
            query_material.encode("utf-8")
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
            tuple[str, str, str, str, int], tuple[datetime, WebSearchProjection]
        ] = {}
        self._lock = RLock()

    @staticmethod
    def _key(
        account_id: str,
        query_hash: str,
        provider: str,
        provider_version: str,
        freshness_window_seconds: int,
    ) -> tuple[str, str, str, str, int]:
        return (
            account_id,
            query_hash,
            provider,
            provider_version,
            freshness_window_seconds,
        )

    def get(
        self, account_id: str, plan: SearchPlan, now: datetime
    ) -> WebSearchProjection | None:
        with self._lock:
            key = self._key(
                account_id,
                plan.query_hash,
                plan.provider,
                plan.provider_version,
                plan.freshness_window_seconds,
            )
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, projection = entry
            if expires_at <= now:
                self._entries.pop(key, None)
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
            self._entries[
                self._key(
                    account_id,
                    plan.query_hash,
                    projection.provider,
                    projection.provider_version,
                    plan.freshness_window_seconds,
                )
            ] = (expires_at, projection)


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
        queries = self._query_variants(query)
        freshness_window = self._freshness_window(content, fresh=fresh)
        return SearchPlan(
            should_search,
            query if should_search else "",
            reason,
            queries=queries if should_search else (),
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
    def _query_variants(query: str) -> tuple[str, ...]:
        """在脱敏查询上增加有限的侧重点，不重新引入用户原文。"""

        if not query:
            return ("公开信息",)
        focus_terms = ("核心概念 原理", "应用 示例", "常见误区 限制")
        normalized_query = " ".join(query.split())
        variants = [query]
        for focus in focus_terms:
            query_prefix = normalized_query[: max(0, 79 - len(focus))].rstrip()
            candidate = f"{query_prefix} {focus}"[:80].strip()
            if candidate not in variants:
                variants.append(candidate)
        return tuple(variants[:3])

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
        fallback_client: SearchClient | None = None,
        fallback_provider: str = "brave_search",
        fallback_provider_version: str = "brave-search-api-v1",
        fallback_reserve_seconds: float = FALLBACK_RESERVE_SECONDS,
        planner: LocalQueryPlanner | None = None,
        observability: ObservabilityService | None = None,
        cache: WebSearchCache | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client or DuckDuckGoClient()
        self._fallback_client = fallback_client
        self._fallback_provider = fallback_provider
        self._fallback_provider_version = fallback_provider_version
        self._fallback_reserve_seconds = max(0.0, fallback_reserve_seconds)
        self._planner = planner or LocalQueryPlanner()
        self._observability = observability
        self._cache = cache or InMemoryWebSearchCache()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._provider_cooldown_until: datetime | None = None
        self._provider_state_lock = RLock()

    def plan(
        self,
        content: str,
        mode: ChatMode,
        *,
        force: bool = False,
    ) -> SearchPlan:
        return self._planner.plan(content, mode, force=force)

    def health_check(self) -> WebSearchHealthSummary:
        """检查已登记来源，并按至少一个来源就绪聚合整体状态。"""

        clients = [(self._client, DEFAULT_PROVIDER, DUCKDUCKGO_PROVIDER_VERSION)]
        if self._fallback_client is not None:
            clients.append(
                (
                    self._fallback_client,
                    self._fallback_provider,
                    self._fallback_provider_version,
                )
            )
        health: list[WebSearchHealth] = []
        for client, provider, provider_version in clients:
            checker = getattr(client, "health_check", None)
            if not callable(checker):
                health.append(
                    WebSearchHealth(
                        provider=provider,
                        provider_version=provider_version,
                        status=WebSearchHealthStatus.UPSTREAM_ERROR,
                        checked_at=datetime.now(UTC),
                        error_code="web_search_health_check",
                    )
                )
                continue
            try:
                result = checker()
            except Exception:  # noqa: BLE001 - 单一健康检查失败不影响备用来源
                result = WebSearchHealth(
                    provider=provider,
                    provider_version=provider_version,
                    status=WebSearchHealthStatus.UPSTREAM_ERROR,
                    checked_at=datetime.now(UTC),
                    error_code="web_search_health_check",
                )
            health.append(
                result.model_copy(
                    update={
                        "provider": provider,
                        "provider_version": provider_version,
                    }
                )
            )
        return aggregate_public_search_health(health)

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
        deadline: float | None = None,
    ) -> WebSearchProjection | None:
        if not plan.should_search:
            return None
        started = time.monotonic()
        if _user_cancelled(stop_event):
            result = self._projection(
                plan,
                status=WebSearchStatus.CANCELLED,
                error_message="已取消本轮联网搜索。",
            )
            self._audit(
                account_id,
                plan,
                result,
                duration_ms=_elapsed_ms(started),
            )
            return result
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
            self._audit(account_id, plan, cached, duration_ms=_elapsed_ms(started))
            return cached
        if _user_cancelled(stop_event):
            result = self._projection(
                plan,
                status=WebSearchStatus.CANCELLED,
                error_message="已取消本轮联网搜索。",
            )
            self._audit(account_id, plan, result, duration_ms=_elapsed_ms(started))
            return result

        cooldown_until = self._active_provider_cooldown(now)
        if self._fallback_client is not None:
            return self._search_with_fallback(
                account_id,
                plan,
                stop_event=stop_event,
                deadline=deadline,
                started=started,
                cooldown_until=cooldown_until,
            )
        if cooldown_until is not None:
            result = self._projection(
                plan,
                status=WebSearchStatus.ERROR,
                searched_at=now,
                error_code="web_search_provider_challenge",
                error_message=(
                    "DuckDuckGo 搜索提供方暂时受阻，正在冷却；请稍后显式重试，"
                    "系统不会在本轮自动重复请求。"
                ),
                can_retry=True,
                page_classification=WebSearchPageClassification.CHALLENGE,
                cooldown_until=cooldown_until,
            )
            self._audit(account_id, plan, result, duration_ms=_elapsed_ms(started))
            return result

        plan_deadline = time.monotonic() + max(0.0, plan.total_timeout_seconds)
        deadline = (
            plan_deadline
            if deadline is None
            else min(deadline, plan_deadline)
        )
        attempts = 0
        query_count = 0
        query_history: list[str] = []
        queries = plan.queries or ((plan.query,) if plan.query else ())
        results: list[WebSearchResult] = []
        errors: list[BaseException] = []
        page_classification: WebSearchPageClassification | None = None
        http_status_category: str | None = None
        same_query_retries = 0
        rewrite_attempted = False
        while queries:
            if _user_cancelled(stop_event):
                result = self._projection(
                    plan,
                    status=WebSearchStatus.CANCELLED,
                    searched_at=self._clock(),
                    error_message="已取消本轮联网搜索。",
                    attempt_count=attempts,
                    query_count=query_count,
                    query_history=query_history,
                )
                self._audit(
                    account_id,
                    plan,
                    result,
                    duration_ms=_elapsed_ms(started),
                    deadline=deadline,
                )
                return result
            if time.monotonic() >= deadline:
                result = self._timeout_projection(
                    plan,
                    attempts,
                    query_count=query_count,
                    query_history=query_history,
                )
                self._audit(
                    account_id,
                    plan,
                    result,
                    duration_ms=_elapsed_ms(started),
                    deadline=deadline,
                )
                return result
            attempts += 1
            (
                round_results,
                round_errors,
                sent_count,
                round_page_classifications,
                round_http_status_categories,
            ) = self._run_queries(
                queries,
                deadline=deadline,
                stop_event=stop_event,
            )
            query_count += sent_count
            query_history.extend(queries[:sent_count])
            if round_page_classifications:
                page_classification = round_page_classifications[-1]
            if round_http_status_categories:
                http_status_category = round_http_status_categories[-1]
            queries = queries[sent_count:]
            results = self._merge_results(results, round_results)
            errors = round_errors
            if results:
                break
            if _user_cancelled(stop_event):
                break
            challenge_error = next(
                (
                    error
                    for error in errors
                    if isinstance(error, WebSearchError)
                    and error.code == "web_search_provider_challenge"
                ),
                None,
            )
            if challenge_error is not None:
                cooldown_until = self._activate_provider_cooldown(
                    self._clock(),
                    challenge_error.cooldown_seconds
                    or DEFAULT_PROVIDER_COOLDOWN_SECONDS,
                )
                result = self._projection(
                    plan,
                    status=WebSearchStatus.ERROR,
                    searched_at=self._clock(),
                    error_code=challenge_error.code,
                    error_message=challenge_error.message,
                    can_retry=True,
                    attempt_count=attempts,
                    query_count=query_count,
                    query_history=query_history,
                    page_classification=(
                        challenge_error.page_classification
                        or WebSearchPageClassification.CHALLENGE
                    ),
                    http_status_category=challenge_error.http_status_category,
                    cooldown_until=cooldown_until,
                )
                self._audit(
                    account_id,
                    plan,
                    result,
                    duration_ms=_elapsed_ms(started),
                    deadline=deadline,
                )
                return result
            if queries and not errors:
                continue
            retryable_errors = any(
                not isinstance(error, WebSearchError) or error.retryable
                for error in errors
            )
            if (
                errors
                and retryable_errors
                and same_query_retries < max(0, plan.max_retries)
            ):
                same_query_retries += 1
                queries = plan.queries or ((plan.query,) if plan.query else ())
                continue
            if (
                not rewrite_attempted
                and plan.max_retries > 0
                and (not errors or retryable_errors)
            ):
                rewrite = self._rewrite_query(plan, query_history)
                if rewrite is not None:
                    rewrite_attempted = True
                    queries = (rewrite,)
                    continue
            break
        if time.monotonic() >= deadline and not results:
            result = self._timeout_projection(
                plan,
                attempts,
                query_count=query_count,
                query_history=query_history,
            )
            self._audit(
                account_id,
                plan,
                result,
                duration_ms=_elapsed_ms(started),
                deadline=deadline,
            )
            return result
        if _user_cancelled(stop_event):
            result = self._projection(
                plan,
                status=WebSearchStatus.CANCELLED,
                searched_at=self._clock(),
                error_message="已取消本轮联网搜索。",
                attempt_count=attempts,
                query_count=query_count,
                query_history=query_history,
            )
            self._audit(
                account_id,
                plan,
                result,
                duration_ms=_elapsed_ms(started),
                deadline=deadline,
            )
            return result
        if not results and errors:
            error = next(
                (item for item in errors if isinstance(item, WebSearchError)), None
            )
            if error is not None:
                result = self._projection(
                    plan,
                    status=self._error_status(error.code, error.permission),
                    searched_at=self._clock(),
                    error_code=error.code,
                    error_message=error.message,
                    can_retry=error.retryable,
                    attempt_count=attempts,
                    query_count=query_count,
                    query_history=query_history,
                    page_classification=error.page_classification,
                    http_status_category=error.http_status_category,
                )
            else:
                result = self._projection(
                    plan,
                    status=WebSearchStatus.ERROR,
                    searched_at=self._clock(),
                    error_code="web_search_provider",
                    error_message="公网搜索提供方暂时不可用，请稍后重试。",
                    can_retry=True,
                    attempt_count=attempts,
                    query_count=query_count,
                    query_history=query_history,
                )
        else:
            result = self._project_results(
                plan,
                results,
                attempts,
                query_count=query_count,
                query_history=query_history,
                page_classification=page_classification,
                http_status_category=http_status_category,
            )
        if result.status in {WebSearchStatus.SUCCESS, WebSearchStatus.PARTIAL}:
            expires_at = self._clock() + timedelta(
                seconds=plan.freshness_window_seconds
            )
            result = result.model_copy(update={"cache_expires_at": expires_at})
            self._cache.put(account_id, plan, result, expires_at)
        self._audit(
            account_id,
            plan,
            result,
            duration_ms=_elapsed_ms(started),
            deadline=deadline,
        )
        return result

    def _run_queries(
        self,
        queries: tuple[str, ...],
        *,
        deadline: float,
        stop_event: Event | None,
    ) -> tuple[
        list[WebSearchResult],
        list[BaseException],
        int,
        list[WebSearchPageClassification],
        list[str],
    ]:
        """在阶段预算内并行执行一组最小查询。"""

        if _user_cancelled(stop_event):
            return [], [], 0, [], []
        request_queries = (
            queries[:1] if isinstance(self._client, DuckDuckGoClient) else queries
        )
        query_stop_event = _SearchStopSignal(stop_event)
        executor = ThreadPoolExecutor(
            max_workers=min(len(request_queries), 4),
            thread_name_prefix="web-search-query",
        )
        futures = {
            executor.submit(self._search_one, query, deadline, query_stop_event): query
            for query in request_queries
        }
        pending = set(futures)
        done: set[Any] = set()
        try:
            while pending:
                if _user_cancelled(stop_event):
                    query_stop_event.set()
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    query_stop_event.set()
                    break
                completed, _ = wait(
                    pending,
                    timeout=min(0.05, remaining),
                    return_when=ALL_COMPLETED,
                )
                done.update(completed)
                pending.difference_update(completed)
            results: list[WebSearchResult] = []
            errors: list[BaseException] = []
            page_classifications: list[WebSearchPageClassification] = []
            http_status_categories: list[str] = []
            for future in futures:
                if future not in done:
                    errors.append(
                        WebSearchError("web_search_timeout", "联网搜索超时，请重试。")
                    )
                    continue
                try:
                    value = future.result()
                except Exception as exc:  # noqa: BLE001 - 单查询失败不拖垮整轮
                    errors.append(exc)
                else:
                    results.extend(value)
                    classification = getattr(value, "page_classification", None)
                    if classification is not None:
                        page_classifications.append(classification)
                    status_category = getattr(value, "http_status_category", None)
                    if status_category is not None:
                        http_status_categories.append(status_category)
            return (
                results,
                errors,
                len(request_queries),
                page_classifications,
                http_status_categories,
            )
        finally:
            for future in pending:
                future.cancel()
            if pending:
                completed, _ = wait(pending, timeout=0.5)
                pending.difference_update(completed)
            executor.shutdown(wait=not pending, cancel_futures=True)

    def _search_with_fallback(
        self,
        account_id: str,
        plan: SearchPlan,
        *,
        stop_event: Event | None,
        deadline: float | None,
        started: float,
        cooldown_until: datetime | None,
    ) -> WebSearchProjection:
        """在共享公网阶段内执行一次主用到备用的有界切换。"""

        stage_deadline = time.monotonic() + max(0.0, plan.total_timeout_seconds)
        if deadline is not None:
            stage_deadline = min(stage_deadline, deadline)
        attempts = 0
        query_count = 0
        query_history: list[str] = []
        provider_attempts: list[WebSearchProviderAttempt] = []
        primary_provider = self._provider_name(self._client, plan.provider)
        primary_version = self._provider_version(
            self._client, plan.provider_version
        )
        primary_classification: WebSearchPageClassification | None = None
        primary_status_category: str | None = None
        primary_error: BaseException | None = None

        if _user_cancelled(stop_event):
            return self._finish_search_result(
                account_id,
                plan,
                self._projection(
                    plan,
                    status=WebSearchStatus.CANCELLED,
                    error_message="已取消本轮联网搜索。",
                    provider_attempts=provider_attempts,
                ),
                started=started,
                deadline=stage_deadline,
            )

        # 主用只使用保留窗口之前的预算；主用挂起时备用仍有非零窗口。
        primary_budget = max(
            0.0,
            stage_deadline
            - time.monotonic()
            - max(self._fallback_reserve_seconds, FALLBACK_MIN_BUDGET_SECONDS),
        )
        primary_deadline = min(stage_deadline, time.monotonic() + primary_budget)
        if cooldown_until is not None:
            provider_attempts.append(
                WebSearchProviderAttempt(
                    provider=primary_provider,
                    provider_version=primary_version,
                    result_code="web_search_provider_challenge",
                    page_classification=WebSearchPageClassification.CHALLENGE,
                )
            )
        elif primary_budget > 0 and plan.query:
            attempts += 1
            query_count += 1
            query_history.append(plan.query)
            primary_started = time.monotonic()
            primary_value, primary_error = self._call_provider_with_deadline(
                self._client,
                plan.query,
                deadline=primary_deadline,
                stop_event=stop_event,
            )
            primary_classification = getattr(primary_value, "page_classification", None)
            primary_status_category = getattr(primary_value, "http_status_category", None)
            if isinstance(primary_error, WebSearchError):
                primary_classification = primary_error.page_classification
                primary_status_category = primary_error.http_status_category
            primary_results = self._annotate_results(
                primary_value, primary_provider, primary_version
            )
            if (
                primary_error is None
                and primary_classification == WebSearchPageClassification.CHALLENGE
            ):
                primary_error = WebSearchError(
                    "web_search_provider_challenge",
                    "DuckDuckGo 搜索提供方返回挑战页。",
                    page_classification=WebSearchPageClassification.CHALLENGE,
                    http_status_category=primary_status_category,
                )
            elif (
                primary_error is None
                and primary_classification == WebSearchPageClassification.INVALID
            ):
                primary_error = WebSearchError(
                    "web_search_parse",
                    "DuckDuckGo 搜索返回无效响应。",
                    page_classification=WebSearchPageClassification.INVALID,
                    http_status_category=primary_status_category,
                )
            provider_attempts.append(
                self._provider_attempt(
                    primary_provider,
                    primary_version,
                    primary_results,
                    primary_error,
                    primary_value,
                    duration_ms=_elapsed_ms(primary_started),
                )
            )
            if primary_results and primary_error is None:
                return self._finish_search_result(
                    account_id,
                    plan,
                    self._project_results(
                        plan,
                        primary_results,
                        attempts,
                        query_count=query_count,
                        query_history=query_history,
                        page_classification=primary_classification,
                        http_status_category=primary_status_category,
                        provider=primary_provider,
                        provider_version=primary_version,
                        selected_provider=primary_provider,
                        selected_provider_version=primary_version,
                        provider_attempts=provider_attempts,
                    ),
                    started=started,
                    deadline=stage_deadline,
                )

            # 真实空结果最多进行一次有界改写；挑战、限流、超时和解析错误
            # 直接进入备用源，避免同一 HTML 提供方重复制造压力。
            if primary_error is None and primary_classification in {
                None,
                WebSearchPageClassification.NORMAL_EMPTY,
            }:
                rewrite = self._rewrite_query(plan, query_history)
                if rewrite is not None and time.monotonic() < primary_deadline:
                    attempts += 1
                    query_count += 1
                    query_history.append(rewrite)
                    rewrite_started = time.monotonic()
                    rewrite_value, rewrite_error = self._call_provider_with_deadline(
                        self._client,
                        rewrite,
                        deadline=primary_deadline,
                        stop_event=stop_event,
                    )
                    rewrite_classification = getattr(
                        rewrite_value, "page_classification", None
                    )
                    rewrite_status_category = getattr(
                        rewrite_value, "http_status_category", None
                    )
                    if isinstance(rewrite_error, WebSearchError):
                        rewrite_classification = rewrite_error.page_classification
                        rewrite_status_category = rewrite_error.http_status_category
                    rewrite_results = self._annotate_results(
                        rewrite_value, primary_provider, primary_version
                    )
                    provider_attempts.append(
                        self._provider_attempt(
                            primary_provider,
                            primary_version,
                            rewrite_results,
                            rewrite_error,
                            rewrite_value,
                            duration_ms=_elapsed_ms(rewrite_started),
                        )
                    )
                    if rewrite_results:
                        return self._finish_search_result(
                            account_id,
                            plan,
                            self._project_results(
                                plan,
                                rewrite_results,
                                attempts,
                                query_count=query_count,
                                query_history=query_history,
                                page_classification=rewrite_classification,
                                http_status_category=rewrite_status_category,
                                provider=primary_provider,
                                provider_version=primary_version,
                                selected_provider=primary_provider,
                                selected_provider_version=primary_version,
                                provider_attempts=provider_attempts,
                            ),
                            started=started,
                            deadline=stage_deadline,
                        )
                    primary_error = rewrite_error
                    primary_classification = rewrite_classification
                    primary_status_category = rewrite_status_category
            if isinstance(primary_error, WebSearchError) and (
                primary_error.code == "web_search_provider_challenge"
            ):
                cooldown_until = self._activate_provider_cooldown(
                    self._clock(),
                    primary_error.cooldown_seconds
                    or DEFAULT_PROVIDER_COOLDOWN_SECONDS,
                )

            if isinstance(primary_error, WebSearchError) and not self._can_failover(
                primary_error
            ):
                return self._finish_search_result(
                    account_id,
                    plan,
                    self._projection(
                        plan,
                        status=(
                            WebSearchStatus.CANCELLED
                            if primary_error.code == "web_search_cancelled"
                            else self._error_status(
                                primary_error.code, primary_error.permission
                            )
                        ),
                        searched_at=self._clock(),
                        error_code=primary_error.code,
                        error_message=primary_error.message,
                        can_retry=primary_error.retryable,
                        attempt_count=attempts,
                        query_count=query_count,
                        query_history=query_history,
                        page_classification=primary_error.page_classification,
                        http_status_category=primary_error.http_status_category,
                        provider_attempts=provider_attempts,
                    ),
                    started=started,
                    deadline=stage_deadline,
                )

        if _user_cancelled(stop_event):
            return self._finish_search_result(
                account_id,
                plan,
                self._projection(
                    plan,
                    status=WebSearchStatus.CANCELLED,
                    searched_at=self._clock(),
                    error_message="已取消本轮联网搜索。",
                    attempt_count=attempts,
                    query_count=query_count,
                    query_history=query_history,
                    provider_attempts=provider_attempts,
                ),
                started=started,
                deadline=stage_deadline,
            )

        remaining = stage_deadline - time.monotonic()
        if remaining < FALLBACK_MIN_BUDGET_SECONDS:
            return self._finish_search_result(
                account_id,
                plan,
                self._projection(
                    plan,
                    status=WebSearchStatus.ERROR,
                    searched_at=self._clock(),
                    error_code="web_search_fallback_not_started",
                    error_message=(
                        "主用搜索已耗尽备用源保留窗口，本轮未启动备用源；请稍后重试。"
                    ),
                    can_retry=True,
                    attempt_count=attempts,
                    query_count=query_count,
                    query_history=query_history,
                    page_classification=primary_classification,
                    http_status_category=primary_status_category,
                    provider_attempts=provider_attempts,
                ),
                started=started,
                deadline=stage_deadline,
            )

        fallback_started = time.monotonic()
        fallback_query = plan.query
        fallback_value: list[WebSearchResult] = []
        fallback_results: list[WebSearchResult] = []
        fallback_error: BaseException | None = None
        if fallback_query:
            attempts += 1
            query_count += 1
            query_history.append(fallback_query)
            fallback_value, fallback_error = self._call_provider_with_deadline(
                self._fallback_client,
                fallback_query,
                deadline=stage_deadline,
                stop_event=stop_event,
            )
            fallback_results = self._annotate_results(
                fallback_value,
                self._fallback_provider_name(),
                self._fallback_provider_version_value(),
            )
            provider_attempts.append(
                self._provider_attempt(
                    self._fallback_provider_name(),
                    self._fallback_provider_version_value(),
                    fallback_results,
                    fallback_error,
                    fallback_value,
                    duration_ms=_elapsed_ms(fallback_started),
                )
            )
        if fallback_results:
            return self._finish_search_result(
                account_id,
                plan,
                self._project_results(
                    plan,
                    fallback_results,
                    attempts,
                    query_count=query_count,
                    query_history=query_history,
                    page_classification=getattr(
                        fallback_value, "page_classification", None
                    ),
                    http_status_category=getattr(
                        fallback_value, "http_status_category", None
                    ),
                    provider=self._fallback_provider_name(),
                    provider_version=self._fallback_provider_version_value(),
                    selected_provider=self._fallback_provider_name(),
                    selected_provider_version=self._fallback_provider_version_value(),
                    cooldown_until=cooldown_until,
                    provider_attempts=provider_attempts,
                ),
                started=started,
                deadline=stage_deadline,
            )

        error_message = (
            f"{primary_provider} 与 {self._fallback_provider_name()} 备用提供方均未完成，"
            "请稍后重试。"
        )
        return self._finish_search_result(
            account_id,
            plan,
            self._projection(
                plan,
                status=WebSearchStatus.ERROR,
                searched_at=self._clock(),
                error_code="web_search_all_providers_failed",
                error_message=error_message,
                can_retry=True,
                attempt_count=attempts,
                query_count=query_count,
                query_history=query_history,
                page_classification=(
                    getattr(fallback_error, "page_classification", None)
                    or primary_classification
                ),
                http_status_category=(
                    getattr(fallback_error, "http_status_category", None)
                    or primary_status_category
                ),
                provider_attempts=provider_attempts,
            ),
            started=started,
            deadline=stage_deadline,
        )

    @staticmethod
    def _provider_name(client: Any, fallback: str) -> str:
        return str(getattr(client, "provider_name", fallback))

    @staticmethod
    def _provider_version(client: Any, fallback: str) -> str:
        return str(getattr(client, "provider_version", fallback))

    def _fallback_provider_name(self) -> str:
        return self._fallback_provider

    def _fallback_provider_version_value(self) -> str:
        return self._fallback_provider_version

    @staticmethod
    def _can_failover(error: WebSearchError) -> bool:
        return error.code in _PRIMARY_FAILOVER_ERROR_CODES

    @staticmethod
    def _annotate_results(
        results: list[WebSearchResult], provider: str, provider_version: str
    ) -> list[WebSearchResult]:
        return [
            result.model_copy(
                update={"provider": provider, "provider_version": provider_version}
            )
            for result in results
        ]

    @staticmethod
    def _call_provider(
        client: SearchClient | None,
        query: str,
        *,
        deadline: float,
        stop_event: Any | None,
    ) -> tuple[list[WebSearchResult], BaseException | None]:
        if client is None:
            return [], WebSearchError(
                "web_search_fallback_not_configured", "备用公网搜索未配置。"
            )
        try:
            return (
                _invoke_search_client(
                    client,
                    query,
                    deadline=deadline,
                    stop_event=stop_event,
                ),
                None,
            )
        except Exception as exc:  # noqa: BLE001 - 单一提供方失败交给备用编排
            return [], exc

    @classmethod
    def _call_provider_with_deadline(
        cls,
        client: SearchClient | None,
        query: str,
        *,
        deadline: float,
        stop_event: Event | None,
    ) -> tuple[list[WebSearchResult], BaseException | None]:
        """把适配器限制在绝对截止时间内，避免耗尽备用窗口。"""

        worker_signal = _SearchStopSignal(stop_event)
        executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="web-search-provider"
        )
        future = executor.submit(
            cls._call_provider,
            client,
            query,
            deadline=deadline,
            stop_event=worker_signal,
        )
        pending = {future}
        result: tuple[list[WebSearchResult], BaseException | None] | None = None
        terminal_error: BaseException | None = None
        try:
            while pending:
                if _user_cancelled(stop_event):
                    worker_signal.set()
                    terminal_error = WebSearchError(
                        "web_search_cancelled", "已取消本轮联网搜索。"
                    )
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    worker_signal.set()
                    terminal_error = WebSearchError(
                        "web_search_timeout", "联网搜索超时，请重试。"
                    )
                    break
                completed, _ = wait(
                    pending,
                    timeout=min(0.05, remaining),
                    return_when=ALL_COMPLETED,
                )
                if completed:
                    result = future.result()
                    pending.difference_update(completed)
            if result is not None:
                return result
            return [], terminal_error or WebSearchError(
                "web_search_provider", "公网搜索提供方暂时不可用，请稍后重试。"
            )
        finally:
            for pending_future in pending:
                pending_future.cancel()
            if pending:
                completed, _ = wait(pending, timeout=0.05)
                pending.difference_update(completed)
            executor.shutdown(wait=not pending, cancel_futures=True)

    @staticmethod
    def _provider_attempt(
        provider: str,
        provider_version: str,
        results: list[WebSearchResult],
        error: BaseException | None,
        raw_results: list[WebSearchResult],
        *,
        duration_ms: int,
    ) -> WebSearchProviderAttempt:
        if error is not None:
            result_code = getattr(error, "code", "web_search_provider")
            classification = getattr(error, "page_classification", None)
            status_category = getattr(error, "http_status_category", None)
        else:
            result_code = "success" if results else "web_search_no_results"
            classification = getattr(raw_results, "page_classification", None)
            status_category = getattr(raw_results, "http_status_category", None)
        return WebSearchProviderAttempt(
            provider=provider,
            provider_version=provider_version,
            result_code=result_code,
            result_count=len(results),
            duration_ms=duration_ms,
            http_status_category=status_category,
            page_classification=classification,
        )

    def _finish_search_result(
        self,
        account_id: str,
        plan: SearchPlan,
        result: WebSearchProjection,
        *,
        started: float,
        deadline: float,
    ) -> WebSearchProjection:
        if result.status in {WebSearchStatus.SUCCESS, WebSearchStatus.PARTIAL}:
            expires_at = self._clock() + timedelta(
                seconds=plan.freshness_window_seconds
            )
            result = result.model_copy(update={"cache_expires_at": expires_at})
            self._cache.put(account_id, plan, result, expires_at)
        self._audit(
            account_id,
            plan,
            result,
            duration_ms=_elapsed_ms(started),
            deadline=deadline,
        )
        return result

    def _search_one(
        self, query: str, deadline: float, stop_event: Any | None
    ) -> list[WebSearchResult]:
        return _invoke_search_client(
            self._client,
            query,
            deadline=deadline,
            stop_event=stop_event,
        )

    @staticmethod
    def _merge_results(
        existing: list[WebSearchResult], incoming: list[WebSearchResult]
    ) -> list[WebSearchResult]:
        merged = [*existing]
        seen_urls = {result.url for result in merged}
        seen_ids = {result.result_id for result in merged}
        for result in incoming:
            if result.url in seen_urls:
                continue
            if result.result_id in seen_ids:
                result = result.model_copy(update={"result_id": f"web-{len(merged) + 1}"})
            merged.append(result)
            seen_urls.add(result.url)
            seen_ids.add(result.result_id)
        return merged

    @staticmethod
    def _rewrite_query(plan: SearchPlan, history: list[str]) -> str | None:
        base = plan.query.strip()
        if not base:
            return None
        candidate = f"{' '.join(base.split()[:4])} 基础定义 原理"[:80].strip()
        return candidate if candidate not in history else None

    def _project_results(
        self,
        plan: SearchPlan,
        results: list[WebSearchResult],
        attempts: int,
        *,
        query_count: int,
        query_history: list[str],
        page_classification: WebSearchPageClassification | None = None,
        http_status_category: str | None = None,
        provider: str | None = None,
        provider_version: str | None = None,
        selected_provider: str | None = None,
        selected_provider_version: str | None = None,
        cooldown_until: datetime | None = None,
        provider_attempts: list[WebSearchProviderAttempt] | None = None,
    ) -> WebSearchProjection:
        resolved_provider = provider or plan.provider
        resolved_provider_version = provider_version or plan.provider_version
        provider_fields: dict[str, Any] = {
            "provider": resolved_provider,
            "provider_version": resolved_provider_version,
            "selected_provider": selected_provider or (resolved_provider if results else None),
            "selected_provider_version": selected_provider_version
            or (resolved_provider_version if results else None),
            "provider_attempts": provider_attempts,
        }
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
                query_count=query_count,
                query_history=query_history,
                page_classification=page_classification,
                http_status_category=http_status_category,
                cooldown_until=cooldown_until,
                **provider_fields,
            )
        verified = [
            result
            for result in results
            if getattr(result, "verification", "verified")
            in {"verified", "cross_verified", "structured"}
        ]
        failed = [
            result
            for result in results
            if getattr(result, "verification", "verified") == "fetch_failed"
        ]
        if not results:
            if page_classification == WebSearchPageClassification.NORMAL_RESULTS:
                return self._projection(
                    plan,
                    status=WebSearchStatus.EVIDENCE_INSUFFICIENT,
                    searched_at=self._clock(),
                    error_code="web_search_evidence_insufficient",
                    error_message="搜索页面包含结果节点，但没有可安全引用的公开来源。",
                    can_retry=True,
                    attempt_count=attempts,
                    query_count=query_count,
                    query_history=query_history,
                    page_classification=page_classification,
                    http_status_category=http_status_category,
                    cooldown_until=cooldown_until,
                    **provider_fields,
                )
            return self._projection(
                plan,
                status=WebSearchStatus.EMPTY,
                searched_at=self._clock(),
                error_code="web_search_no_results",
                error_message="没有找到可核实的公开网页结果。",
                can_retry=True,
                attempt_count=attempts,
                query_count=query_count,
                query_history=query_history,
                page_classification=page_classification,
                http_status_category=http_status_category,
                cooldown_until=cooldown_until,
                **provider_fields,
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
                status=(
                    WebSearchStatus.FETCH_ERROR
                    if failed
                    else WebSearchStatus.EVIDENCE_INSUFFICIENT
                ),
                results=results,
                searched_at=self._clock(),
                error_code=code,
                error_message=message,
                can_retry=True,
                attempt_count=attempts,
                query_count=query_count,
                query_history=query_history,
                page_classification=page_classification,
                http_status_category=http_status_category,
                cooldown_until=cooldown_until,
                **provider_fields,
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
            query_count=query_count,
            query_history=query_history,
            page_classification=page_classification,
            http_status_category=http_status_category,
            cooldown_until=cooldown_until,
            **provider_fields,
        )

    @staticmethod
    def _error_status(code: str, permission: bool) -> WebSearchStatus:
        if permission:
            return WebSearchStatus.PERMISSION
        if code.startswith("web_search_page_"):
            return WebSearchStatus.FETCH_ERROR
        return WebSearchStatus.ERROR

    def _timeout_projection(
        self,
        plan: SearchPlan,
        attempts: int,
        *,
        query_count: int,
        query_history: list[str],
    ) -> WebSearchProjection:
        return self._projection(
            plan,
            status=WebSearchStatus.ERROR,
            searched_at=self._clock(),
            error_code="web_search_timeout",
            error_message="联网搜索超时，请重试。",
            can_retry=True,
            attempt_count=attempts,
            query_count=query_count,
            query_history=query_history,
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
        query_history: list[str] | None = None,
        cache_expires_at: datetime | None = None,
        page_classification: WebSearchPageClassification | None = None,
        http_status_category: str | None = None,
        cooldown_until: datetime | None = None,
        provider: str | None = None,
        provider_version: str | None = None,
        selected_provider: str | None = None,
        selected_provider_version: str | None = None,
        provider_attempts: list[WebSearchProviderAttempt] | None = None,
    ) -> WebSearchProjection:
        rewrite_count = sum(
            query not in plan.queries for query in (query_history or [])
        )
        trigger_reason = plan.reason
        if rewrite_count:
            trigger_reason = f"{plan.reason}；已按阶段预算改写查询 {rewrite_count} 次"
        return WebSearchProjection(
            status=status,
            trigger_reason=trigger_reason,
            query_summary=plan.query,
            query_history=query_history or [],
            results=results or [],
            searched_at=searched_at,
            error_code=error_code,
            error_message=error_message,
            http_status_category=http_status_category,
            page_classification=page_classification,
            cooldown_until=cooldown_until,
            can_retry=can_retry,
            can_cancel=can_cancel,
            plan_id=plan.plan_id,
            provider=provider or plan.provider,
            provider_version=provider_version or plan.provider_version,
            selected_provider=selected_provider,
            selected_provider_version=selected_provider_version,
            provider_attempts=provider_attempts or [],
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
        self,
        account_id: str,
        plan: SearchPlan,
        result: WebSearchProjection,
        *,
        duration_ms: int = 0,
        deadline: float | None = None,
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
                "provider": result.provider,
                "provider_version": result.provider_version,
                "selected_provider": result.selected_provider,
                "selected_provider_version": result.selected_provider_version,
                "provider_attempts": [
                    attempt.model_dump(mode="json")
                    for attempt in result.provider_attempts
                ],
                "rules_version": plan.rules_version,
                "plan_id": plan.plan_id,
                "query_hash": plan.query_hash,
                "original_query_hash": plan.original_query_hash,
                "deleted_categories": list(plan.deleted_categories),
                "query_length": len(plan.query),
                "result_count": len(result.results),
                "attempt_count": result.attempt_count,
                "query_count": result.query_count,
                "query_history_hashes": [
                    hashlib.sha256(query.encode("utf-8")).hexdigest()
                    for query in result.query_history
                ],
                "query_history_count": len(result.query_history),
                "query_rewrite_count": sum(
                    query not in plan.queries for query in result.query_history
                ),
                "cache_hit": result.cache_hit,
                "status": result.status.value,
                "http_status_category": result.http_status_category,
                "page_classification": (
                    result.page_classification.value
                    if result.page_classification is not None
                    else None
                ),
                "request_profile_version": DUCKDUCKGO_REQUEST_PROFILE_VERSION,
                "cooldown_active": result.cooldown_until is not None,
                "stage_duration_ms": duration_ms,
                "active_sources": [
                    *dict.fromkeys(
                        attempt.provider for attempt in result.provider_attempts
                    )
                ]
                or [result.provider],
                "budget_source": "web_search",
                "deadline_remaining_ms": (
                    max(0, int((deadline - time.monotonic()) * 1000))
                    if deadline is not None
                    else None
                ),
            },
        )

    def _active_provider_cooldown(self, now: datetime) -> datetime | None:
        with self._provider_state_lock:
            cooldown_until = self._provider_cooldown_until
            if cooldown_until is None:
                return None
            if cooldown_until <= now:
                self._provider_cooldown_until = None
                return None
            return cooldown_until

    def _activate_provider_cooldown(
        self, now: datetime, seconds: int
    ) -> datetime:
        cooldown_until = now + timedelta(seconds=max(1, seconds))
        with self._provider_state_lock:
            if (
                self._provider_cooldown_until is None
                or cooldown_until > self._provider_cooldown_until
            ):
                self._provider_cooldown_until = cooldown_until
            return self._provider_cooldown_until


def _remaining_seconds(deadline: float) -> float:
    return max(0.001, deadline - time.monotonic())


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _user_cancelled(stop_event: Event | None) -> bool:
    """区分用户取消与编排器为截止时间发出的内部停止信号。"""
    if stop_event is None:
        return False
    marker = getattr(stop_event, "user_is_set", None)
    return bool(marker()) if callable(marker) else stop_event.is_set()


def _invoke_search_client(
    client: Any,
    query: str,
    *,
    deadline: float,
    stop_event: Event | None,
) -> list[WebSearchResult]:
    """把同一绝对截止时间传给客户端，同时兼容旧测试替身。"""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")
    search = client.search
    try:
        parameters = tuple(signature(search).parameters.values())
    except (TypeError, ValueError):
        parameters = ()
        accepts_kwargs = True
    else:
        accepts_kwargs = any(
            parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters
        )
    names = {
        parameter.name
        for parameter in parameters
        if parameter.kind
        in {Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY}
    }
    kwargs: dict[str, Any] = {}
    if accepts_kwargs or "timeout" in names:
        kwargs["timeout"] = remaining
    if accepts_kwargs or "deadline" in names:
        kwargs["deadline"] = deadline
    if accepts_kwargs or "stop_event" in names:
        kwargs["stop_event"] = stop_event
    return cast(list[WebSearchResult], search(query, **kwargs))
