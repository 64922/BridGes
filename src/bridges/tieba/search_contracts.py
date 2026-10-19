"""贴吧搜索候选、发现路径与页范围的公开类型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TiebaSearchDiscovery(BaseModel):
    """一条候选发现路径：主题搜索或回复搜索、页码与回复定位。

    同一个帖子可能先由主题搜索发现，又由回复搜索命中；两条路径都要保留，
    否则读取与总结阶段会丢掉「标题不含话题、只有回复提到」的线索。
    """

    discovery_type: str = Field(default="thread", description="thread 或 reply。")
    query: str | None = Field(default=None, description="实际发送的吧内检索词。")
    search_page: int | None = Field(default=None, description="命中所处的搜索页（1 基）。")
    matched_reply: str | None = Field(
        default=None, description="回复搜索中的实际命中片段；仅用于发现候选。"
    )
    matched_reply_id: str | None = Field(
        default=None, description="回复命中对应的楼层或楼中楼 ID；未知时为 None。"
    )
    matched_author: str | None = Field(
        default=None, description="回复命中对应的发言者；未知时为 None。"
    )
    source: str = Field(default="tieba_browser", description="发现路径的来源。")


class TiebaSearchScope(BaseModel):
    """一个搜索页签实际读到的页范围与停止原因。"""

    tab: str = Field(description="thread（主题）或 reply（回复）。")
    query: str = Field(description="实际发送的吧内检索词。")
    pages_read: int = Field(default=0, description="实际成功解析的页数。")
    pages_limit: int = Field(default=0, description="本页签允许读取的最大页数。")
    visited_pages: list[int] = Field(default_factory=list, description="实际读到的页码。")
    last_page: int | None = Field(default=None, description="最后读到的页码。")
    has_more: bool | None = Field(
        default=None, description="最后响应是否声明还有后续页；未知时为 None。"
    )
    stop_reason: str | None = Field(
        default=None, description="停止翻页的分类码；正常读完为 None。"
    )
    note: str | None = Field(default=None, description="面向用户的页范围说明。")


class TiebaSearchHit(BaseModel):
    """允许的搜索服务返回的候选（尚未确认真属目标贴吧）。"""

    url: str = Field(description="候选帖链接。")
    title: str = Field(description="搜索服务给出的标题（未核实页面）。")
    snippet: str = Field(default="", description="搜索摘要（只作排除他吧的证据使用）。")
    thread_id: str | None = Field(default=None, description="帖子 ID；非帖子链接为 None。")
    source: str = Field(default="tavily", description="候选实际发现来源。")
    discovery_type: str = Field(
        default="thread", description="从主题搜索或回复搜索发现。"
    )
    matched_reply: str | None = Field(
        default=None, description="回复搜索结果中的实际命中片段；仅用于发现候选。"
    )
    matched_reply_id: str | None = Field(
        default=None, description="回复命中对应的楼层或楼中楼 ID；未知时为 None。"
    )
    matched_author: str | None = Field(
        default=None, description="回复命中对应的发言者；未知时为 None。"
    )
    search_page: int | None = Field(
        default=None, description="主发现路径所在的搜索页；未知时为 None。"
    )
    discoveries: list[TiebaSearchDiscovery] = Field(
        default_factory=list, description="同一帖子的全部发现路径（去重后保留）。"
    )
