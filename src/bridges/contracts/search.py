"""跨内容统一桌面搜索的公开契约（Issue 24）。

统一搜索覆盖聊天、图片与文档三类账户级内容，全部实时查询权威数据库
（无进程内缓存）。任何投影都不包含对象库路径、原文全量内容或凭据，
只携带跳转所需的定位锚点（会话/消息/对象标识与文档页码/章节）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: 统一搜索支持的结果类型。
SearchResultType = Literal["chat", "image", "document"]

#: 全部结果类型（counts 字典固定给出三类，便于前端筛选 tab 计数）。
ALL_RESULT_TYPES: tuple[SearchResultType, ...] = ("chat", "image", "document")


class SearchSegment(BaseModel):
    """结果片段的一段文字；``matched`` 标记该段是否为真实命中词。"""

    text: str = Field(description="片段文字。")
    matched: bool = Field(default=False, description="是否为查询命中词（前端高亮）。")


class SearchResultItem(BaseModel):
    """单条搜索结果（扁平结构，代码生成友好）。

    ``result_id`` 在类型内唯一：消息命中为消息标识，会话标题命中为会话
    标识，图片为对象标识，文档为文档标识。跳转锚点字段按类型填充，
    不适用时为 null。
    """

    result_type: SearchResultType = Field(description="结果类型。")
    result_id: str = Field(description="类型内唯一的结果标识（跳转主键）。")
    title: str = Field(description="结果标题（会话、文档或图片名）。")
    snippet: list[SearchSegment] = Field(
        default_factory=list,
        description="真实命中片段；命中词段 matched=true。标题命中时片段取自标题。",
    )
    updated_at: datetime = Field(description="结果最近更新时间。")
    conversation_id: str | None = Field(default=None, description="聊天结果的会话标识。")
    message_id: str | None = Field(default=None, description="消息命中时的消息标识。")
    object_id: str | None = Field(default=None, description="图片/文档结果的对象标识。")
    page_number: int | None = Field(default=None, description="文档命中分块的页码锚点。")
    section_title: str | None = Field(default=None, description="文档命中分块的章节标题锚点。")


class SearchResponse(BaseModel):
    """统一搜索响应：查询回显、索引就绪信号、结果列表与分类计数。"""

    query: str = Field(description="原始查询词（回显）。")
    index_ready: bool = Field(
        description=(
            "账户索引是否就绪；有待处理/处理中文档或索引未就绪时为 false，"
            "结果仍返回，由前端显示「索引尚未就绪」提示。"
        )
    )
    results: list[SearchResultItem] = Field(
        default_factory=list, description="按最近更新倒序的合并结果（截断到 limit）。"
    )
    counts: dict[str, int] = Field(
        default_factory=dict,
        description="三类结果各自的总命中数（不受 limit 截断），用于筛选 tab 计数。",
    )
