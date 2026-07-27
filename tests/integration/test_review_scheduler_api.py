"""Integration tests for the review-scheduling API seam (T024).

The seam under test: an authenticated user receives a justified review task,
can postpone or cancel it, completes it to create new learning evidence, and
sees future tasks cancelled or rescheduled when the learning path changes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def authenticated_client(
    client: TestClient,
    registered_user: Any,
) -> TestClient:
    """Return a client authenticated as a freshly registered user."""
    registered_user(client, "review-user@example.com", "correct-horse-12")
    return client


def _create_mission(client: TestClient) -> str:
    resp = client.post(
        "/learning/missions",
        json={
            "title": "理解光合作用",
            "goal": "能解释光反应与暗反应的关系。",
            "scope_concepts": ["光反应", "暗反应"],
            "constraints": [],
            "success_criteria": ["能说明光反应产物如何驱动暗反应"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["mission_id"]


def _complete_diagnostic_and_record(client: TestClient, mission_id: str) -> None:
    run_resp = client.post(f"/learning/missions/{mission_id}/runs", json=[])
    assert run_resp.status_code == 201, run_resp.text
    run = run_resp.json()
    question_id = run["questions"][0]["question_id"]

    answer_resp = client.post(
        f"/learning/runs/{run['run_id']}/answers",
        json={
            "question_id": question_id,
            "response_text": "光反应在类囊体膜上进行，产物 ATP 和 NADPH 用于暗反应。",
            "evaluated_state": "correct",
            "evaluator": "rule",
            "evaluation_reason": "回答完整。",
        },
    )
    assert answer_resp.status_code == 200, answer_resp.text

    complete_resp = client.post(f"/learning/runs/{run['run_id']}/complete")
    assert complete_resp.status_code == 200, complete_resp.text

    record_resp = client.post(
        f"/learning/missions/{mission_id}/learning-records",
        json={
            "concept_id": "光反应",
            "record_type": "exercise_attempt",
            "source_type": "exercise_attempt",
            "source_id": "attempt-1",
            "response_text": "正确。",
            "evaluated_state": "correct",
            "record_reason": "正确完成检索练习。",
        },
    )
    assert record_resp.status_code == 201, record_resp.text


def _accept_proposal(client: TestClient, mission_id: str) -> str:
    propose_resp = client.post(
        f"/learning/missions/{mission_id}/knowledge-state-proposals",
        json={"concept_id": "光反应"},
    )
    assert propose_resp.status_code == 201, propose_resp.text
    proposal = propose_resp.json()

    decide_resp = client.post(
        f"/learning/knowledge-state-proposals/{proposal['proposal_id']}/decide",
        json={"decision": "accept", "reason": "我同意这个判断。"},
    )
    assert decide_resp.status_code == 200, decide_resp.text
    return proposal["proposal_id"]


class TestReviewScheduleAPI:
    def test_schedule_reviews_after_learning_records(
        self, authenticated_client: TestClient
    ) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic_and_record(authenticated_client, mission_id)
        _accept_proposal(authenticated_client, mission_id)

        resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )
        assert resp.status_code == 201, resp.text
        schedule = resp.json()
        assert schedule["mission_id"] == mission_id
        assert schedule["task_ids"]

    def test_get_review_schedule(self, authenticated_client: TestClient) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic_and_record(authenticated_client, mission_id)
        _accept_proposal(authenticated_client, mission_id)
        authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )

        resp = authenticated_client.get(
            f"/learning/missions/{mission_id}/review-schedule"
        )
        assert resp.status_code == 200, resp.text
        schedule = resp.json()
        assert schedule["mission_id"] == mission_id
        assert schedule["task_ids"]

    def test_review_task_reason_is_visible(
        self, authenticated_client: TestClient
    ) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic_and_record(authenticated_client, mission_id)
        _accept_proposal(authenticated_client, mission_id)

        schedule_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )
        task_id = schedule_resp.json()["task_ids"][0]

        task_resp = authenticated_client.get(f"/learning/review-tasks/{task_id}")
        assert task_resp.status_code == 200, task_resp.text
        task = task_resp.json()
        assert task["reason"]
        assert "理解光合作用" in task["reason"]
        assert task["knowledge_state_id"] is not None
        assert task["source_record_ids"]

    def test_list_pending_tasks_interleaved(
        self, authenticated_client: TestClient
    ) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic_and_record(authenticated_client, mission_id)
        _accept_proposal(authenticated_client, mission_id)

        # Add a record for the second concept so it also gets a task.
        authenticated_client.post(
            f"/learning/missions/{mission_id}/learning-records",
            json={
                "concept_id": "暗反应",
                "record_type": "exercise_attempt",
                "source_type": "exercise_attempt",
                "source_id": "attempt-dark",
                "response_text": "正确。",
                "evaluated_state": "correct",
                "record_reason": "正确完成检索练习。",
            },
        )

        authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )

        resp = authenticated_client.get(
            f"/learning/missions/{mission_id}/review-tasks"
        )
        assert resp.status_code == 200, resp.text
        tasks = resp.json()
        assert len(tasks) == 2
        assert tasks[0]["concept_id"] != tasks[1]["concept_id"]

    def test_postpone_review_task(self, authenticated_client: TestClient) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic_and_record(authenticated_client, mission_id)
        _accept_proposal(authenticated_client, mission_id)

        schedule_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )
        task_id = schedule_resp.json()["task_ids"][0]

        new_due = (datetime.now(UTC) + timedelta(days=7)).isoformat()
        resp = authenticated_client.post(
            f"/learning/review-tasks/{task_id}/postpone",
            json={"new_due_at": new_due, "reason": "本周忙碌。"},
        )
        assert resp.status_code == 200, resp.text
        task = resp.json()
        assert task["status"] == "postponed"
        assert task["postponed_to"].startswith(new_due[:10])

    def test_cancel_review_task(self, authenticated_client: TestClient) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic_and_record(authenticated_client, mission_id)
        _accept_proposal(authenticated_client, mission_id)

        schedule_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )
        task_id = schedule_resp.json()["task_ids"][0]

        resp = authenticated_client.post(
            f"/learning/review-tasks/{task_id}/cancel",
            json={"reason": "已经掌握。"},
        )
        assert resp.status_code == 200, resp.text
        task = resp.json()
        assert task["status"] == "cancelled"
        assert task["cancellation_reason"] == "已经掌握。"

    def test_adjust_review_task(self, authenticated_client: TestClient) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic_and_record(authenticated_client, mission_id)
        _accept_proposal(authenticated_client, mission_id)

        schedule_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )
        task_id = schedule_resp.json()["task_ids"][0]

        new_due = (datetime.now(UTC) + timedelta(days=14)).isoformat()
        resp = authenticated_client.post(
            f"/learning/review-tasks/{task_id}/adjust",
            json={"new_due_at": new_due, "new_interval_days": 14, "reason": "调整间隔。"},
        )
        assert resp.status_code == 200, resp.text
        task = resp.json()
        assert task["status"] == "scheduled"
        assert task["interval_days"] == 14
        assert "调整间隔" in task["reason"]

    def test_submit_review_task_as_work_order(
        self, authenticated_client: TestClient
    ) -> None:
        """Materialize a review task as a WorkOrder on the task stage."""
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic_and_record(authenticated_client, mission_id)
        _accept_proposal(authenticated_client, mission_id)

        schedule_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )
        task_id = schedule_resp.json()["task_ids"][0]

        resp = authenticated_client.post(
            f"/learning/review-tasks/{task_id}/work-order"
        )
        assert resp.status_code == 201, resp.text
        projection = resp.json()
        assert projection["run_id"]
        assert projection["workflow_name"] == "review_task"

        # Task should now reference the workflow run.
        task_resp = authenticated_client.get(f"/learning/review-tasks/{task_id}")
        assert task_resp.status_code == 200, task_resp.text
        assert task_resp.json()["run_id"] == projection["run_id"]

    def test_complete_review_task_creates_new_record(
        self, authenticated_client: TestClient
    ) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic_and_record(authenticated_client, mission_id)
        _accept_proposal(authenticated_client, mission_id)

        schedule_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )
        task_id = schedule_resp.json()["task_ids"][0]

        before_records = authenticated_client.get(
            f"/learning/missions/{mission_id}/learning-records?concept_id=光反应"
        ).json()

        resp = authenticated_client.post(
            f"/learning/review-tasks/{task_id}/complete",
            json={
                "response_text": "光反应在类囊体膜上进行。",
                "evaluated_state": "correct",
                "record_reason": "延迟复测正确。",
            },
        )
        assert resp.status_code == 201, resp.text
        record = resp.json()
        assert record["record_type"] == "delayed_retrieval"
        assert record["source_id"] == task_id
        assert record["concept_id"] == "光反应"

        after_records = authenticated_client.get(
            f"/learning/missions/{mission_id}/learning-records?concept_id=光反应"
        ).json()
        assert len(after_records) == len(before_records) + 1

        task_resp = authenticated_client.get(f"/learning/review-tasks/{task_id}")
        assert task_resp.json()["status"] == "completed"

    def test_path_change_reschedules_review_tasks(
        self, authenticated_client: TestClient
    ) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic_and_record(authenticated_client, mission_id)
        _accept_proposal(authenticated_client, mission_id)

        schedule_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )
        original_task_id = schedule_resp.json()["task_ids"][0]

        # Accepting a proposal recompiles the path and triggers reschedule.
        # Schedule again to simulate a path change.
        new_schedule_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )
        new_task_id = new_schedule_resp.json()["task_ids"][0]

        old_task = authenticated_client.get(
            f"/learning/review-tasks/{original_task_id}"
        ).json()
        assert old_task["status"] == "cancelled"
        assert new_task_id != original_task_id


class TestReviewTaskIsolationAPI:
    def test_other_user_cannot_access_review_task(
        self,
        authenticated_client: TestClient,
        registered_user: Any,
    ) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic_and_record(authenticated_client, mission_id)
        _accept_proposal(authenticated_client, mission_id)

        schedule_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/review-schedule"
        )
        task_id = schedule_resp.json()["task_ids"][0]

        other_client = TestClient(authenticated_client.app)
        registered_user(other_client, "other-review@example.com", "correct-horse-12")

        get_resp = other_client.get(f"/learning/review-tasks/{task_id}")
        assert get_resp.status_code == 404
