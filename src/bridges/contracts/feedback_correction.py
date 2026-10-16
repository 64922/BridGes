"""画像工单 12：反馈修正闭环的合同。

用户在回答之后指出问题（事实记错、状态变化、表达太难、本次要求或指向
不明），系统据此识别反馈类别；含义明确时直接给出修正回答并提交有依据的
长期记忆变更，指向不明时只澄清。

本模块只定义确定性路由结果、记忆操作结果与公开投影；不承载用户原文，
路由规则见 :mod:`bridges.profiles.feedback`。修正回答与被修正回答、实际
采用版本的关联随运行持久化（``generation_runs.config_json`` 的
``feedback_correction`` 键），沿用既有备份、导出与账户删除边界。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.atomic_profile import AtomicProfileMemoryBlockedReason


class FeedbackCorrectionKind(StrEnum):
    """一次反馈的确定性类别（画像工单 12）。"""

    FACT_CORRECTION = "fact_correction"
    STATE_CHANGE = "state_change"
    LONG_TERM_PREFERENCE = "long_term_preference"
    TURN_REQUEST = "turn_request"
    EXPRESSION_QUALITY = "expression_quality"
    UNCLEAR = "unclear"


class FeedbackAdjustment(StrEnum):
    """反馈要求本轮修正回答遵守的调整签名（不含用户原文）。"""

    SHORTER = "shorter"
    SIMPLER = "simpler"
    MORE_DETAILED = "more_detailed"
    CONCLUSION_FIRST = "conclusion_first"
    NO_TABLES = "no_tables"
    FOLLOW_USER_WORDS = "follow_user_words"


class FeedbackMemoryKind(StrEnum):
    """反馈触发的长期记忆操作种类。"""

    CORRECTION = "correction"
    STATE_CHANGE = "state_change"
    PREFERENCE = "preference"
    NONE = "none"


class FeedbackMemoryStatus(StrEnum):
    """反馈触发的记忆操作结果（与回答生成分别报告）。"""

    WRITTEN = "written"
    PROTECTED = "protected"
    UNRESOLVED = "unresolved"
    NO_ACTIVE_RECORD = "no_active_record"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class FeedbackCorrectionRoute(BaseModel):
    """确定性反馈路由结果（进程内使用）。

    ``target_item_*`` 由原回答的实际采用记录定位；``new_fact_text`` 是用户
    本轮明确给出的新事实/状态（用于定点纠正或显式记住）；``preference_text``
    是明确的长期偏好原话；``requires_clarification`` 表示指向不明、只澄清。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: FeedbackCorrectionKind = Field(description="反馈类别。")
    adjustment: FeedbackAdjustment | None = Field(
        default=None, description="本轮修正回答的调整签名；无具体调整为空。"
    )
    target_item_id: str | None = Field(
        default=None, description="原回答实际采用的对应条目标识（可定位时）。"
    )
    target_item_version: int | None = Field(
        default=None, description="定位条目的版本（定点纠正的乐观锁）。"
    )
    target_item_text: str | None = Field(
        default=None, description="定位条目正文（仅进程内定位用，不进入审计）。"
    )
    new_fact_text: str | None = Field(
        default=None, description="用户本轮明确的新事实或状态正文（可为空）。"
    )
    has_replacement: bool = Field(
        default=False,
        description=(
            "new_fact_text 是否来自用户明确给出的替代值或新状态（而非单纯"
            "否定旧值）。定位不到旧条目时只有它为真才允许写入新值。"
        ),
    )
    preference_text: str | None = Field(
        default=None, description="明确长期偏好的原话（仅长期偏好类别）。"
    )
    requires_clarification: bool = Field(
        default=False, description="指向不明，只澄清不修改。"
    )

    @property
    def produces_corrected_answer(self) -> bool:
        """该类别是否应直接给出修正回答（指向不明只澄清）。"""

        return self.kind is not FeedbackCorrectionKind.UNCLEAR


