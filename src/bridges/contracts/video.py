"""文生视频的公开契约（Issue 32）。

文生视频固定使用 wan2.7-t2v-2026-06-12（ADR-0007：Wan 是模型矩阵中
唯一的非 Qwen 系列例外，仍使用同一全局百炼运行凭据，遵守单类别单快照、
用户不可更改、不可用即明确停用的共同合同）。用户从聊天
提交视频要求后，请求进入可恢复的异步任务（后台执行器按租约领取并轮询
DashScope 云端任务），完成后成为账户隔离的本地资产：包含提示、模型、
供应商任务标识、创建时间、可访问文字说明，支持预览、下载与带确认的
删除。

状态机：queued → submitting → generating → succeeded / failed /
cancelled；另有 cancelling（用户已发起取消、worker 收敛中）。租约过期
且未终态的任务在呈现层映射为 ``recovery``（上次处理中断，后台恢复中）。
取消后 worker 通过条件发布保证迟到结果不进入对话或资产库。

可访问文字说明默认由提示词确定性生成（source=prompt），用户可随时修改
（source=manual）；不冒充模型理解。审计与日志不复制视频字节与提示词
正文。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class VideoTaskStatus(StrEnum):
    """视频任务的呈现状态。

    - ``queued``：已提交，等待后台执行器领取；
    - ``submitting``：已领取，正在向供应商提交云端任务；
    - ``generating``：云端任务已接受，正在轮询生成；
    - ``recovery``：处理被中断（租约过期），后台正在恢复重领；
    - ``succeeded``：真实模型结果已落为账户资产；
    - ``failed``：失败，error_code/error_message 说明原因，可重试；
    - ``cancelling``：用户已发起取消，worker 正在收敛（尽力通知云端）；
    - ``cancelled``：已取消；迟到结果不会发布为成功资产。
    """

    QUEUED = "queued"
    SUBMITTING = "submitting"
    GENERATING = "generating"
    RECOVERY = "recovery"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class VideoTaskProjection(BaseModel):
    """一次视频任务的公开投影；不包含视频字节与账户信息。

    消息的 ``video`` 列直接保存同一投影：进行中渲染任务卡（状态芯片 +
    取消/重试），成功后前端据此拉取资产详情渲染资产卡。
    """

    task_id: str = Field(description="任务标识。")
    prompt: str = Field(description="用户提交的生成要求（用于追溯与前端展示）。")
    model_id: str | None = Field(
        default=None, description="实际使用的固定视频模型快照（Wan 例外）。"
    )
    status: VideoTaskStatus = Field(description="当前呈现状态。")
    error_code: str | None = Field(default=None, description="稳定错误码。")
    error_message: str | None = Field(
        default=None, description="可操作的中文错误说明。"
    )
    retryable: bool = Field(
        default=False, description="失败后是否可原样重试同一输入。"
    )
    asset_id: str | None = Field(
        default=None, description="结果归属资产标识。"
    )
    result_object_id: str | None = Field(
        default=None, description="成功时创建的资产对象标识。"
    )
    deleted: bool = Field(
        default=False,
        description="资产已被删除（消息引用维护：删除资产时标记，前端据此"
        "显示已删除状态，不再请求资产详情）。",
    )
    created_at: datetime = Field(description="任务创建时间。")
    updated_at: datetime = Field(description="最近状态更新时间。")


class VideoDescriptionSource(StrEnum):
    """可访问文字说明来源：提示词确定性生成 / 用户手动修改。"""

    PROMPT = "prompt"
    MANUAL = "manual"


class VideoAssetProjection(BaseModel):
    """视频资产的公开投影：说明文字、模型/供应商快照与对象指针。

    每个成功任务恰好产生一个视频对象；资产与任务一一对应，无版本链
    （视频生成不提供编辑）。``cloud_task_id`` 是供应商任务标识，用于
    追溯真实云端任务。
    """

    asset_id: str = Field(description="资产标识。")
    description: str = Field(default="", description="可访问文字说明（可修改）。")
    description_source: VideoDescriptionSource = Field(
        default=VideoDescriptionSource.PROMPT, description="说明文字来源。"
    )
    object_id: str = Field(description="账户对象库中的视频对象标识。")
    prompt: str = Field(description="本资产使用的提示词。")
    model_id: str | None = Field(
        default=None, description="本资产使用的固定视频模型快照（运行锁实际标识）。"
    )
    cloud_task_id: str | None = Field(
        default=None, description="供应商（DashScope）任务标识，用于追溯。"
    )
    media_type: str = Field(description="视频媒体类型。")
    content_length: int = Field(description="视频字节数。")
    deleted: bool = Field(
        default=False, description="资产已删除（消息投影维护）。"
    )
    created_at: datetime = Field(description="资产创建时间。")
    updated_at: datetime = Field(description="最近更新（含说明修改）时间。")


class VideoDescriptionUpdateRequest(BaseModel):
    """修改可访问文字说明的请求。"""

    description: str = Field(
        min_length=1, max_length=500, description="新的可访问文字说明。"
    )


class VideoDeletionProjection(BaseModel):
    """删除资产的影响说明与结果（幂等：已删除资产返回零计数）。"""

    asset_id: str = Field(description="已删除的资产标识。")
    removed_objects: int = Field(description="实际移除的对象数量（0 或 1）。")
    updated_messages: int = Field(description="引用该资产的助手消息投影更新数量。")
    object_status: str = Field(
        description="对象处置：cleaned（已物理清理）或 pending_cleanup（待清理轮重试）。"
    )
    deleted_at: datetime = Field(description="删除完成时间。")


class VideoError(Exception):
    """视频域错误；message 为面向用户的中文说明。"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


__all__ = [
    "VideoAssetProjection",
    "VideoDeletionProjection",
    "VideoDescriptionSource",
    "VideoDescriptionUpdateRequest",
    "VideoError",
    "VideoTaskProjection",
    "VideoTaskStatus",
]
