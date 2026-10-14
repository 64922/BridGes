"""后台独立诊断与账户级学习档案的公开投影与语义合同。

学习诊断在回复交付后独立核验实际作答；本模块只承载用户可见的
诊断任务状态与学习档案投影。私有评价依据与诊断提示词永不进入用户
投影；诊断结论是证据语义（完成方式、具体误解与不确定性），不是
百分比、人格、注意力或整体能力标签。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.study_verification import StudyEvidenceAssistance

#: 本模块语义的协议版本；诊断幂等单位与结果行记录同一版本。
DIAGNOSIS_CONTRACT_VERSION = "study-diagnosis-v1"


class StudyDiagnosisStatus(StrEnum):
    """诊断任务的真实业务状态；队列领取状态不算正式学情。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INVALIDATED = "invalidated"


class StudyDiagnosisVerdict(StrEnum):
    """独立诊断对本次作答的结论；不是能力等级或掌握百分比。"""

    INDEPENDENT_COMPLETION = "independent_completion"
    HINTED_COMPLETION = "hinted_completion"
    MISCONCEPTION = "misconception"
    UNVERIFIED = "unverified"
    UNDETERMINED = "undetermined"


class StudyDiagnosisTaskProjection(BaseModel):
    """一条诊断任务的用户可见状态；不含评价依据或提示词。"""

    task_id: str = Field(description="稳定诊断任务标识。")
    status: StudyDiagnosisStatus = Field(description="任务业务状态。")
    question: str = Field(default="", description="被核验的问题摘要。")
    knowledge_context: str = Field(
        default="", description="作答时固定的知识上下文摘要。"
    )
    evidence_message_id: str | None = Field(
        default=None, description="作答消息标识（真实依据定位）。"
    )
    failure_reason: str = Field(
        default="", description="失败的真实中文说明；成功或待处理时为空。"
    )
    attempts: int = Field(default=0, ge=0, description="已执行的诊断尝试次数。")
    retryable: bool = Field(
        default=False, description="失败后是否允许针对同一任务手动重试。"
    )
    created_at: datetime = Field(description="任务登记时间。")
    updated_at: datetime = Field(description="任务最近状态变化时间。")


class StudyRecordEvidenceProjection(BaseModel):
    """档案中一条已核验证据的定位；不复制回答正文或私有依据。"""

    evidence_id: str = Field(description="证据标识。")
    question: str = Field(default="", description="当时的题目摘要。")
    knowledge_context: str = Field(default="", description="当时的知识上下文摘要。")
    assistance: StudyEvidenceAssistance = Field(
        default=StudyEvidenceAssistance.NONE, description="作答前的实际帮助程度。"
    )
    verdict: StudyDiagnosisVerdict | None = Field(
        default=None, description="该证据的独立诊断结论。"
    )
    answer_message_id: str | None = Field(
        default=None, description="实际回答消息标识。"
    )
    feedback_message_id: str | None = Field(
        default=None, description="当轮反馈消息标识。"
    )
    occurred_at: datetime = Field(description="证据发生时间（归并顺序依据）。")


class StudyLearningRecordProjection(BaseModel):
    """账户级档案中一条知识点判断的最小可用投影。

    知识点身份包含学科与用途上下文；同名但上下文不同的概念是不同
    档案条目。单次完成只是当前证据下的判断，不表示长期掌握。
    """

    record_id: str = Field(description="稳定档案条目标识。")
    subject: str = Field(default="", description="知识点所属学科。")
    title: str = Field(default="", description="知识点标题。")
    usage_context: str = Field(
        default="", description="知识点的用途上下文；同名概念据此区分。"
    )
    verdict: StudyDiagnosisVerdict = Field(description="当前知识判断（证据语义）。")
    performance: str = Field(default="", description="最近一次核验到的具体表现。")
    supporting: list[str] = Field(
        default_factory=list, description="支持当前判断的依据要点。"
    )
    refuting: list[str] = Field(
        default_factory=list, description="与当前判断相抵触的依据要点。"
    )
    uncertainty: str = Field(default="", description="当前判断的不确定性说明。")
    next_validation: str = Field(default="", description="建议的后续验证方向。")
    pending_confirmation: bool = Field(
        default=False,
        description="存在方向冲突的证据，当前判断待进一步验证。",
    )
    evidence_count: int = Field(default=0, ge=0, description="已归并的证据条数。")
    evidences: list[StudyRecordEvidenceProjection] = Field(
        default_factory=list, description="按证据发生顺序排列的核验定位（有界）。"
    )
    created_at: datetime = Field(description="档案条目创建时间。")
    updated_at: datetime = Field(description="档案条目最近更新时间。")
