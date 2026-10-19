"""帖子读取与逐页证据的领域类型（由 ``contracts`` 统一再导出）。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.tieba.lexicon import TARGET_FORUM_NAME
from bridges.tieba.search_contracts import TiebaSearchDiscovery


class ReadStatus(StrEnum):
    """单个帖子的真实读取结果分类。"""

    READ = "read"
    PARTIAL = "partial"
    ACCESS_RESTRICTED = "access_restricted"
    UNRECOGNIZED = "unrecognized"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    ERROR = "error"
    CANCELLED = "cancelled"


class TiebaTemporalScope(StrEnum):
    """实际发言时间相对本轮条件的分类。"""

    RECENT = "recent"
    HISTORICAL = "historical"
    UNKNOWN = "unknown"


class TiebaReply(BaseModel):
    """真实读到的楼层；时间与楼层按页面原文记录。"""

    floor: int | None = Field(default=None, description="楼层号；页面未给出为 None。")
    reply_id: str | None = Field(default=None, description="页面提供的发言 ID；缺失时为 None。")
    parent_reply_id: str | None = Field(
        default=None, description="楼中楼所属的父楼层 ID；未知时为 None。"
    )
    posted_at: str | None = Field(default=None, description="页面上的发帖时间原文。")
    author: str | None = Field(default=None, description="页面显示的发言身份；缺失时为 None。")
    temporal_scope: TiebaTemporalScope = Field(
        default=TiebaTemporalScope.UNKNOWN,
        description="按实际发言时间确定的近期、历史或未知分类。",
    )
    is_original_poster: bool = Field(default=False, description="是否楼主本人发言。")
    content: str = Field(description="楼层正文（页面原文，已去标签）。")


class TiebaPageEvidence(BaseModel):
    """一页成功读取的真实范围；未成功页不会出现在这里。"""

    page: int = Field(description="实际成功读取的页码。")
    total_pages: int | None = Field(default=None, description="该页声明的总页数。")
    has_more: bool | None = Field(default=None, description="该页是否声明有后继页。")
    top_level_count: int = Field(default=0, description="该页 post_list 的条目数。")
    reply_count: int = Field(default=0, description="该页实际记录的楼层数（含楼中楼）。")


class TiebaReadResult(BaseModel):
    """一次帖子读取的真实范围与结果。"""

    url: str
    thread_id: str | None = None
    status: ReadStatus
    forum_name: str | None = Field(default=None, description="页面自身声明所属贴吧。")
    title: str | None = Field(default=None, description="页面上的帖子标题。")
    pages_read: int = Field(default=0, description="实际读取成功的页数。")
    pages_limit: int = Field(default=0, description="本轮允许读取的最大页数。")
    pages_complete: bool = Field(
        default=False, description="普通楼层分页是否已按页面声明完整读取。"
    )
    page_evidence: list[TiebaPageEvidence] = Field(
        default_factory=list, description="每页成功读取的范围证据。"
    )
    scroll_rounds: int = Field(default=0, description="实际执行的页面向下加载轮数。")
    scroll_rounds_limit: int = Field(default=0, description="本轮允许执行的向下加载轮数。")
    total_pages: int | None = Field(default=None, description="页面声明的总页数上限。")
    total_replies: int | None = Field(default=None, description="页面声明的回复数；未知时为 None。")
    floor_min: int | None = Field(default=None, description="实际读到的最小楼层。")
    floor_max: int | None = Field(default=None, description="实际读到的最大楼层。")
    replies: list[TiebaReply] = Field(default_factory=list, description="真实读到的楼层。")
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文失败说明。")
    pagination_error_code: str | None = Field(
        default=None, description="分页未完成时的稳定分类码。"
    )
    pagination_error_message: str | None = Field(
        default=None, description="分页未完成时的中文原因。"
    )
    excluded_wait_ms: int = Field(default=0, description="外部浏览器中人工登录/验证等待时长。")
    retrieved_at: datetime = Field(description="读取时间。")

    @property
    def forum_matches_target(self) -> bool:
        """页面自身的吧名是否确为目标贴吧（唯一的归属确认来源）。"""
        return (self.forum_name or "").strip() == TARGET_FORUM_NAME


class TiebaPostProjection(BaseModel):
    """一个真实读到并确认属于目标贴吧的帖子。"""

    thread_id: str | None = Field(default=None, description="帖子 ID。")
    url: str = Field(description="帖子直达链接。")
    title: str | None = Field(default=None, description="页面上的帖子标题。")
    affiliation_evidence: str = Field(description="归属确认依据的中文说明。")
    read_status: ReadStatus = Field(description="读取结果分类。")
    pages_read: int = Field(default=0, description="实际读取页数。")
    pages_limit: int = Field(default=0, description="本轮页数上限。")
    pages_complete: bool = Field(
        default=False, description="普通楼层分页是否已按页面声明完整读取。"
    )
    page_evidence: list[TiebaPageEvidence] = Field(
        default_factory=list, description="每页成功读取的范围证据。"
    )
    scroll_rounds: int = Field(default=0, description="实际执行的页面向下加载轮数。")
    scroll_rounds_limit: int = Field(default=0, description="本轮允许执行的向下加载轮数。")
    total_pages: int | None = Field(default=None, description="页面声明的总页数。")
    total_replies: int | None = Field(default=None, description="页面声明的回复数；未知时为 None。")
    floor_min: int | None = Field(default=None, description="实际读到的最小楼层。")
    floor_max: int | None = Field(default=None, description="实际读到的最大楼层。")
    replies_obtained: bool = Field(description="是否真的取得了回复文本。")
    replies: list[TiebaReply] = Field(default_factory=list, description="真实读到的楼层。")
    read_error_code: str | None = Field(default=None, description="未取得内容时的分类码。")
    read_error_message: str | None = Field(
        default=None, description="未取得内容时的中文原因说明。"
    )
    pagination_error_code: str | None = Field(
        default=None, description="分页未完成时的稳定分类码。"
    )
    pagination_error_message: str | None = Field(
        default=None, description="分页未完成时的中文原因。"
    )
    discoveries: list[TiebaSearchDiscovery] = Field(
        default_factory=list, description="该帖在搜索阶段的发现路径。"
    )
    retrieved_at: datetime = Field(description="读取时间。")


__all__ = [
    "ReadStatus",
    "TiebaPageEvidence",
    "TiebaPostProjection",
    "TiebaReadResult",
    "TiebaReply",
    "TiebaTemporalScope",
]
