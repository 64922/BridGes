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

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256

from bridges.kernel.contracts import (
    ARTIFACT_SCHEMA_VERSION,
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

#: 质量门处理器：读取节点调用与该次执行结果，返回结构化裁决。
GateHandler = Callable[[NodeInvocation, NodeExecution], QualityGateResult]

#: 节点执行体：返回类型化产物、裁决与待投递事件。
NodeRunner = Callable[[NodeInvocation], NodeExecution]

#: 事件汇：把真实开始/完成的节点事件投递给进度持久化（SSE 回放）。
EventSink = Callable[[str, str, int | None], None]



class _NodeKernelExecutionMixin(NodeKernelContract):
    def execute(
        self,
        *,
        recipe: RecipeDefinition,
        inputs: RecipeInputs,
        remaining_budget_ms: int | None = None,
        event_sink: EventSink | None = None,
        stop_event: threading.Event | None = None,
    ) -> KernelResult:
        """按配方的必经顺序执行；返回节点回执、产物与整体状态。

        连续登记在同一 ``parallel_group`` 的节点互不依赖，在组内并发执行
        （并发数受 ``recipe.parallel_limit`` 约束）；组内每个节点仍在自己的
        局部事务里提交产物、收据与事件，提交按配方顺序串行。
        """
        self._registry.validate(recipe)
        self._guard.capture()
        self._deliver_outbox(inputs.account_id, inputs.run_id, event_sink)
        artifacts: dict[str, NodeArtifact] = {}
        states: list[NodeState] = []

        index = 0
        nodes = recipe.nodes
        while index < len(nodes):
            spec = nodes[index]
            if spec.parallel_group is not None:
                members: list[NodeSpec] = []
                while (
                    index < len(nodes)
                    and nodes[index].parallel_group == spec.parallel_group
                ):
                    members.append(nodes[index])
                    index += 1
                terminal = self._execute_group(
                    recipe=recipe,
                    members=members,
                    inputs=inputs,
                    artifacts=artifacts,
                    states=states,
                    remaining_budget_ms=remaining_budget_ms,
                    event_sink=event_sink,
                    stop_event=stop_event,
                )
                if terminal is not None:
                    return terminal
                continue
            index += 1
            terminal = self._execute_single(
                recipe=recipe,
                spec=spec,
                inputs=inputs,
                artifacts=artifacts,
                states=states,
                remaining_budget_ms=remaining_budget_ms,
                event_sink=event_sink,
                stop_event=stop_event,
            )
            if terminal is not None:
                return terminal

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
    def _prepare(
        self,
        recipe: RecipeDefinition,
        spec: NodeSpec,
        inputs: RecipeInputs,
        artifacts: Mapping[str, NodeArtifact],
    ) -> tuple[RecipeInputs, str, str, NodeReceipt | None, NodeArtifact | None]:
        """计算输入键并解析复用：完成收据优先，其次按输入键回填产物。"""
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
        # 收据按实际合同区分，避免升级后命中不可覆盖的旧完成收据。
        receipt_key = sha256(json.dumps([
            input_key, recipe.recipe_id, recipe.recipe_version,
            spec.capability_version, ARTIFACT_SCHEMA_VERSION, spec.artifact_type,
            spec.required_gates, spec.optional_gates,
        ]).encode()).hexdigest()
        receipt = self._repository.load_receipt(
            inputs.account_id, inputs.run_id, spec.name, receipt_key
        )
        reused = self._reuse_from_receipt(receipt, spec.name)
        if reused is not None and not self._compatible(reused, recipe, spec):
            reused = None
        if reused is None:
            # 跨运行恢复：收据是运行内的事实，产物按会话与输入键持久；
            # 只重跑输入变化的节点及其下游。
            reused = self._reuse_from_artifact(inputs, spec.name, input_key)
            if reused is not None and not self._compatible(reused, recipe, spec):
                reused = None
        return key_inputs, input_key, receipt_key, receipt, reused
    def _record_reuse(
        self,
        spec: NodeSpec,
        reused: NodeArtifact,
        receipt: NodeReceipt | None,
        states: list[NodeState],
        artifacts: dict[str, NodeArtifact],
    ) -> None:
        artifacts[spec.name] = reused
        states.append(
            NodeState(
                node=spec.name,
                status=NodeReceiptStatus.COMPLETED,
                verdict=(receipt.quality_verdict if receipt else QualityVerdict.PASS),
                artifact_id=reused.artifact_id,
                reused=True,
                detail={"reason": "receipt_completed"},
            )
        )
    def _execute_single(
        self,
        *,
        recipe: RecipeDefinition,
        spec: NodeSpec,
        inputs: RecipeInputs,
        artifacts: dict[str, NodeArtifact],
        states: list[NodeState],
        remaining_budget_ms: int | None,
        event_sink: EventSink | None,
        stop_event: threading.Event | None,
    ) -> KernelResult | None:
        """执行一个串行节点；返回非 None 表示配方已终止。"""
        # 停止请求在节点边界生效：不进入节点、不提交任何产物。
        if stop_event is not None and stop_event.is_set():
            return self._terminal_result(
                KernelStatus.STOPPED, states, artifacts, stopped_at=spec.name
            )
        key_inputs, input_key, receipt_key, receipt, reused = self._prepare(
            recipe, spec, inputs, artifacts
        )
        if reused is not None:
            self._record_reuse(spec, reused, receipt, states, artifacts)
            self._deliver_outbox(inputs.account_id, inputs.run_id, event_sink)
            return None
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
        terminal = self._commit_execution(
            inputs=inputs,
            spec=spec,
            receipt=receipt,
            receipt_key=receipt_key,
            execution=execution,
            duration_ms=duration_ms,
            artifacts=artifacts,
            states=states,
            event_sink=event_sink,
        )
        if terminal is not None:
            return terminal
        if execution.stop_recipe:
            return self._terminal_result(
                self._status_for(execution),
                states,
                artifacts,
                delivery=execution.artifact,
                failure=KernelFailure(
                    node=spec.name,
                    verdict=execution.verdict,
                    code=str(execution.detail.get("code", "")),
                    message=str(execution.detail.get("message", "")),
                    retryable=bool(execution.detail.get("retryable", False)),
                    recovery=execution.recovery,
                ),
            )
        return None
    def _execute_group(
        self,
        *,
        recipe: RecipeDefinition,
        members: Sequence[NodeSpec],
        inputs: RecipeInputs,
        artifacts: dict[str, NodeArtifact],
        states: list[NodeState],
        remaining_budget_ms: int | None,
        event_sink: EventSink | None,
        stop_event: threading.Event | None,
    ) -> KernelResult | None:
        """并发执行一组互不依赖的节点；提交与失效按配方顺序串行。

        组内某个节点失败且声明 ``stop_recipe`` 时，同组其他节点已提交的
        结果仍然保留（部分交付由领域层按产物与收据解释），随后按配方顺序
        以第一个停止节点收敛整条配方。
        """
        if stop_event is not None and stop_event.is_set():
            return self._terminal_result(
                KernelStatus.STOPPED, states, artifacts, stopped_at=members[0].name
            )
        pending: list[tuple[NodeSpec, NodeReceipt | None, str, NodeInvocation]] = []
        for spec in members:
            key_inputs, _input_key, receipt_key, receipt, reused = self._prepare(
                recipe, spec, inputs, artifacts
            )
            if reused is not None:
                self._record_reuse(spec, reused, receipt, states, artifacts)
                self._deliver_outbox(inputs.account_id, inputs.run_id, event_sink)
                continue
            pending.append(
                (
                    spec,
                    receipt,
                    receipt_key,
                    NodeInvocation(
                        spec=spec,
                        inputs=key_inputs,
                        dependencies={name: artifacts[name] for name in spec.depends_on},
                        remaining_budget_ms=remaining_budget_ms,
                    ),
                )
            )
        if not pending:
            return None
        for spec, _receipt, _receipt_key, _invocation in pending:
            if event_sink is not None:
                event_sink(spec.name, "started", None)
        executions: dict[str, tuple[NodeExecution, int]] = {}
        if len(pending) == 1 or recipe.parallel_limit <= 1:
            for spec, _receipt, _receipt_key, invocation in pending:
                executions[spec.name] = self._run_timed(invocation)
        else:
            with ThreadPoolExecutor(
                max_workers=min(recipe.parallel_limit, len(pending)),
                thread_name_prefix="bridges-node",
            ) as pool:
                futures = {
                    pool.submit(self._run_timed, invocation): spec.name
                    for spec, _receipt, _receipt_key, invocation in pending
                }
                for future in as_completed(futures):
                    executions[futures[future]] = future.result()
        for spec, receipt, receipt_key, _invocation in pending:
            execution, duration_ms = executions[spec.name]
            terminal = self._commit_execution(
                inputs=inputs,
                spec=spec,
                receipt=receipt,
                receipt_key=receipt_key,
                execution=execution,
                duration_ms=duration_ms,
                artifacts=artifacts,
                states=states,
                event_sink=event_sink,
            )
            if terminal is not None:
                return terminal
        for spec, _receipt, _receipt_key, _invocation in pending:
            execution, _duration_ms = executions[spec.name]
            if execution.stop_recipe:
                return self._terminal_result(
                    self._status_for(execution),
                    states,
                    artifacts,
                    delivery=execution.artifact,
                    failure=KernelFailure(
                        node=spec.name,
                        verdict=execution.verdict,
                        code=str(execution.detail.get("code", "")),
                        message=str(execution.detail.get("message", "")),
                        retryable=bool(execution.detail.get("retryable", False)),
                        recovery=execution.recovery,
                    ),
                )
        return None
    def _run_timed(self, invocation: NodeInvocation) -> tuple[NodeExecution, int]:
        started = time.monotonic()
        execution = self._run_with_gates(invocation)
        return execution, max(1, int((time.monotonic() - started) * 1000))
