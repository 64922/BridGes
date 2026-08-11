"""Issue 15：自动画像抽取、重试与隐私说明的内部合同。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictStr,
    field_validator,
)

from bridges.contracts.profiles import FourDimension


class ProfileExtractionStatus(StrEnum):
    """一条用户消息的画像预处理状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    EXHAUSTED = "exhausted"


class ProfileExtractionOutcome(StrEnum):
    """独立于队列生命周期状态的稳定内部结果。"""

    NO_SIGNAL = "no_signal"
    SUCCEEDED_EMPTY = "succeeded_empty"
    SUCCEEDED_OBSERVED = "succeeded_observed"
    SUCCEEDED_WRITTEN = "succeeded_written"
    PENDING_RETRY = "pending_retry"
    PERMANENT_FAILURE = "permanent_failure"


class ProfilePageStatus(StrEnum):
    """投影到当前账户画像页面的状态。"""

    READY = "ready"
    EMPTY = "empty"
    PENDING = "pending"
    FAILED = "failed"


class ProfileExtractionAction(StrEnum):
    """模型允许返回的最小动作集合。"""

    CREATE = "create"
    UPDATE = "update"
    OBSERVE = "observe"
    IGNORE = "ignore"


class ProfileExtractionItem(BaseModel):
    """单条抽取结果；额外字段直接拒绝，防止合同漂移。"""

    model_config = ConfigDict(extra="forbid")

    dimension: FourDimension = Field(description="四维画像枚举。")
    normalized_value: StrictStr = Field(
        min_length=1, max_length=200, description="规范化画像值。"
    )
    evidence_ref: StrictStr = Field(
        min_length=1, max_length=200, description="内部消息证据引用。"
    )
    reliability: StrictFloat = Field(ge=0, le=1, description="内部可靠度。")
    action: ProfileExtractionAction = Field(
        description="create/update/observe/ignore。"
    )

    @field_validator("normalized_value", "evidence_ref", mode="before")
    @classmethod
    def _require_string(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("profile extraction text fields must be strings")
        return value

    @field_validator("reliability", mode="before")
    @classmethod
    def _require_number(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("profile extraction reliability must be a number")
        return value


class ProfileExtractionOutput(BaseModel):
    """抽取器唯一允许返回的结构化载荷。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ProfileExtractionItem] = Field(max_length=4)


class ProfileExtractionRun(BaseModel):
    """消息级幂等预处理记录。"""

    extraction_id: str
    account_id: str
    message_id: str
    extractor_version: str
    source_hash: str
    source_snapshot: str = Field(max_length=4000)
    status: ProfileExtractionStatus
    outcome: ProfileExtractionOutcome = ProfileExtractionOutcome.SUCCEEDED_EMPTY
    attempts: int = Field(ge=0)
    committed_record_ids: list[str] = Field(default_factory=list)
    observed_count: int = Field(default=0, ge=0)
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ProfileExtractionRetryTask(BaseModel):
    """抽取失败后的持久重试任务投影。"""

    task_id: str
    account_id: str
    message_id: str
    extractor_version: str
    source_hash: str
    status: ProfileExtractionStatus
    attempts: int = Field(ge=0)
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class AutomaticProfileObservation(BaseModel):
    """不进入模型上下文的内部观察；同一消息只保留一次。"""

    observation_id: str
    account_id: str
    message_id: str
    extractor_version: str
    dimension: FourDimension
    normalized_value: str = Field(min_length=1, max_length=200)
    evidence_ref: str
    reliability: float = Field(ge=0, le=1)
    created_at: datetime


class ProfilePrivacyNotice(BaseModel):
    """首次启用自动画像时的一次性非交互说明。"""

    version: str
    text: str
    shown_at: datetime


class ProfilePreprocessResult(BaseModel):
    """消息预处理返回值；主聊天失败时仍可继续生成。"""

    run: ProfileExtractionRun
    privacy_notice: ProfilePrivacyNotice | None = None
    committed_record_ids: list[str] = Field(default_factory=list)
    observed_count: int = Field(default=0, ge=0)


class ProfileStatusProjection(BaseModel):
    """画像页面使用的当前账户最小聚合状态。"""

    status: ProfilePageStatus
    has_records: bool
    can_retry: bool = False
