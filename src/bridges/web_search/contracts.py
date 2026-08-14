"""公网搜索的内部与 API 投影契约。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class WebSearchStatus(StrEnum):
    """一次公网搜索的可见状态。"""

    LOADING = "loading"
    SUCCESS = "success"
    PARTIAL = "partial"
    EMPTY = "empty"
    FETCH_ERROR = "fetch_error"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    SOURCE_CONFLICT = "source_conflict"
    ERROR = "error"
    PERMISSION = "permission"
    RECOVERY = "recovery"
    CANCELLED = "cancelled"


class WebSearchPageClassification(StrEnum):
    """公网搜索响应页面的确定性分类。"""

    NORMAL_RESULTS = "normal_results"
    NORMAL_EMPTY = "normal_empty"
    CHALLENGE = "challenge"
    INVALID = "invalid"


class WebSearchVerification(StrEnum):
    """单个来源的确定性证据状态。"""

    VERIFIED = "verified"
    CROSS_VERIFIED = "cross_verified"
    STRUCTURED = "structured"
    SUMMARY_ONLY = "summary_only"
    FETCH_FAILED = "fetch_failed"
    CONFLICTING = "conflicting"


class WebSearchHealthStatus(StrEnum):
    """受控搜索提供方的健康检查状态。"""

    READY = "ready"
    DNS_ERROR = "dns_error"
    CONNECT_ERROR = "connect_error"
    AUTH_ERROR = "auth_error"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"


class WebSearchHealth(BaseModel):
    """不带用户查询的提供方健康检查结果。"""

    provider: str
    provider_version: str = Field(
        default="unknown", description="提供方健康检查所使用的版本。"
    )
    status: WebSearchHealthStatus
    checked_at: datetime
    error_code: str | None = None


#: 当前生产唯一的通用公网搜索提供方（Issue 01 起为 Tavily）。出现任何
#: 其他通用搜索提供方均视为配置漂移，健康摘要与发布门必须以稳定错误码失败。
PRIMARY_WEB_SEARCH_PROVIDER = "tavily"


class WebSearchHealthSnapshot(BaseModel):
    """运行期公网搜索健康快照：脱敏状态、新鲜度与恢复语义。

    快照只承载稳定分类与时间戳，绝不包含健康查询正文、响应正文、
    代理 URL、Cookie、Authorization 或任何凭据。
    """

    provider: str = Field(default=PRIMARY_WEB_SEARCH_PROVIDER, description="唯一提供方。")
    provider_version: str = Field(
        default="unknown", description="最近一次探测使用的提供方合同版本。"
    )
    status: WebSearchHealthStatus = Field(description="当前健康状态。")
    checked_at: datetime | None = Field(
        default=None, description="最近一次实际探测时间；从未探测时为空。"
    )
    age_ms: int | None = Field(
        default=None, description="快照年龄（毫秒），由读取时刻计算。"
    )
    latency_ms: int | None = Field(
        default=None, description="最近一次探测耗时（毫秒）。"
    )
    last_success_at: datetime | None = Field(
        default=None, description="最近一次 READY 时间；失败刷新会保留它。"
    )
    error_code: str | None = Field(
        default=None, description="脱敏稳定错误码；READY 时为空。"
    )
    stale_ready: bool = Field(
        default=False,
        description=(
            "当前 READY 是否来自超过缓存有效期的旧成功；旧成功超过上限后"
            "不再显示 READY（web_search_stale_ready）。"
        ),
    )
    pending: bool = Field(
        default=False, description="尚未完成首次探测；此时不应宣称 READY。"
    )
    refresh_count: int = Field(
        default=0, ge=0, description="已完成探测次数；并发刷新合并计数。"
    )


class WebSearchHealthSummary(BaseModel):
    """公开搜索整体健康摘要；只登记 Tavily（Issue 01 起）。"""

    available: bool = Field(description="Tavily 当前是否 READY。")
    status: WebSearchHealthStatus = Field(description="整体健康状态。")
    checked_at: datetime = Field(description="本次整体健康检查时间。")
    providers: list[WebSearchHealth] = Field(
        default_factory=list, description="已登记提供方的逐项健康状态。"
    )
    error_code: str | None = Field(
        default=None,
        description="脱敏稳定错误码；出现配置漂移时固定为 unexpected_search_provider。",
    )


def aggregate_public_search_health(
    providers: list[WebSearchHealth], *, checked_at: datetime | None = None
) -> WebSearchHealthSummary:
    """聚合公开搜索健康状态；精确等价于“Tavily READY”。

    Issue 01/04：当前产品不使用备用搜索源，“至少一个提供方就绪”不再是容错
    语义。出现任何非 ``tavily`` 提供方即配置漂移，整体健康以稳定错误码
    ``unexpected_search_provider`` 失败关闭。
    """

    unexpected = [
        provider.provider
        for provider in providers
        if provider.provider != PRIMARY_WEB_SEARCH_PROVIDER
    ]
    if unexpected:
        return WebSearchHealthSummary(
            available=False,
            status=WebSearchHealthStatus.UPSTREAM_ERROR,
            checked_at=checked_at or datetime.now(UTC),
            providers=providers,
            error_code="unexpected_search_provider",
        )
    tavily = next(
        (
            provider
            for provider in providers
            if provider.provider == PRIMARY_WEB_SEARCH_PROVIDER
        ),
        None,
    )
    if tavily is None:
        return WebSearchHealthSummary(
            available=False,
            status=WebSearchHealthStatus.UPSTREAM_ERROR,
            checked_at=checked_at or datetime.now(UTC),
            providers=providers,
            error_code="web_search_health_check",
        )
    return WebSearchHealthSummary(
        available=tavily.status == WebSearchHealthStatus.READY,
        status=tavily.status,
        checked_at=checked_at or tavily.checked_at,
        providers=providers,
        error_code=tavily.error_code,
    )


class WebSearchResult(BaseModel):
    """来自真实搜索响应的最小可引用结果。"""

    result_id: str = Field(description="本次搜索内稳定的结果编号。")
    title: str = Field(description="网页标题。")
    site: str = Field(description="网页站点。")
    url: str = Field(description="供应商返回的真实网页地址。")
    snippet: str = Field(default="", description="网页摘要。")
    accessed_at: datetime = Field(description="本次访问时间。")
    published_at: datetime | None = Field(
        default=None, description="来源声明的发布时间（无法解析时为空）。"
    )
    fetched_at: datetime | None = Field(
        default=None, description="本地实际抓取来源页面的时间。"
    )
    content_summary: str = Field(
        default="", description="由实际来源页面提取的有界内容摘要。"
    )
    verification: WebSearchVerification = Field(
        default=WebSearchVerification.VERIFIED,
        description=(
            "来源证据状态：verified、cross_verified、structured、summary_only、fetch_failed 或 conflicting。"
        ),
    )
    fetch_error_code: str | None = Field(
        default=None, description="来源页面抓取失败分类码。"
    )
    redirect_count: int = Field(default=0, ge=0, description="本次抓取重定向次数。")
    provider: str = Field(default="tavily", description="实际返回该来源的提供方。")
    provider_version: str = Field(
        default="tavily-search-api-v1", description="实际返回该来源的提供方版本。"
    )


class WebSearchProviderAttempt(BaseModel):
    """一次提供方尝试的脱敏审计投影。"""

    provider: str = Field(description="提供方注册标识。")
    provider_version: str = Field(description="提供方合同版本。")
    attempt_number: int = Field(default=1, ge=1, description="本轮提供方尝试序号。")
    query_hash: str | None = Field(
        default=None, description="本次脱敏查询的不可逆指纹。"
    )
    started_at: datetime | None = Field(default=None, description="尝试开始时间。")
    ended_at: datetime | None = Field(default=None, description="尝试结束时间。")
    result_code: str = Field(description="脱敏结果码，不包含供应商正文。")
    result_count: int = Field(default=0, ge=0, description="该次尝试返回的安全结果数。")
    duration_ms: int = Field(default=0, ge=0, description="该次尝试耗时。")
    http_status_category: str | None = Field(default=None, description="HTTP 状态类别。")
    retry_planned: bool = Field(default=False, description="该尝试结束时是否计划重试。")
    page_classification: WebSearchPageClassification | None = Field(
        default=None, description="页面或结构化响应分类。"
    )


class WebSearchProjection(BaseModel):
    """聊天消息与 SSE 使用的公网搜索状态投影。"""

    status: WebSearchStatus = Field(description="搜索状态。")
    trigger_reason: str = Field(description="触发联网搜索的中文原因。")
    query_summary: str = Field(description="本地脱敏后发送的最小查询概述。")
    query_history: list[str] = Field(
        default_factory=list,
        description="本轮实际发出的脱敏查询及有限改写轨迹。",
    )
    results: list[WebSearchResult] = Field(default_factory=list)
    searched_at: datetime | None = Field(default=None, description="搜索完成时间。")
    error_code: str | None = Field(default=None, description="搜索失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文提示。")
    http_status_category: str | None = Field(
        default=None, description="提供方 HTTP 状态类别，例如 2xx、4xx。"
    )
    page_classification: WebSearchPageClassification | None = Field(
        default=None, description="提供方页面分类，不保存页面正文。"
    )
    cooldown_until: datetime | None = Field(
        default=None, description="提供方受阻冷却结束时间。"
    )
    can_retry: bool = Field(default=False, description="本轮是否可以重试。")
    can_cancel: bool = Field(default=False, description="本轮是否可以取消。")
    plan_id: str | None = Field(default=None, description="持久化联网计划标识。")
    provider: str = Field(default="tavily", description="最终选用或主用提供方。")
    provider_version: str = Field(
        default="tavily-search-api-v1", description="提供方合同版本。"
    )
    selected_provider: str | None = Field(
        default=None, description="实际采用结果的提供方；全部失败时为空。"
    )
    selected_provider_version: str | None = Field(
        default=None, description="实际采用结果的提供方版本。"
    )
    provider_attempts: list[WebSearchProviderAttempt] = Field(
        default_factory=list, description="本轮各提供方尝试的脱敏结果轨迹。"
    )
    rules_version: str = Field(
        default="web-search-plan-v2", description="本地触发/脱敏规则版本。"
    )
    query_hash: str | None = Field(default=None, description="外发规范化查询哈希。")
    original_query_hash: str | None = Field(
        default=None, description="原始查询的不可逆引用哈希，不保存原文。"
    )
    freshness_window_seconds: int = Field(
        default=86400, ge=0, description="结果可作为当前证据使用的时间窗口。"
    )
    cache_hit: bool = Field(default=False, description="是否复用了未过期结果缓存。")
    cache_expires_at: datetime | None = Field(
        default=None, description="缓存过期时间；过期结果不得冒充当前搜索。"
    )
    attempt_count: int = Field(default=0, ge=0, description="实际供应商尝试次数。")
    query_count: int = Field(default=0, ge=0, description="本计划发出的查询次数。")
    deleted_categories: list[str] = Field(
        default_factory=list, description="外发查询前删除的敏感类别。"
    )
