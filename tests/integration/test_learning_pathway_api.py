"""Integration tests for the learning-evidence and learning-path API seam (T023).

The seam under test: an authenticated user completes a retrieval exercise,
corrects a misconception, or only browses material. Only the first two produce
qualified learning records that can propose knowledge-state and path updates.
The user confirms important changes and rejections cannot be bypassed.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def authenticated_client(
    client: TestClient,
    registered_user: Any,
) -> TestClient:
    """Return a client authenticated as a freshly registered user."""
    registered_user(client, "pathway-user", "110001@qq.com", "correct-horse-12")
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


def _complete_diagnostic(client: TestClient, mission_id: str) -> None:
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


class TestLearningRecordAPI:
    def test_create_learning_record_via_api(
        self, authenticated_client: TestClient
    ) -> None:
        mission_id = _create_mission(authenticated_client)

        resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/learning-records",
            json={
                "concept_id": "光反应",
                "record_type": "exercise_attempt",
                "source_type": "exercise_attempt",
                "source_id": "attempt-1",
                "response_text": "光反应在类囊体膜上进行。",
                "evaluated_state": "correct",
                "record_reason": "正确完成检索练习。",
            },
        )
        assert resp.status_code == 201, resp.text
        record = resp.json()
        assert record["concept_id"] == "光反应"
        assert record["source_id"] == "attempt-1"
        assert record["record_type"] == "exercise_attempt"

    def test_list_learning_records_via_api(
        self, authenticated_client: TestClient
    ) -> None:
        mission_id = _create_mission(authenticated_client)
        authenticated_client.post(
            f"/learning/missions/{mission_id}/learning-records",
            json={
                "concept_id": "光反应",
                "record_type": "exercise_attempt",
                "source_type": "exercise_attempt",
                "source_id": "attempt-1",
                "response_text": "光反应在类囊体膜上进行。",
                "evaluated_state": "correct",
                "record_reason": "正确完成检索练习。",
            },
        )

        list_resp = authenticated_client.get(
            f"/learning/missions/{mission_id}/learning-records?concept_id=光反应"
        )
        assert list_resp.status_code == 200, list_resp.text
        records = list_resp.json()
        assert len(records) == 1
        assert records[0]["concept_id"] == "光反应"


class TestKnowledgeStateProposalAPI:
    def test_propose_and_accept_updates_state_and_path(
        self, authenticated_client: TestClient
    ) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic(authenticated_client, mission_id)

        record_resp = authenticated_client.post(
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

        propose_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/knowledge-state-proposals",
            json={"concept_id": "光反应"},
        )
        assert propose_resp.status_code == 201, propose_resp.text
        proposal = propose_resp.json()
        assert proposal["status"] == "pending"
        assert proposal["proposed_status"] == "supported"
        assert proposal["supporting_record_ids"]

        decide_resp = authenticated_client.post(
            f"/learning/knowledge-state-proposals/{proposal['proposal_id']}/decide",
            json={
                "decision": "accept",
                "reason": "我同意这个判断。",
            },
        )
        assert decide_resp.status_code == 200, decide_resp.text
        decided = decide_resp.json()
        assert decided["status"] == "accepted"
        assert decided["decision"]["decision"] == "accept"

        states_resp = authenticated_client.get(f"/learning/missions/{mission_id}/states")
        assert states_resp.status_code == 200, states_resp.text
        states = states_resp.json()
        light_state = next(s for s in states if s["concept_id"] == "光反应")
        assert light_state["state_id"] == proposal["proposal_id"]
        assert light_state["status"] == "supported"

        path_resp = authenticated_client.get(f"/learning/missions/{mission_id}/learning-path")
        assert path_resp.status_code == 200, path_resp.text
        path = path_resp.json()
        assert path["mission_id"] == mission_id
        assert path["nodes"]

    def test_rejected_proposal_does_not_update_state(
        self, authenticated_client: TestClient
    ) -> None:
        mission_id = _create_mission(authenticated_client)
        _complete_diagnostic(authenticated_client, mission_id)

        authenticated_client.post(
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
        propose_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/knowledge-state-proposals",
            json={"concept_id": "光反应"},
        )
        proposal = propose_resp.json()

        states_before = authenticated_client.get(
            f"/learning/missions/{mission_id}/states"
        ).json()
        light_state_before = next(
            s for s in states_before if s["concept_id"] == "光反应"
        )

        decide_resp = authenticated_client.post(
            f"/learning/knowledge-state-proposals/{proposal['proposal_id']}/decide",
            json={
                "decision": "reject",
                "reason": "我觉得还需要更多证据。",
            },
        )
        assert decide_resp.status_code == 200, decide_resp.text
        decided = decide_resp.json()
        assert decided["status"] == "rejected"

        states_after = authenticated_client.get(
            f"/learning/missions/{mission_id}/states"
        ).json()
        light_state_after = next(
            s for s in states_after if s["concept_id"] == "光反应"
        )
        assert light_state_after["state_id"] == light_state_before["state_id"]
        assert light_state_after["status"] == light_state_before["status"]

    def test_only_qualified_records_can_propose(
        self, authenticated_client: TestClient
    ) -> None:
        mission_id = _create_mission(authenticated_client)

        propose_resp = authenticated_client.post(
            f"/learning/missions/{mission_id}/knowledge-state-proposals",
            json={"concept_id": "光反应"},
        )
        assert propose_resp.status_code == 400, propose_resp.text
        detail = propose_resp.json().get("detail", {})
        assert detail.get("error") == "proposal_failed"


class TestIsolationAPI:
    def test_other_user_cannot_access_learning_path(
        self,
        authenticated_client: TestClient,
        registered_user: Any,
    ) -> None:
        mission_id = _create_mission(authenticated_client)

        other_client = TestClient(authenticated_client.app)
        registered_user(other_client, "other-pathway", "110002@qq.com", "correct-horse-12")

        get_resp = other_client.get(f"/learning/missions/{mission_id}/learning-path")
        assert get_resp.status_code == 404
