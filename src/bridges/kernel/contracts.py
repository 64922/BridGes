"""内核共享合同：配方、产物、收据、质量门与执行结果。

字段是逻辑合同，不预先规定领域表布局；所有产物载荷都必须 JSON 可
序列化（检查点与审计只保存引用，不保存服务或协程）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

#: 内核合同版本（配方、产物、收据与提交守卫共同解释该版本）。
KERNEL_CONTRACT_VERSION = "node-kernel-v1"

#: 产物结构版本（载荷字段变化时递增；与内核合同版本独立）。
ARTIFACT_SCHEMA_VERSION = "artifact-v1"


class ArtifactTrust(StrEnum):
    """产物可信状态：与运行状态、任务完成状态相互独立。"""

    DRAFT = "draft"
    EVIDENCE_BOUND = "evidence_bound"
    QUALIFIED = "qualified"
    CONFLICTED = "conflicted"
    INVALIDATED = "invalidated"


class QualityVerdict(StrEnum):
    """质量门的结构化裁决（模型不能自行宣布“已核实”）。"""

    PASS = "pass"
    NEED_INPUT = "need_input"
    REPAIRABLE_FAILURE = "repairable_failure"
    BLOCKED = "blocked"
    INVALIDATED = "invalidated"


class NodeReceiptStatus(StrEnum):
    """完成收据状态；只有 ``completed`` 会被恢复路径回填复用。"""

    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    BLOCKED = "blocked"
    FAILED = "failed"


class RecoveryPolicy(StrEnum):
    """节点失败/暂停后的恢复分支（由配方声明，代码执行）。"""

    RETRY_NODE = "retry_node"
    ASK_INPUT = "ask_input"
    BLOCK = "block"
    FAIL_RUN = "fail_run"


class KernelStatus(StrEnum):
    """一次内核执行的整体收敛状态（不等于任务完成）。"""

    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    BLOCKED = "blocked"
    FAILED = "failed"
    STOPPED = "stopped"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True)
class InputDependency:
    """一个输入依赖：上游节点、产物引用与内容哈希。"""

    node: str
    artifact_id: str | None
    content_hash: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> InputDependency:
        return cls(
            node=str(payload.get("node", "")),
            artifact_id=payload.get("artifact_id"),
            content_hash=payload.get("content_hash"),
        )


@dataclass(frozen=True, slots=True)
class QualityGateResult:
    """一条质量门的结构化裁决结果。"""

    gate: str
    verdict: QualityVerdict
    code: str = ""
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict is QualityVerdict.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "verdict": self.verdict.value,
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class NodeArtifact:
    """类型化节点产物（版本化、带可信状态、输入依赖与来源范围）。"""

    artifact_id: str
    account_id: str
    conversation_id: str
    run_id: str
    task_id: str | None
    task_version: int | None
    recipe_id: str
    recipe_version: str
    node: str
    artifact_type: str
    schema_version: str
    capability_version: str
    trust_state: ArtifactTrust
    input_key: str
    input_deps: tuple[InputDependency, ...]
    source_refs: tuple[str, ...]
    read_scope: str
    requirement_coverage: tuple[dict[str, Any], ...]
    unconfirmed: tuple[str, ...]
    error: dict[str, Any] | None
    payload: dict[str, Any]
    content_hash: str
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def identity_id(account_id: str, conversation_id: str, node: str, input_key: str) -> str:
        """产物身份：同一账户/会话/节点/输入键只保留一个产物行。"""
        material = f"{account_id}\x1f{conversation_id}\x1f{node}\x1f{input_key}"
        return "art_" + sha256(material.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def hash_payload(payload: Mapping[str, Any]) -> str:
        material = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(material.encode("utf-8")).hexdigest()

    @classmethod
    def build(
        cls,
        *,
        account_id: str,
        conversation_id: str,
        run_id: str,
        task_id: str | None,
        task_version: int | None,
        recipe_id: str,
        recipe_version: str,
        node: str,
        artifact_type: str,
        capability_version: str,
        trust_state: ArtifactTrust,
        input_key: str,
        input_deps: tuple[InputDependency, ...],
        source_refs: tuple[str, ...],
        read_scope: str,
        requirement_coverage: tuple[dict[str, Any], ...],
        unconfirmed: tuple[str, ...],
        error: dict[str, Any] | None,
        payload: dict[str, Any],
        now: datetime,
    ) -> NodeArtifact:
        return cls(
            artifact_id=cls.identity_id(account_id, conversation_id, node, input_key),
            account_id=account_id,
            conversation_id=conversation_id,
            run_id=run_id,
            task_id=task_id,
            task_version=task_version,
            recipe_id=recipe_id,
            recipe_version=recipe_version,
            node=node,
            artifact_type=artifact_type,
            schema_version=ARTIFACT_SCHEMA_VERSION,
            capability_version=capability_version,
            trust_state=trust_state,
            input_key=input_key,
            input_deps=input_deps,
            source_refs=source_refs,
            read_scope=read_scope,
            requirement_coverage=requirement_coverage,
            unconfirmed=unconfirmed,
            error=error,
            payload=payload,
            content_hash=cls.hash_payload(payload),
            created_at=now,
            updated_at=now,
        )

    def verify_hash(self) -> bool:
        """内容哈希自校验：载荷被篡改时拒绝复用。"""
        return self.content_hash == self.hash_payload(self.payload)

    @property
    def reusable(self) -> bool:
        return self.trust_state in {
            ArtifactTrust.DRAFT,
            ArtifactTrust.EVIDENCE_BOUND,
            ArtifactTrust.QUALIFIED,
        }


@dataclass(frozen=True, slots=True)
class NodeReceipt:
    """节点完成收据：恢复时先读它，再决定回填产物还是重试节点。"""

    receipt_id: str
    account_id: str
    conversation_id: str
    run_id: str
    node: str
    input_key: str
    output_key: str
    artifact_id: str | None
    status: NodeReceiptStatus
    quality_verdict: QualityVerdict
    attempt: int
    lease_owner: str | None
    detail: dict[str, Any]
    committed_at: datetime


@dataclass(frozen=True, slots=True)
class PendingNodeEvent:
    """随收据同一事务保存、待投递的真实节点事件。"""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RecipeInputs:
    """``input_key`` 函数的输入：不可变回合引用 + 已完成节点产物。"""

    account_id: str
    conversation_id: str
    run_id: str
    user_message_id: str
    user_content: str
    task_id: str | None
    task_version: int | None
    wait_identity: str | None
    artifacts: Mapping[str, NodeArtifact]
    #: 领域提供的不可变上下文摘要（例如会话前文与求解时间快照），
    #: 只进入输入键计算，不作为独立状态保存。
    prior_digest: str | None = None
    buffer_snapshot: str | None = None


@dataclass(frozen=True, slots=True)
class NodeInvocation:
    """一个工作节点的最小输入：引用、依赖产物与剩余预算。"""

    spec: NodeSpec
    inputs: RecipeInputs
    dependencies: Mapping[str, NodeArtifact]
    remaining_budget_ms: int | None

    @property
    def account_id(self) -> str:
        return self.inputs.account_id

    @property
    def conversation_id(self) -> str:
        return self.inputs.conversation_id

    @property
    def run_id(self) -> str:
        return self.inputs.run_id

    @property
    def node(self) -> str:
        return self.spec.name


@dataclass(frozen=True, slots=True)
class NodeExecution:
    """节点执行结果：产物、结构化裁决、待投递事件与恢复分支提示。"""

    artifact: NodeArtifact
    verdict: QualityVerdict
    status: NodeReceiptStatus
    detail: dict[str, Any] = field(default_factory=dict)
    events: tuple[PendingNodeEvent, ...] = ()
    stop_recipe: bool = False
    recovery: RecoveryPolicy = RecoveryPolicy.RETRY_NODE
    failure: dict[str, Any] | None = None
    #: 本节点确认后显式失效的历史节点产物（方式变更使路线/时间失效）。
    invalidate_nodes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NodeState:
    """内核回执里一个节点的实际状态（执行或收据复用）。"""

    node: str
    status: NodeReceiptStatus
    verdict: QualityVerdict
    artifact_id: str | None
    reused: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KernelFailure:
    """停止整条配方的失败/等待描述（由领域层翻译为用户可见状态）。"""

    node: str
    verdict: QualityVerdict
    code: str
    message: str
    retryable: bool
    recovery: RecoveryPolicy


@dataclass(frozen=True, slots=True)
class KernelResult:
    """一次内核执行的结果：节点回执、产物引用、交付产物与整体状态。"""

    status: KernelStatus
    nodes: tuple[NodeState, ...]
    artifacts: tuple[NodeArtifact, ...]
    delivery: NodeArtifact | None
    failure: KernelFailure | None = None
    stopped_at: str | None = None
    rejection_code: str | None = None

    def artifact(self, node: str) -> NodeArtifact | None:
        for artifact in self.artifacts:
            if artifact.node == node:
                return artifact
        return None


#: 配方声明的节点输入键函数：从回合引用与依赖产物计算出确定性复用键。
InputKey = Callable[[RecipeInputs], str]


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """一个注册节点：能力、依赖、必要/可选门与恢复分支。"""

    name: str
    capability: str
    artifact_type: str
    capability_version: str
    input_key: InputKey = field(compare=False, repr=False)
    depends_on: tuple[str, ...] = ()
    required_gates: tuple[str, ...] = ()
    optional_gates: tuple[str, ...] = ()
    recovery: RecoveryPolicy = RecoveryPolicy.RETRY_NODE
    description: str = ""


@dataclass(frozen=True, slots=True)
class RecipeDefinition:
    """登记配方：必经顺序、能力集合、门与恢复分支。"""

    recipe_id: str
    recipe_version: str
    nodes: tuple[NodeSpec, ...]

    def node(self, name: str) -> NodeSpec:
        for spec in self.nodes:
            if spec.name == name:
                return spec
        raise KeyError(name)
