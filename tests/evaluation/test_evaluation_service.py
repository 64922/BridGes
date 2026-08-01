"""Module-interface tests for the evaluation service.

The seam under test: an authenticated subject creates an evaluation run from a
completed project task run, replays it twice with the same lock, and compares
the result bundles in the evaluation center. Inputs, model locks, schema and
workflow versions are frozen; private body is never copied into logs.
"""

from __future__ import annotations

import os

import pytest

from bridges.contracts.evaluation import (
    EvaluationCreateRequest,
    EvaluationFailureCategory,
    EvaluationRunStatus,
)
from bridges.contracts.workflows import WorkOrder, WorkflowRunStatus
from bridges.evaluation import EvaluationError, EvaluationService
from bridges.workflows import WorkflowService


@pytest.fixture
def workflow_service() -> WorkflowService:
    """Workflow service with a deterministic two-node workflow and no model gateway."""
    svc = WorkflowService()
    svc.register_workflow(
        name="demo_eval_task",
        version="1",
        nodes=[
            {"node_id": "compile_context", "node_name": "编译上下文", "human_gate": False},
            {"node_id": "produce_output", "node_name": "生成产物", "human_gate": False},
        ],
        terminal_states=[
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.BLOCKED,
            WorkflowRunStatus.CANCELLED,
        ],
    )
    return svc


@pytest.fixture
def evaluation_service(workflow_service: WorkflowService) -> EvaluationService:
    return EvaluationService(workflow_service=workflow_service)


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


@pytest.fixture
def bob_id() -> str:
    return "account-bob"


def _completed_run(workflow_service: WorkflowService, account_id: str) -> str:
    """Submit, confirm and fully advance a run; return its run id."""
    order = WorkOrder(
        workflow_name="demo_eval_task",
        workflow_version="1",
        project_id="project-eval",
        objective="为大学生解释贝尔不等式",
        success_criteria="生成带引用和事实锁的科普草稿",
        risk_statement="量子基础解释可能过度简化",
        object_refs=["source-1"],
        memory_slice_refs=["memory-1"],
    )
    draft = workflow_service.submit_work_order(account_id=account_id, order=order)
    confirmed = workflow_service.confirm_work_order(account_id=account_id, run_id=draft.run_id)
    while confirmed.run_status == WorkflowRunStatus.RUNNING:
        confirmed = workflow_service.advance_run(account_id=account_id, run_id=confirmed.run_id)
    return confirmed.run_id


def test_create_evaluation_run_freezes_inputs_and_versions(
    workflow_service: WorkflowService,
    evaluation_service: EvaluationService,
    alice_id: str,
) -> None:
    run_id = _completed_run(workflow_service, alice_id)
    source_run = workflow_service.get_run(account_id=alice_id, run_id=run_id)

    request = EvaluationCreateRequest(
        suite_id="science-baseline",
        suite_version="1.0.0",
        case_id="bell-inequality",
        runtime_identifier="conda-agent",
        random_seed=123,
        execution_count=2,
    )
    eval_run = evaluation_service.create_evaluation_run(
        account_id=alice_id,
        source_run=source_run,
        request=request,
    )

    assert eval_run.source_run_id == run_id
    assert eval_run.account_id == alice_id
    assert eval_run.project_id == "project-eval"
    assert eval_run.status == EvaluationRunStatus.PENDING
    assert eval_run.lock.workflow_name == "demo_eval_task"
    assert eval_run.lock.workflow_version == "1"
    assert eval_run.lock.work_order.objective == source_run.objective
    assert eval_run.lock.work_order.success_criteria == source_run.success_criteria
    assert eval_run.lock.work_order.risk_statement == source_run.risk_statement
    assert eval_run.lock.suite.suite_id == "science-baseline"
    assert eval_run.lock.suite.case_id == "bell-inequality"
    assert eval_run.lock.random_seed == 123
    assert eval_run.lock.execution_count == 2
    assert eval_run.lock.runtime_identifier == "conda-agent"
    # Model locks from the source run are captured.
    assert len(eval_run.lock.model_run_locks) == len(source_run.model_run_locks)


