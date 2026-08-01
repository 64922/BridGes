"""Integration tests for the evaluation center API seam.

The seam under test: an authenticated user creates an evaluation run from a
completed project task run, replays it twice through the API, and compares the
result bundles. Inputs, locks, versions and failures are visible; private body
is not exposed.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _register(client: TestClient, email: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password, "agreed_to_terms": True},
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def _create_project(client: TestClient, name: str) -> str:
    response = client.post("/projects", json={"name": name})
    assert response.status_code == 201
    return cast(str, response.json()["id"])


def _submit_and_complete_run(client: TestClient, project_id: str) -> str:
    """Submit a generic science task, confirm it and advance to success."""
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
    draft = response.json()
    run_id = cast(str, draft["run_id"])

    confirm = client.post(
        f"/projects/{project_id}/runs/{run_id}/confirm",
        json={"confirmed": True},
    )
    assert confirm.status_code == 200

    # Advance through the deterministic two-node workflow.
    advance1 = client.post(f"/_test/runs/{run_id}/advance")
    assert advance1.status_code == 200
    advance2 = client.post(f"/_test/runs/{run_id}/advance")
    assert advance2.status_code == 200
    succeeded = advance2.json()
    assert succeeded["run_status"] == "succeeded"
    return run_id


def test_create_evaluation_run_from_completed_task(client: TestClient) -> None:
    _register(client, "eval-create@example.com", "correct-horse-12")
    project_id = _create_project(client, "评测项目")
    run_id = _submit_and_complete_run(client, project_id)

    response = client.post(
        f"/projects/{project_id}/runs/{run_id}/evaluations",
        json={
            "suite_id": "science-baseline",
            "suite_version": "1.0.0",
            "case_id": "bell-inequality",
            "runtime_identifier": "conda-agent",
            "random_seed": 123,
            "execution_count": 1,
        },
    )
    assert response.status_code == 201
    eval_run = response.json()
    assert eval_run["source_run_id"] == run_id
    assert eval_run["project_id"] == project_id
    assert eval_run["status"] == "pending"
    assert eval_run["lock"]["workflow_name"] == "generic_science_task"
    assert eval_run["lock"]["workflow_version"] == "1"
    assert eval_run["lock"]["work_order"]["objective"] == "为大学生解释贝尔不等式"
    assert eval_run["lock"]["suite"]["suite_id"] == "science-baseline"
    assert eval_run["lock"]["suite"]["case_id"] == "bell-inequality"
    assert eval_run["lock"]["random_seed"] == 123
    # Inputs and model locks from the source run are frozen in the lock.
    assert eval_run["lock"]["model_run_locks"] is not None
    assert eval_run["lock"]["runtime_identifier"] == "conda-agent"


def test_evaluation_replay_and_compare(client: TestClient) -> None:
    _register(client, "eval-replay@example.com", "correct-horse-12")
    project_id = _create_project(client, "重放项目")
    run_id = _submit_and_complete_run(client, project_id)

    create_response = client.post(
        f"/projects/{project_id}/runs/{run_id}/evaluations",
        json={
            "suite_id": "science-baseline",
            "suite_version": "1.0.0",
            "case_id": "bell-inequality",
            "runtime_identifier": "conda-agent",
            "random_seed": 42,
        },
    )
    assert create_response.status_code == 201
    eval_run_id = create_response.json()["evaluation_run_id"]

    # Replay twice.
    replay_a = client.post(
        f"/projects/{project_id}/evaluations/{eval_run_id}/replay"
    )
    assert replay_a.status_code == 200
    bundle_a = replay_a.json()
    assert bundle_a["lock_id"] == eval_run_id
    assert bundle_a["source_run_id"] == run_id
    assert bundle_a["status"] == "succeeded"
    assert bundle_a["failure_category"] == "none"
    assert bundle_a["reproduction_command"]

    replay_b = client.post(
        f"/projects/{project_id}/evaluations/{eval_run_id}/replay"
    )
    assert replay_b.status_code == 200
    bundle_b = replay_b.json()
    assert bundle_b["lock_id"] == eval_run_id
    assert bundle_b["status"] == "succeeded"

    # Get evaluation run shows both bundles.
    get_response = client.get(f"/projects/{project_id}/evaluations/{eval_run_id}")
    assert get_response.status_code == 200
    projection = get_response.json()
    assert projection["bundle_ids"] == [bundle_a["bundle_id"], bundle_b["bundle_id"]]
    assert projection["latest_bundle_id"] == bundle_b["bundle_id"]

    # Compare the two bundles.
    diff_response = client.post(
        f"/projects/{project_id}/evaluations/diff",
        json={
            "bundle_id_a": bundle_a["bundle_id"],
            "bundle_id_b": bundle_b["bundle_id"],
        },
    )
    assert diff_response.status_code == 200
    diff = diff_response.json()
    assert diff["lock_match"] is True
    assert diff["evaluation_run_id_a"] == eval_run_id
    assert diff["evaluation_run_id_b"] == eval_run_id
    # Same lock -> inputs and locks do not differ.
    assert not diff["input_diffs"]
    assert not diff["lock_diffs"]
    assert not diff["failure_diffs"]


def test_evaluation_logs_do_not_expose_private_body(client: TestClient) -> None:
    _register(client, "eval-privacy@example.com", "correct-horse-12")
    project_id = _create_project(client, "隐私项目")
    run_id = _submit_and_complete_run(client, project_id)

    create_response = client.post(
        f"/projects/{project_id}/runs/{run_id}/evaluations",
        json={
            "suite_id": "science-baseline",
            "suite_version": "1.0.0",
            "case_id": "bell-inequality",
            "runtime_identifier": "conda-agent",
        },
    )
    assert create_response.status_code == 201
    eval_run_id = create_response.json()["evaluation_run_id"]

    replay_response = client.post(
        f"/projects/{project_id}/evaluations/{eval_run_id}/replay"
    )
    assert replay_response.status_code == 200
    bundle = replay_response.json()

    bundle_str = str(bundle)
    assert "private_body" not in bundle_str
    assert "full_prompt" not in bundle_str
    assert "api_key" not in bundle_str
    assert "secret" not in bundle_str


def test_cross_account_evaluation_access_is_rejected(client: TestClient) -> None:
    alice_client = TestClient(client.app)
    _register(alice_client, "alice-eval@example.com", "correct-horse-12")

    bob_client = TestClient(client.app)
    _register(bob_client, "bob-eval@example.com", "correct-horse-12")
    bob_project = _create_project(bob_client, "Bob 评测项目")
    bob_run_id = _submit_and_complete_run(bob_client, bob_project)

    create_response = bob_client.post(
        f"/projects/{bob_project}/runs/{bob_run_id}/evaluations",
        json={
            "suite_id": "science-baseline",
            "suite_version": "1.0.0",
        },
    )
    assert create_response.status_code == 201
    eval_run_id = create_response.json()["evaluation_run_id"]

    # Alice cannot access Bob's evaluation run deep link.
    response = alice_client.get(
        f"/projects/{bob_project}/evaluations/{eval_run_id}"
    )
    assert response.status_code == 404

    replay = bob_client.post(
        f"/projects/{bob_project}/evaluations/{eval_run_id}/replay"
    )
    assert replay.status_code == 200
    bundle_id = replay.json()["bundle_id"]

    # Alice cannot access Bob's result bundle.
    response = alice_client.get(
        f"/projects/{bob_project}/evaluations/{eval_run_id}/bundles/{bundle_id}"
    )
    assert response.status_code == 404


def test_evaluation_create_rejects_nonexistent_run(client: TestClient) -> None:
    _register(client, "eval-missing-run@example.com", "correct-horse-12")
    project_id = _create_project(client, "缺失运行项目")

    response = client.post(
        f"/projects/{project_id}/runs/no-such-run/evaluations",
        json={
            "suite_id": "science-baseline",
            "suite_version": "1.0.0",
        },
    )
    assert response.status_code == 404


def test_evaluation_create_rejects_project_mismatch(client: TestClient) -> None:
    _register(client, "eval-mismatch@example.com", "correct-horse-12")
    project_id = _create_project(client, "匹配项目")
    run_id = _submit_and_complete_run(client, project_id)

    response = client.post(
        f"/projects/different-project/runs/{run_id}/evaluations",
        json={
            "suite_id": "science-baseline",
            "suite_version": "1.0.0",
        },
    )
    assert response.status_code == 404
