"""个人记忆页对学习领域的目标与学情引用投影（画像工单 14）。

个人记忆视图只引用学习领域的所有权字段，不复制、不推断掌握程度：

- **目标**：来自学习领域的既有目标（L08），只读其稳定标识、原话、状态与
  版本；暂停/继续等状态修改不在这里发生，必须经所属领域服务执行。
- **学情判断**：来自学习档案的有效读取边界（L06 ``effective_records``）：
  只有未被删除的记录进入本投影；``verified`` 为真才是依据实际作答核验过
  的学情，``state=corrected``（用户纠正）与 ``pending_confirmation``（待
  进一步验证）都是未经核验的，不得当作掌握证明。用户自述（「我学过某
  主题」「我懂了」）属于个人记忆条目，不在这里伪装成核验结论。

投影是每次读取时实时生成的只读视图：学习侧纠正或删除记录后，本投影
立即反映；它不落画像库，也不作为事实来源参与回答。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.study_diagnosis import (
    StudyDiagnosisVerdict,
    StudyRecordState,
)
from bridges.contracts.study_goals import StudyGoalStatus

#: 引用投影的合同版本；字段语义变化时递增。
LEARNING_REFERENCES_CONTRACT_VERSION = "learning-references-v1"

#: 投影字段的所有权标识：所有条目由学习领域拥有，这里只是引用。
LEARNING_REFERENCES_OWNER = "study"


class LearningGoalReference(BaseModel):
    """一条学习目标的只读引用（所有权在学习领域）。"""

    model_config = ConfigDict(extra="forbid")

    goal_id: str = Field(description="学习领域内的稳定目标标识。")
    statement: str = Field(description="用户目标原话（学习领域所有）。")
    status: StudyGoalStatus = Field(description="目标状态：活动/暂停/完成。")
    version: int = Field(ge=1, description="目标版本（并发操作守卫依据）。")
    current_node_title: str = Field(
        default="", description="当前路线节点名称；未形成路线时为空。"
    )
    route_version: int = Field(
        default=0, ge=0, description="当前路线版本；未形成时为 0。"
    )
    updated_at: datetime = Field(description="最近更新时间。")


class LearningEvidenceReference(BaseModel):
    """一条学习学情判断的只读引用（所有权在学习领域）。

    ``verified`` 为真表示该判断仍处于活动状态且来源可读（依据实际作答
    核验）；用户纠正（``corrected``）、来源不可读与待进一步验证都不构成
    已核验掌握。阅读、单次答对或用户自评不会出现在该字段语义里。
    """

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(description="学习领域内的稳定记录标识。")
    record_version: int = Field(ge=1, description="记录版本。")
    subject: str = Field(description="学科。")
    title: str = Field(description="知识点标题。")
    verdict: StudyDiagnosisVerdict = Field(description="上次核验结论。")
    state: StudyRecordState = Field(
        description="记录状态：active 或 corrected（已删除不进入投影）。"
    )
    verified: bool = Field(
        description="是否依据实际作答处于已核验状态；用户纠正后为假。"
    )
    pending_confirmation: bool = Field(
        description="是否待进一步验证（不确定，不作确定学情）。"
    )
    source_available: bool = Field(description="来源消息是否仍可读。")
    uncertainty: str = Field(
        default="", description="学习领域记录的未确认说明；没有时为空。"
    )
    user_note: str = Field(
        default="", description="用户主动纠正内容；未纠正时为空。"
    )
    updated_at: datetime = Field(description="最近更新时间。")


class LearningReferencesProjection(BaseModel):
    """记忆页的学习引用区投影：目标与学情分别列出，来源实时读取。"""

    model_config = ConfigDict(extra="forbid")

    owner: str = Field(
        default=LEARNING_REFERENCES_OWNER,
        description="字段所有权：所有条目由本标识的领域拥有，这里只是引用。",
    )
    contract_version: str = Field(
        default=LEARNING_REFERENCES_CONTRACT_VERSION,
        description="引用投影合同版本。",
    )
    goals: list[LearningGoalReference] = Field(
        default_factory=list,
        description="本人活动/暂停的学习目标，最近更新在前。",
    )
    evidences: list[LearningEvidenceReference] = Field(
        default_factory=list,
        description="未被删除的学情判断（纠正与未核验如实标注），最近更新在前。",
    )


__all__ = [
    "LEARNING_REFERENCES_CONTRACT_VERSION",
    "LEARNING_REFERENCES_OWNER",
    "LearningEvidenceReference",
    "LearningGoalReference",
    "LearningReferencesProjection",
]
