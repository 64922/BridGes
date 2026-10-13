"""持久节点执行内核：收据优先恢复、质量门与统一提交。

执行语义（与 ``docs/workflow/orchestration.md`` 第 8 节一致）：

- 每个节点在局部事务里提交产物、输入依赖/哈希、质量裁决、完成收据与
  待投递事件；检查点只保存引用；
- 恢复先读完成收据：已完成且输入一致则回填产物引用（不重复执行、
  不重复本地效果），未完成或输入变化则安全重试该节点；
- 提交前在同一事务内校验租约、停止、运行终态、任务版本与消息归属；
  旧租约或停止后的迟到结果被拒绝，不覆盖新状态；
- 只有配置内的节点与质量门可以执行；必要门未通过则节点产物失效并
  按声明的恢复分支收敛。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from bridges.kernel.contracts import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactTrust,
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
    QualityGateResult,
    QualityVerdict,
    RecipeDefinition,
    RecipeInputs,
)
from bridges.kernel.executor_contract import NodeKernelContract
from bridges.kernel.repository import (
    output_identity,
    receipt_identity,
)

#: 质量门处理器：读取节点调用与该次执行结果，返回结构化裁决。
GateHandler = Callable[[NodeInvocation, NodeExecution], QualityGateResult]

#: 节点执行体：返回类型化产物、裁决与待投递事件。
NodeRunner = Callable[[NodeInvocation], NodeExecution]

#: 事件汇：把真实开始/完成的节点事件投递给进度持久化（SSE 回放）。
EventSink = Callable[[str, str, int | None], None]



class _NodeKernelReceiptsMixin(NodeKernelContract):
    def _compatible(
        self, artifact: NodeArtifact, recipe: RecipeDefinition, spec: NodeSpec
    ) -> bool:
        return (
            artifact.recipe_id == recipe.recipe_id
            and artifact.recipe_version == recipe.recipe_version
            and artifact.capability_version == spec.capability_version
            and artifact.schema_version == ARTIFACT_SCHEMA_VERSION
            and artifact.artifact_type == spec.artifact_type
        )
    def _reuse_from_receipt(
        self, receipt: NodeReceipt | None, node: str
    ) -> NodeArtifact | None:
        if receipt is None or receipt.status is not NodeReceiptStatus.COMPLETED:
            return None
        if receipt.artifact_id is None:
            return None
        artifact = self._repository.get_artifact(receipt.account_id, receipt.artifact_id)
        if artifact is None or artifact.node != node:
            return None
        if (
            not artifact.reusable
            or artifact.payload.get("partial") is True
            or not artifact.verify_hash()
        ):
            return None
        if receipt.output_key != output_identity(artifact.content_hash, receipt.quality_verdict):
            return None
        return artifact
    def _reuse_from_artifact(
        self, inputs: RecipeInputs, node: str, input_key: str
    ) -> NodeArtifact | None:
        artifact = self._repository.find_artifact(
            inputs.account_id, inputs.conversation_id, node, input_key
        )
        if (
            artifact is None
            or not artifact.reusable
            or artifact.payload.get("partial") is True
            or not artifact.verify_hash()
        ):
            return None
        return artifact
    def _run_with_gates(self, invocation: NodeInvocation) -> NodeExecution:
        execution = self._runner(invocation)
        spec = invocation.spec
        optional: list[dict[str, object]] = []
        for gate in spec.optional_gates:
            optional.append(self._gates[gate](invocation, execution).to_dict())
        for gate in spec.required_gates:
            result = self._gates[gate](invocation, execution)
            if result.passed:
                continue
            artifact = replace(
                execution.artifact,
                trust_state=ArtifactTrust.INVALIDATED,
                error={
                    "gate": gate,
                    "code": result.code,
                    "message": result.message,
                },
            )
            return NodeExecution(
                artifact=artifact,
                verdict=result.verdict,
                status=(
                    NodeReceiptStatus.NEEDS_INPUT
                    if result.verdict is QualityVerdict.NEED_INPUT
                    else NodeReceiptStatus.FAILED
                ),
                detail={
                    **execution.detail,
                    "code": result.code,
                    "message": result.message,
                    "retryable": result.verdict is QualityVerdict.REPAIRABLE_FAILURE,
                    "failed_gate": gate,
                    "optional_gates": optional,
                },
                stop_recipe=True,
                recovery=spec.recovery,
                failure=execution.failure,
            )
        if optional:
            execution = replace(
                execution,
                detail={**execution.detail, "optional_gates": optional},
            )
        return execution
    def _commit_receipt(
        self,
        inputs: RecipeInputs,
        node: str,
        input_key: str,
        execution: NodeExecution,
        duration_ms: int,
    ) -> NodeReceipt:
        artifact = execution.artifact
        return self._repository.save_receipt(
            NodeReceipt(
                receipt_id=receipt_identity(
                    inputs.account_id, inputs.run_id, node, input_key
                ),
                account_id=inputs.account_id,
                conversation_id=inputs.conversation_id,
                run_id=inputs.run_id,
                node=node,
                input_key=input_key,
                output_key=output_identity(artifact.content_hash, execution.verdict),
                artifact_id=artifact.artifact_id,
                status=execution.status,
                quality_verdict=execution.verdict,
                attempt=1,
                lease_owner=self._guard.lease_owner,
                detail={**execution.detail, "duration_ms": duration_ms},
                committed_at=self._clock(),
            )
        )
    def _status_for(self, execution: NodeExecution) -> KernelStatus:
        if execution.verdict is QualityVerdict.NEED_INPUT:
            return KernelStatus.NEEDS_INPUT
        if execution.verdict is QualityVerdict.BLOCKED:
            return KernelStatus.BLOCKED
        if execution.verdict is QualityVerdict.INVALIDATED:
            return KernelStatus.INVALIDATED
        return KernelStatus.FAILED
    def _terminal_result(
        self,
        status: KernelStatus,
        states: Sequence[NodeState],
        artifacts: Mapping[str, NodeArtifact],
        *,
        delivery: NodeArtifact | None = None,
        failure: KernelFailure | None = None,
        stopped_at: str | None = None,
        rejection_code: str | None = None,
    ) -> KernelResult:
        return KernelResult(
            status=status,
            nodes=tuple(states),
            artifacts=tuple(artifacts.values()),
            delivery=delivery,
            failure=failure,
            stopped_at=stopped_at,
            rejection_code=rejection_code,
        )
    def _deliver_outbox(
        self, account_id: str, run_id: str, event_sink: EventSink | None
    ) -> None:
        if event_sink is None:
            return
        pending = self._repository.undelivered_events(account_id, run_id)
        for item in pending:
            if item["kind"] == "node_completed":
                duration = item["payload"].get("duration_ms")
                event_sink(str(item["node"]), "completed", duration)
            with self._repository.transaction():
                self._repository.mark_events_delivered(
                    account_id,
                    str(item["receipt_id"]),
                    (int(item["seq"]),),
                    now=self._clock(),
                )