def test_replay_produces_comparable_bundle(
    workflow_service: WorkflowService,
    evaluation_service: EvaluationService,
    alice_id: str,
) -> None:
    run_id = _completed_run(workflow_service, alice_id)
    source_run = workflow_service.get_run(account_id=alice_id, run_id=run_id)

    eval_run = evaluation_service.create_evaluation_run(
        account_id=alice_id,
        source_run=source_run,
        request=EvaluationCreateRequest(
            suite_id="science-baseline",
            suite_version="1.0.0",
            runtime_identifier="conda-agent",
        ),
    )

    bundle = evaluation_service.replay_evaluation_run(
        account_id=alice_id,
        evaluation_run_id=eval_run.evaluation_run_id,
    )

    assert bundle.lock_id == eval_run.evaluation_run_id
    assert bundle.source_run_id == run_id
    assert bundle.account_id == alice_id
    assert bundle.project_id == "project-eval"
    assert bundle.status == EvaluationRunStatus.SUCCEEDED
    assert bundle.failure_category == EvaluationFailureCategory.NONE
    assert bundle.frozen_inputs.objective == source_run.objective
    assert bundle.run_projection is not None
    assert bundle.run_projection.run_status == WorkflowRunStatus.SUCCEEDED
    # The bundle stores observed model locks separately.
    assert len(bundle.model_tool_calls) >= 0
    # Logs and traces are scrubbed: no private body, full prompt or secret keys.
    assert "private_body" not in str(bundle.logs_and_traces)
    assert "full_prompt" not in str(bundle.logs_and_traces)
    assert "api_key" not in str(bundle.logs_and_traces)
    assert bundle.reproduction_command
    assert bundle.reproduction_command.startswith("BridGes evaluation replay")


def test_same_lock_replay_produces_matching_results(
    workflow_service: WorkflowService,
    evaluation_service: EvaluationService,
    alice_id: str,
) -> None:
    run_id = _completed_run(workflow_service, alice_id)
    source_run = workflow_service.get_run(account_id=alice_id, run_id=run_id)

    eval_run = evaluation_service.create_evaluation_run(
        account_id=alice_id,
        source_run=source_run,
        request=EvaluationCreateRequest(
            suite_id="science-baseline",
            suite_version="1.0.0",
            runtime_identifier="conda-agent",
            random_seed=7,
        ),
    )

    bundle_a = evaluation_service.replay_evaluation_run(
        account_id=alice_id,
        evaluation_run_id=eval_run.evaluation_run_id,
    )
    bundle_b = evaluation_service.replay_evaluation_run(
        account_id=alice_id,
        evaluation_run_id=eval_run.evaluation_run_id,
    )

    # Both bundles share the same lock and inputs.
    assert bundle_a.lock_id == bundle_b.lock_id
    assert bundle_a.frozen_inputs == bundle_b.frozen_inputs
    # Workflow name/version and model-lock capability contracts are identical.
    assert (
        [lock.capability_name for lock in bundle_a.model_tool_calls]
        == [lock.capability_name for lock in bundle_b.model_tool_calls]
    )
    assert bundle_a.status == bundle_b.status
    assert bundle_a.failure_category == bundle_b.failure_category

    diff = evaluation_service.compare_bundles(
        account_id=alice_id,
        bundle_id_a=bundle_a.bundle_id,
        bundle_id_b=bundle_b.bundle_id,
    )
    assert diff.lock_match is True
    assert diff.bundle_id_a == bundle_a.bundle_id
    assert diff.bundle_id_b == bundle_b.bundle_id
    # Inputs and locks do not differ between two replays of the same lock.
    assert not diff.input_diffs
    assert not diff.lock_diffs
    # Results are deterministic: same status, outputs and trajectory.
    assert not diff.result_diffs
    assert not diff.failure_diffs


def test_build_digest_respects_environment_override(
    workflow_service: WorkflowService,
    evaluation_service: EvaluationService,
    alice_id: str,
) -> None:
    run_id = _completed_run(workflow_service, alice_id)
    source_run = workflow_service.get_run(account_id=alice_id, run_id=run_id)

    os.environ["BRIDGES_BUILD_DIGEST"] = "git-deadbeef"
    try:
        eval_run = evaluation_service.create_evaluation_run(
            account_id=alice_id,
            source_run=source_run,
            request=EvaluationCreateRequest(
                suite_id="science-baseline",
                suite_version="1.0.0",
            ),
        )
    finally:
        del os.environ["BRIDGES_BUILD_DIGEST"]

    assert eval_run.lock.code_commit_or_build_digest == "git-deadbeef"


def test_cross_account_access_is_rejected(
    workflow_service: WorkflowService,
    evaluation_service: EvaluationService,
    alice_id: str,
    bob_id: str,
) -> None:
    run_id = _completed_run(workflow_service, alice_id)
    source_run = workflow_service.get_run(account_id=alice_id, run_id=run_id)

    eval_run = evaluation_service.create_evaluation_run(
        account_id=alice_id,
        source_run=source_run,
        request=EvaluationCreateRequest(
            suite_id="science-baseline",
            suite_version="1.0.0",
        ),
    )

    with pytest.raises(EvaluationError, match="不存在或没有访问权限"):
        evaluation_service.get_evaluation_run(
            account_id=bob_id,
            evaluation_run_id=eval_run.evaluation_run_id,
        )

    bundle = evaluation_service.replay_evaluation_run(
        account_id=alice_id,
        evaluation_run_id=eval_run.evaluation_run_id,
    )
    with pytest.raises(EvaluationError, match="不存在或没有访问权限"):
        evaluation_service.get_result_bundle(account_id=bob_id, bundle_id=bundle.bundle_id)

    with pytest.raises(EvaluationError, match="不存在或没有访问权限"):
        evaluation_service.replay_evaluation_run(
            account_id=bob_id,
            evaluation_run_id=eval_run.evaluation_run_id,
        )


