"""Tests for run summary generation."""

from datetime import datetime, timezone

from bridges.contracts.ai import ModelCallStatus, ModelRunLock
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import (
    ArtifactTrustStatus,
    NodeProgress,
    NodeStatus,
    RunContextEnvelope,
    RunProjection,
    WorkflowRunStatus,
)
from bridges.observability.run_summary import build_run_summary
from bridges.observability.telemetry_context import build_correlation


def _sample_projection() -> RunProjection:
    ctx = RunContextEnvelope(
        run_id="run-1",
        account_id="account-alice",
        project_id="project-1",
        tenant_id="tenant-alpha",
        workflow_name="demo_lesson",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(timezone.utc),
    )
    lock = ModelRunLock(
        lock_id="lock-1",
        run_id="run-1",
        account_id="account-alice",
        project_id="project-1",
        capability_name="qwen_text_chat",
        capability_version="1",
        actual_model_id="qwen3.7-plus",
        region="cn-beijing",
        prompt_version="2026-07-24",
        input_output_contract="qwen_text_chat:chat-messages-v1->chat-completion-v1",
        status=ModelCallStatus.SUCCESS,
        created_at=datetime.now(timezone.utc),
    )
    return RunProjection(
        run_id="run-1",
        project_id="project-1",
        workflow_name="demo_lesson",
        workflow_version="1",
        run_status=WorkflowRunStatus.SUCCEEDED,
        artifact_trust_status=ArtifactTrustStatus.QUALIFIED,
        publish_eligible=False,
        objective="explain Bell inequality",
        success_criteria="draft with citations",
        risk_statement="may oversimplify",
        nodes=[
            NodeProgress(
                node_id="n1",
                node_name="compile",
                status=NodeStatus.COMPLETED,
                capability_ref="qwen_text_chat@1",
                output_ref="artifact://run-1/n1",
            )
        ],
        model_run_locks=[lock],
        context_envelope=ctx,
    )


def test_run_summary_pseudonymizes_account_and_project() -> None:
    projection = _sample_projection()
    summary = build_run_summary(projection)
    assert summary.account_hash
    assert summary.account_hash != "account-alice"
    assert summary.project_hash
    assert summary.project_hash != "project-1"
    assert summary.tenant_hash != "tenant-alpha"


def test_run_summary_includes_model_lock_summary_without_private_output() -> None:
    projection = _sample_projection()
    summary = build_run_summary(projection)
    assert len(summary.model_lock_summaries) == 1
    lock_summary = summary.model_lock_summaries[0]
    assert lock_summary.lock_id == "lock-1"
    assert lock_summary.capability_name == "qwen_text_chat"
    assert lock_summary.actual_model_id == "qwen3.7-plus"
    assert lock_summary.status == ModelCallStatus.SUCCESS.value
    assert not hasattr(lock_summary, "output")


def test_run_summary_privacy_manifest_declares_no_private_content() -> None:
    projection = _sample_projection()
    summary = build_run_summary(projection)
    assert summary.privacy_manifest.includes_private_body is False
    assert summary.privacy_manifest.includes_full_prompt is False
    assert summary.privacy_manifest.includes_secret is False


def test_run_summary_uses_provided_correlation() -> None:
    projection = _sample_projection()
    correlation = build_correlation(
        trace_id="trace-abc",
        run_id="run-1",
        account_id="account-alice",
        workflow_name="demo_lesson",
    )
    summary = build_run_summary(projection, correlation=correlation)
    assert summary.correlation.trace_id == "trace-abc"
    assert summary.correlation.run_id == "run-1"


def test_run_summary_includes_node_summaries() -> None:
    projection = _sample_projection()
    summary = build_run_summary(projection)
    assert len(summary.node_summaries) == 1
    node_summary = summary.node_summaries[0]
    assert node_summary.node_id == "n1"
    assert node_summary.output_ref == "artifact://run-1/n1"
    assert node_summary.status == NodeStatus.COMPLETED.value
