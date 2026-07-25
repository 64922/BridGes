"""Build privacy-preserving run summaries from workflow projections."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from science_companion.contracts.observability import (
    ModelLockSummary,
    NodeSummary,
    PrivacyManifest,
    RunSummary,
    TelemetryCorrelation,
)
from science_companion.contracts.workflows import RunProjection
from science_companion.observability.scrubber import scrub_payload
from science_companion.observability.telemetry_context import build_correlation


def build_run_summary(
    projection: RunProjection,
    *,
    audit_event_refs: list[str] | None = None,
    terminal_reason: str | None = None,
    correlation: TelemetryCorrelation | None = None,
) -> RunSummary:
    """Build a RunSummary from a RunProjection.

    The summary never includes private body, full prompts, keys or unnecessary
    model output. It carries only references and metadata so operators can
    reconstruct the run from trace/metric/log backends.
    """
    now = datetime.now(timezone.utc)
    ctx = projection.context_envelope

    node_summaries = [
        NodeSummary(
            node_id=node.node_id,
            node_name=node.node_name,
            status=node.status.value,
            capability_ref=node.capability_ref,
            started_at=node.started_at,
            completed_at=node.completed_at,
            failure_reason=node.failure_reason,
            output_ref=node.output_ref,
        )
        for node in projection.nodes
    ]

    model_lock_summaries = [
        ModelLockSummary(
            lock_id=lock.lock_id,
            capability_name=lock.capability_name,
            capability_version=lock.capability_version,
            actual_model_id=lock.actual_model_id,
            region=lock.region,
            status=lock.status.value,
            retry_count=lock.retry_count,
            error_code=lock.error_code,
            degradation_reason=lock.degradation_reason,
            created_at=lock.created_at,
        )
        for lock in projection.model_run_locks
    ]

    effective_correlation = correlation or build_correlation(
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

    # Build a payload manifest by inspecting the work order fields that could
    # contain private content. The projection objective/success/risk are user
    # task metadata, not private body; they are kept but any nested forbidden
    # fields would be flagged by the scrubber if passed.
    _, manifest = scrub_payload(
        {
            "objective": projection.objective,
            "success_criteria": projection.success_criteria,
            "risk_statement": projection.risk_statement,
        }
    )

    return RunSummary(
        summary_id=secrets.token_urlsafe(16),
        run_id=ctx.run_id,
        account_hash=effective_correlation.account_hash or "",
        project_hash=effective_correlation.project_hash or "",
        tenant_hash=effective_correlation.tenant_hash,
        object_domain=ctx.object_domain,
        workflow_name=ctx.workflow_name,
        workflow_version=ctx.workflow_version,
        run_status=projection.run_status.value,
        artifact_trust_status=projection.artifact_trust_status.value,
        terminal_reason=terminal_reason,
        publish_eligible=projection.publish_eligible,
        node_summaries=node_summaries,
        model_lock_summaries=model_lock_summaries,
        audit_event_refs=list(audit_event_refs or []),
        correlation=effective_correlation,
        privacy_manifest=manifest,
        created_at=now,
    )
