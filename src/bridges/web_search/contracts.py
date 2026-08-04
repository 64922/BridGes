"""公网搜索的内部与 API 投影契约。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class WebSearchStatus(StrEnum):
    """一次公网搜索的可见状态。"""

    LOADING = "loading"
    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"
    PERMISSION = "permission"
    RECOVERY = "recovery"
    CANCELLED = "cancelled"


class WebSearchResult(BaseModel):
    """来自真实搜索响应的最小可引用结果。"""

    result_id: str = Field(description="本次搜索内稳定的结果编号。")
    title: str = Field(description="网页标题。")
    site: str = Field(description="网页站点。")
    url: str = Field(description="供应商返回的真实网页地址。")
    snippet: str = Field(default="", description="网页摘要。")
    accessed_at: datetime = Field(description="本次访问时间。")


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
