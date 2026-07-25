"""Module-interface tests for the workflow / WorkOrder lifecycle.

The seam under test: an authenticated subject submits a WorkOrder, confirms the
goal / success criteria / risk, watches the task stage separate run status from
artifact trust status and publish eligibility, cancels or resolves named human
todos, and receives deterministic rejections for illegal transitions.
"""

import pytest

from science_companion.contracts.workflows import (
    ArtifactTrustStatus,
    HumanTodoStatus,
    NodeStatus,
    WorkOrder,
    WorkflowRunStatus,
)
from science_companion.workflows import WorkflowError, WorkflowService


@pytest.fixture
def service() -> WorkflowService:
    svc = WorkflowService()
    # Register a tiny two-node workflow for deterministic tests.
    svc.register_workflow(
        name="demo_lesson",
        version="1",
        nodes=[
            {"node_id": "compile_context", "node_name": "编译上下文", "human_gate": False},
            {"node_id": "produce_output", "node_name": "生成产物", "human_gate": False},
        ],
        terminal_states=[WorkflowRunStatus.SUCCEEDED, WorkflowRunStatus.BLOCKED, WorkflowRunStatus.CANCELLED],
    )
    return svc


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


@pytest.fixture
def bob_id() -> str:
    return "account-bob"


def _demo_order(project_id: str) -> WorkOrder:
    return WorkOrder(
        workflow_name="demo_lesson",
        workflow_version="1",
        project_id=project_id,
        objective="为大学生解释贝尔不等式",
        success_criteria="生成一份带引用和事实锁的科普草稿",
        risk_statement="涉及量子基础解释，可能产生过度简化",
    )


def test_submit_work_order_creates_draft_projection(service: WorkflowService, alice_id: str) -> None:
    order = _demo_order(project_id="project-1")
    projection = service.submit_work_order(account_id=alice_id, order=order)

    assert projection.run_status == WorkflowRunStatus.DRAFT
    assert projection.artifact_trust_status == ArtifactTrustStatus.NOT_CREATED
    assert projection.objective == order.objective
    assert projection.success_criteria == order.success_criteria
    assert projection.risk_statement == order.risk_statement
    assert projection.context_envelope.account_id == alice_id
    assert projection.context_envelope.project_id == order.project_id
    assert not projection.nodes
    assert not projection.human_todos


def test_confirm_work_order_starts_run(service: WorkflowService, alice_id: str) -> None:
    order = _demo_order(project_id="project-1")
    draft = service.submit_work_order(account_id=alice_id, order=order)

    projection = service.confirm_work_order(account_id=alice_id, run_id=draft.run_id)

    assert projection.run_status == WorkflowRunStatus.RUNNING
    assert projection.artifact_trust_status == ArtifactTrustStatus.DRAFT
    assert projection.run_started_at is not None
    assert projection.current_node_id == "compile_context"
    assert len(projection.nodes) == 2
    assert projection.nodes[0].status == NodeStatus.RUNNING
    assert projection.nodes[1].status == NodeStatus.PENDING
    assert not projection.publish_eligible


def test_confirm_requires_explicit_confirmation(service: WorkflowService, alice_id: str) -> None:
    order = _demo_order(project_id="project-1")
    draft = service.submit_work_order(account_id=alice_id, order=order)

    with pytest.raises(WorkflowError, match="必须明确确认"):
        service.confirm_work_order(account_id=alice_id, run_id=draft.run_id, confirmed=False)


def test_confirm_requires_draft_state(service: WorkflowService, alice_id: str) -> None:
    order = _demo_order(project_id="project-1")
    draft = service.submit_work_order(account_id=alice_id, order=order)
    service.confirm_work_order(account_id=alice_id, run_id=draft.run_id)

    with pytest.raises(WorkflowError, match="非法状态转换"):
        service.confirm_work_order(account_id=alice_id, run_id=draft.run_id)


def test_confirm_rejects_unknown_workflow(service: WorkflowService, alice_id: str) -> None:
    order = WorkOrder(
        workflow_name="unknown_workflow",
        workflow_version="1",
        project_id="project-1",
        objective="x",
        success_criteria="y",
        risk_statement="z",
    )
    draft = service.submit_work_order(account_id=alice_id, order=order)

    with pytest.raises(WorkflowError, match="未知工作流"):
        service.confirm_work_order(account_id=alice_id, run_id=draft.run_id)


def test_register_workflow_rejects_empty_terminal_states(service: WorkflowService) -> None:
    with pytest.raises(ValueError, match="至少一个终止状态"):
        service.register_workflow(
            name="no_terminal",
            version="1",
            nodes=[{"node_id": "only", "node_name": "唯一节点", "human_gate": False}],
            terminal_states=[],
        )


def test_get_run_projection_recovers_without_chat_history(service: WorkflowService, alice_id: str) -> None:
    order = _demo_order(project_id="project-1")
    draft = service.submit_work_order(account_id=alice_id, order=order)
    confirmed = service.confirm_work_order(account_id=alice_id, run_id=draft.run_id)

    recovered = service.get_run(account_id=alice_id, run_id=draft.run_id)

    assert recovered.run_id == confirmed.run_id
    assert recovered.run_status == confirmed.run_status
    assert recovered.context_envelope.run_id == confirmed.context_envelope.run_id


