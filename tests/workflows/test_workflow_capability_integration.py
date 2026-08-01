"""Workflow service tests for T009 model-lock integration.

The seam: a workflow node that declares a capability triggers the model gateway
when advanced. Successful calls record immutable model run locks; unregistered
capabilities or gateway failures block the run deterministically.
"""

from __future__ import annotations

from typing import Any

import pytest

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterResult, CapabilityAdapter, RateLimitError
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    FallbackPolicy,
    ModelCallStatus,
    RetryPolicy,
)
from bridges.contracts.workflows import (
    ArtifactTrustStatus,
    NodeStatus,
    WorkOrder,
    WorkflowRunStatus,
)
from bridges.workflows import WorkflowError, WorkflowService


class _FixedAdapter:
    """Returns the same result every call."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        return AdapterResult(actual_model_id=self.model_id, output={"stub": True})


def _service_with_capability(
    *,
    capability_name: str = "test_cap",
    capability_version: str = "1",
    adapter: CapabilityAdapter | None = None,
    fallback: FallbackPolicy | None = None,
) -> WorkflowService:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name=capability_name,
            version=capability_version,
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="test-model",
            input_schema_version="in-v1",
            output_schema_version="out-v1",
            status=CapabilityStatus.VERIFIED,
            fallback_policy=fallback or FallbackPolicy(),
        )
    )
    gateway = ModelGateway(registry)
    if adapter is not None:
        gateway.register_adapter(capability_name, capability_version, adapter)
    return WorkflowService(model_gateway=gateway)


def _order(workflow_name: str = "cap_task") -> WorkOrder:
    return WorkOrder(
        workflow_name=workflow_name,
        workflow_version="1",
        project_id="project-1",
        objective="调用 Qwen 能力",
        success_criteria="生成模型运行锁",
        risk_statement="测试能力调用",
    )


def test_capability_node_records_model_run_lock() -> None:
    svc = _service_with_capability(adapter=_FixedAdapter("actual-model"))
    svc.register_workflow(
        name="cap_task",
        version="1",
        nodes=[
            {
                "node_id": "call_model",
                "node_name": "调用模型",
                "human_gate": False,
                "capability_name": "test_cap",
                "capability_version": "1",
            }
        ],
        terminal_states=[WorkflowRunStatus.SUCCEEDED],
    )

    draft = svc.submit_work_order(account_id="alice", order=_order())
    confirmed = svc.confirm_work_order(account_id="alice", run_id=draft.run_id)
    assert confirmed.nodes[0].capability_ref == "test_cap@1"

    advanced = svc.advance_run(account_id="alice", run_id=draft.run_id)
    assert advanced.run_status == WorkflowRunStatus.SUCCEEDED
    assert len(advanced.model_run_locks) == 1
    lock = advanced.model_run_locks[0]
    assert lock.capability_name == "test_cap"
    assert lock.actual_model_id == "actual-model"
    assert lock.status == ModelCallStatus.SUCCESS
    assert advanced.nodes[0].status == NodeStatus.COMPLETED
    assert advanced.nodes[0].output_ref == lock.lock_id


def test_unregistered_capability_blocks_run() -> None:
    svc = WorkflowService(model_gateway=ModelGateway(CapabilityRegistry()))
    svc.register_workflow(
        name="cap_task",
        version="1",
        nodes=[
            {
                "node_id": "call_model",
                "node_name": "调用模型",
                "human_gate": False,
                "capability_name": "missing_cap",
                "capability_version": "1",
            }
        ],
        terminal_states=[WorkflowRunStatus.BLOCKED],
    )

    draft = svc.submit_work_order(account_id="alice", order=_order())
    svc.confirm_work_order(account_id="alice", run_id=draft.run_id)
    advanced = svc.advance_run(account_id="alice", run_id=draft.run_id)

    assert advanced.run_status == WorkflowRunStatus.BLOCKED
    assert advanced.nodes[0].status == NodeStatus.FAILED
    assert len(advanced.model_run_locks) == 1
    assert advanced.model_run_locks[0].status == ModelCallStatus.BLOCKED
    assert advanced.model_run_locks[0].error_code == "unregistered_capability"


def test_capability_without_adapter_blocks_run() -> None:
    svc = _service_with_capability()
    svc.register_workflow(
        name="cap_task",
        version="1",
        nodes=[
            {
                "node_id": "call_model",
                "node_name": "调用模型",
                "human_gate": False,
                "capability_name": "test_cap",
                "capability_version": "1",
            }
        ],
        terminal_states=[WorkflowRunStatus.BLOCKED],
    )

    draft = svc.submit_work_order(account_id="alice", order=_order())
    svc.confirm_work_order(account_id="alice", run_id=draft.run_id)
    advanced = svc.advance_run(account_id="alice", run_id=draft.run_id)

    assert advanced.run_status == WorkflowRunStatus.BLOCKED
    assert advanced.model_run_locks[0].status == ModelCallStatus.BLOCKED
    assert advanced.model_run_locks[0].error_code == "no_adapter"


def test_rate_limited_capability_without_fallback_blocks_run() -> None:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="rate_limited",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="model",
            input_schema_version="in-v1",
            output_schema_version="out-v1",
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0),
        )
    )
    gateway = ModelGateway(registry)

    class _RateLimitAdapter:
        def call(self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]) -> AdapterResult:
            raise RateLimitError()

    gateway.register_adapter("rate_limited", "1", _RateLimitAdapter())
    svc = WorkflowService(model_gateway=gateway)
    svc.register_workflow(
        name="cap_task",
        version="1",
        nodes=[
            {
                "node_id": "call_model",
                "node_name": "调用模型",
                "human_gate": False,
                "capability_name": "rate_limited",
                "capability_version": "1",
            }
        ],
        terminal_states=[WorkflowRunStatus.BLOCKED],
    )

    draft = svc.submit_work_order(account_id="alice", order=_order())
    svc.confirm_work_order(account_id="alice", run_id=draft.run_id)
    advanced = svc.advance_run(account_id="alice", run_id=draft.run_id)

    assert advanced.run_status == WorkflowRunStatus.BLOCKED
    assert advanced.model_run_locks[0].status == ModelCallStatus.RETRYABLE_FAIL
    assert advanced.model_run_locks[0].retry_count == 1


def test_node_without_capability_keeps_original_behavior() -> None:
    svc = WorkflowService(model_gateway=ModelGateway(CapabilityRegistry()))
    svc.register_workflow(
        name="plain_task",
        version="1",
        nodes=[{"node_id": "step", "node_name": "步骤"}],
        terminal_states=[WorkflowRunStatus.SUCCEEDED],
    )
    order = WorkOrder(
        workflow_name="plain_task",
        workflow_version="1",
        project_id="project-1",
        objective="无能力节点",
        success_criteria="完成",
        risk_statement="无",
    )

    draft = svc.submit_work_order(account_id="alice", order=order)
    svc.confirm_work_order(account_id="alice", run_id=draft.run_id)
    advanced = svc.advance_run(account_id="alice", run_id=draft.run_id)

    assert advanced.run_status == WorkflowRunStatus.SUCCEEDED
    assert advanced.artifact_trust_status == ArtifactTrustStatus.QUALIFIED
    assert not advanced.model_run_locks
    assert advanced.nodes[0].output_ref == f"artifact://{draft.run_id}/step"
