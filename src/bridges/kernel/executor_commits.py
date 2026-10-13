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

from collections.abc import Callable
from hashlib import sha256

from bridges.kernel.contracts import (
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
    RecipeInputs,
)
from bridges.kernel.executor_contract import NodeKernelContract

#: 质量门处理器：读取节点调用与该次执行结果，返回结构化裁决。
GateHandler = Callable[[NodeInvocation, NodeExecution], QualityGateResult]

#: 节点执行体：返回类型化产物、裁决与待投递事件。
NodeRunner = Callable[[NodeInvocation], NodeExecution]

#: 事件汇：把真实开始/完成的节点事件投递给进度持久化（SSE 回放）。
EventSink = Callable[[str, str, int | None], None]



class _NodeKernelCommitsMixin(NodeKernelContract):
    def _commit_execution(
        self,
        *,
        inputs: RecipeInputs,
        spec: NodeSpec,
        receipt: NodeReceipt | None,
        receipt_key: str,
        execution: NodeExecution,
        duration_ms: int,
        artifacts: dict[str, NodeArtifact],
        states: list[NodeState],
        event_sink: EventSink | None,
    ) -> KernelResult | None:
        """节点局部事务：守卫校验与产物+收据+事件在同一事务提交。

        返回非 None 表示守卫拒绝（租约/终态/版本变化），配方立即终止。
        """
        artifact = execution.artifact
        with self._repository.transaction():
            decision = self._guard.verify()
            if not decision.ok:
                if decision.code == "run_stopped":
                    return self._terminal_result(
                        KernelStatus.STOPPED,
                        states,
                        artifacts,
                        stopped_at=spec.name,
                        rejection_code=decision.code,
                    )
                return self._terminal_result(
                    KernelStatus.REJECTED,
                    states,
                    artifacts,
                    stopped_at=spec.name,
                    rejection_code=decision.code,
                )
            self._repository.save_artifact(artifact)
            invalidated_ids: list[str] = []
            if execution.invalidate_nodes:
                invalidated_ids = self._repository.invalidate_node_artifacts(
                    inputs.account_id,
                    inputs.conversation_id,
                    execution.invalidate_nodes,
                    except_artifact_ids=(artifact.artifact_id,),
                    now=self._clock(),
                )
            # 同一运行内失效后重算也保留旧完成证明，另记新的产物版本收据。
            commit_key = receipt_key
            if receipt is not None and receipt.status is NodeReceiptStatus.COMPLETED:
                commit_key = sha256(f"{receipt_key}:{artifact.artifact_id}".encode()).hexdigest()
            receipt_record = self._commit_receipt(
                inputs, spec.name, commit_key, execution, duration_ms
            )
            completion_event = PendingNodeEvent(
                kind="node_completed",
                payload={"duration_ms": duration_ms},
            )
            # 失效事实随收据进入外箱，旧产物版本同时保留供审计和导出。
            invalidation_events = (
                (
                    PendingNodeEvent(
                        kind="node_artifacts_invalidated",
                        payload={
                            "nodes": list(execution.invalidate_nodes),
                            "artifact_ids": invalidated_ids,
                        },
                    ),
                )
                if invalidated_ids
                else ()
            )
            self._repository.enqueue_events(
                receipt_id=receipt_record.receipt_id,
                account_id=inputs.account_id,
                run_id=inputs.run_id,
                node=spec.name,
                events=(*execution.events, *invalidation_events, completion_event),
                now=self._clock(),
            )
            # 领域属主的本地效果与产物、收据、外箱同事务；失败整体回滚。
            if (
                self._commit_effect is not None
                and execution.status is NodeReceiptStatus.COMPLETED
                and execution.verdict is QualityVerdict.PASS
            ):
                self._commit_effect(artifact)
        artifacts[spec.name] = artifact
        states.append(
            NodeState(
                node=spec.name,
                status=execution.status,
                verdict=execution.verdict,
                artifact_id=artifact.artifact_id,
                reused=False,
                detail=dict(execution.detail),
            )
        )
        self._deliver_outbox(inputs.account_id, inputs.run_id, event_sink)
        return None
