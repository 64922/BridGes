"""本地查询计划、缓存和输入脱敏。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Protocol

from bridges import public_search_budget as search_budget
from bridges.contracts.chat import ChatMode
from bridges.web_search.contracts import WebSearchProjection, WebSearchResult
from bridges.web_search.tavily_support import TAVILY_SEARCH_PROVIDER_VERSION

WEB_SEARCH_RULES_VERSION = "web-search-plan-v2"
DEFAULT_PROVIDER = "tavily"
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
_FRESH_CACHE_TTL_SECONDS = 60 * 60
_CURRENT_CACHE_TTL_SECONDS = 15 * 60

@dataclass(frozen=True)
class SearchPlan:
    """本地生成并可在网络请求前持久化的最小公网查询计划。"""

    should_search: bool
    query: str
    reason: str
    queries: tuple[str, ...] = ()
    plan_id: str = ""
    provider: str = DEFAULT_PROVIDER
    provider_version: str = TAVILY_SEARCH_PROVIDER_VERSION
    rules_version: str = WEB_SEARCH_RULES_VERSION
    query_hash: str = ""
    original_query_hash: str = ""
    freshness_window_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    deleted_categories: tuple[str, ...] = ()
    max_queries: int = 3
    max_results: int = 5
    max_retries: int = 1
    total_timeout_seconds: float = search_budget.PUBLIC_SEARCH_STAGE_SECONDS
    max_response_bytes: int = 1_000_000
    max_redirects: int = 2

    def __post_init__(self) -> None:
        max_queries = max(1, min(self.max_queries, 4))
        max_retries = max(0, min(self.max_retries, 1))
        raw_queries = tuple(query.strip() for query in self.queries if query.strip())
        if not raw_queries and self.query.strip():
            raw_queries = (self.query.strip(),)
        bounded_queries = raw_queries[:max_queries]
        primary_query = bounded_queries[0] if bounded_queries else ""
        object.__setattr__(self, "max_queries", max_queries)
        object.__setattr__(self, "max_retries", max_retries)
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
        r"联网|上网|网页(?!开发|设计)|在线|搜索|查找|查一下|查查|查下|查一查|搜一下|帮我搜|帮我查"
    )
    _FRESHNESS = re.compile(
        r"最新|最近|近期|当前|截至|本周|本月|本季度|本年|最新消息|变化|"
        r"今天(?:新闻|消息|天气|股价|价格|汇率|比赛)|现在(?:新闻|消息|天气|股价|价格|汇率)|\b20\d{2}\b"
    )
    # 只剔除对助手的近况问候，不剔除“股价最近怎么样”等外部事实对象。
    _SOCIAL_CHECK_IN = re.compile(
        r"(?:(?:你|您)|(?:^|[，。！？；;\n]))(?:最近|近期|现在|当前)"
        r"(?:怎么样|如何|还好吗|过得怎样)"
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
        # 对话内的预算、人数与车辆状态不依赖公网时效；显式联网仍优先。
        if not explicit:
            local_state_removed = re.sub(
                r"当前这条[^。！？\n]{0,12}预算|当前的?人数与车辆数", "", content)
            local_state_removed = self._SOCIAL_CHECK_IN.sub("", local_state_removed)
            fresh = bool(self._FRESHNESS.search(local_state_removed))
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
