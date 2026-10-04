"""跨模块复合计划、调度与综合核验（改进工单 37）。

对外入口：

- :class:`~bridges.orchestration.planner.CompositePlanner`：登记组合的
  确定性计划构造与结构化校验（循环、未登记能力、参数来源、硬条件、预算）；
- :class:`~bridges.orchestration.executor.CompositeExecutor`：依赖排序、
  独立分支并行、共享 09 账本、一轮受控调整与条件失效传播；
- :class:`~bridges.orchestration.synthesis.Synthesizer` /
  :class:`~bridges.orchestration.synthesis.FinalGate`：按一个目标综合并
  执行新事实/限定条件/引用错连的最终门；
- :class:`~bridges.orchestration.service.CompositeOrchestrationService`：
  把注册模块的无终态交付接到调度器并在同一事务统一提交。
"""

from bridges.orchestration.contracts import (
    COMPOSITE_ARTIFACT_TYPE,
    COMPOSITE_CONTRACT_VERSION,
    COMPOSITE_RECIPE_ID,
    Claim,
    CompositeOutcome,
    CompositePlan,
    CompositeStatus,
    CompositeStep,
    DataClassification,
    IndependentVerification,
    ParameterBinding,
    ParameterSource,
    PlanValidation,
    PlanViolation,
    PlanViolationCode,
    StepFailure,
    StepResult,
    StepState,
    SynthesisDraft,
    SynthesisGateResult,
    SynthesisSection,
    VerificationSource,
    VerificationTrigger,
    VerificationVerdict,
)
from bridges.orchestration.executor import (
    CompositeBudget,
    CompositeExecutor,
    FunctionStepRunner,
    StepRunContext,
    StepRunner,
)
from bridges.orchestration.planner import CompositePlanner
from bridges.orchestration.registry import (
    COMPOSITE_COMBINATIONS,
    ModuleDescriptor,
    ModuleRegistry,
)
from bridges.orchestration.synthesis import (
    RISK_TRIGGERS,
    FinalGate,
    IndependentVerifier,
    Synthesizer,
    synthesis_payload,
)

__all__ = [
    "COMPOSITE_ARTIFACT_TYPE",
    "COMPOSITE_COMBINATIONS",
    "COMPOSITE_CONTRACT_VERSION",
    "COMPOSITE_RECIPE_ID",
    "RISK_TRIGGERS",
    "Claim",
    "CompositeBudget",
    "CompositeExecutor",
    "CompositeOutcome",
    "CompositePlan",
    "CompositePlanner",
    "CompositeStatus",
    "CompositeStep",
    "DataClassification",
    "FinalGate",
    "FunctionStepRunner",
    "IndependentVerification",
    "IndependentVerifier",
    "ModuleDescriptor",
    "ModuleRegistry",
    "ParameterBinding",
    "ParameterSource",
    "PlanValidation",
    "PlanViolation",
    "PlanViolationCode",
    "StepFailure",
    "StepResult",
    "StepRunContext",
    "StepRunner",
    "StepState",
    "SynthesisDraft",
    "SynthesisGateResult",
    "SynthesisSection",
    "Synthesizer",
    "VerificationSource",
    "VerificationTrigger",
    "VerificationVerdict",
    "synthesis_payload",
]
