"""贴吧运行的公开元数据：标识、版本、调用计数与未读范围。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TiebaRunMetadata(BaseModel):
    """一轮贴吧采集的可核对运行记录（不含会话秘密或原始响应）。"""

    contract_version: str = Field(default="tieba-run-metadata-v1")
    run_id: str = Field(description="本轮运行的稳定标识。")
    recipe_version: str = Field(description="执行的贴吧配方版本。")
    component_versions: dict[str, str] = Field(
        default_factory=dict, description="本轮实际使用的协议与节点能力版本。"
    )
    started_at: datetime | None = Field(default=None, description="本轮开始时间。")
    completed_at: datetime | None = Field(default=None, description="投影完成时间。")
    elapsed_ms: int | None = Field(default=None, description="本轮实际耗时（毫秒）。")
    request_count: int = Field(default=0, description="外部请求总数。")
    search_request_count: int = Field(default=0, description="贴吧检索请求数。")
    search_page_count: int = Field(default=0, description="实际读取的搜索页数（含主题与回复）。")
    read_request_count: int = Field(default=0, description="帖子读取请求数。")
    official_request_count: int = Field(default=0, description="学校官方页面请求数。")
    model_call_count: int = Field(default=0, description="实际发生的模型调用数。")
    confirmed_post_count: int = Field(default=0, description="确认属于目标吧的帖子数。")
    read_reply_count: int = Field(default=0, description="实际记录的楼层数（含楼中楼）。")
    source_urls: list[str] = Field(default_factory=list, description="可核对的帖子来源链接。")
    unread_scope: list[str] = Field(
        default_factory=list, description="实际未读取的页、楼层或内容范围。"
    )


__all__ = ["TiebaRunMetadata"]
