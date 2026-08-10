"""公网搜索的内部与 API 投影契约。"""

from __future__ import annotations

from datetime import datetime
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


class WebSearchVerification(StrEnum):
    """单个来源的确定性证据状态。"""

    VERIFIED = "verified"
    CROSS_VERIFIED = "cross_verified"
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
    status: WebSearchHealthStatus
    checked_at: datetime
    error_code: str | None = None


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
        description="来源证据状态：verified、cross_verified、summary_only、fetch_failed 或 conflicting。",
    )
    fetch_error_code: str | None = Field(
        default=None, description="来源页面抓取失败分类码。"
    )
    redirect_count: int = Field(default=0, ge=0, description="本次抓取重定向次数。")


class WebSearchProjection(BaseModel):
    """聊天消息与 SSE 使用的公网搜索状态投影。"""

    status: WebSearchStatus = Field(description="搜索状态。")
    trigger_reason: str = Field(description="触发联网搜索的中文原因。")
    query_summary: str = Field(description="本地脱敏后发送的最小查询概述。")
    results: list[WebSearchResult] = Field(default_factory=list)
    searched_at: datetime | None = Field(default=None, description="搜索完成时间。")
    error_code: str | None = Field(default=None, description="搜索失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文提示。")
    can_retry: bool = Field(default=False, description="本轮是否可以重试。")
    can_cancel: bool = Field(default=False, description="本轮是否可以取消。")
    plan_id: str | None = Field(default=None, description="持久化联网计划标识。")
    provider: str = Field(default="duckduckgo", description="固定联网提供方。")
    provider_version: str = Field(
        default="duckduckgo-instant-answer-v1", description="提供方合同版本。"
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
