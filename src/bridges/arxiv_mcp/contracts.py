"""arXiv MCP 的内部记录与聊天公开投影。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ArxivSearchStatus(StrEnum):
    """一次论文搜索的用户可见状态。"""

    LOADING = "loading"
    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"
    PERMISSION = "permission"
    RECOVERY = "recovery"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ArxivPaper:
    """只由 arXiv 响应解析出的真实论文元数据。"""

    arxiv_id: str
    title: str
    authors: list[str]
    published_at: datetime
    abs_url: str
    pdf_url: str
    abstract: str


class ArxivPaperProjection(BaseModel):
    """绑定 arXiv 返回值的论文结果与可展开引用内容。"""

    citation_id: str = Field(description="本次搜索内稳定的论文引用标识。")
    arxiv_id: str = Field(description="arXiv 返回的标识符（含版本时保留版本）。")
    title: str = Field(description="arXiv 返回的论文标题。")
    authors: list[str] = Field(description="arXiv 返回的作者列表。")
    published_at: datetime = Field(description="arXiv 返回的发布日期。")
    abs_url: str = Field(description="与 arXiv 标识符一致的摘要页链接。")
    pdf_url: str = Field(description="与 arXiv 标识符一致的 PDF 链接。")
    abstract: str = Field(description="arXiv 返回的原始摘要，供核对。")
    summary_zh: str = Field(description="基于标题和摘要的中文简介。")
    relevance_basis: str = Field(description="与确认查询的相关依据。")
    learning_advice_zh: str = Field(description="面向当前学习目标的后续阅读建议。")


class ArxivSearchProjection(BaseModel):
    """聊天消息和 SSE 使用的论文搜索状态投影。"""

    status: ArxivSearchStatus = Field(description="论文搜索状态。")
    trigger_reason: str = Field(description="触发论文搜索的中文原因。")
    query_summary: str = Field(description="本地脱敏后发送给 arXiv 的最小查询概述。")
    papers: list[ArxivPaperProjection] = Field(default_factory=list)
    searched_at: datetime | None = Field(default=None, description="搜索完成时间。")
    error_code: str | None = Field(default=None, description="搜索失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文提示。")
    upstream_status: str | None = Field(
        default=None,
        description="脱敏的上游状态类别，例如 http_4xx、http_5xx 或 parse。",
    )
    can_retry: bool = Field(default=False, description="本轮是否可以重试。")
    can_cancel: bool = Field(default=False, description="本轮是否可以取消。")
