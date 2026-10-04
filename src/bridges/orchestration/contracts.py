"""跨模块复合计划与综合核验的公共合同（改进工单 37）。

本模块把 ``docs/workflow/orchestration.md`` 的跨模块调度、质量门与交付
合同固化为可序列化类型，供调度器、综合门、聊天父图与 38 投影共用：

1. **有限计划**：计划只引用服务端已登记的模块配方与能力版本；每一步都
   标注依赖、必要/可选、参数来源与数据分级，代码拒绝循环、未登记能力、
   无来源参数与硬条件绕过。
2. **共享预算**：所有分支共用 09 的运行预算账本；自动调整整次运行最多
   一轮（``adjustment_rounds_max``），第二轮由账本拒绝。
3. **运行状态与可信状态分开**：``StepState`` 描述调度，``trust_state``
   使用内核 ``ArtifactTrust``；失败的必要步骤只阻塞依赖它的结论。
4. **综合质量门**：综合必须逐条绑定来源步骤与证据；最终门检查新事实、
   限定条件与引用错连；独立核验按风险触发，同模型独立调用不等于独立
   事实来源。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.modules import ModuleDelivery, ModuleQueryRecord

#: 复合编排合同版本（计划、步骤与综合载荷共同解释该版本）。
COMPOSITE_CONTRACT_VERSION = "composite-orchestration-v1"

#: 综合产物使用的登记配方标识（持久化到内核产物表，供 38 与审计读取）。
COMPOSITE_RECIPE_ID = "composite-orchestration"

#: 综合产物类型。
COMPOSITE_ARTIFACT_TYPE = "orchestration.synthesis"


class StepState(StrEnum):
    """一个复合步骤的调度状态（与产物可信状态相互独立）。"""

    PENDING = "pending"
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    INVALIDATED = "invalidated"


class CompositeStatus(StrEnum):
    """一次复合运行的整体收敛状态（不等于任务完成）。"""

    COMPLETED = "completed"
    PARTIAL = "partial"
    NEEDS_INPUT = "needs_input"
    BLOCKED = "blocked"
    FAILED = "failed"
    STOPPED = "stopped"
    REJECTED = "rejected"


class ParameterSource(StrEnum):
    """步骤参数的权威来源；没有来源的参数一律拒绝。"""

    USER_MESSAGE = "user_message"
    TASK_CONDITION = "task_condition"
    UPSTREAM_ARTIFACT = "upstream_artifact"
    PUBLIC_MINIMAL_QUERY = "public_minimal_query"


class DataClassification(StrEnum):
    """参数数据分级：私人材料绝不进入公网步骤。"""

    PUBLIC = "public"
    PRIVATE = "private"
    DERIVED_PUBLIC = "derived_public"


class PlanViolationCode(StrEnum):
    """计划校验的结构化拒绝码（模型不能绕过）。"""

    UNKNOWN_MODULE = "unknown_module"
    UNKNOWN_CAPABILITY = "unknown_capability"
    RECIPE_VERSION_MISMATCH = "recipe_version_mismatch"
    DUPLICATE_STEP = "duplicate_step"
    CYCLIC_PLAN = "cyclic_plan"
    MISSING_PREREQUISITE = "missing_prerequisite"
    SKIPPED_PREREQUISITE = "skipped_prerequisite"
    MODE_CONFLICT = "mode_conflict"
    HARD_CONDITION_BYPASS = "hard_condition_bypass"
    PARAMETER_SOURCE_MISSING = "parameter_source_missing"
    STEP_MISSING_BINDING = "step_missing_binding"
    BUDGET_EXCEEDED = "budget_exceeded"
    ADJUSTMENT_ROUNDS_EXHAUSTED = "adjustment_rounds_exhausted"
    IDENTITY_REQUIRED = "identity_required"
    MANDATORY_STEP_MISSING = "mandatory_step_missing"


class PlanViolation(BaseModel):
    """一条计划拒绝记录。"""

    model_config = ConfigDict(extra="forbid")

    code: PlanViolationCode
    message: str
    step_id: str | None = None


class PlanValidation(BaseModel):
    """计划校验裁决；``ok`` 为假时不得进入执行。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    violations: list[PlanViolation] = Field(default_factory=list)

    def codes(self) -> list[str]:
        return [item.code.value for item in self.violations]


