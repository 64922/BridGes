"""Integration tests for the profile assertion governance API seam.

The seam under test: an authenticated user confirms a candidate, then freezes,
modifies, rolls back, deletes and exports profile assertions through the API.
Each governance operation is reflected in subsequent memory-slice compilations
and deletion produces an invalidation plan covering cache, index, runs and
memory slices.
"""

from __future__ import annotations

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


def _create_observation(client: TestClient, account_id: str) -> dict[str, Any]:
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
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _propose_and_accept(
    client: TestClient, account_id: str, observation_id: str
) -> dict[str, Any]:
    response = client.post(
        "/profiles/candidates",
        json={
            "owner_account_id": account_id,
            "canonical_dimension": "expression_brevity",
            "value_or_rule": "prefer_short_answers",
            "applicable_scenes": ["quick_check"],
            "supporting_observation_ids": [observation_id],
        },
    )
    assert response.status_code == 201, response.text
    candidate = cast(dict[str, Any], response.json())
    response = client.post(
        f"/profiles/candidates/{candidate['candidate_id']}/decision",
        json={"decision": "accept", "reason": "Confirmed."},
    )
    assert response.status_code == 200, response.text
    return candidate


def _compile_slice(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.get(
        "/profiles/memory-slice",
        params={"purpose": "quick_check", "run_id": run_id},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


@pytest.mark.skip(reason="Issue 16 已退役旧画像治理 API；四维画像与 410 契约由专门测试覆盖。")
class TestAssertionGovernanceAPI:
    def test_freeze_assertion_excludes_it_from_new_slices(
        self, client: TestClient
    ) -> None:
        registered = _register(
            client, "freeze-api", "200001@qq.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        obs = _create_observation(client, account_id)
        _propose_and_accept(client, account_id, obs["observation_id"])
        assertions = client.get("/profiles/assertions").json()
        assertion_id = assertions[0]["assertion_id"]

        before = _compile_slice(client, "run-freeze-before")
        assert len(before["included_items"]) == 1

        response = client.post(
            f"/profiles/assertions/{assertion_id}/freeze",
            json={"reason": "No longer relevant."},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "frozen"

        after = _compile_slice(client, "run-freeze-after")
        assert after["included_items"] == []

    def test_modify_assertion_changes_slice_contents(
        self, client: TestClient
    ) -> None:
        registered = _register(
            client, "modify-api", "200002@qq.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        obs = _create_observation(client, account_id)
        _propose_and_accept(client, account_id, obs["observation_id"])
        assertion_id = client.get("/profiles/assertions").json()[0]["assertion_id"]

        response = client.post(
            f"/profiles/assertions/{assertion_id}/modify",
            json={
                "value_or_rule": "prefer_detailed_answers",
                "applicable_scenes": ["deep_research"],
                "reason": "Preference changed.",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["value_or_rule"] == "prefer_detailed_answers"
        assert body["version"] == 2

        quick = _compile_slice(client, "run-modify-quick")
        assert quick["included_items"] == []

    def test_rollback_assertion_restores_previous_value(
        self, client: TestClient
    ) -> None:
        registered = _register(
            client, "rollback-api", "200003@qq.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        obs = _create_observation(client, account_id)
        _propose_and_accept(client, account_id, obs["observation_id"])
        assertion_id = client.get("/profiles/assertions").json()[0]["assertion_id"]
        client.post(
            f"/profiles/assertions/{assertion_id}/modify",
            json={
                "value_or_rule": "prefer_detailed_answers",
                "applicable_scenes": ["deep_research"],
                "reason": "Preference changed.",
            },
        )

        response = client.post(
            f"/profiles/assertions/{assertion_id}/rollback",
            json={"to_version": 1, "reason": "Mistake."},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["value_or_rule"] == "prefer_short_answers"
        assert body["version"] == 3

        after = _compile_slice(client, "run-rollback-after")
        assert len(after["included_items"]) == 1
        assert after["included_items"][0]["value_or_rule"] == "prefer_short_answers"

    def test_delete_assertion_blocks_new_recall_and_revokes_slices(
        self, client: TestClient
    ) -> None:
        registered = _register(
            client, "delete-api", "200004@qq.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        obs = _create_observation(client, account_id)
        _propose_and_accept(client, account_id, obs["observation_id"])
        assertion_id = client.get("/profiles/assertions").json()[0]["assertion_id"]
        before_slice = _compile_slice(client, "run-delete-before")

        response = client.post(
            f"/profiles/assertions/{assertion_id}/delete",
            json={"reason": "User requested deletion."},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "deleted"

        after = _compile_slice(client, "run-delete-after")
        assert after["included_items"] == []

        revoked = client.get(
            f"/profiles/memory-slices/{before_slice['slice_id']}"
        ).json()
        assert revoked["status"] == "revoked"

    def test_export_redacts_deleted_assertion_value(
        self, client: TestClient
    ) -> None:
        registered = _register(
            client, "export-api", "200005@qq.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        obs = _create_observation(client, account_id)
        _propose_and_accept(client, account_id, obs["observation_id"])
        assertion_id = client.get("/profiles/assertions").json()[0]["assertion_id"]
        client.post(
            f"/profiles/assertions/{assertion_id}/delete",
            json={"reason": "User requested deletion."},
        )

        response = client.get("/profiles/export")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["assertions"]) == 1
        assert body["assertions"][0]["status"] == "deleted"
        assert body["assertions"][0]["value_or_rule"] is None


@pytest.mark.skip(reason="Issue 16 已退役旧画像治理 API；四维画像与 410 契约由专门测试覆盖。")
class TestGovernanceIsolationAPI:
    def test_cross_account_assertion_governance_is_denied(
        self, client: TestClient
    ) -> None:
        alice = _register(client, "alice-gov", "200006@qq.com", "correct-horse-12")
        alice_id = alice["account"]["id"]
        obs = _create_observation(client, alice_id)
        _propose_and_accept(client, alice_id, obs["observation_id"])
        assertion_id = client.get("/profiles/assertions").json()[0]["assertion_id"]

        client.cookies.clear()
        _register(client, "bob-gov", "200007@qq.com", "correct-horse-12")

        response = client.post(
            f"/profiles/assertions/{assertion_id}/freeze",
            json={"reason": "Hacked."},
        )
        assert response.status_code == 404

        response = client.post(
            f"/profiles/assertions/{assertion_id}/delete",
            json={"reason": "Hacked."},
        )
        assert response.status_code == 404