class FeedbackCorrectionResult(BaseModel):
    """一次反馈修正的确定性处理结果（脱敏；不携带用户原文与条目正文）。

    记忆操作结果与回答生成分别报告：``memory_status`` 只说明长期记忆是否
    真的写入，模型是否重写回答由运行本身与提示块决定；失败如实返回
    ``failed``，不伪造成功、不假装已更新画像。
    """

    model_config = ConfigDict(extra="forbid")

    kind: FeedbackCorrectionKind = Field(description="反馈类别。")
    adjustment: FeedbackAdjustment | None = Field(
        default=None, description="本轮修正回答的调整签名；无具体调整为空。"
    )
    memory_kind: FeedbackMemoryKind = Field(
        default=FeedbackMemoryKind.NONE, description="记忆操作种类。"
    )
    memory_status: FeedbackMemoryStatus = Field(
        default=FeedbackMemoryStatus.NOT_APPLICABLE, description="记忆操作结果。"
    )
    target_item_id: str | None = Field(
        default=None, description="反馈定位到的原采用条目标识。"
    )
    applied_item_id: str | None = Field(
        default=None, description="实际写入的条目标识；未写入为空。"
    )
    blocked_reason: AtomicProfileMemoryBlockedReason | None = Field(
        default=None, description="记忆写入被隐私门拒绝时的确定性原因。"
    )
    clarification_required: bool = Field(
        default=False, description="指向不明：只澄清、不修改。"
    )

    def context_metadata(self) -> dict[str, object]:
        """返回允许进入聊天运行配置与提示词的最小脱敏元数据。"""

        metadata: dict[str, object] = {
            "kind": self.kind.value,
            "adjustment": self.adjustment.value if self.adjustment else None,
            "memory_kind": self.memory_kind.value,
            "memory_status": self.memory_status.value,
            "target_item_id": self.target_item_id,
            "applied_item_id": self.applied_item_id,
            "clarification_required": self.clarification_required,
        }
        if self.blocked_reason is not None:
            metadata["blocked_reason"] = self.blocked_reason.value
        return metadata


class FeedbackCorrectionMetadata(BaseModel):
    """随运行持久化的反馈修正关系与结果（脱敏）。

    修正回答 ↔ 原回答、原运行、原采用切片的关联，以及记忆操作结果与
    调整签名；不保存用户原文、条目正文或排除原因正文。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: FeedbackCorrectionKind = Field(description="反馈类别。")
    adjustment: FeedbackAdjustment | None = Field(
        default=None, description="本轮修正回答的调整签名。"
    )
    memory_kind: FeedbackMemoryKind = Field(
        default=FeedbackMemoryKind.NONE, description="记忆操作种类。"
    )
    memory_status: FeedbackMemoryStatus = Field(
        default=FeedbackMemoryStatus.NOT_APPLICABLE, description="记忆操作结果。"
    )
    target_item_id: str | None = Field(default=None, description="被定位的原采用条目。")
    applied_item_id: str | None = Field(default=None, description="实际写入的条目。")
    original_message_id: str | None = Field(
        default=None, description="被修正的原助手回答消息标识。"
    )
    original_run_id: str | None = Field(default=None, description="原回答的运行标识。")
    original_slice_id: str | None = Field(
        default=None, description="原回答实际采用切片的标识。"
    )
    clarification_required: bool = Field(
        default=False, description="是否只澄清、不修改。"
    )
    created_at: datetime = Field(description="反馈处理时间。")


class FeedbackCorrectionProjection(BaseModel):
    """聊天消息公开投影中的反馈修正关系（读取/重启/导出复用）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: FeedbackCorrectionKind = Field(description="反馈类别。")
    memory_kind: FeedbackMemoryKind = Field(description="记忆操作种类。")
    memory_status: FeedbackMemoryStatus = Field(description="记忆操作结果。")
    original_message_id: str | None = Field(default=None, description="被修正的原回答。")
    original_run_id: str | None = Field(default=None, description="原回答运行标识。")
    original_slice_id: str | None = Field(default=None, description="原采用切片标识。")
    target_item_id: str | None = Field(default=None, description="被定位的条目。")
    applied_item_id: str | None = Field(default=None, description="实际写入的条目。")
    clarification_required: bool = Field(default=False, description="是否只澄清。")
    created_at: datetime = Field(description="反馈处理时间。")

    @classmethod
    def from_metadata(
        cls, metadata: FeedbackCorrectionMetadata
    ) -> FeedbackCorrectionProjection:
        """由持久化关系折算公开投影（字段子集，语义一致）。"""

        return cls(
            kind=metadata.kind,
            memory_kind=metadata.memory_kind,
            memory_status=metadata.memory_status,
            original_message_id=metadata.original_message_id,
            original_run_id=metadata.original_run_id,
            original_slice_id=metadata.original_slice_id,
            target_item_id=metadata.target_item_id,
            applied_item_id=metadata.applied_item_id,
            clarification_required=metadata.clarification_required,
            created_at=metadata.created_at,
        )


__all__ = [
    "FeedbackAdjustment",
    "FeedbackCorrectionKind",
    "FeedbackCorrectionMetadata",
    "FeedbackCorrectionProjection",
    "FeedbackCorrectionResult",
    "FeedbackCorrectionRoute",
    "FeedbackMemoryKind",
    "FeedbackMemoryStatus",
]
