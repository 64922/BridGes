"""Workflow and WorkOrder lifecycle domain service.

This module implements the deep module boundary that compiles a `WorkOrder`
into a versioned run, drives the dual state machine (workflow run status and
artifact trust status), and produces `RunProjection`s for the task stage.

T006 uses an in-memory adapter so the seam can be exercised without requiring a
persistent orchestrator. The public interface is stable and will later be backed
by the workflow schema.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from science_companion.contracts.identity import SubjectContext
from science_companion.contracts.projects import ObjectDomain, ObjectRef
from science_companion.contracts.scope import ScopeAction, ScopeIsolationError
from science_companion.contracts.workflows import (
    ArtifactTrustStatus,
    HumanTodoItem,
    HumanTodoStatus,
    NodeProgress,
    NodeStatus,
    RunContextEnvelope,
    RunProjection,
    WorkOrder,
    WorkflowRunStatus,
)
from science_companion.scope import ScopeEnforcer


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
    current_node_index: int | None = None
    run_started_at: datetime | None = None
    run_ended_at: datetime | None = None
    cancel_reason: str | None = None


class WorkflowService:
    """In-memory workflow service for T006.

    The interface intentionally mirrors the eventual orchestrator-backed adapter
    so that later tickets can swap the implementation without changing callers.
    """

    def __init__(self, scope_enforcer: ScopeEnforcer | None = None) -> None:
        self._workflows: dict[tuple[str, str], _WorkflowDefinition] = {}
        self._runs: dict[str, _RunRecord] = {}
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()

    def _subject(self, account_id: str) -> SubjectContext:
        """Build a minimal subject context from an account id for scope checks."""
        from science_companion.contracts.identity import AuthMethod

        return SubjectContext(
            account_id=account_id,
            session_id="service-session",
            auth_method=AuthMethod.PASSWORD,
        )

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

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
            parsed_nodes.append(
                _NodeDefinition(
                    node_id=str(node["node_id"]),
                    node_name=str(node["node_name"]),
                    human_gate=bool(node.get("human_gate", False)),
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
            run_started_at=record.run_started_at,
            run_ended_at=record.run_ended_at,
            cancel_reason=record.cancel_reason,
            context_envelope=record.context,
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
        return self._build_projection(record)

    def confirm_work_order(
        self,
        account_id: str,
        run_id: str,
        *,
        confirmed: bool = True,
    ) -> RunProjection:
        """Confirm the WorkOrder goal, success criteria, and risk, then compile and start the run."""
        record = self._require_record(account_id, run_id)
        self._assert_transition(record, {WorkflowRunStatus.DRAFT})
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

        record.nodes = [
            NodeProgress(
                node_id=node.node_id,
                node_name=node.node_name,
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

        return self._build_projection(record)

    def get_run(self, account_id: str, run_id: str) -> RunProjection:
        """Return the current task-stage projection for a run.

        The projection is rebuilt from the immutable run context and stored state,
        not from chat history or transient session state.
        """
        record = self._require_record(account_id, run_id)
        return self._build_projection(record)

    def advance_run(self, account_id: str, run_id: str) -> RunProjection:
        """Deterministically advance the run by one node.

        This method is exposed for tests and deterministic simulation; production
        advancement will be driven by the orchestrator.
        """
        record = self._require_record(account_id, run_id)
        self._assert_transition(record, {WorkflowRunStatus.RUNNING, WorkflowRunStatus.RETRYING})

        if record.current_node_index is None:
            raise WorkflowError("当前没有可执行的节点。")

        now = self._now()
        current = record.nodes[record.current_node_index]
        current.status = NodeStatus.COMPLETED
        current.completed_at = now
        current.output_ref = f"artifact://{record.run_id}/{current.node_id}"

        return self._advance_to_next(record, now)

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
            return self._build_projection(record)

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
            },
        )
        now = self._now()
        record.status = WorkflowRunStatus.CANCELLED
        record.cancel_reason = reason
        record.run_ended_at = now
        return self._build_projection(record)

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

        return self._advance_to_next(record, now)
