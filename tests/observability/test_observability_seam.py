"""Integration test for T010 observability seam.

The seam: a user executes a successful, a retryable-failure, and a blocked
basic task. Operators can reconstruct the run from unified trace/audit data and
observability payloads never contain private body. New workloads can register
their own SLIs via the SLI/SLO contract.
"""

from datetime import datetime, timezone
from typing import Any

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import (
    AdapterError,
    AdapterResult,
    CapabilityAdapter,
    RateLimitError,
    RegionError,
)
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    FallbackPolicy,
    ModelCallStatus,
    RetryPolicy,
)
from bridges.contracts.observability import (
    AuditAction,
    AuditResult,
    SLIMetricKind,
    SLISeverity,
)
from bridges.contracts.workflows import (
    ArtifactTrustStatus,
    NodeStatus,
    WorkOrder,
    WorkflowRunStatus,
)
from bridges.observability.service import ObservabilityService
from bridges.observability.sli_registry import SLIRegistry
from bridges.observability.telemetry_context import TelemetryCorrelationScope
from bridges.workflows import WorkflowService


class _ProgrammableAdapter:
    """Test adapter that returns programmed results or raises errors."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.call_count = 0

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.call_count += 1
        if not self.responses:
            raise RateLimitError("exhausted")
        item: AdapterResult | AdapterError = self.responses.pop(0)
        if isinstance(item, AdapterError):
            raise item
        return item


def _register_capability(
    registry: CapabilityRegistry,
    gateway: ModelGateway,
    name: str,
    responses: list[Any],
    *,
    retry: RetryPolicy | None = None,
    fallback: FallbackPolicy | None = None,
) -> _ProgrammableAdapter:
    cap = CapabilityRecord(
        name=name,
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id=f"{name}-model",
        input_schema_version="in-v1",
        output_schema_version="out-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=retry or RetryPolicy(max_attempts=1),
        fallback_policy=fallback or FallbackPolicy(),
        prompt_version="2026-07-24",
    )
    registry.register(cap)
    adapter = _ProgrammableAdapter(responses)
    gateway.register_adapter(name, "1", adapter)
    return adapter


def _setup_service(observability: ObservabilityService) -> WorkflowService:
    registry = CapabilityRegistry()
    gateway = ModelGateway(registry)

    # Primary succeeds on first attempt.
    _register_capability(
        registry,
        gateway,
        "success_cap",
        [AdapterResult(actual_model_id="success-model", output={"ok": True})],
    )
    # Primary retries then succeeds.
    _register_capability(
        registry,
        gateway,
        "retry_cap",
        [RateLimitError("rate limited"), AdapterResult(actual_model_id="retry-model", output={"ok": True})],
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0),
    )
    # Primary region error -> blocked.
    _register_capability(
        registry,
        gateway,
        "blocked_cap",
        [RegionError("region unavailable")],
    )

    service = WorkflowService(
        model_gateway=gateway,
        observability_service=observability,
    )
    service.register_workflow(
        name="observable_task",
        version="1",
        nodes=[
            {"node_id": "step", "node_name": "唯一步骤", "human_gate": False, "capability_name": "success_cap"},
        ],
        terminal_states=[WorkflowRunStatus.SUCCEEDED, WorkflowRunStatus.BLOCKED],
    )
    service.register_workflow(
        name="retry_task",
        version="1",
        nodes=[
            {"node_id": "step", "node_name": "唯一步骤", "human_gate": False, "capability_name": "retry_cap"},
        ],
        terminal_states=[WorkflowRunStatus.SUCCEEDED, WorkflowRunStatus.BLOCKED],
    )
    service.register_workflow(
        name="blocked_task",
        version="1",
        nodes=[
            {"node_id": "step", "node_name": "唯一步骤", "human_gate": False, "capability_name": "blocked_cap"},
        ],
        terminal_states=[WorkflowRunStatus.SUCCEEDED, WorkflowRunStatus.BLOCKED],
    )
    return service


def _submit_and_run(service: WorkflowService, workflow_name: str, account_id: str) -> Any:
    order = WorkOrder(
        workflow_name=workflow_name,
        workflow_version="1",
        project_id="project-1",
        objective="objective",
        success_criteria="success",
        risk_statement="risk",
    )
    draft = service.submit_work_order(account_id=account_id, order=order)
    confirmed = service.confirm_work_order(account_id=account_id, run_id=draft.run_id)
    if confirmed.run_status == WorkflowRunStatus.SUCCEEDED:
        return confirmed
    return service.advance_run(account_id=account_id, run_id=draft.run_id)


def test_success_task_emits_audit_and_summary() -> None:
    observability = ObservabilityService()
    service = _setup_service(observability)

    with TelemetryCorrelationScope(trace_id="trace-success"):
        projection = _submit_and_run(service, "observable_task", "account-alice")

    assert projection.run_status == WorkflowRunStatus.SUCCEEDED
    assert projection.artifact_trust_status == ArtifactTrustStatus.QUALIFIED
    events = observability.get_run_audit_events(projection.run_id)
    assert any(e.action == AuditAction.WORKORDER_SUBMIT for e in events)
    assert any(e.action == AuditAction.WORKORDER_CONFIRM for e in events)
    assert any(
        e.action == AuditAction.MODEL_INVOCATION and e.result == AuditResult.SUCCESS
        for e in events
    )
    # Correlation trace_id propagated to all audit events.
    assert all(e.correlation.trace_id == "trace-success" for e in events)


def test_retry_task_records_retryable_failure_then_success() -> None:
    observability = ObservabilityService()
    service = _setup_service(observability)

    projection = _submit_and_run(service, "retry_task", "account-alice")

    assert projection.run_status == WorkflowRunStatus.SUCCEEDED
    assert len(projection.model_run_locks) == 1
    assert projection.model_run_locks[0].retry_count == 1
    assert projection.model_run_locks[0].status == ModelCallStatus.SUCCESS

    events = observability.get_run_audit_events(projection.run_id)
    invocation_events = [e for e in events if e.action == AuditAction.MODEL_INVOCATION]
    assert invocation_events
    assert all(e.correlation.run_id == projection.run_id for e in events)


def test_blocked_task_records_blocked_model_invocation() -> None:
    observability = ObservabilityService()
    service = _setup_service(observability)

    projection = _submit_and_run(service, "blocked_task", "account-alice")

    assert projection.run_status == WorkflowRunStatus.BLOCKED
    assert projection.nodes[0].status == NodeStatus.FAILED
    events = observability.get_run_audit_events(projection.run_id)
    invocation = next(e for e in events if e.action == AuditAction.MODEL_INVOCATION)
    assert invocation.result == AuditResult.BLOCKED


def test_observability_payload_does_not_contain_private_body() -> None:
    observability = ObservabilityService()
    service = _setup_service(observability)

    projection = _submit_and_run(service, "observable_task", "account-alice")
    summary = observability.summarize_run(projection)

    assert summary.privacy_manifest.includes_private_body is False
    assert summary.privacy_manifest.includes_full_prompt is False
    assert summary.privacy_manifest.includes_secret is False
    # Raw model output is not included in the summary.
    for lock_summary in summary.model_lock_summaries:
        assert not hasattr(lock_summary, "output")


def test_new_workload_can_register_sli() -> None:
    """SLI/SLO 注册直接由 SLIRegistry 承担（门面瘦身后不再转发）。"""
    registry = SLIRegistry()
    sli = registry.register_sli(
        workload_name="future_ingestion",
        metric_kind=SLIMetricKind.CORRECTNESS,
        description="Claim correctness rate for ingestion pipeline",
        unit="ratio",
        window="5m",
        owner="data-platform",
        runbook_url="https://runbooks.example/ingestion-correctness",
    )
    slo = registry.register_slo(
        sli_id=sli.sli_id,
        target=0.98,
        alert_threshold=0.90,
        severity=SLISeverity.HIGH,
    )
    assert sli.metric_kind == SLIMetricKind.CORRECTNESS
    assert slo.target == 0.98
    assert slo.alert_threshold == 0.90
    assert slo.severity == SLISeverity.HIGH

    slis = registry.list_slis(workload_name="future_ingestion")
    assert len(slis) == 1
    assert slis[0].owner == "data-platform"
