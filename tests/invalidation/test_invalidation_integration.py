"""Integration tests for invalidation foundation with workflows and vault.

The seam under test: a generic object used by a task, cache and index projection
is revoked or deleted. The invalidation event/tombstone is written first, new
reads and new runs are blocked immediately, the impact set is scope-correct, and
revalidation is scheduled idempotently.
"""

from __future__ import annotations

from typing import Any

import pytest

from bridges.contracts.identity import AuthMethod, SubjectContext
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.invalidation import (
    AffectedDownstream,
    InvalidationEventType,
    InvalidationState,
)
from bridges.invalidation import InvalidationService
from bridges.vault import (
    InMemoryVaultRepository,
    MemoryDeviceVaultPort,
    VaultError,
    VaultService,
)
from bridges.workflows import WorkflowError, WorkflowService
from bridges.contracts.workflows import WorkOrder, WorkflowRunStatus


@pytest.fixture
def subject() -> SubjectContext:
    return SubjectContext(
        account_id="account-alice",
        session_id="session-alice",
        auth_method=AuthMethod.PASSWORD,
    )


@pytest.fixture
def invalidation_service() -> InvalidationService:
    service = InvalidationService()

    def cache_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"cache:{event.object_ref.object_id}",
                downstream_type="cache",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="invalidate",
            )
        ]

    def index_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"index:{event.object_ref.object_id}",
                downstream_type="index_projection",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="revalidate",
            )
        ]

    def run_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"run:{event.object_ref.object_id}",
                downstream_type="workflow_run",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="block_new_use",
            )
        ]

    service.register_impact_resolver("cache", cache_resolver)
    service.register_impact_resolver("index_projection", index_resolver)
    service.register_impact_resolver("workflow_run", run_resolver)
    return service


@pytest.fixture
def object_ref(subject: SubjectContext) -> ObjectRef:
    return ObjectRef(
        domain=ObjectDomain.PERSONAL_VAULT,
        owner_id=subject.account_id,
        object_id="common-test-object",
        version=1,
    )


@pytest.fixture
def workflow_service(invalidation_service: InvalidationService) -> WorkflowService:
    service = WorkflowService(invalidation_service=invalidation_service)
    service.register_workflow(
        name="generic_science_task",
        version="1",
        nodes=[
            {"node_id": "compile_context", "node_name": "编译上下文", "human_gate": False},
            {"node_id": "produce_output", "node_name": "生成产物", "human_gate": False},
        ],
        terminal_states=[WorkflowRunStatus.SUCCEEDED, WorkflowRunStatus.BLOCKED],
    )
    return service


@pytest.fixture
def vault_service(invalidation_service: InvalidationService) -> VaultService:
    repository = InMemoryVaultRepository()
    return VaultService(
        repository=repository,
        device_port=MemoryDeviceVaultPort(repository),
        invalidation_service=invalidation_service,
    )


def _work_order(project_id: str, object_refs: list[str]) -> WorkOrder:
    return WorkOrder(
        workflow_name="generic_science_task",
        workflow_version="1",
        project_id=project_id,
        objective="使用通用测试对象",
        success_criteria="运行成功",
        risk_statement="无",
        object_refs=object_refs,
    )


def test_revoke_object_then_block_new_run(
    workflow_service: WorkflowService,
    invalidation_service: InvalidationService,
    subject: SubjectContext,
    object_ref: ObjectRef,
) -> None:
    # Simulate the object being used by a previous run (omitted); now revoke it.
    event = invalidation_service.record_invalidation_event(
        subject, object_ref, InvalidationEventType.REVOKE, "撤权"
    )
    plan = invalidation_service.plan_invalidation(event.event_id)

    # Impact set includes cache, index and run downstreams scoped to Alice.
    types = {d.downstream_type for d in plan.impact_set.affected_downstreams}
    assert "cache" in types
    assert "index_projection" in types
    assert "workflow_run" in types
    for d in plan.impact_set.affected_downstreams:
        assert d.scope_envelope.account_id == subject.account_id

    # A new run referencing the revoked object is blocked immediately.
    order = _work_order("project-1", [object_ref.object_id])
    with pytest.raises(WorkflowError, match="对象已失效"):
        workflow_service.submit_work_order(subject.account_id, order)


def test_delete_object_then_block_new_run_and_vault_read(
    invalidation_service: InvalidationService,
    workflow_service: WorkflowService,
    vault_service: VaultService,
    subject: SubjectContext,
    object_ref: ObjectRef,
) -> None:
    # Create a vault object and align the invalidation object_ref with its id.
    vault_object = vault_service.create_private_object(
        owner_account_id=subject.account_id,
        content=b"common test object body",
    )
    aligned_ref = object_ref.model_copy(update={"object_id": vault_object.ref.object_id})

    event, tombstone = invalidation_service.record_tombstone(
        subject, aligned_ref, "删除"
    )

    state = invalidation_service.check_state(aligned_ref)
    assert state.state == InvalidationState.TOMBSTONED
    assert state.effective_event_id == event.event_id
    assert tombstone.sequence == event.sequence

    # New run is blocked.
    order = _work_order("project-1", [aligned_ref.object_id])
    with pytest.raises(WorkflowError, match="对象已被删除"):
        workflow_service.submit_work_order(subject.account_id, order)

    # New vault read is blocked.
    with pytest.raises(VaultError, match="对象已被删除"):
        vault_service.get_object(subject.account_id, aligned_ref.object_id)

    with pytest.raises(VaultError, match="对象已被删除"):
        vault_service.get_content(subject.account_id, aligned_ref.object_id)

    # Capsule issuance is blocked.
    with pytest.raises(VaultError, match="对象已被删除"):
        vault_service.issue_task_capsule(
            subject.account_id, aligned_ref.object_id, "run-1", "process"
        )


def test_revoke_between_submit_and_confirm_blocks_start(
    workflow_service: WorkflowService,
    invalidation_service: InvalidationService,
    subject: SubjectContext,
    object_ref: ObjectRef,
) -> None:
    order = _work_order("project-1", [object_ref.object_id])
    draft = workflow_service.submit_work_order(subject.account_id, order)

    # Revoke the object after submission but before confirmation.
    invalidation_service.record_invalidation_event(
        subject, object_ref, InvalidationEventType.REVOKE, "撤权"
    )

    with pytest.raises(WorkflowError, match="对象已失效"):
        workflow_service.confirm_work_order(subject.account_id, draft.run_id)


def test_cross_account_cannot_revoke_and_impact_set_stays_scoped(
    invalidation_service: InvalidationService,
    object_ref: ObjectRef,
) -> None:
    bob = SubjectContext(
        account_id="account-bob",
        session_id="session-bob",
        auth_method=AuthMethod.PASSWORD,
    )

    with pytest.raises(Exception):  # ScopeIsolationError wrapped as InvalidationError
        invalidation_service.record_invalidation_event(
            bob, object_ref, InvalidationEventType.REVOKE, "越权撤权"
        )

    # Object remains active and impact-free.
    state = invalidation_service.check_state(object_ref)
    assert state.state == InvalidationState.ACTIVE
