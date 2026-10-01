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

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from bridges.kernel.contracts import (
    ArtifactTrust,
    KernelFailure,
    KernelResult,
    KernelStatus,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceipt,
    NodeReceiptStatus,
    NodeState,
    PendingNodeEvent,
    QualityGateResult,
    QualityVerdict,
    RecipeDefinition,
    RecipeInputs,
)
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.registry import RecipeRegistry
from bridges.kernel.repository import (
    NodeKernelRepository,
    output_identity,
    receipt_identity,
)

#: 质量门处理器：读取节点调用与该次执行结果，返回结构化裁决。
GateHandler = Callable[[NodeInvocation, NodeExecution], QualityGateResult]

#: 节点执行体：返回类型化产物、裁决与待投递事件。
NodeRunner = Callable[[NodeInvocation], NodeExecution]

#: 事件汇：把真实开始/完成的节点事件投递给进度持久化（SSE 回放）。
EventSink = Callable[[str, str, int | None], None]


class NodeKernel:
    """受控的持久节点执行器（配方 + 收据 + 质量门 + 提交守卫）。"""

    def __init__(
        self,
        *,
        registry: RecipeRegistry,
        repository: NodeKernelRepository,
        guard: RunCommitGuard,
        gates: Mapping[str, GateHandler],
        runner: NodeRunner,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._guard = guard
        self._gates = dict(gates)
        self._runner = runner
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        *,
        recipe: RecipeDefinition,
        inputs: RecipeInputs,
        remaining_budget_ms: int | None = None,
        event_sink: EventSink | None = None,
        stop_event: threading.Event | None = None,
    ) -> KernelResult:
        """按配方的必经顺序执行；返回节点回执、产物与整体状态。"""
        self._registry.validate(recipe)
        self._guard.capture()
        self._deliver_outbox(inputs.account_id, inputs.run_id, event_sink)
        artifacts: dict[str, NodeArtifact] = {}
        states: list[NodeState] = []

        for spec in recipe.nodes:
            # 停止请求在节点边界生效：不进入节点、不提交任何产物。
            if stop_event is not None and stop_event.is_set():
                return self._terminal_result(
                    KernelStatus.STOPPED,
                    states,
                    artifacts,
                    stopped_at=spec.name,
                )
            key_inputs = RecipeInputs(
                account_id=inputs.account_id,
                conversation_id=inputs.conversation_id,
                run_id=inputs.run_id,
                user_message_id=inputs.user_message_id,
                user_content=inputs.user_content,
                task_id=inputs.task_id,
                task_version=inputs.task_version,
                wait_identity=inputs.wait_identity,
                artifacts=artifacts,
                prior_digest=inputs.prior_digest,
                buffer_snapshot=inputs.buffer_snapshot,
            )
            input_key = spec.input_key(key_inputs)
            receipt = self._repository.load_receipt(
                inputs.account_id, inputs.run_id, spec.name, input_key
            )
            reused = self._reuse_from_receipt(receipt, spec.name)
            if reused is None:
                # 跨运行恢复：收据是运行内的事实，产物按会话与输入键持久；
                # 地点成功而路线失败的重试据此跳过解析/定位，只重跑路线及下游。
                reused = self._reuse_from_artifact(inputs, spec.name, input_key)
            if reused is not None:
                artifacts[spec.name] = reused
                states.append(
                    NodeState(
                        node=spec.name,
                        status=NodeReceiptStatus.COMPLETED,
                        verdict=(
                            receipt.quality_verdict if receipt else QualityVerdict.PASS
                        ),
                        artifact_id=reused.artifact_id,
                        reused=True,
                        detail={"reason": "receipt_completed"},
                    )
                )
                self._deliver_outbox(inputs.account_id, inputs.run_id, event_sink)
                continue

            invocation = NodeInvocation(
                spec=spec,
                inputs=key_inputs,
                dependencies={name: artifacts[name] for name in spec.depends_on},
                remaining_budget_ms=remaining_budget_ms,
            )
            if event_sink is not None:
                event_sink(spec.name, "started", None)
            started = time.monotonic()
            execution = self._run_with_gates(invocation)
            duration_ms = max(1, int((time.monotonic() - started) * 1000))
            artifact = execution.artifact

            # 节点局部事务：守卫校验与产物+收据+事件在同一事务提交。
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
                receipt_record = self._commit_receipt(
                    inputs, spec.name, input_key, execution, duration_ms
                )
                completion_event = PendingNodeEvent(
                    kind="node_completed",
                    payload={"duration_ms": duration_ms},
                )
                # 失效事实随收据进入外箱（审计/导出可读）：同输入重跑会原位
                # 更新产物行，失效轨迹不能只留在被覆盖的产物行里。
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
            if execution.stop_recipe:
                return self._terminal_result(
                    self._status_for(execution),
                    states,
                    artifacts,
                    delivery=artifact,
                    failure=KernelFailure(
                        node=spec.name,
                        verdict=execution.verdict,
                        code=str(execution.detail.get("code", "")),
                        message=str(execution.detail.get("message", "")),
                        retryable=bool(execution.detail.get("retryable", False)),
                        recovery=execution.recovery,
                    ),
                )

        # 交付前最终校验：即使整条配方全部命中收据/产物（本轮没有任何节点
        # 提交），失去租约、已停止或任务版本变化后的迟到交付也必须被拒绝，
        # 绝不因“没有写入”而绕过提交守卫。
        with self._repository.transaction():
            final_decision = self._guard.verify()
        if not final_decision.ok:
            if final_decision.code == "run_stopped":
                return self._terminal_result(KernelStatus.STOPPED, states, artifacts)
            return self._terminal_result(
                KernelStatus.REJECTED,
                states,
                artifacts,
                rejection_code=final_decision.code,
            )
        delivery = artifacts.get(recipe.nodes[-1].name) if recipe.nodes else None
        return KernelResult(
            status=KernelStatus.COMPLETED,
            nodes=tuple(states),
            artifacts=tuple(artifacts.values()),
            delivery=delivery,
        )

    # ------------------------------------------------------------------

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
        if not artifact.reusable or not artifact.verify_hash():
            return None
        return artifact

    def _reuse_from_artifact(
        self, inputs: RecipeInputs, node: str, input_key: str
    ) -> NodeArtifact | None:
        artifact = self._repository.find_artifact(
            inputs.account_id, inputs.conversation_id, node, input_key
        )
        if artifact is None or not artifact.reusable or not artifact.verify_hash():
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


__all__ = ["EventSink", "GateHandler", "NodeKernel", "NodeRunner"]
