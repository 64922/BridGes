"""Integration tests for the audio/video media asset API (T031).

The seam under test: an authenticated user uploads audio or video via the API,
retrieves the timed projection, corrects a low-confidence scientific term, and
sees a new manifest version; cross-account access is rejected.
"""

from __future__ import annotations

import base64
from typing import cast

import pytest
from fastapi.testclient import TestClient

from science_companion.api.main import create_app
from science_companion.contracts.media import (
    MediaCorrectionRequest,
    MediaCorrectionType,
    MediaUploadRequest,
)
from science_companion.contracts.science import LicenseState, MediaType


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def _register(client: TestClient) -> str:
    email = "av-user@example.com"
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


def _mp3_bytes() -> bytes:
    return b"ID3\x04\x00" + b"\x00" * 28


def _mp4_bytes() -> bytes:
    return b"\x00\x00\x00\x20ftypisom" + b"\x00" * 12


class TestAudioVideoUploadAndRetrieve:
    def test_upload_and_get_audio_projection(
        self, client: TestClient
    ) -> None:
        _account_id = _register(client)
        request = MediaUploadRequest(
            filename="biology-photosynthesis.mp3",
            media_type=MediaType.AUDIO_MPEG,
            content=base64.b64encode(_mp3_bytes()).decode("ascii"),
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
        assert len(payload["transcript_segments"]) >= 1
        assert len(payload["speaker_segments"]) == 1
        assert len(payload["captions"]) == len(payload["transcript_segments"])
        assert payload["keyframes"] == []

    def test_upload_and_get_video_projection(
        self, client: TestClient
    ) -> None:
        _account_id = _register(client)
        request = MediaUploadRequest(
            filename="slides-diagram.mp4",
            media_type=MediaType.VIDEO_MP4,
            content=base64.b64encode(_mp4_bytes()).decode("ascii"),
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
        assert len(payload["keyframes"]) >= 2


class TestAudioVideoCorrectionApi:
    def test_api_correction_creates_new_derived_version(
        self, client: TestClient
    ) -> None:
        _account_id = _register(client)
        request = MediaUploadRequest(
            filename="biology-photosynthesis.mp3",
            media_type=MediaType.AUDIO_MPEG,
            content=base64.b64encode(_mp3_bytes()).decode("ascii"),
            license_state=LicenseState.USER_OWNED,
        )
        upload_response = client.post(
            "/media/assets",
            json=request.model_dump(mode="json"),
        )
        asset_id = upload_response.json()["asset_id"]

        projection_response = client.get(f"/media/assets/{asset_id}")
        projection = projection_response.json()
        derived_id = projection["derived_assets"][0]["derived_asset_id"]
        payload = projection["derived_assets"][0]["payload"]
        low_confidence = next(
            seg for seg in payload["transcript_segments"] if seg["low_confidence"]
        )

        correction = MediaCorrectionRequest(
            derived_asset_id=derived_id,
            correction_type=MediaCorrectionType.TRANSCRIPT_TERM,
            target_ref=low_confidence["segment_id"],
            corrected_value="photosynthesis",
            reason="fix ASR error",
        )
        response = client.post(
            f"/media/assets/{asset_id}/derived",
            json=correction.model_dump(mode="json"),
        )
        assert response.status_code == 201
        new_derived = response.json()
        assert new_derived["derived_from_asset_id"] == derived_id

        updated_payload = new_derived["payload"]
        corrected_segment = next(
            seg
            for seg in updated_payload["transcript_segments"]
            if seg["segment_id"] == low_confidence["segment_id"]
        )
        assert corrected_segment["text"] == "photosynthesis"
        assert corrected_segment["low_confidence"] is False

        # Manifest version must have bumped.
        updated_projection = client.get(f"/media/assets/{asset_id}").json()
        assert updated_projection["manifest"]["version"] == projection["manifest"]["version"] + 1


class TestAudioVideoScopeApi:
    def test_cross_account_audio_asset_is_rejected(
        self, client: TestClient
    ) -> None:
        _register(client)
        request = MediaUploadRequest(
            filename="private.mp3",
            media_type=MediaType.AUDIO_MPEG,
            content=base64.b64encode(_mp3_bytes()).decode("ascii"),
            license_state=LicenseState.USER_OWNED,
        )
        upload_response = client.post(
            "/media/assets",
            json=request.model_dump(mode="json"),
        )
        asset_id = upload_response.json()["asset_id"]

        # A fresh client simulates another browser/session.
        other_client = TestClient(create_app())
        response = other_client.get(f"/media/assets/{asset_id}")
        assert response.status_code == 401
