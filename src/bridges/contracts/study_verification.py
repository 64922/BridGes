"""嵌入式学习验证的公开投影与语义合同。

学习模式的即时验证与正式诊断分开：本模块只承载「待答问题」的
用户可见投影、证据语义枚举与评价依据形状。私有评价依据永不进入
用户投影；证据语义不是能力等级或掌握百分比。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

#: 本模块语义的协议版本；证据行随诊断消费记录同一版本。
VERIFICATION_CONTRACT_VERSION = "study-verification-v1"


class StudyCheckKind(StrEnum):
    """检查类型：开放探查记录理解线索，正式练习有固定评价依据。"""

    OPEN_PROBE = "open_probe"
    FORMAL_CHECK = "formal_check"


class StudyCheckAssistance(StrEnum):
    """待答问题实际获得过的帮助状态（按确实提供的提示派生）。"""

    NONE = "none"
    HINTED = "hinted"


class StudyUserIntent(StrEnum):
    """用户消息相对待答问题的关系；只有作答进入评价。"""

    ANSWER = "answer"
    QUESTION = "question"
    HINT_REQUEST = "hint_request"
    EXPLAIN_REQUEST = "explain_request"
    DIGRESSION = "digression"
    SKIP = "skip"
    OTHER = "other"


class StudyEvidenceOutcome(StrEnum):
    """一次评价的证据结论；没有作答时用 ``NO_ANSWER``。"""

    CORRECT = "correct"
    PARTIAL = "partial"
    MISCONCEPTION = "misconception"
    UNDETERMINED = "undetermined"
    NO_ANSWER = "no_answer"


class StudyEvidenceSemantics(StrEnum):
    """证据语义：实际完成方式与不确定性，不是能力等级。"""

    INDEPENDENT_COMPLETION = "independent_completion"
    HINTED_COMPLETION = "hinted_completion"
    PARTIAL_UNDERSTANDING = "partial_understanding"
    MISCONCEPTION = "misconception"
    UNDETERMINED = "undetermined"
    UNVERIFIED = "unverified"
    UNDERSTANDING_CLUE = "understanding_clue"


class StudyEvidenceAssistance(StrEnum):
    """作答发生前的实际帮助程度（不采信模型自称独立）。"""

    INDEPENDENT = "independent"
    AFTER_HINT = "after_hint"
    NONE = "none"


class StudyPendingCheckProjection(BaseModel):
    """待答问题的用户可见投影；不含评价依据或未提出的问题。"""

    check_id: str = Field(description="稳定检查标识。")
    kind: StudyCheckKind = Field(description="检查类型：开放探查或正式练习。")
    question: str = Field(description="已经展示给用户的题目或探查问题。")
    knowledge_context: str = Field(
        default="", description="展示问题时固定的知识上下文摘要。"
    )
    assistance: StudyCheckAssistance = Field(
        default=StudyCheckAssistance.NONE,
        description="本问题至今实际提供过的帮助状态。",
    )
    version: int = Field(ge=1, description="同一对话内检查的递增版本。")
    source_message_id: str = Field(description="展示该问题的助手消息标识。")
    created_at: datetime = Field(description="问题首次展示时间。")


class StudyEvaluationBasis(BaseModel):
    """正式练习展示前固定的私有评价依据（版本与含义不随作答改变）。"""

    evaluation_points: list[str] = Field(
        default_factory=list, description="要求的推理环节或应用点。"
    )
    accepted_equivalents: list[str] = Field(
        default_factory=list, description="按含义判断可接受的等价表述。"
    )
    key_misconceptions: list[str] = Field(
        default_factory=list, description="需要辨别的关键误解。"
    )
    assumptions: list[str] = Field(
        default_factory=list, description="题目必要假设与条件。"
    )
    expected_answer: str = Field(
        default="", description="数值或推导题的核验依据；无法核验时为空。"
    )
    contract_version: str = Field(
        default=VERIFICATION_CONTRACT_VERSION,
        description="评价依据协议版本。",
    )


class StudyCheckEvaluation(BaseModel):
    """作答评价；无法判断时保留待确认，不以格式失败判错。"""

    outcome: StudyEvidenceOutcome = StudyEvidenceOutcome.UNDETERMINED
    equivalent_accepted: bool = Field(
        default=False, description="等价表述是否按含义被接受。"
    )
    reasoning_note: str = Field(default="", description="观察到的具体推理或误解摘要。")
    supporting: list[str] = Field(default_factory=list, description="支持的证据要点。")
    missing: list[str] = Field(default_factory=list, description="缺失或不确定的信息。")
    feedback_direction: str = Field(
        default="", description="即时反馈的私有方向（不直接展示）。"
    )


class StudyCheckDraft(BaseModel):
    """展示前固定的检查草案；正式练习的评价依据随其冻结。"""

    kind: StudyCheckKind
    question: str
    knowledge_context: str = ""
    evaluation_points: list[str] = Field(default_factory=list)
    accepted_equivalents: list[str] = Field(default_factory=list)
    key_misconceptions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    expected_answer: str = ""

    def basis(self) -> StudyEvaluationBasis:
        return StudyEvaluationBasis(
            evaluation_points=list(self.evaluation_points),
            accepted_equivalents=list(self.accepted_equivalents),
            key_misconceptions=list(self.key_misconceptions),
            assumptions=list(self.assumptions),
            expected_answer=self.expected_answer,
        )
