"""Integration tests for the profile observation-candidate API seam.

The seam under test: an authenticated user records a lawful signal, sees a
candidate profile, accepts/rejects/modifies it, and the memory-slice endpoint
only returns confirmed assertions — never unconfirmed candidates — as stable
facts for subsequent tasks.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from science_companion.api.main import create_app


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


def _login(client: TestClient, email: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return cast(dict[str, Any], response.json())


def _create_observation(client: TestClient, account_id: str) -> dict[str, Any]:
    response = client.post(
        "/profiles/observations",
        json={
            "owner_account_id": account_id,
            "source_type": "explicit_statement",
            "source_ref": "conversation-1",
            "source_span_or_event": "user-message-7",
            "scene": "quick_check",
            "purpose": "expression_preference",
            "observed_content": "I prefer short answers when I am in a hurry.",
            "signal_kind": "preference",
            "extractor_and_version": "rule-extractor-1",
            "reliability_factors": ["explicit_statement", "context_qualified"],
            "sensitivity_class": "preference",
            "retention_policy": "account_lifetime",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _propose_candidate(
    client: TestClient, account_id: str, observation_id: str
) -> dict[str, Any]:
    response = client.post(
        "/profiles/candidates",
        json={
            "owner_account_id": account_id,
            "canonical_dimension": "expression_brevity",
            "value_or_rule": "prefer_short_answers_when_hurried",
            "applicable_scenes": ["quick_check", "mobile_on_the_go"],
            "non_applicable_scenes": ["deep_research_discussion"],
            "supporting_observation_ids": [observation_id],
            "contradicting_observation_ids": [],
            "evidence_summary": "User explicitly stated a scene-qualified preference.",
            "authorization_scope": "expression_preference",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


class TestObservationAPI:
    def test_create_observation_requires_authentication(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/profiles/observations",
            json={
                "owner_account_id": "any",
                "source_type": "explicit_statement",
                "source_ref": "conversation-1",
                "source_span_or_event": "msg-1",
                "scene": "quick_check",
                "purpose": "expression_preference",
                "observed_content": "hello",
                "signal_kind": "preference",
                "extractor_and_version": "rule-extractor-1",
                "reliability_factors": [],
                "sensitivity_class": "preference",
                "retention_policy": "account_lifetime",
            },
        )
        assert response.status_code == 401

    def test_create_observation_returns_traceable_fields(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "profile-obs@example.com", "correct-horse-12")
        account_id = registered["account"]["id"]

        body = _create_observation(client, account_id)

        assert body["owner_account_id"] == account_id
        assert body["source_type"] == "explicit_statement"
        assert body["source_ref"] == "conversation-1"
        assert body["source_span_or_event"] == "user-message-7"
        assert body["scene"] == "quick_check"
        assert body["signal_kind"] == "preference"
        assert body["content_hash"]
        assert body["status"] == "active"

    def test_transient_emotion_observation_is_discarded(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "profile-emotion@example.com", "correct-horse-12")
        account_id = registered["account"]["id"]

        response = client.post(
            "/profiles/observations",
            json={
                "owner_account_id": account_id,
                "source_type": "system_inference",
                "source_ref": "conversation-2",
                "source_span_or_event": "user-message-3",
                "scene": "frustrated_moment",
                "purpose": "adaptation_signal",
                "observed_content": "User seems annoyed today.",
                "signal_kind": "transient_emotion",
                "extractor_and_version": "emotion-detector-1",
                "reliability_factors": ["single_turn"],
                "sensitivity_class": "sensitive",
                "retention_policy": "session_only",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "discarded"
        assert body["signal_kind"] == "transient_emotion"


class TestCandidateAPI:
    def test_propose_candidate_requires_matching_owner(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "profile-cand@example.com", "correct-horse-12")
        account_id = registered["account"]["id"]
        obs = _create_observation(client, account_id)

        response = client.post(
            "/profiles/candidates",
            json={
                "owner_account_id": "other-account",
                "canonical_dimension": "expression_brevity",
                "value_or_rule": "prefer_short_answers_when_hurried",
                "applicable_scenes": ["quick_check"],
                "supporting_observation_ids": [obs["observation_id"]],
            },
        )
        assert response.status_code == 403

    def test_candidate_lists_decision_and_rationale(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "profile-list@example.com", "correct-horse-12")
        account_id = registered["account"]["id"]
        obs = _create_observation(client, account_id)
        candidate = _propose_candidate(client, account_id, obs["observation_id"])

        response = client.post(
            f"/profiles/candidates/{candidate['candidate_id']}/decision",
            json={
                "decision": "reject",
                "reason": "That was a one-off deadline, not a habit.",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["review_status"] == "rejected"
        assert body["human_decision"]["decision"] == "reject"
        assert body["human_decision"]["reason"] == "That was a one-off deadline, not a habit."


class TestMemorySliceAPI:
    def test_unconfirmed_candidate_is_not_included_in_slice(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "profile-slice@example.com", "correct-horse-12")
        account_id = registered["account"]["id"]
        obs = _create_observation(client, account_id)
        candidate = _propose_candidate(client, account_id, obs["observation_id"])

        response = client.get(
            "/profiles/memory-slice",
            params={"purpose": "quick_check", "run_id": "run-slice-1"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run_id"] == "run-slice-1"
        assert candidate["candidate_id"] in body["excluded_candidate_ids"]
        assert body["included_items"] == []

    def test_accepted_assertion_is_included_in_slice(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "profile-slice2@example.com", "correct-horse-12")
        account_id = registered["account"]["id"]
        obs = _create_observation(client, account_id)
        candidate = _propose_candidate(client, account_id, obs["observation_id"])

        response = client.post(
            f"/profiles/candidates/{candidate['candidate_id']}/decision",
            json={"decision": "accept", "reason": "Confirmed."},
        )
        assert response.status_code == 200, response.text

        response = client.get(
            "/profiles/memory-slice",
            params={"purpose": "quick_check", "run_id": "run-slice-2"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["included_items"]) == 1
        assert body["included_items"][0]["dimension"] == "expression_brevity"
        assert body["excluded_candidate_ids"] == []


class TestCrossAccountIsolation:
    def test_cross_account_profile_access_is_denied(
        self, client: TestClient
    ) -> None:
        alice = _register(client, "alice-profile@example.com", "correct-horse-12")
        alice_id = alice["account"]["id"]
        obs = _create_observation(client, alice_id)
        candidate = _propose_candidate(client, alice_id, obs["observation_id"])

        client.cookies.clear()
        _register(client, "bob-profile@example.com", "correct-horse-12")
        _login(client, "bob-profile@example.com", "correct-horse-12")

        response = client.get(f"/profiles/observations/{obs['observation_id']}")
        assert response.status_code == 404

        response = client.get(f"/profiles/candidates/{candidate['candidate_id']}")
        assert response.status_code == 404

        response = client.post(
            f"/profiles/candidates/{candidate['candidate_id']}/decision",
            json={"decision": "accept", "reason": "Hacked."},
        )
        assert response.status_code == 404
