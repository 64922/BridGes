"""旧学习会话主动续学的公开投影与请求合同（工单 12）。

旧五阶段会话保持只读；用户在旧会话中主动发起续学：选择本人学习目标
（可选）与仍可访问的旧材料，可补充说明。服务端把材料复制为新账户级
草稿、原子创建新模式会话，并在新附件行上标注来源会话与来源对象；
重复请求按幂等键复用同一新会话，失败不改变旧历史、不留下孤立引用。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from bridges.contracts.chat import ChatAttachmentProjection
from bridges.contracts.study_goals import StudyGoalSummaryProjection


class StudyRestartOptionsProjection(BaseModel):
    """旧会话的续学选项：本人目标候选与仍可访问材料，逐项如实标注。"""

    conversation_id: str = Field(min_length=1, description="旧会话标识。")
    can_restart: bool = Field(
        description="是否至少存在一个可选目标或一份仍可访问的材料。"
    )
    linked_goal_id: str | None = Field(
        default=None, description="旧会话曾关联的目标标识；从未关联时为 None。"
    )
    goals: list[StudyGoalSummaryProjection] = Field(
        default_factory=list, description="本人活动/暂停目标（最近在前）。"
    )
    materials: list[ChatAttachmentProjection] = Field(
        default_factory=list,
        description="旧会话内仍可访问的已绑定材料（来源身份只读呈现）。",
    )


class StudyRestartRequest(BaseModel):
    """主动续学请求：目标与材料至少选择一项；说明为选填。"""

    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        description="客户端生成的一次性幂等键；同账户同键重放复用同一新会话。",
    )
    goal_id: str | None = Field(
        default=None,
        max_length=64,
        description="选择继续的本人学习目标；不选择时为 None。",
    )
    object_ids: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="选择采用的旧材料对象标识（来源会话内已绑定且仍可访问）。",
    )
    message: str = Field(
        default="",
        max_length=8000,
        description="补充说明（选填）；无材料时必填，作为新会话首条消息正文。",
    )
