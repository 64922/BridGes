"""Workflow and WorkOrder lifecycle domain service.

This module implements the deep module boundary that compiles a `WorkOrder`
into a versioned run, drives the dual state machine (workflow run status and
artifact trust status), and produces `RunProjection`s for the task stage.

T006 uses an in-memory adapter so the seam can be exercised without requiring a
persistent orchestrator. The public interface is stable and will later be backed
by the workflow schema.
"""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bridges.ai import ModelGateway
from bridges.contracts.ai import ModelCallStatus, ModelRunLock
from bridges.contracts.identity import SubjectContext
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.profiles import SliceStatus
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.scope import ScopeAction, ScopeIsolationError
from bridges.contracts.workflows import (
    ArtifactTrustStatus,
    HumanTodoItem,
    HumanTodoStatus,
    NodeProgress,
    NodeStatus,
    RunContextEnvelope,
    RunProjection,
    WorkflowRunStatus,
    WorkOrder,
)
from bridges.invalidation import InvalidationError, InvalidationService
from bridges.profiles import ProfileService
from bridges.profiles.adapters import ProfileError
from bridges.scope import ScopeEnforcer

if TYPE_CHECKING:
    from bridges.observability.service import ObservabilityService


class WorkflowError(Exception):
    """Domain exception for workflow failures.

    The message is safe to expose to callers; it never leaks whether a run
    exists or belongs to another account.
    """


@dataclass
class _NodeDefinition:
    node_id: str
    node_name: str
    human_gate: bool = False
    capability_name: str | None = None
    capability_version: str | None = None


@dataclass
class _WorkflowDefinition:
    name: str
    version: str
    nodes: list[_NodeDefinition]
    terminal_states: list[WorkflowRunStatus]


@dataclass
class _RunRecord:
    run_id: str
    context: RunContextEnvelope
    work_order: WorkOrder
    status: WorkflowRunStatus
    artifact_trust_status: ArtifactTrustStatus
    nodes: list[NodeProgress] = field(default_factory=list)
    human_todos: list[HumanTodoItem] = field(default_factory=list)
    model_run_locks: list[ModelRunLock] = field(default_factory=list)
    current_node_index: int | None = None
    run_started_at: datetime | None = None
    run_ended_at: datetime | None = None
    cancel_reason: str | None = None


