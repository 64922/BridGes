"""持久节点执行内核（改进工单 10）。

模块黑盒 ``service.run`` 不再构成恢复边界：解析、消歧、校验、请求、
缓冲、核验与呈现都成为真实的持久节点；节点在自己的事务里提交类型化
产物、输入依赖/哈希、质量裁决、完成收据与待投递事件，检查点只保存
引用。恢复先读完成收据：已完成且输入一致则回填产物引用，未完成则
安全重试。通勤是首个纵向试点，其余领域按工单 24–36 迁移。
"""

from bridges.kernel.contracts import (
    ARTIFACT_SCHEMA_VERSION,
    KERNEL_CONTRACT_VERSION,
    ArtifactTrust,
    InputDependency,
    KernelFailure,
    KernelResult,
    KernelStatus,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceipt,
    NodeReceiptStatus,
    NodeSpec,
    NodeState,
    PendingNodeEvent,
    QualityGateResult,
    QualityVerdict,
    RecipeDefinition,
    RecipeInputs,
    RecoveryPolicy,
)
from bridges.kernel.executor import GateHandler, NodeKernel, NodeRunner
from bridges.kernel.guard import CommitDecision, RunCommitGuard
from bridges.kernel.registry import RecipeRegistry, RecipeValidationError
from bridges.kernel.repository import NodeKernelRepository

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "KERNEL_CONTRACT_VERSION",
    "ArtifactTrust",
    "CommitDecision",
    "GateHandler",
    "InputDependency",
    "KernelFailure",
    "KernelResult",
    "KernelStatus",
    "NodeArtifact",
    "NodeExecution",
    "NodeInvocation",
    "NodeKernel",
    "NodeKernelRepository",
    "NodeReceipt",
    "NodeReceiptStatus",
    "NodeRunner",
    "NodeSpec",
    "NodeState",
    "PendingNodeEvent",
    "QualityGateResult",
    "QualityVerdict",
    "RecipeDefinition",
    "RecipeInputs",
    "RecipeRegistry",
    "RecipeValidationError",
    "RecoveryPolicy",
    "RunCommitGuard",
]