def test_create_evaluation_from_foreign_run_is_rejected(
    workflow_service: WorkflowService,
    evaluation_service: EvaluationService,
    alice_id: str,
    bob_id: str,
) -> None:
    run_id = _completed_run(workflow_service, alice_id)
    source_run = workflow_service.get_run(account_id=alice_id, run_id=run_id)

    with pytest.raises(EvaluationError, match="没有权限"):
        evaluation_service.create_evaluation_run(
            account_id=bob_id,
            source_run=source_run,
            request=EvaluationCreateRequest(
                suite_id="science-baseline",
                suite_version="1.0.0",
            ),
        )


def test_create_evaluation_rejects_non_terminal_run(
    workflow_service: WorkflowService,
    evaluation_service: EvaluationService,
    alice_id: str,
) -> None:
    """Creating an evaluation from a draft/running run is rejected."""
    order = WorkOrder(
        workflow_name="demo_eval_task",
        workflow_version="1",
        project_id="project-eval",
        objective="测试",
        success_criteria="测试通过",
        risk_statement="无风险",
    )
    draft = workflow_service.submit_work_order(account_id=alice_id, order=order)
    source_run = workflow_service.get_run(account_id=alice_id, run_id=draft.run_id)

    with pytest.raises(EvaluationError, match="已完成"):
        evaluation_service.create_evaluation_run(
            account_id=alice_id,
            source_run=source_run,
            request=EvaluationCreateRequest(
                suite_id="science-baseline",
                suite_version="1.0.0",
            ),
        )


def test_replay_honors_execution_count(
    workflow_service: WorkflowService,
    evaluation_service: EvaluationService,
    alice_id: str,
) -> None:
    """Replay with execution_count=3 produces three bundles on the record."""
    run_id = _completed_run(workflow_service, alice_id)
    source_run = workflow_service.get_run(account_id=alice_id, run_id=run_id)

    eval_run = evaluation_service.create_evaluation_run(
        account_id=alice_id,
        source_run=source_run,
        request=EvaluationCreateRequest(
            suite_id="science-baseline",
            suite_version="1.0.0",
            execution_count=3,
        ),
    )

    bundle = evaluation_service.replay_evaluation_run(
        account_id=alice_id,
        evaluation_run_id=eval_run.evaluation_run_id,
    )

    # The record should have 3 bundles after a single replay call.
    projection = evaluation_service.get_evaluation_run(
        account_id=alice_id,
        evaluation_run_id=eval_run.evaluation_run_id,
    )
    assert len(projection.bundle_ids) == 3
    assert projection.latest_bundle_id == bundle.bundle_id
    assert bundle.cost_latency.get("execution_count") == 3
    assert bundle.cost_latency.get("execution_index") == 2


def test_compare_requires_same_lock(
    workflow_service: WorkflowService,
    evaluation_service: EvaluationService,
    alice_id: str,
) -> None:
    run_id_a = _completed_run(workflow_service, alice_id)
    run_id_b = _completed_run(workflow_service, alice_id)
    source_a = workflow_service.get_run(account_id=alice_id, run_id=run_id_a)
    source_b = workflow_service.get_run(account_id=alice_id, run_id=run_id_b)

    eval_a = evaluation_service.create_evaluation_run(
        account_id=alice_id,
        source_run=source_a,
        request=EvaluationCreateRequest(suite_id="suite-a", suite_version="1.0.0"),
    )
    eval_b = evaluation_service.create_evaluation_run(
        account_id=alice_id,
        source_run=source_b,
        request=EvaluationCreateRequest(suite_id="suite-b", suite_version="1.0.0"),
    )

    bundle_a = evaluation_service.replay_evaluation_run(
        account_id=alice_id,
        evaluation_run_id=eval_a.evaluation_run_id,
    )
    bundle_b = evaluation_service.replay_evaluation_run(
        account_id=alice_id,
        evaluation_run_id=eval_b.evaluation_run_id,
    )

    with pytest.raises(EvaluationError, match="同一评测运行锁"):
        evaluation_service.compare_bundles(
            account_id=alice_id,
            bundle_id_a=bundle_a.bundle_id,
            bundle_id_b=bundle_b.bundle_id,
        )
