"""Integration tests for the WorkOrder / task-stage API seam.

The seam under test: an authenticated user submits a WorkOrder through the API,
confirms it, observes the task-stage projection separate run status from artifact
trust status and publish eligibility, cancels or resolves human todos, and cannot
access another user's run.
"""

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _register(
    client: TestClient, username: str, qq_email: str, password: str
) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={"username": username, "qq_email": qq_email, "password": password},
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def _create_project(client: TestClient, name: str) -> str:
    response = client.post("/projects", json={"name": name})
    assert response.status_code == 201
    return cast(str, response.json()["id"])


def _submit_work_order(client: TestClient, project_id: str) -> dict[str, Any]:
    response = client.post(
        f"/projects/{project_id}/work-orders",
        json={
            "workflow_name": "generic_science_task",
            "workflow_version": "1",
            "project_id": project_id,
            "objective": "为大学生解释贝尔不等式",
            "success_criteria": "生成带引用和事实锁的科普草稿",
            "risk_statement": "量子基础解释可能过度简化",
        },
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def test_submit_work_order_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/projects/some-project/work-orders",
        json={
            "workflow_name": "generic_science_task",
            "workflow_version": "1",
            "project_id": "some-project",
            "objective": "x",
            "success_criteria": "y",
            "risk_statement": "z",
        },
    )
    assert response.status_code == 401


def test_submit_work_order_rejects_unknown_project(client: TestClient) -> None:
    _register(client, "wo-unknown-project", "200001@qq.com", "correct-horse-12")
    response = client.post(
        "/projects/missing-project/work-orders",
        json={
            "workflow_name": "generic_science_task",
            "workflow_version": "1",
            "project_id": "missing-project",
            "objective": "x",
            "success_criteria": "y",
            "risk_statement": "z",
        },
    )
    assert response.status_code == 404


def test_submit_work_order_rejects_project_mismatch(client: TestClient) -> None:
    _register(client, "wo-mismatch", "200002@qq.com", "correct-horse-12")
    project_id = _create_project(client, "匹配项目")
    response = client.post(
        f"/projects/{project_id}/work-orders",
        json={
            "workflow_name": "generic_science_task",
            "workflow_version": "1",
            "project_id": "different-project",
            "objective": "x",
            "success_criteria": "y",
            "risk_statement": "z",
        },
    )
    assert response.status_code == 400


def test_work_order_lifecycle_through_api(client: TestClient) -> None:
    _register(client, "wo-lifecycle", "200003@qq.com", "correct-horse-12")
    project_id = _create_project(client, "生命周期项目")

    draft = _submit_work_order(client, project_id)
    run_id = draft["run_id"]
    assert draft["run_status"] == "draft"
    assert draft["artifact_trust_status"] == "not_created"
    assert draft["objective"]
    assert draft["success_criteria"]
    assert draft["risk_statement"]

    confirm_response = client.post(
        f"/projects/{project_id}/runs/{run_id}/confirm",
        json={"confirmed": True},
    )
    assert confirm_response.status_code == 200
    confirmed = confirm_response.json()
    assert confirmed["run_status"] == "running"
    assert confirmed["artifact_trust_status"] == "draft"
    assert confirmed["current_node_id"] == "compile_context"
    assert confirmed["run_started_at"] is not None
    # Running is not the same as approved or publishable.
    assert confirmed["publish_eligible"] is False

    # Advance through the deterministic two-node workflow.
    advance1 = client.post(f"/_test/runs/{run_id}/advance")
    assert advance1.status_code == 200
    after_first = advance1.json()
    assert after_first["run_status"] == "running"
    assert after_first["nodes"][0]["status"] == "completed"
    assert after_first["nodes"][1]["status"] == "running"

    advance2 = client.post(f"/_test/runs/{run_id}/advance")
    assert advance2.status_code == 200
    succeeded = advance2.json()
    assert succeeded["run_status"] == "succeeded"
    assert succeeded["artifact_trust_status"] == "qualified"
    assert succeeded["run_ended_at"] is not None
    assert succeeded["publish_eligible"] is False

    # Refresh projection from a deep link returns the same stored state.
    get_response = client.get(f"/projects/{project_id}/runs/{run_id}")
    assert get_response.status_code == 200
    refreshed = get_response.json()
    assert refreshed["run_status"] == "succeeded"
    assert refreshed["artifact_trust_status"] == "qualified"
    assert refreshed["context_envelope"]["run_id"] == run_id


