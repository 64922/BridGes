"""图片生成与编辑的公开契约（Issue 31）。

图片生成与编辑固定使用 qwen-image-2.0-pro-2026-06-22（ADR-0009）：用户可
输入生成要求，或选择当前账户有权访问的图片（本账户图片资产版本或聊天
附件对象）并给出编辑指令；请求进入可恢复的异步任务（后台执行器按租约
领取并轮询 DashScope 云端任务），完成后成为账户隔离的版本化资产——
编辑结果创建新版本并保留来源、提示、模型快照与时间关系，绝不覆盖原图。

状态机：queued → running → succeeded / failed / cancelled。租约过期且
未终态的任务在呈现层映射为 ``recovery``（上次处理中断，后台恢复中）。
取消后 worker 通过条件更新保证迟到结果不发布为成功资产（迟到结果隔离）。
替代文本由核心视觉模型自动生成，失败时确定性降级为提示词摘要，之后
用户可随时修改。

审计与日志不复制图片字节与提示词正文；请求只携带编辑所需图片、提示
与最小授权上下文（编辑图片经内存 base64 直传供应商临时任务，不落库）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ImageTaskKind(StrEnum):
    """图片任务类型：从提示词生成，或在来源图片上按指令编辑。"""

    GENERATE = "generate"
    EDIT = "edit"


class ImageTaskStatus(StrEnum):
    """图片任务的呈现状态。

    - ``queued``：已提交，等待后台执行器领取；
    - ``running``：已领取，正在提交/轮询云端任务；
    - ``recovery``：处理被中断（租约过期），后台正在恢复重领；
    - ``succeeded``：真实模型结果已落为账户版本化资产；
    - ``failed``：失败，error_code/error_message 说明原因，可重试；
    - ``cancelled``：用户取消；迟到结果不会发布为成功资产。
    """

    QUEUED = "queued"
    RUNNING = "running"
    RECOVERY = "recovery"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImageTaskProjection(BaseModel):
    """一次图片任务的公开投影；不包含图片字节与账户信息。

    消息的 ``image`` 列直接保存同一投影：进行中渲染任务卡（状态芯片 +
    取消/重试），成功后前端据此拉取资产详情渲染资产卡。
    """

    task_id: str = Field(description="任务标识。")
    attempt_number: int = Field(
        default=1, ge=1, description="该图片任务的显式提交轮次；手动重试时递增。"
    )
    kind: ImageTaskKind = Field(description="任务类型：生成或编辑。")
    prompt: str = Field(
        description="用户提交的生成要求或编辑指令（用于追溯与前端展示）。"
    )
    source_version_id: str | None = Field(
        default=None, description="编辑来源版本标识（kind=edit 时存在）。"
    )
    source_object_id: str | None = Field(
        default=None, description="编辑来源聊天附件对象标识（kind=edit 时存在）。"
    )
    model_id: str | None = Field(
        default=None, description="实际使用的固定图片模型快照。"
    )
    status: ImageTaskStatus = Field(description="当前呈现状态。")
    error_code: str | None = Field(default=None, description="稳定错误码。")
    error_message: str | None = Field(
        default=None, description="可操作的中文错误说明。"
    )
    retryable: bool = Field(
        default=False, description="失败后是否可原样重试同一输入。"
    )
    asset_id: str | None = Field(
        default=None, description="结果归属资产标识；编辑时为来源资产。"
    )
    result_version_id: str | None = Field(
        default=None, description="成功时创建的版本标识（下载/切换版本用）。"
    )
    deleted: bool = Field(
        default=False,
        description="资产已被删除（消息引用维护：删除资产时标记，前端据此"
        "显示已删除状态，不再请求资产详情）。",
    )
    synthetic: bool = Field(
        default=True, description="结果是否为图片模型生成的合成内容。"
    )
    created_at: datetime = Field(description="任务创建时间。")
    updated_at: datetime = Field(description="最近状态更新时间。")


class ImageVersionProjection(BaseModel):
    """图片资产中的一个版本；每个成功任务恰好产生一个版本对象。"""

    version_id: str = Field(description="版本标识（全局唯一）。")
    asset_id: str = Field(description="所属资产标识。")
    parent_version_id: str | None = Field(
        default=None, description="编辑来源版本；生成版本为 None。"
    )
    kind: ImageTaskKind = Field(description="本版本来源：生成或编辑。")
    prompt: str = Field(description="本版本使用的提示词。")
    model_id: str | None = Field(
        default=None, description="本版本使用的固定模型快照（运行锁实际标识）。"
    )
    object_id: str = Field(description="账户对象库中的图片对象标识。")
    media_type: str = Field(description="图片媒体类型。")
    content_length: int = Field(description="图片字节数。")
    synthetic: bool = Field(
        default=True, description="该版本是否为图片模型生成的合成内容。"
    )
    created_at: datetime = Field(description="版本创建时间。")


class ImageAltTextSource(StrEnum):
    """替代文本来源：模型自动生成 / 确定性降级 / 用户手动修改。"""

    MODEL = "model"
    FALLBACK = "fallback"
    MANUAL = "manual"


class ImageAssetProjection(BaseModel):
    """图片资产的公开投影：版本链、替代文本与当前版本指针。"""

    asset_id: str = Field(description="资产标识。")
    alt_text: str = Field(default="", description="当前替代文本（可修改）。")
    alt_text_source: ImageAltTextSource = Field(
        default=ImageAltTextSource.FALLBACK, description="替代文本来源。"
    )
    current_version_id: str | None = Field(
        default=None, description="当前版本指针（删除后为 None）。"
    )
    version_count: int = Field(default=0, description="版本数量。")
    synthetic: bool = Field(
        default=True, description="该资产是否由图片模型生成或编辑产生。"
    )
    versions: list[ImageVersionProjection] = Field(
        default_factory=list, description="版本列表（按创建时间升序）。"
    )
    created_at: datetime = Field(description="资产创建时间。")
    updated_at: datetime = Field(description="最近更新（含版本追加/替代文本）时间。")


class ImageAltTextUpdateRequest(BaseModel):
    """修改替代文本的请求。"""

    alt_text: str = Field(min_length=1, max_length=500, description="新的替代文本。")


class ImageDeletionProjection(BaseModel):
    """删除资产的影响说明与结果（幂等：已删除资产返回零计数）。"""

    asset_id: str = Field(description="已删除的资产标识。")
    removed_versions: int = Field(description="实际移除的版本数量。")
    updated_messages: int = Field(description="引用该资产的助手消息投影更新数量。")
    object_status: str = Field(
        description="对象处置：cleaned（已物理清理）或 pending_cleanup（待清理轮重试）。"
    )
    deleted_at: datetime = Field(description="删除完成时间。")


class ImageError(Exception):
    """图片域错误；message 为面向用户的中文说明。"""

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
    "ImageAltTextSource",
    "ImageAltTextUpdateRequest",
    "ImageAssetProjection",
    "ImageDeletionProjection",
    "ImageError",
    "ImageTaskKind",
    "ImageTaskProjection",
    "ImageTaskStatus",
    "ImageVersionProjection",
]
