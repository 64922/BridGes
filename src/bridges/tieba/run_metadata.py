"""贴吧运行的公开元数据：标识、版本、调用计数与未读范围。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TiebaRequestBreakdown(BaseModel):
    """按真实用途归并的外部请求计数与耗时。"""

    request_type: str = Field(description="稳定请求类别：search/read/official/browser/other。")
    purpose: str = Field(description="实际登记的请求用途。")
    count: int = Field(default=0, description="完成的物理请求数。")
    network_ms: int = Field(default=0, description="网络耗时合计（毫秒）。")
    queued_ms: int = Field(default=0, description="自动排队等待合计（毫秒）。")


class TiebaBrowserCleanup(BaseModel):
    """浏览器 worker 清理的实际耗时与事先定义的上界。"""

    count: int = Field(default=0, description="已测量的清理次数。")
    max_ms: int = Field(default=0, description="实际最大清理耗时（毫秒）。")
    tolerance_ms: int = Field(default=0, description="事先定义的清理容差（毫秒）。")
    timed_out: int = Field(default=0, description="超过清理容差的次数。")


class TiebaRunMetadata(BaseModel):
    """一轮贴吧采集的可核对运行记录（不含会话秘密或原始响应）。"""

    contract_version: str = Field(default="tieba-run-metadata-v3")
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
    candidate_count: int = Field(default=0, description="搜索取得的原始候选数（去重前）。")
    usable_candidate_count: int = Field(default=0, description="可读取的候选帖数。")
    read_post_count: int = Field(default=0, description="实际尝试读取的候选页面数。")
    confirmed_post_count: int = Field(default=0, description="确认属于目标吧的帖子数。")
    read_reply_count: int = Field(default=0, description="实际记录的楼层数（含楼中楼）。")
    qualified_evidence_count: int = Field(
        default=0, description="符合话题、校区与时间条件、可作为观点依据的发言数。"
    )
    context_only_evidence_count: int = Field(
        default=0, description="仅作为必要上下文保留、不能引用的发言数。"
    )
    excluded_evidence_count: int = Field(
        default=0, description="已读但被话题／校区／时间条件排除的发言数。"
    )
    exclusion_reasons: dict[str, int] = Field(
        default_factory=dict, description="排除发言的稳定原因与数量。"
    )
    source_urls: list[str] = Field(default_factory=list, description="可核对的帖子来源链接。")
    unread_scope: list[str] = Field(
        default_factory=list, description="实际未读取的页、楼层或内容范围。"
    )
    first_pass_post_count: int = Field(
        default=0, description="跨帖首轮实际取得页面的帖子数。"
    )
    deepened_post_count: int = Field(
        default=0, description="首轮之后成功深入读取的帖子数。"
    )
    physical_request_count: int = Field(
        default=0, description="账本实际完成的物理外部请求数。"
    )
    request_breakdown: list[TiebaRequestBreakdown] = Field(
        default_factory=list, description="按用途归并的请求数、网络与排队耗时。"
    )
    stage_timings_ms: dict[str, int] = Field(
        default_factory=dict, description="各节点实际阶段耗时（毫秒）。"
    )
    remaining_work_ms: int | None = Field(
        default=None, description="完成投影时账本剩余工作时间（毫秒）。"
    )
    closeout_reasons: dict[str, str] = Field(
        default_factory=dict, description="检索、读取与官方路径的收尾原因。"
    )
    browser_cleanup: TiebaBrowserCleanup | None = Field(
        default=None, description="浏览器 worker 清理耗时与容差。"
    )


__all__ = [
    "TiebaBrowserCleanup",
    "TiebaRequestBreakdown",
    "TiebaRunMetadata",
]