def test_cancel_run_through_api(client: TestClient) -> None:
    _register(client, "wo-cancel", "200004@qq.com", "correct-horse-12")
    project_id = _create_project(client, "取消项目")
    draft = _submit_work_order(client, project_id)
    run_id = draft["run_id"]

    # Confirm the run first so cancellation leaves the running state.
    confirm = client.post(f"/projects/{project_id}/runs/{run_id}/confirm", json={"confirmed": True})
    assert confirm.status_code == 200

    cancel_response = client.post(
        f"/projects/{project_id}/runs/{run_id}/cancel",
        json={"reason": "用户主动取消"},
    )
    assert cancel_response.status_code == 200
    cancelled = cancel_response.json()
    assert cancelled["run_status"] == "cancelled"
    assert cancelled["cancel_reason"] == "用户主动取消"
    assert cancelled["run_ended_at"] is not None

    # Illegal transition: cannot cancel an already terminal run.
    second_cancel = client.post(
        f"/projects/{project_id}/runs/{run_id}/cancel",
        json={"reason": "再次取消"},
    )
    assert second_cancel.status_code == 400


def test_cross_account_run_access_is_rejected(client: TestClient) -> None:
    # T007: use separate browser sessions so account-switch cleanup does not
    # revoke Alice's session while Bob is being registered.
    alice_client = TestClient(client.app)
    _register(alice_client, "alice-run", "200005@qq.com", "correct-horse-12")

    bob_client = TestClient(client.app)
    _register(bob_client, "bob-run", "200006@qq.com", "correct-horse-12")
    bob_project = _create_project(bob_client, "Bob 项目")
    bob_draft = _submit_work_order(bob_client, bob_project)
    run_id = bob_draft["run_id"]

    # Alice's session cannot access Bob's run deep link.
    response = alice_client.get(f"/projects/{bob_project}/runs/{run_id}")
    assert response.status_code == 404


def test_human_todo_flow_through_api(client: TestClient) -> None:
    _register(client, "wo-todo", "200009@qq.com", "correct-horse-12")
    project_id = _create_project(client, "人工门项目")

    # Register a gated workflow for this test via the service attached to the app.
    workflow_service = client.app.state.workflow_service  # type: ignore[attr-defined]
    from bridges.contracts.workflows import WorkflowRunStatus

    workflow_service.register_workflow(
        name="gated_science_task",
        version="1",
        nodes=[
            {"node_id": "human_check", "node_name": "人工确认", "human_gate": True},
            {"node_id": "finalize", "node_name": "完成", "human_gate": False},
        ],
        terminal_states=[
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.CANCELLED,
        ],
    )

    response = client.post(
        f"/projects/{project_id}/work-orders",
        json={
            "workflow_name": "gated_science_task",
            "workflow_version": "1",
            "project_id": project_id,
            "objective": "需要人工确认",
            "success_criteria": "人工确认后完成",
            "risk_statement": "人工门示例",
        },
    )
    assert response.status_code == 201
    draft = response.json()
    run_id = draft["run_id"]

    confirm_response = client.post(
        f"/projects/{project_id}/runs/{run_id}/confirm",
        json={"confirmed": True},
    )
    assert confirm_response.status_code == 200
    waiting = confirm_response.json()
    assert waiting["run_status"] == "waiting_human"
    assert len(waiting["human_todos"]) == 1
    todo_id = waiting["human_todos"][0]["todo_id"]

    resolve_response = client.post(
        f"/projects/{project_id}/runs/{run_id}/todos/{todo_id}/resolve",
        json={"resolution": "已确认继续"},
    )
    assert resolve_response.status_code == 200
    resumed = resolve_response.json()
    assert resumed["run_status"] == "running"
    assert resumed["human_todos"][0]["status"] == "resolved"
    assert resumed["current_node_id"] == "finalize"


