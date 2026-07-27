"""T015 integration tests: locatable Claim--Evidence--Citation API.

The seam under test: an authenticated user submits a scientific question through
the API and receives a claim graph with source-versioned evidence and precise
citations. The user can open the citation inspector, see verification status,
observe the effect of source versioning, and run the publish gate.
"""

from __future__ import annotations

import base64
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from science_companion.api.main import create_app
from science_companion.contracts.science import (
    CitationVerificationStatus,
    ClaimImportance,
    ClaimTrustStatus,
    EvidenceRelation,
    LicenseState,
    MediaType,
    PublishGateCheck,
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


class TestClaimGraphAPI:
    def test_generate_project_claim_graph_returns_locatable_claims(
        self, client: TestClient
    ) -> None:
        _register(client, "alice-claims@example.com", "correct-horse-12")
        project_id = _create_project(client, "Claim Project")
        _upload_text(
            client,
            project_id,
            "# Results\n\nMitochondria generate ATP through cellular respiration.",
        )

        response = client.post(
            f"/science/projects/{project_id}/claim-graphs",
            json={"query": "mitochondria ATP respiration", "top_k": 3},
        )
        assert response.status_code == 201, response.text
        result = response.json()

        assert result["graph"]["query"] == "mitochondria ATP respiration"
        assert result["graph"]["project_id"] == project_id
        assert len(result["graph"]["claims"]) > 0
        assert result["publish_gate"]["passed"] is True

        key_claim = next(
            c for c in result["graph"]["claims"] if c["importance"] == ClaimImportance.KEY.value
        )
        assert key_claim["evidence_ids"]
        assert key_claim["citation_ids"]

        first_evidence_id = key_claim["evidence_ids"][0]
        evidence = next(
            e for e in result["graph"]["evidence"] if e["evidence_id"] == first_evidence_id
        )
        assert evidence["relation"] == EvidenceRelation.SUPPORTS.value
        assert evidence["document_id"]
        assert evidence["chunk_ids"]

        first_citation_id = key_claim["citation_ids"][0]
        citation = next(
            c for c in result["graph"]["citations"] if c["citation_id"] == first_citation_id
        )
        assert citation["evidence_id"] == evidence["evidence_id"]
        assert citation["locator"]
        assert citation["source_version_label"] == "v1"
        assert citation["verification_status"] == CitationVerificationStatus.VERIFIED.value

    def test_get_claim_graph_by_id(self, client: TestClient) -> None:
        _register(client, "bob-claims@example.com", "correct-horse-12")
        project_id = _create_project(client, "Get Claim Project")
        _upload_text(client, project_id, "Claim content.")

        create_response = client.post(
            f"/science/projects/{project_id}/claim-graphs",
            json={"query": "claim content", "top_k": 3},
        )
        graph_id = create_response.json()["graph"]["graph_id"]

        response = client.get(f"/science/claim-graphs/{graph_id}")
        assert response.status_code == 200
        assert response.json()["graph_id"] == graph_id

    def test_cross_account_claim_graph_access_is_rejected(self, client: TestClient) -> None:
        alice_client = TestClient(client.app)
        bob_client = TestClient(client.app)

        _register(alice_client, "alice-private-claims@example.com", "correct-horse-12")
        alice_project = _create_project(alice_client, "Alice Claim Project")
        _upload_text(alice_client, alice_project, "Alice private claim.")
        graph_id = alice_client.post(
            f"/science/projects/{alice_project}/claim-graphs",
            json={"query": "Alice private claim", "top_k": 3},
        ).json()["graph"]["graph_id"]

        _register(bob_client, "bob-no-claims@example.com", "correct-horse-12")
        response = bob_client.get(f"/science/claim-graphs/{graph_id}")
        assert response.status_code == 404

    def test_publish_gate_api_blocks_after_source_version_change(
        self, client: TestClient
    ) -> None:
        _register(client, "version-gate@example.com", "correct-horse-12")
        project_id = _create_project(client, "Version Gate Project")
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

        gate_response = client.get(f"/science/claim-graphs/{graph_id}/publish-gate")
        assert gate_response.status_code == 200
        gate = gate_response.json()
        assert gate["passed"] is False
        assert PublishGateCheck.SOURCE_CURRENT_VERSION.value in gate["failed_checks"]

    def test_empty_retrieval_marks_graph_metadata_only(self, client: TestClient) -> None:
        _register(client, "empty-claims@example.com", "correct-horse-12")
        project_id = _create_project(client, "Empty Claim Project")
        _upload_text(client, project_id, "Neuroscience content.")

        response = client.post(
            f"/science/projects/{project_id}/claim-graphs",
            json={"query": "xyznonexistentterm12345", "top_k": 3},
        )
        assert response.status_code == 201
        result = response.json()
        assert result["graph"]["status"] == ClaimTrustStatus.METADATA_ONLY.value
        assert result["publish_gate"]["passed"] is False
