"""Integration tests for the short-lesson API seam (T022)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from science_companion.contracts.learning import LessonStatus


@pytest.fixture
def authenticated_client(
    client: TestClient,
    registered_user: Any,
) -> TestClient:
    """Return a client authenticated as a freshly registered user."""
    registered_user(client, "teaching-user@example.com", "correct-horse-12")
    return client


def _fact_lock(lock_id: str) -> dict[str, Any]:
    return {
        "lock_id": lock_id,
        "claim_id": "claim-1",
        "lock_type": "term_formula",
        "canonical_value": "光合作用 = 光反应 + 暗反应",
        "allowed_variants": [],
        "forbidden_transformations": ["不得改变定义对象或内涵"],
        "required_qualifiers": [],
        "evidence_ids": ["ev-1"],
        "citation_ids": ["cite-1"],
        "wording_strength_ceiling": "high",
        "verification_method": "rule",
    }


def _create_mission_and_plan(client: TestClient) -> tuple[str, str, str]:
    mission_resp = client.post(
        "/learning/missions",
        json={
            "title": "理解光合作用",
            "goal": "能解释光反应与暗反应的关系。",
            "scope_concepts": ["光反应", "暗反应"],
            "constraints": [],
            "success_criteria": ["能说明光反应产物如何驱动暗反应"],
        },
    )
    assert mission_resp.status_code == 201, mission_resp.text
    mission = mission_resp.json()
    mission_id = mission["mission_id"]

    run_resp = client.post(f"/learning/missions/{mission_id}/runs", json=[])
    assert run_resp.status_code == 201, run_resp.text
    run = run_resp.json()
    run_id = run["run_id"]
    question_id = run["questions"][0]["question_id"]

    answer_resp = client.post(
        f"/learning/runs/{run_id}/answers",
        json={
            "question_id": question_id,
            "response_text": "光反应在类囊体膜上进行，产物 ATP 和 NADPH 用于暗反应还原 C3。",
            "evaluated_state": "correct",
            "evaluator": "rule",
            "evaluation_reason": "回答完整。",
        },
    )
    assert answer_resp.status_code == 200, answer_resp.text

    complete_resp = client.post(f"/learning/runs/{run_id}/complete")
    assert complete_resp.status_code == 200, complete_resp.text

    plan_resp = client.get(f"/learning/missions/{mission_id}/teaching-plan")
    assert plan_resp.status_code == 200, plan_resp.text
    plan = plan_resp.json()

    return mission_id, run_id, plan["plan_id"]


class TestGenerateShortLesson:
    def test_generate_short_lesson_via_api(
        self, authenticated_client: TestClient
    ) -> None:
        _mission_id, _run_id, plan_id = _create_mission_and_plan(authenticated_client)

        lesson_resp = authenticated_client.post(
            f"/learning/plans/{plan_id}/lessons",
            json={
                "evidence_bundle": {
                    "graph_id": "graph-1",
                    "evidence_refs": [
                        {
                            "claim_id": "claim-1",
                            "evidence_id": "ev-1",
                            "source_id": "source-bio",
                            "document_id": "doc-v1",
                            "reason": "教材中关于光反应的证据。",
                        }
                    ],
                    "fact_locks": [_fact_lock("lock-1")],
                }
            },
        )
        assert lesson_resp.status_code == 201, lesson_resp.text
        lesson = lesson_resp.json()
        assert lesson["mission_id"] == _mission_id
        assert len(lesson["target_concepts"]) == 1
        assert lesson["status"] == LessonStatus.QUALIFIED.value
        assert lesson["quality_gate"]["passed"] is True
        assert lesson["exercises"]

        # Retrieve the lesson.
        lesson_id = lesson["lesson_id"]
        get_resp = authenticated_client.get(f"/learning/lessons/{lesson_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["lesson_id"] == lesson_id

    def test_missing_evidence_blocks_lesson_via_api(
        self, authenticated_client: TestClient
    ) -> None:
        _mission_id, _run_id, plan_id = _create_mission_and_plan(authenticated_client)

        lesson_resp = authenticated_client.post(
            f"/learning/plans/{plan_id}/lessons",
            json={"evidence_bundle": {"graph_id": "graph-1", "fact_locks": [_fact_lock("lock-1")]}},
        )
        assert lesson_resp.status_code == 201, lesson_resp.text
        lesson = lesson_resp.json()
        assert lesson["status"] == LessonStatus.BLOCKED.value
        assert lesson["quality_gate"]["passed"] is False


class TestExerciseAttemptAPI:
    def test_submit_attempt_and_receive_feedback(
        self, authenticated_client: TestClient
    ) -> None:
        _mission_id, _run_id, plan_id = _create_mission_and_plan(authenticated_client)

        lesson_resp = authenticated_client.post(
            f"/learning/plans/{plan_id}/lessons",
            json={
                "evidence_bundle": {
                    "graph_id": "graph-1",
                    "evidence_refs": [
                        {"claim_id": "claim-1", "reason": "证据。"}
                    ],
                    "fact_locks": [_fact_lock("lock-1")],
                }
            },
        )
        assert lesson_resp.status_code == 201
        lesson = lesson_resp.json()
        exercise_id = lesson["exercises"][0]["exercise_id"]
        lesson_id = lesson["lesson_id"]

        attempt_resp = authenticated_client.post(
            f"/learning/lessons/{lesson_id}/exercises/{exercise_id}/attempts",
            json={"response_text": "光反应的定义是在类囊体膜上进行，核心内容是吸收光能。"},
        )
        assert attempt_resp.status_code == 201, attempt_resp.text
        attempt = attempt_resp.json()
        assert attempt["evaluated_state"] == "correct"
        assert attempt["feedback"]["is_correct"] is True
        assert attempt["feedback"]["evidence_refs"]


class TestIsolationAPI:
    def test_other_user_cannot_access_lesson(
        self,
        authenticated_client: TestClient,
        registered_user: Any,
    ) -> None:
        _mission_id, _run_id, plan_id = _create_mission_and_plan(authenticated_client)
        lesson_resp = authenticated_client.post(
            f"/learning/plans/{plan_id}/lessons",
            json={
                "evidence_bundle": {
                    "graph_id": "graph-1",
                    "evidence_refs": [{"claim_id": "claim-1", "reason": "证据。"}],
                    "fact_locks": [_fact_lock("lock-1")],
                }
            },
        )
        assert lesson_resp.status_code == 201
        lesson_id = lesson_resp.json()["lesson_id"]

        other_client = TestClient(authenticated_client.app)
        registered_user(other_client, "other-teaching@example.com", "correct-horse-12")
        get_resp = other_client.get(f"/learning/lessons/{lesson_id}")
        assert get_resp.status_code == 404