class WorkflowService:
    """In-memory workflow service for T006.

    The interface intentionally mirrors the eventual orchestrator-backed adapter
    so that later tickets can swap the implementation without changing callers.
    """

    def __init__(
        self,
        scope_enforcer: ScopeEnforcer | None = None,
        model_gateway: ModelGateway | None = None,
        observability_service: ObservabilityService | None = None,
        invalidation_service: InvalidationService | None = None,
        profile_service: ProfileService | None = None,
        pack_gate: Callable[[Sequence[str]], None] | None = None,
    ) -> None:
        self._workflows: dict[tuple[str, str], _WorkflowDefinition] = {}
        self._runs: dict[str, _RunRecord] = {}
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._model_gateway = model_gateway
        self._observability = observability_service
        self._invalidation = invalidation_service
        self._profile_service = profile_service
        self._pack_gate = pack_gate

    def _subject(self, account_id: str) -> SubjectContext:
        """Build a minimal subject context from an account id for scope checks."""
        from bridges.contracts.identity import AuthMethod

        return SubjectContext(
            account_id=account_id,
            session_id="service-session",
            auth_method=AuthMethod.PASSWORD,
        )

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _require_objects_active(self, object_refs: list[str], account_id: str) -> None:
        """Fail closed if any referenced object is revoked or tombstoned.

        T011: new reads and new runs must check current invalidation state before
        touching storage, cache, index or models.
        """
        if self._invalidation is None:
            return
        for object_id in object_refs:
            ref = ObjectRef(
                domain=ObjectDomain.PERSONAL_VAULT,
                owner_id=account_id,
                object_id=object_id,
                version=1,
            )
            try:
                self._invalidation.require_active(ref)
            except InvalidationError as exc:
                raise WorkflowError(str(exc)) from exc

    def set_pack_gate(self, gate: Callable[[Sequence[str]], None] | None) -> None:
        """T047: 设置领域包运行门；撤销或失效的包不能开始新运行。

        门由应用装配线注入（默认无门，保持单元测试独立）。
        """
        self._pack_gate = gate

    def _require_packs_usable(self, domain_pack_refs: Sequence[str]) -> None:
        """任一引用的领域包已撤销或失效时失败闭锁。"""
        if self._pack_gate is None:
            return
        self._pack_gate(list(domain_pack_refs))

    def register_workflow(
        self,
        *,
        name: str,
        version: str,
        nodes: list[dict[str, object]],
        terminal_states: list[WorkflowRunStatus],
    ) -> None:
        """Register a workflow template that can be compiled from a WorkOrder.

        This method is intended for test fixtures and built-in workflow setup;
        it is not exposed through the public API.

        Raises ``ValueError`` if ``terminal_states`` is empty — every workflow
        must declare at least one legal terminal state.
        """
        if not terminal_states:
            raise ValueError("工作流必须声明至少一个终止状态。")

        parsed_nodes: list[_NodeDefinition] = []
        for node in nodes:
            cap_name = node.get("capability_name")
            cap_version = node.get("capability_version")
            parsed_nodes.append(
                _NodeDefinition(
                    node_id=str(node["node_id"]),
                    node_name=str(node["node_name"]),
                    human_gate=bool(node.get("human_gate", False)),
                    capability_name=str(cap_name) if cap_name is not None else None,
                    capability_version=str(cap_version) if cap_version is not None else None,
                )
            )
        self._workflows[(name, version)] = _WorkflowDefinition(
            name=name,
            version=version,
            nodes=parsed_nodes,
            terminal_states=list(terminal_states),
        )

    def _require_record(self, account_id: str, run_id: str) -> _RunRecord:
        record = self._runs.get(run_id)
        if record is None or record.context.account_id != account_id:
            raise WorkflowError("运行不存在或没有访问权限。")
        subject = self._subject(account_id)
        project_ref = ObjectRef(
            domain=record.context.object_domain,
            owner_id=account_id,
            object_id=record.context.project_id,
            version=1,
        )
        try:
            self._scope_enforcer.authorize(subject, ScopeAction.READ, project_ref)
        except ScopeIsolationError as exc:
            raise WorkflowError(str(exc)) from exc
        return record

    def _lookup_workflow(self, name: str, version: str) -> _WorkflowDefinition:
        definition = self._workflows.get((name, version))
        if definition is None:
            raise WorkflowError("未知工作流。")
        return definition

    def _assert_transition(self, record: _RunRecord, allowed: set[WorkflowRunStatus]) -> None:
        if record.status not in allowed:
            raise WorkflowError("非法状态转换。")

    def _build_projection(self, record: _RunRecord) -> RunProjection:
        publish_eligible = (
            record.status == WorkflowRunStatus.SUCCEEDED
            and record.artifact_trust_status == ArtifactTrustStatus.APPROVED
            and all(todo.status != HumanTodoStatus.OPEN for todo in record.human_todos)
        )
        current_node_id = None
        if record.current_node_index is not None and 0 <= record.current_node_index < len(
            record.nodes
        ):
            current_node_id = record.nodes[record.current_node_index].node_id

        return RunProjection(
            run_id=record.run_id,
            project_id=record.context.project_id,
            workflow_name=record.context.workflow_name,
            workflow_version=record.context.workflow_version,
            run_status=record.status,
            artifact_trust_status=record.artifact_trust_status,
            publish_eligible=publish_eligible,
            objective=record.work_order.objective,
            success_criteria=record.work_order.success_criteria,
            risk_statement=record.work_order.risk_statement,
            current_node_id=current_node_id,
            nodes=list(record.nodes),
            human_todos=list(record.human_todos),
            model_run_locks=list(record.model_run_locks),
            run_started_at=record.run_started_at,
            run_ended_at=record.run_ended_at,
            cancel_reason=record.cancel_reason,
            context_envelope=record.context,
        )

    def _emit_audit(
        self,
        record: _RunRecord,
        action: AuditAction,
        result: AuditResult,
        *,
        reason: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        """Emit an audit event for a run if observability is attached."""
        if self._observability is None:
            return
        ctx = record.context
        from bridges.observability.telemetry_context import (
            build_correlation,
            get_correlation,
        )

        existing = get_correlation()
        correlation = build_correlation(
            trace_id=existing.trace_id if existing else None,
            span_id=existing.span_id if existing else None,
            run_id=ctx.run_id,
            account_id=ctx.account_id,
            project_id=ctx.project_id,
            tenant_id=ctx.tenant_id,
            object_domain=ctx.object_domain,
            workflow_name=ctx.workflow_name,
            workflow_version=ctx.workflow_version,
            authorization_version=ctx.authorization_snapshot,
            key_epoch=ctx.key_epoch,
        )
        self._observability.log_audit(
            actor_account_id=ctx.account_id,
            action=action,
            result=result,
            object_refs=list(ctx.object_refs),
            reason=reason,
            details=details or {},
            correlation=correlation,
        )

    def _maybe_summarize(self, record: _RunRecord, terminal_reason: str | None = None) -> None:
        """Build a run summary when the run reaches a terminal state.

        The summary is only materialized when observability is attached; callers
        can also build summaries explicitly from RunProjections.
        """
        if self._observability is None:
            return
        if record.status not in record.context.terminal_states:
            return
        projection = self._build_projection(record)
        audit_events = self._observability.get_run_audit_events(record.run_id)
        self._observability.summarize_run(
            projection,
            audit_event_refs=[e.event_id for e in audit_events],
            terminal_reason=terminal_reason,
        )

    def _compile_memory_slice_for_run(self, record: _RunRecord) -> list[str]:
        """Compile the minimal memory slice for a run if none was supplied.

        The slice is bound to the run, scoped to the project, and filtered by the
        workflow purpose. Models and worker nodes receive only the slice
        reference, never direct access to the full profile vault.
        """
        if self._profile_service is None:
            return []
        if record.work_order.memory_slice_refs:
            return list(record.work_order.memory_slice_refs)
        purpose = record.work_order.workflow_name
        slice_ = self._profile_service.compile_memory_slice(
            record.context.account_id,
            purpose=purpose,
            run_id=record.run_id,
            project_id=record.context.project_id,
            authorization_version=record.context.authorization_snapshot,
            key_epoch=record.context.key_epoch,
        )
        return [slice_.slice_id]

    def _invalidate_run_slices(
        self, record: _RunRecord, reason: str, status: SliceStatus
    ) -> None:
        """Invalidate all memory slices bound to a run."""
        if self._profile_service is None:
            return
        for slice_id in record.context.memory_slice_refs:
            with contextlib.suppress(ProfileError):
                self._profile_service.invalidate_slice(
                    record.context.account_id, slice_id, reason, status
                )

    def submit_work_order(self, account_id: str, order: WorkOrder) -> RunProjection:
        """Submit a WorkOrder and return a draft task-stage projection."""
        subject = self._subject(account_id)
        project_ref = ObjectRef(
            domain=ObjectDomain.PERSONAL_VAULT,
            owner_id=account_id,
            object_id=order.project_id,
            version=1,
        )
        try:
            self._scope_enforcer.authorize(subject, ScopeAction.EXECUTE, project_ref)
        except ScopeIsolationError as exc:
            raise WorkflowError(str(exc)) from exc

        self._require_objects_active(list(order.object_refs), account_id)
        self._require_packs_usable(order.domain_pack_refs)

        now = self._now()
        run_id = secrets.token_urlsafe(16)
        context = RunContextEnvelope(
            run_id=run_id,
            account_id=account_id,
            project_id=order.project_id,
            workflow_name=order.workflow_name,
            workflow_version=order.workflow_version,
            object_domain=ObjectDomain.PERSONAL_VAULT,
            object_refs=list(order.object_refs),
            memory_slice_refs=list(order.memory_slice_refs),
            domain_pack_refs=list(order.domain_pack_refs),
            submitted_at=now,
        )
        record = _RunRecord(
            run_id=run_id,
            context=context,
            work_order=order,
            status=WorkflowRunStatus.DRAFT,
            artifact_trust_status=ArtifactTrustStatus.NOT_CREATED,
        )
        self._runs[run_id] = record
        self._emit_audit(
            record,
            AuditAction.WORKORDER_SUBMIT,
            AuditResult.SUCCESS,
            details={
                "workflow_name": order.workflow_name,
                "workflow_version": order.workflow_version,
            },
        )
        return self._build_projection(record)

    def confirm_work_order(
        self,
        account_id: str,
        run_id: str,
        *,
        confirmed: bool = True,
    ) -> RunProjection:
        """Confirm the WorkOrder and compile/start the run."""
        record = self._require_record(account_id, run_id)
        self._assert_transition(record, {WorkflowRunStatus.DRAFT})
        self._require_objects_active(list(record.work_order.object_refs), account_id)
        self._require_packs_usable(record.context.domain_pack_refs)
        if not confirmed:
            raise WorkflowError("必须明确确认任务目标、成功标准和风险后才能启动。")

        definition = self._lookup_workflow(
            record.work_order.workflow_name,
            record.work_order.workflow_version,
        )
        if not definition.terminal_states:
            raise WorkflowError("工作流无终止条件，无法启动。")

        now = self._now()
        record.context.terminal_states = list(definition.terminal_states)
        record.context.confirmed_at = now
        record.run_started_at = now
        record.artifact_trust_status = ArtifactTrustStatus.DRAFT
        record.context.memory_slice_refs = self._compile_memory_slice_for_run(record)

        record.nodes = [
            NodeProgress(
                node_id=node.node_id,
                node_name=node.node_name,
                capability_ref=(
                    f"{node.capability_name}@{node.capability_version}"
                    if node.capability_name
                    else None
                ),
                status=NodeStatus.PENDING,
            )
            for node in definition.nodes
        ]

        if definition.nodes:
            record.current_node_index = 0
            first_node = definition.nodes[0]
            if first_node.human_gate:
                record.status = WorkflowRunStatus.WAITING_HUMAN
                record.human_todos.append(
                    HumanTodoItem(
                        todo_id=secrets.token_urlsafe(12),
                        title=f"人工确认：{first_node.node_name}",
                        description="请在继续前检查该节点的输入、范围和风险。",
                        status=HumanTodoStatus.OPEN,
                        created_at=now,
                    )
                )
            else:
                record.nodes[0].status = NodeStatus.RUNNING
                record.nodes[0].started_at = now
                record.status = WorkflowRunStatus.RUNNING
        else:
            # Empty workflow is allowed only if it declares a terminal state.
            record.status = WorkflowRunStatus.SUCCEEDED
            record.artifact_trust_status = ArtifactTrustStatus.QUALIFIED
            record.run_ended_at = now

        projection = self._build_projection(record)
        self._emit_audit(
            record,
            AuditAction.WORKORDER_CONFIRM,
            AuditResult.SUCCESS,
            details={"run_status": record.status.value},
        )
        self._maybe_summarize(record, terminal_reason="empty_workflow")
        return projection

    def get_run(self, account_id: str, run_id: str) -> RunProjection:
        """Return the current task-stage projection for a run.

        The projection is rebuilt from the immutable run context and stored state,
        not from chat history or transient session state.
        """
        record = self._require_record(account_id, run_id)
        return self._build_projection(record)

    def find_runs_using_pack(self, pack_id: str, version: str) -> list[RunProjection]:
        """T047: 返回引用指定包版本的全部运行投影（用于失效影响定位）。"""
        ref = f"{pack_id}@{version}"
        return [
            self._build_projection(record)
            for record in self._runs.values()
            if ref in record.context.domain_pack_refs
        ]

    def _invoke_node_capability(
        self, record: _RunRecord, node_def: _NodeDefinition
    ) -> ModelRunLock | None:
        """Invoke the capability required by a node and record the run lock.

        Raises ``WorkflowError`` when the gateway is missing, which is treated as
        a deterministic blocking condition.
        """
        if self._model_gateway is None:
            raise WorkflowError("模型网关未配置，无法调用能力。")
        if node_def.capability_name is None:
            raise WorkflowError("节点未声明能力。")

        result = self._model_gateway.invoke(
            capability_name=node_def.capability_name,
            capability_version=node_def.capability_version or "1",
            run_context=record.context,
            payload={"node_id": node_def.node_id},
        )
        if result.lock is not None:
            record.model_run_locks.append(result.lock)
        return result.lock

    def advance_run(self, account_id: str, run_id: str) -> RunProjection:
        """Deterministically advance the run by one node.

        This method is exposed for tests and deterministic simulation; production
        advancement will be driven by the orchestrator.
        """
        record = self._require_record(account_id, run_id)
        self._assert_transition(record, {WorkflowRunStatus.RUNNING, WorkflowRunStatus.RETRYING})
        self._require_objects_active(list(record.work_order.object_refs), account_id)

        if record.current_node_index is None:
            raise WorkflowError("当前没有可执行的节点。")

        now = self._now()
        current = record.nodes[record.current_node_index]
        definition = self._lookup_workflow(
            record.work_order.workflow_name,
            record.work_order.workflow_version,
        )
        current_def = definition.nodes[record.current_node_index]

        if current_def.capability_name is not None:
            lock = self._invoke_node_capability(record, current_def)
            self._emit_audit(
                record,
                AuditAction.MODEL_INVOCATION,
                (
                    AuditResult.SUCCESS
                    if lock is not None and lock.status == ModelCallStatus.SUCCESS
                    else AuditResult.BLOCKED
                    if lock is not None and lock.status == ModelCallStatus.BLOCKED
                    else AuditResult.RETRYABLE_FAIL
                ),
                details={
                    "node_id": current_def.node_id,
                    "capability_ref": (
                        f"{current_def.capability_name}@{current_def.capability_version}"
                    ),
                    "model_call_status": lock.status.value if lock else "none",
                    "retry_count": lock.retry_count if lock else 0,
                },
            )
            if lock is None or lock.status in {
                ModelCallStatus.BLOCKED,
                ModelCallStatus.RETRYABLE_FAIL,
            }:
                current.status = NodeStatus.FAILED
                current.failure_reason = lock.degradation_reason if lock else "模型调用失败"
                record.status = WorkflowRunStatus.BLOCKED
                record.run_ended_at = now
                projection = self._build_projection(record)
                self._maybe_summarize(record, terminal_reason=current.failure_reason)
                return projection
            current.status = NodeStatus.COMPLETED
            current.completed_at = now
            current.output_ref = lock.lock_id
        else:
            current.status = NodeStatus.COMPLETED
            current.completed_at = now
            current.output_ref = f"artifact://{record.run_id}/{current.node_id}"

        projection = self._advance_to_next(record, now)
        self._emit_audit(
            record,
            AuditAction.RUN_ADVANCE,
            AuditResult.SUCCESS,
            details={"node_id": current_def.node_id, "run_status": record.status.value},
        )
        self._maybe_summarize(record)
        return projection

    def _advance_to_next(self, record: _RunRecord, now: datetime) -> RunProjection:
        definition = self._lookup_workflow(
            record.work_order.workflow_name,
            record.work_order.workflow_version,
        )
        # current_node_index is guaranteed non-None when this method is called
        # (both callers check before invoking), but we guard explicitly so that
        # a bug elsewhere fails noisily rather than silently producing wrong
        # indices via Python's falsy-0 coercion.
        if record.current_node_index is None:
            raise WorkflowError("当前没有可执行的节点。")
        next_index = record.current_node_index + 1
        if next_index >= len(definition.nodes):
            record.status = WorkflowRunStatus.SUCCEEDED
            record.artifact_trust_status = ArtifactTrustStatus.QUALIFIED
            record.run_ended_at = now
            record.current_node_index = None
            projection = self._build_projection(record)
            self._maybe_summarize(record, terminal_reason="all_nodes_completed")
            return projection

        record.current_node_index = next_index
        next_node_def = definition.nodes[next_index]
        next_node = record.nodes[next_index]
        if next_node_def.human_gate:
            record.status = WorkflowRunStatus.WAITING_HUMAN
            record.human_todos.append(
                HumanTodoItem(
                    todo_id=secrets.token_urlsafe(12),
                    title=f"人工确认：{next_node_def.node_name}",
                    description="请在继续前检查该节点的输入、范围和风险。",
                    status=HumanTodoStatus.OPEN,
                    created_at=now,
                )
            )
        else:
            record.status = WorkflowRunStatus.RUNNING
            next_node.status = NodeStatus.RUNNING
            next_node.started_at = now

        return self._build_projection(record)

    def cancel_run(self, account_id: str, run_id: str, reason: str) -> RunProjection:
        """Cancel a run that has not reached a terminal state."""
        record = self._require_record(account_id, run_id)
        self._assert_transition(
            record,
            {
                WorkflowRunStatus.DRAFT,
                WorkflowRunStatus.COMPILED,
                WorkflowRunStatus.RUNNING,
                WorkflowRunStatus.WAITING_HUMAN,
                WorkflowRunStatus.RETRYING,
                WorkflowRunStatus.BLOCKED,
            },
        )
        now = self._now()
        record.status = WorkflowRunStatus.CANCELLED
        record.cancel_reason = reason
        record.run_ended_at = now
        self._invalidate_run_slices(
            record, f"run_cancelled:{reason}", SliceStatus.CANCELLED
        )
        projection = self._build_projection(record)
        self._emit_audit(
            record,
            AuditAction.RUN_CANCEL,
            AuditResult.SUCCESS,
            details={"reason": reason},
        )
        self._maybe_summarize(record, terminal_reason=f"cancelled:{reason}")
        return projection

    def resolve_human_todo(
        self,
        account_id: str,
        run_id: str,
        todo_id: str,
        resolution: str,
    ) -> RunProjection:
        """Resolve a named human todo and continue past its gate."""
        record = self._require_record(account_id, run_id)
        self._assert_transition(record, {WorkflowRunStatus.WAITING_HUMAN})

        todo = next((t for t in record.human_todos if t.todo_id == todo_id), None)
        if todo is None or todo.status != HumanTodoStatus.OPEN:
            raise WorkflowError("没有待处理的人工待办。")

        now = self._now()
        todo.status = HumanTodoStatus.RESOLVED
        todo.resolved_at = now
        todo.resolution = resolution

        if record.current_node_index is not None:
            current = record.nodes[record.current_node_index]
            current.status = NodeStatus.COMPLETED
            current.completed_at = now
            current.output_ref = f"artifact://{record.run_id}/{current.node_id}"

        self._emit_audit(
            record,
            AuditAction.HUMAN_TODO_RESOLVE,
            AuditResult.SUCCESS,
            details={"todo_id": todo_id, "resolution": resolution},
        )
        return self._advance_to_next(record, now)
