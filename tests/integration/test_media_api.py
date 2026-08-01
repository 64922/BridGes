"""Integration tests for the media asset API (T030).

The seam under test: an authenticated user uploads an image/formula/table via the
API, retrieves the projection, applies a correction, and revokes the asset. The
API preserves scope isolation and integrates with the invalidation foundation.
"""

from __future__ import annotations

import base64
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.contracts.media import (
    MediaCorrectionRequest,
    MediaCorrectionType,
    MediaType,
    MediaUploadRequest,
)
from bridges.contracts.science import ClaimRequest, LicenseState


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def _register(client: TestClient) -> str:
    email = "media-user@example.com"
    password = "correct-horse-battery-staple"
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": password,
            "agreed_to_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["account"]["id"])


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class TestMediaUploadAndRetrieve:
    def test_upload_and_get_image_projection(
        self, client: TestClient
    ) -> None:
        _account_id = _register(client)
        request = MediaUploadRequest(
            filename="formula-scan.png",
            media_type=MediaType.IMAGE_PNG,
            content=base64.b64encode(_png_bytes()).decode("ascii"),
            license_state=LicenseState.USER_OWNED,
        )
        response = client.post(
            "/media/assets",
            json=request.model_dump(mode="json"),
        )
        assert response.status_code == 201
        run = response.json()
        assert run["status"] == "completed"
        asset_id = run["asset_id"]

        response = client.get(f"/media/assets/{asset_id}")
        assert response.status_code == 200
        projection = response.json()
        assert projection["source_asset"]["asset_id"] == asset_id
        assert projection["can_enter_evidence"] is True
        assert len(projection["derived_assets"]) == 1

        payload = projection["derived_assets"][0]["payload"]
        assert "regions" in payload
        assert "ocr_tokens" in payload

    def test_upload_formula_and_read_symbol_table(
        self, client: TestClient
    ) -> None:
        _account_id = _register(client)
        request = MediaUploadRequest(
            filename="mass-energy.tex",
            media_type=MediaType.APPLICATION_X_LATEX,
            content=base64.b64encode(b"E = mc^2").decode("ascii"),
            license_state=LicenseState.USER_OWNED,
        )
        response = client.post(
            "/media/assets",
            json=request.model_dump(mode="json"),
        )
        assert response.status_code == 201
        asset_id = response.json()["asset_id"]

        response = client.get(f"/media/assets/{asset_id}")
        payload = response.json()["derived_assets"][0]["payload"]
        symbols = {s["symbol"]: s for s in payload["symbol_table"]}
        assert "E" in symbols
        assert payload["accessible_text"] is not None


class TestMediaCorrectionApi:
    def test_api_correction_creates_new_derived_version(
        self, client: TestClient
    ) -> None:
        _account_id = _register(client)
        request = MediaUploadRequest(
            filename="mass-energy.tex",
            media_type=MediaType.APPLICATION_X_LATEX,
            content=base64.b64encode(b"E = mc^2").decode("ascii"),
            license_state=LicenseState.USER_OWNED,
        )
        upload_response = client.post(
            "/media/assets",
            json=request.model_dump(mode="json"),
        )
        asset_id = upload_response.json()["asset_id"]

        projection_response = client.get(f"/media/assets/{asset_id}")
        derived_id = projection_response.json()["derived_assets"][0]["derived_asset_id"]

        correction = MediaCorrectionRequest(
            derived_asset_id=derived_id,
            correction_type=MediaCorrectionType.FORMULA_LATEX,
            target_ref="latex",
            corrected_value="E = mc^2 + 0",
            reason="demonstration",
        )
        response = client.post(
            f"/media/assets/{asset_id}/derived",
            json=correction.model_dump(mode="json"),
        )
        assert response.status_code == 201
        new_derived = response.json()
        assert new_derived["derived_from_asset_id"] == derived_id
        assert new_derived["payload"]["latex"] == "E = mc^2 + 0"


class TestMediaRevocationApi:
    def test_revoke_media_asset_blocks_retrieval(
        self, client: TestClient
    ) -> None:
        _account_id = _register(client)
        request = MediaUploadRequest(
            filename="scan.png",
            media_type=MediaType.IMAGE_PNG,
            content=base64.b64encode(_png_bytes()).decode("ascii"),
            license_state=LicenseState.USER_OWNED,
        )
        upload_response = client.post(
            "/media/assets",
            json=request.model_dump(mode="json"),
        )
        asset_id = upload_response.json()["asset_id"]

        revoke_response = client.post(f"/media/assets/{asset_id}/revoke")
        assert revoke_response.status_code == 200
        assert revoke_response.json()["status"] == "revoked"

        response = client.get(f"/media/assets/{asset_id}")
        assert response.status_code == 404


class TestMediaClaimGraphApi:
    def test_generate_claim_graph_from_media(
        self, client: TestClient
    ) -> None:
        _account_id = _register(client)
        request = MediaUploadRequest(
            filename="mass-energy.tex",
            media_type=MediaType.APPLICATION_X_LATEX,
            content=base64.b64encode(b"E = mc^2").decode("ascii"),
            license_state=LicenseState.USER_OWNED,
        )
        upload_response = client.post(
            "/media/assets",
            json=request.model_dump(mode="json"),
        )
        asset_id = upload_response.json()["asset_id"]

        # First upload a text source so the claim graph has evidence to bind.
        text_request = MediaUploadRequest(
            filename="note.txt",
            media_type=MediaType.TEXT_PLAIN,
            content=base64.b64encode(b"Energy and mass are equivalent.").decode("ascii"),
            license_state=LicenseState.USER_OWNED,
        )
        client.post(
            "/media/assets",
            json=text_request.model_dump(mode="json"),
        )

        claim_request = ClaimRequest(query="energy mass equivalence", top_k=3)
        response = client.post(
            f"/media/assets/{asset_id}/claim-graph",
            json=claim_request.model_dump(mode="json"),
        )
        assert response.status_code == 201
        result = response.json()
        assert "graph" in result
        assert result["graph"]["account_id"] == _account_id
