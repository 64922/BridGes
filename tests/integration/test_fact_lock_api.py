"""T016 integration tests: fact locks, honest degradation and scientific gate.

The seam under test: an authenticated user generates a claim graph and then
reads its fact lock set, validation report and scientific quality gate through
the API. The API must expose deterministic evidence-state-derived statuses and
wording ceilings.
"""

from __future__ import annotations

import base64
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.contracts.science import (
    ClaimTrustStatus,
    LicenseState,
    MediaType,
    ScientificQualityGateCheck,
    WordingStrength,
)


def _register(client: TestClient, email: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password, "agreed_to_terms": True},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_project(client: TestClient, name: str) -> str:
    response = client.post("/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return cast(str, response.json()["id"])


def _upload_text(
    client: TestClient,
    project_id: str,
    text: str,
    filename: str = "paper.txt",
) -> dict[str, Any]:
    response = client.post(
        f"/science/projects/{project_id}/sources",
        json={
            "filename": filename,
            "media_type": MediaType.TEXT_PLAIN.value,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "license_state": LicenseState.USER_OWNED.value,
            "title": filename,
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class TestFactLockAPI:
    def test_fact_locks_endpoint_returns_locks(self, client: TestClient) -> None:
        _register(client, "alice-locks@example.com", "correct-horse-12")
        project_id = _create_project(client, "Lock Project")
        _upload_text(
            client,
            project_id,
            "Mitochondria generate ATP through cellular respiration.",
        )

        create_response = client.post(
            f"/science/projects/{project_id}/claim-graphs",
            json={"query": "mitochondria ATP respiration", "top_k": 3},
        )
        assert create_response.status_code == 201, create_response.text
        graph_id = create_response.json()["graph"]["graph_id"]

        response = client.get(f"/science/claim-graphs/{graph_id}/fact-locks")
        assert response.status_code == 200, response.text
        lock_set = response.json()
        assert lock_set["graph_id"] == graph_id
        assert len(lock_set["locks"]) > 0
        lock_types = {lock["lock_type"] for lock in lock_set["locks"]}
        assert "identifier" in lock_types
        assert "strength" in lock_types

    def test_validate_endpoint_returns_verified_for_supported_evidence(
        self, client: TestClient
    ) -> None:
        _register(client, "alice-verify@example.com", "correct-horse-12")
        project_id = _create_project(client, "Verify Project")
        _upload_text(
            client,
            project_id,
            "Cellular respiration produces ATP in mitochondria.",
        )

        create_response = client.post(
            f"/science/projects/{project_id}/claim-graphs",
            json={"query": "ATP mitochondria", "top_k": 3},
        )
        graph_id = create_response.json()["graph"]["graph_id"]

        response = client.get(f"/science/claim-graphs/{graph_id}/validate")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["status"] == ClaimTrustStatus.VERIFIED.value
        assert report["wording_strength_ceiling"] == WordingStrength.HIGH.value
        assert report["human_gate_required"] is False
        assert report["scientific_gate"]["passed"] is True

    def test_validate_endpoint_returns_metadata_only_for_empty_retrieval(
        self, client: TestClient
    ) -> None:
        _register(client, "alice-empty@example.com", "correct-horse-12")
        project_id = _create_project(client, "Empty Verify Project")
        _upload_text(client, project_id, "Neuroscience content.")

        create_response = client.post(
            f"/science/projects/{project_id}/claim-graphs",
            json={"query": "xyznonexistentterm12345", "top_k": 3},
        )
        graph_id = create_response.json()["graph"]["graph_id"]

        response = client.get(f"/science/claim-graphs/{graph_id}/validate")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["status"] == ClaimTrustStatus.METADATA_ONLY.value
        assert report["wording_strength_ceiling"] == WordingStrength.METADATA_ONLY.value
        assert report["scientific_gate"]["passed"] is False
        assert (
            ScientificQualityGateCheck.KEY_CLAIM_COVERAGE.value
            in report["scientific_gate"]["failed_checks"]
        )

    def test_scientific_quality_gate_endpoint_blocks_after_source_version_change(
        self, client: TestClient
    ) -> None:
        _register(client, "alice-scigate@example.com", "correct-horse-12")
        project_id = _create_project(client, "Scientific Gate Project")
        run = _upload_text(client, project_id, "Original claim.")
        source_id = run["source_id"]

        create_response = client.post(
            f"/science/projects/{project_id}/claim-graphs",
            json={"query": "Original claim", "top_k": 3},
        )
        graph_id = create_response.json()["graph"]["graph_id"]

        projection = client.get(f"/science/sources/{source_id}").json()
        document_id = projection["current_document"]["document_id"]
        chunk_id = projection["current_chunks"][0]["chunk_id"]

        client.post(
            f"/science/sources/{source_id}/versions",
            json={
                "base_document_id": document_id,
                "chunk_corrections": [
                    {
                        "chunk_id": chunk_id,
                        "corrected_text": "Corrected claim.",
                        "reason": "typo",
                    }
                ],
                "reason": "typo",
            },
        )

        response = client.get(
            f"/science/claim-graphs/{graph_id}/scientific-quality-gate"
        )
        assert response.status_code == 200, response.text
        gate = response.json()
        assert gate["passed"] is False
        assert (
            ScientificQualityGateCheck.CITATION_LOCATABLE.value
            in gate["failed_checks"]
            or ScientificQualityGateCheck.SOURCE_ACTIVE.value in gate["failed_checks"]
        )

    def test_cross_account_validation_access_is_rejected(
        self, client: TestClient
    ) -> None:
        alice_client = TestClient(client.app)
        bob_client = TestClient(client.app)

        _register(alice_client, "alice-private-validate@example.com", "correct-horse-12")
        alice_project = _create_project(alice_client, "Alice Validate Project")
        _upload_text(alice_client, alice_project, "Alice private claim.")
        graph_id = alice_client.post(
            f"/science/projects/{alice_project}/claim-graphs",
            json={"query": "Alice private claim", "top_k": 3},
        ).json()["graph"]["graph_id"]

        _register(bob_client, "bob-no-validate@example.com", "correct-horse-12")
        response = bob_client.get(f"/science/claim-graphs/{graph_id}/validate")
        assert response.status_code == 404

    def test_claim_graph_result_includes_validation_report(
        self, client: TestClient
    ) -> None:
        _register(client, "alice-report@example.com", "correct-horse-12")
        project_id = _create_project(client, "Report Project")
        _upload_text(client, project_id, "Photosynthesis converts light energy.")

        response = client.post(
            f"/science/projects/{project_id}/claim-graphs",
            json={"query": "photosynthesis light energy", "top_k": 3},
        )
        assert response.status_code == 201, response.text
        result = response.json()
        assert "validation_report" in result
        assert result["validation_report"]["graph_id"] == result["graph"]["graph_id"]