@pytest.mark.skip(reason="Issue 16 已移除旧画像切片公共 API；当前聊天切片为内部能力。")
class TestMemorySliceBoundToRun:
    def _create_assertion(self, client: TestClient, account_id: str) -> None:
        response = client.post(
            "/profiles/observations",
            json={
                "owner_account_id": account_id,
                "source_type": "explicit_statement",
                "source_ref": "conversation-1",
                "source_span_or_event": "user-message-1",
                "scene": "quick_check",
                "purpose": "expression_preference",
                "observed_content": "I prefer short answers.",
                "signal_kind": "preference",
                "extractor_and_version": "rule-extractor-1",
                "reliability_factors": ["explicit_statement"],
                "sensitivity_class": "preference",
                "retention_policy": "account_lifetime",
            },
        )
        assert response.status_code == 201
        obs_id = response.json()["observation_id"]

        response = client.post(
            "/profiles/candidates",
            json={
                "owner_account_id": account_id,
                "canonical_dimension": "expression_brevity",
                "value_or_rule": "prefer_short_answers",
                "applicable_scenes": ["generic_science_task"],
                "supporting_observation_ids": [obs_id],
            },
        )
        assert response.status_code == 201
        candidate_id = response.json()["candidate_id"]

        response = client.post(
            f"/profiles/candidates/{candidate_id}/decision",
            json={"decision": "accept", "reason": "Confirmed."},
        )
        assert response.status_code == 200

    def test_run_confirmation_compiles_memory_slice(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "wo-slice", "200007@qq.com", "correct-horse-12")
        account_id = registered["account"]["id"]
        self._create_assertion(client, account_id)
        project_id = _create_project(client, "切片项目")

        draft = _submit_work_order(client, project_id)
        run_id = draft["run_id"]
        assert draft["context_envelope"]["memory_slice_refs"] == []

        confirm = client.post(
            f"/projects/{project_id}/runs/{run_id}/confirm",
            json={"confirmed": True},
        )
        assert confirm.status_code == 200
        confirmed = confirm.json()
        slice_refs = confirmed["context_envelope"]["memory_slice_refs"]
        assert len(slice_refs) == 1

        # The context inspector can read the slice and see the included assertion.
        slice_id = slice_refs[0]
        response = client.get(f"/profiles/memory-slices/{slice_id}/inspector")
        assert response.status_code == 200
        inspected = response.json()
        assert inspected["run_id"] == run_id
        assert len(inspected["included_items"]) == 1
        assert inspected["included_items"][0]["dimension"] == "expression_brevity"

    def test_run_cancellation_invalidates_memory_slice(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "wo-slice-cancel", "200008@qq.com", "correct-horse-12")
        account_id = registered["account"]["id"]
        self._create_assertion(client, account_id)
        project_id = _create_project(client, "取消切片项目")

        draft = _submit_work_order(client, project_id)
        run_id = draft["run_id"]
        client.post(
            f"/projects/{project_id}/runs/{run_id}/confirm",
            json={"confirmed": True},
        )
        slice_id = client.get(f"/projects/{project_id}/runs/{run_id}").json()[
            "context_envelope"
        ]["memory_slice_refs"][0]

        cancel = client.post(
            f"/projects/{project_id}/runs/{run_id}/cancel",
            json={"reason": "用户主动取消"},
        )
        assert cancel.status_code == 200

        # Model access check now fails closed for the invalidated slice.
        response = client.post(
            f"/profiles/memory-slices/{slice_id}/access-check",
            params={"run_id": run_id},
        )
        assert response.status_code == 403
