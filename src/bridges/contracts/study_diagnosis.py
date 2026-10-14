"""后台独立诊断与账户级学习档案的公开投影与语义合同。

学习诊断在回复交付后独立核验实际作答；本模块只承载用户可见的
诊断任务状态与学习档案投影。私有评价依据与诊断提示词永不进入用户
投影；诊断结论是证据语义（完成方式、具体误解与不确定性），不是
百分比、人格、注意力或整体能力标签。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

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


class StudyRecordState(StrEnum):
    """学习档案的用户控制状态；纠正与删除都是账户级真实持久状态。"""

    ACTIVE = "active"
    CORRECTED = "corrected"
    DELETED = "deleted"


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
    """档案中一条已核验证据的定位；不复制回答正文或私有依据。

    来源（回答/反馈消息）已删除或不可读时 ``source_available`` 为 false
    并给出真实说明，不伪造引文。
    """

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
    occurred_at: datetime | None = Field(
        default=None, description="证据发生时间（归并顺序依据）；来源不可读时为 null。"
    )
    source_available: bool = Field(
        default=True, description="来源消息是否仍可读取；不可读时如实标注。"
    )
    source_note: str = Field(
        default="", description="来源不可用的真实说明；可用时为空。"
    )


class StudyLearningRecordProjection(BaseModel):
    """账户级档案中一条知识点判断的最小可用投影。

    知识点身份包含学科与用途上下文；同名但上下文不同的概念是不同
    档案条目。单次完成只是当前证据下的判断，不表示长期掌握。
    用户纠正与原始核验结论分开呈现：纠正保留主动来源与被取代结论，
    用户自评不冒充已核验掌握。
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
    record_version: int = Field(
        default=1, ge=1, description="记录版本；纠正与删除按该版本做并发校验。"
    )
    state: StudyRecordState = Field(
        default=StudyRecordState.ACTIVE, description="记录的用户控制状态。"
    )
    user_note: str = Field(
        default="",
        description="用户主动纠正的内容（用户自评，未经核验）；未纠正时为空。",
    )
    corrected_at: datetime | None = Field(
        default=None, description="用户最近一次纠正的时间。"
    )
    superseded_verdict: StudyDiagnosisVerdict | None = Field(
        default=None,
        description="被用户纠正取代的原核验结论；不再用于后续教学。",
    )
    superseded_performance: str = Field(
        default="", description="被取代结论的原表现描述（保留作对照）。"
    )
    source_available: bool = Field(
        default=True, description="本条档案的全部依据来源是否仍可读取。"
    )


class StudyRecordCorrectionRequest(BaseModel):
    """用户对一条学习记录的主动纠正（用户自评，未经独立核验）。"""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(
        min_length=1,
        max_length=500,
        description="用户说明的正确理解或判断依据；作为主动来源保存。",
    )
    expected_version: int = Field(
        ge=1, description="用户编辑时看到的记录版本；冲突时拒绝覆盖。"
    )


class StudyRecordDeletionRequest(BaseModel):
    """用户删除一条学习记录；按既有用户管理规范确认后提交。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(
        ge=1, description="用户确认删除时看到的记录版本；冲突时拒绝删除。"
    )


class StudyRecordBoundaryEntry(BaseModel):
    """供后续消费者使用的唯一有效档案读取边界条目。

    返回当前可用版本与失效原因：已删除记录（``effective=False``）不进入
    正式采用；用户纠正（``reason="user_corrected"``）保留主动来源而原核验
    结论被取代；来源不可读（``reason="source_unavailable"``）不得继续当作
    已核验事实。只有 ``verified`` 为 true 的条目可进入正式学情上下文。
    """

    record_id: str = Field(description="稳定档案条目标识。")
    record_version: int = Field(description="当前可用记录版本。")
    subject: str = Field(default="", description="知识点所属学科。")
    title: str = Field(default="", description="知识点标题。")
    usage_context: str = Field(default="", description="知识点的用途上下文。")
    verdict: StudyDiagnosisVerdict = Field(
        description="当前自动核验结论；用户纠正后仅作被取代的对照。"
    )
    performance: str = Field(default="", description="最近一次核验到的具体表现。")
    supporting: list[str] = Field(default_factory=list)
    refuting: list[str] = Field(default_factory=list)
    uncertainty: str = Field(default="", description="不确定性说明。")
    next_validation: str = Field(default="", description="建议的后续验证方向。")
    pending_confirmation: bool = Field(
        default=False, description="当前自动判断是否仍待进一步验证。"
    )
    state: StudyRecordState = Field(description="记录的用户控制状态。")
    effective: bool = Field(description="记录未被用户删除，可供读取。")
    verified: bool = Field(
        description="自动核验结论仍在生效（未被纠正且来源可用），可正式采用。"
    )
    reason: str = Field(
        default="",
        description="失效或不完全可用原因：user_corrected / user_deleted / "
        "source_unavailable；完全可用时为空。",
    )
    user_note: str = Field(
        default="", description="用户主动纠正内容（用户自评）；未纠正时为空。"
    )
    source_available: bool = Field(
        default=True, description="全部依据来源是否仍可读取。"
    )
    latest_evidence_at: datetime | None = Field(
        default=None, description="最新证据发生时间。"
    )
    updated_at: datetime = Field(description="记录最近更新时间。")