class ParameterBinding(BaseModel):
    """一个步骤参数的来源绑定。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="参数名（与配方/模块约定一致）。")
    source: ParameterSource = Field(description="权威来源。")
    source_ref: str | None = Field(
        default=None, description="来源引用（上游步骤 ID／消息 ID／条件键）。"
    )
    classification: DataClassification = Field(
        default=DataClassification.PRIVATE,
        description="数据分级；私人数据不得进入公网步骤。",
    )
    leaves_device: bool = Field(
        default=False,
        description="该参数是否离开设备/进入外部服务；私人参数为真即硬限制绕过。",
    )
    note: str = Field(default="", description="中文说明（可审计）。")


class CompositeStep(BaseModel):
    """复合计划中的一个登记步骤（模块级，对应一条完整配方）。"""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(description="计划内唯一 ID，例如 paper／resources。")
    module_id: str
    recipe_id: str
    recipe_version: str
    capability: str = Field(description="入口能力（已登记）。")
    capability_version: str
    depends_on: list[str] = Field(default_factory=list)
    required: bool = Field(
        default=True,
        description="必要步骤失败会阻塞依赖它的结论；可选失败只留缺口。",
    )
    purpose: str = Field(description="这一步服务于原始目标的哪一部分。")
    public_network: bool = Field(
        default=False, description="该步骤是否向公网发出最小查询。"
    )
    parameter_bindings: list[ParameterBinding] = Field(default_factory=list)
    identity_required: bool = Field(
        default=False,
        description="结论依赖上游确认的身份（如选定论文）；未确认时不得宣称对应。",
    )
    identity_source_step: str | None = Field(
        default=None, description="提供身份确认的上游步骤。"
    )
    condition_keys: list[str] = Field(
        default_factory=list,
        description="该步骤输入依赖的条件键；条件变化时随输入依赖失效。",
    )


class CompositePlan(BaseModel):
    """经代码验证后冻结的复合计划版本（绑定任务版本与运行）。"""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(default=COMPOSITE_CONTRACT_VERSION)
    plan_id: str
    revision: int = Field(default=0, description="0 为初始计划；最多 1 为受控调整。")
    goal: str
    user_message_id: str
    task_id: str | None = None
    task_version: int | None = None
    mode: str = "companion"
    steps: list[CompositeStep] = Field(default_factory=list)
    hard_conditions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime

    def step(self, step_id: str) -> CompositeStep:
        for item in self.steps:
            if item.step_id == step_id:
                return item
        raise KeyError(step_id)


class Claim(BaseModel):
    """一条可核验的关键结论：必须绑定支持它的来源证据。"""

    model_config = ConfigDict(extra="forbid")

    text: str
    evidence_refs: list[str] = Field(default_factory=list)
    qualification: str | None = Field(
        default=None, description="限定条件（未确认项、范围、时效等）。"
    )
    risk: str | None = Field(
        default=None,
        description="风险触发类别（见 VerificationTrigger）；纯工具/闲聊为空。",
    )


class StepFailure(BaseModel):
    """步骤失败的可操作描述。"""

    model_config = ConfigDict(extra="forbid")

    node: str | None = None
    code: str
    message: str
    retryable: bool = False


class StepResult(BaseModel):
    """一个步骤的完整结果：调度状态、可信状态、证据与交付。"""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    module_id: str
    state: StepState
    trust_state: str = Field(default="draft", description="ArtifactTrust 值。")
    reused: bool = False
    delivery: ModuleDelivery | None = None
    artifact_refs: dict[str, str] = Field(default_factory=dict)
    claims: list[Claim] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    read_scope: str = ""
    summary: str = Field(default="", description="供综合使用的简短结论。")
    unconfirmed: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    failure: StepFailure | None = None
    queries: list[ModuleQueryRecord] = Field(default_factory=list)
    duration_ms: int | None = None
    input_fingerprint: str | None = Field(
        default=None,
        description="已绑定参数（不含正文）的确定性指纹；仅用于兼容复用判定。",
    )
    resolved_param_names: list[str] = Field(
        default_factory=list,
        description="实际解析的参数名（不保存私人值）；供审计核对参数来源。",
    )
    external_calls: int = Field(default=0, ge=0, description="本步骤实际发生的外部调用数。")
    evidence: dict[str, str] = Field(default_factory=dict, description="引用对应的实际证据。")
    scope_key: str | None = None

    @property
    def delivered(self) -> bool:
        return self.state is StepState.COMPLETED


class VerificationTrigger(StrEnum):
    """独立核验的风险触发条件（普通闲聊/纯工具不强制模型裁判）。"""

    COMPLEX_COMPARISON = "complex_comparison"
    SOURCE_CONFLICT = "source_conflict"
    KEY_FORMULA_OR_NUMBER = "key_formula_or_number"
    PERSONAL_GAP = "personal_gap"
    IMPLEMENTATION_EVIDENCE = "implementation_evidence"
    ORDINARY_CHAT = "ordinary_chat"
    DETERMINISTIC_TOOL = "deterministic_tool"


class VerificationVerdict(StrEnum):
    """独立核验裁决（核验结构与引用也要校验）。"""

    PASS = "pass"
    REPAIR = "repair"
    BLOCK = "block"
    NOT_APPLICABLE = "not_applicable"


class VerificationSource(StrEnum):
    """核验来源：同模型独立调用不等于独立事实来源。"""

    DETERMINISTIC_CODE = "deterministic_code"
    INDEPENDENT_MODEL = "independent_model"
    SAME_MODEL_INDEPENDENT_CALL = "same_model_independent_call"


class IndependentVerification(BaseModel):
    """一条独立核验记录（只看结论、原证据与规则，不看生成者辩护）。"""

    model_config = ConfigDict(extra="forbid")

    required: bool
    trigger: VerificationTrigger
    verdict: VerificationVerdict
    source: VerificationSource
    independent_fact_source: bool = False
    rules: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    note: str = ""


class SynthesisSection(BaseModel):
    """综合正文的一段：按目标组织，不拼独立长回答。"""

    model_config = ConfigDict(extra="forbid")

    title: str
    body: str
    step_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    qualification: str | None = None


class SynthesisDraft(BaseModel):
    """综合草案：结论、限定条件与阻塞项分开。"""

    model_config = ConfigDict(extra="forbid")

    summary: str
    sections: list[SynthesisSection] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    blocked: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class SynthesisGateResult(BaseModel):
    """综合后的最终门裁决：新事实、限定条件与引用错连。"""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    code: str = ""
    message: str = ""
    new_facts: list[str] = Field(default_factory=list)
    missing_qualifications: list[str] = Field(default_factory=list)
    mislinked_refs: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    verifications: list[IndependentVerification] = Field(default_factory=list)


class CompositeOutcome(BaseModel):
    """一次复合运行的整体结果（供聊天终态提交与 38 投影）。"""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(default=COMPOSITE_CONTRACT_VERSION)
    status: CompositeStatus
    plan: CompositePlan
    steps: list[StepResult] = Field(default_factory=list)
    synthesis: SynthesisDraft | None = None
    gate: SynthesisGateResult | None = None
    final_content: str = ""
    blocked_conclusions: list[str] = Field(default_factory=list)
    delivered_steps: list[str] = Field(default_factory=list)
    adjustments_used: int = 0
    rejection_code: str | None = None
    created_at: datetime

    def step(self, step_id: str) -> StepResult:
        for item in self.steps:
            if item.step_id == step_id:
                return item
        raise KeyError(step_id)


__all__ = [
    "COMPOSITE_ARTIFACT_TYPE",
    "COMPOSITE_CONTRACT_VERSION",
    "COMPOSITE_RECIPE_ID",
    "Claim",
    "CompositeOutcome",
    "CompositePlan",
    "CompositeStatus",
    "CompositeStep",
    "DataClassification",
    "IndependentVerification",
    "ParameterBinding",
    "ParameterSource",
    "PlanValidation",
    "PlanViolation",
    "PlanViolationCode",
    "StepFailure",
    "StepResult",
    "StepState",
    "SynthesisDraft",
    "SynthesisGateResult",
    "SynthesisSection",
    "VerificationSource",
    "VerificationTrigger",
    "VerificationVerdict",
]