def test_advance_run_completes_nodes_and_reaches_success(service: WorkflowService, alice_id: str) -> None:
    order = _demo_order(project_id="project-1")
    draft = service.submit_work_order(account_id=alice_id, order=order)
    service.confirm_work_order(account_id=alice_id, run_id=draft.run_id)

    after_first = service.advance_run(account_id=alice_id, run_id=draft.run_id)
    assert after_first.run_status == WorkflowRunStatus.RUNNING
    assert after_first.nodes[0].status == NodeStatus.COMPLETED
    assert after_first.nodes[1].status == NodeStatus.RUNNING

    after_second = service.advance_run(account_id=alice_id, run_id=draft.run_id)
    assert after_second.run_status == WorkflowRunStatus.SUCCEEDED
    assert after_second.artifact_trust_status == ArtifactTrustStatus.QUALIFIED
    assert after_second.nodes[1].status == NodeStatus.COMPLETED
    assert after_second.run_ended_at is not None
    # Succeeded != approved/publishable.
    assert not after_second.publish_eligible


def test_cancel_run_from_running(service: WorkflowService, alice_id: str) -> None:
    order = _demo_order(project_id="project-1")
    draft = service.submit_work_order(account_id=alice_id, order=order)
    service.confirm_work_order(account_id=alice_id, run_id=draft.run_id)

    cancelled = service.cancel_run(account_id=alice_id, run_id=draft.run_id, reason="用户主动取消")

    assert cancelled.run_status == WorkflowRunStatus.CANCELLED
    assert cancelled.cancel_reason == "用户主动取消"
    assert cancelled.run_ended_at is not None


def test_cancel_run_from_draft(service: WorkflowService, alice_id: str) -> None:
    order = _demo_order(project_id="project-1")
    draft = service.submit_work_order(account_id=alice_id, order=order)

    cancelled = service.cancel_run(account_id=alice_id, run_id=draft.run_id, reason="放弃")
    assert cancelled.run_status == WorkflowRunStatus.CANCELLED


def test_cannot_cancel_terminal_run(service: WorkflowService, alice_id: str) -> None:
    order = _demo_order(project_id="project-1")
    draft = service.submit_work_order(account_id=alice_id, order=order)
    service.confirm_work_order(account_id=alice_id, run_id=draft.run_id)
    service.advance_run(account_id=alice_id, run_id=draft.run_id)
    service.advance_run(account_id=alice_id, run_id=draft.run_id)

    with pytest.raises(WorkflowError, match="非法状态转换"):
        service.cancel_run(account_id=alice_id, run_id=draft.run_id, reason="x")


def test_human_gate_creates_named_todo_and_resolves(service: WorkflowService, alice_id: str) -> None:
    service.register_workflow(
        name="gated_workflow",
        version="1",
        nodes=[
            {"node_id": "human_check", "node_name": "人工确认", "human_gate": True},
            {"node_id": "finalize", "node_name": "完成", "human_gate": False},
        ],
        terminal_states=[WorkflowRunStatus.SUCCEEDED, WorkflowRunStatus.CANCELLED],
    )
    order = WorkOrder(
        workflow_name="gated_workflow",
        workflow_version="1",
        project_id="project-1",
        objective="需要人工确认",
        success_criteria="人工确认后完成",
        risk_statement="人工门示例",
    )
    draft = service.submit_work_order(account_id=alice_id, order=order)
    confirmed = service.confirm_work_order(account_id=alice_id, run_id=draft.run_id)

    assert confirmed.run_status == WorkflowRunStatus.WAITING_HUMAN
    assert len(confirmed.human_todos) == 1
    todo = confirmed.human_todos[0]
    assert todo.status == HumanTodoStatus.OPEN
    assert todo.title
    assert confirmed.current_node_id == "human_check"

    resolved = service.resolve_human_todo(
        account_id=alice_id,
        run_id=draft.run_id,
        todo_id=todo.todo_id,
        resolution="已确认",
    )
    assert resolved.run_status == WorkflowRunStatus.RUNNING
    assert resolved.human_todos[0].status == HumanTodoStatus.RESOLVED
    assert resolved.current_node_id == "finalize"


def test_cannot_resolve_todo_when_not_waiting(service: WorkflowService, alice_id: str) -> None:
    order = _demo_order(project_id="project-1")
    draft = service.submit_work_order(account_id=alice_id, order=order)
    service.confirm_work_order(account_id=alice_id, run_id=draft.run_id)

    with pytest.raises(WorkflowError, match="非法状态转换"):
        service.resolve_human_todo(
            account_id=alice_id,
            run_id=draft.run_id,
            todo_id="any-id",
            resolution="x",
        )


def test_cross_account_access_is_rejected(service: WorkflowService, alice_id: str, bob_id: str) -> None:
    order = _demo_order(project_id="project-1")
    draft = service.submit_work_order(account_id=alice_id, order=order)

    with pytest.raises(WorkflowError, match="不存在或没有访问权限"):
        service.get_run(account_id=bob_id, run_id=draft.run_id)


def test_illegal_advance_from_non_running_is_rejected(service: WorkflowService, alice_id: str) -> None:
    order = _demo_order(project_id="project-1")
    draft = service.submit_work_order(account_id=alice_id, order=order)

    with pytest.raises(WorkflowError, match="非法状态转换"):
        service.advance_run(account_id=alice_id, run_id=draft.run_id)
