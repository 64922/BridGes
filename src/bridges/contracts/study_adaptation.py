"""按学情调整后续教法的公开合同（Issue 09）。

本模块承载两类对象：

- 采用快照（``StudyAdaptationSnapshot``）：一轮教学实际采用的已核验学情
  与每条对应的调整方向；带记录身份、记录版本与目标/路线版本，供模型
  载荷渲染、调用前复验与审计使用。
- 用户可见投影（``StudyAdoptionProjection``）：本轮采用了哪些学情、按
  什么方向调整；只含必要引用与状态，不复制模型提示词或内部推理。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.study_diagnosis import StudyDiagnosisVerdict

#: 本模块语义的协议版本；采用行随记录版本冻结同一协议。
ADAPTATION_CONTRACT_VERSION = "study-adaptation-v1"


class StudyAdaptationAdjustment(StrEnum):
    """一条学情影响教学的具体调整方向（不是能力等级）。"""

    #: 已核验误解：补讲/检查该前置知识点，并改用更小粒度的提示。
    PREREQUISITE_REVIEW = "prerequisite_review"
    #: 提示后完成：安排一次独立应用验证，不当作已稳定掌握。
    INDEPENDENT_APPLICATION = "independent_application"
    #: 独立完成：改用迁移应用或更高一层的检查，不重复同类基础题。
    TRANSFER_APPLICATION = "transfer_application"
    #: 结论带有不确定性或仅单次验证：在合适位置安排复核。
    RECHECK = "recheck"
    #: 具体误解处改用更小步提示、反例或直接讲解，避免同类追问。
    HINT_GRANULARITY = "hint_granularity"


class StudyAdoptedRecord(BaseModel):
    """采用快照中的一条已核验学情（身份、版本与调整方向）。"""

    record_id: str = Field(description="档案条目标识。")
    record_version: int = Field(ge=1, description="采用时的记录版本；变化即使快照失效。")
    subject: str = Field(default="", description="知识点所属学科。")
    title: str = Field(default="", description="知识点标题。")
    usage_context: str = Field(default="", description="知识点用途上下文（同名区分依据）。")
    verdict: StudyDiagnosisVerdict = Field(description="已核验的当前知识判断。")
    performance: str = Field(default="", description="最近一次核验到的具体表现。")
    adjustments: list[StudyAdaptationAdjustment] = Field(
        default_factory=list, description="本条学情对本轮教学的具体调整方向。"
    )
    reason: str = Field(default="", description="简短的调整原因（可展示给用户）。")

    def identity(self) -> tuple[str, str, str]:
        return (self.subject, self.title, self.usage_context)


class StudyAdaptationSnapshot(BaseModel):
    """一轮教学的唯一采用快照；重试沿用同一快照，调用前逐条复验。"""

    adoption_id: str = Field(description="采用快照标识。")
    protocol_version: str = Field(
        default=ADAPTATION_CONTRACT_VERSION, description="采用协议版本。"
    )
    conversation_id: str = Field(description="采用发生的会话标识。")
    goal_id: str = Field(default="", description="采用时的当前学习目标；无目标为空。")
    goal_version: int = Field(default=0, ge=0, description="采用时的目标版本（无目标为 0）。")
    route_version: int = Field(default=0, ge=0, description="采用时的路线版本（无路线为 0）。")
    entries: list[StudyAdoptedRecord] = Field(
        default_factory=list, description="正式采用的已核验学情（有界、按相关性选择）。"
    )
    uncertainty_note: str = Field(
        default="",
        description="相关但存在方向冲突、待进一步验证的学情说明；不当作正式事实。",
    )
    created_at: datetime = Field(description="快照形成时间。")

    def is_empty(self) -> bool:
        return not self.entries and not self.uncertainty_note


class StudyAdoptionProjection(BaseModel):
    """用户可见的一次采用记录：采用了哪些学情、按什么方向调整。"""

    adoption_id: str = Field(description="采用记录标识。")
    goal_id: str = Field(default="", description="采用时的学习目标。")
    goal_version: int = Field(default=0, ge=0, description="采用时的目标版本。")
    route_version: int = Field(default=0, ge=0, description="采用时的路线版本。")
    status: str = Field(description="采用状态：applied / invalidated。")
    invalid_reason: str = Field(
        default="", description="失效的真实原因；正常生效时为空。"
    )
    entries: list[StudyAdoptedRecord] = Field(
        default_factory=list, description="正式采用的已核验学情与调整方向。"
    )
    uncertainty_note: str = Field(
        default="", description="相关但待进一步验证的学情说明。"
    )
    created_at: datetime = Field(description="采用发生时间。")
