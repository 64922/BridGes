"""Integration tests for T034 accessibility alternative API routes.

The seam under test: an authenticated user creates a storyboard, generates a
complete accessibility bundle for it, validates the bundle and controls
playback (pause, seek, reduced motion) through the API. Bundles are scoped
to the owning account.
"""

from __future__ import annotations

from typing import cast

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.contracts.media import (
    StoryboardGenerationRequest,
    StoryboardNarration,
    StoryboardScene,
)


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def _register(client: TestClient, username: str = "a11y-user") -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "qq_email": "110001@qq.com",
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["account"]["id"])


def _create_storyboard(client: TestClient) -> str:
    request = StoryboardGenerationRequest(
        title="自由落体运动",
        media_type="animation",
        scenes=[
            StoryboardScene(
                scene_id="scene-001",
                scene_number=1,
                scene_spec_id="spec-001",
                timing_seconds=6.0,
                narration=StoryboardNarration(
                    text="在地球表面附近，重力加速度约为 9.8 m/s²。",
                    claim_ids=["claim-gravity"],
                ),
            ),
            StoryboardScene(
                scene_id="scene-002",
                scene_number=2,
                scene_spec_id="spec-002",
                timing_seconds=4.0,
                narration=StoryboardNarration(
                    text="下落距离与时间的平方成正比。",
                    claim_ids=["claim-distance"],
                ),
            ),
        ],
    )
    response = client.post(
        "/media/storyboards", json=request.model_dump(mode="json")
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["storyboard"]["storyboard_id"])


def _generate_bundle(client: TestClient, storyboard_id: str) -> dict[str, object]:
    response = client.post(
        "/media/accessibility/bundles",
        json={"target_kind": "storyboard", "target_id": storyboard_id},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


class TestBundleRoutes:
    def test_generate_and_get_bundle(self, client: TestClient) -> None:
        _register(client)
        storyboard_id = _create_storyboard(client)
        bundle = _generate_bundle(client, storyboard_id)

        assert bundle["target_kind"] == "storyboard"
        assert bundle["alt_text"]
        transcript = cast(dict[str, object], bundle["transcript"])
        assert transcript["segments"]
        narration = cast(dict[str, object], bundle["narration"])
        assert narration["status"] == "synthesized"
        assert set(cast(list[str], bundle["claim_ids"])) == {
            "claim-gravity",
            "claim-distance",
        }

        response = client.get(f"/media/accessibility/bundles/{bundle['bundle_id']}")
        assert response.status_code == 200
        assert response.json()["bundle_id"] == bundle["bundle_id"]

    def test_bundle_for_unknown_target_returns_404(
        self, client: TestClient
    ) -> None:
        _register(client)
        response = client.post(
            "/media/accessibility/bundles",
            json={"target_kind": "storyboard", "target_id": "missing"},
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "accessibility_target_not_found"

    def test_validate_bundle(self, client: TestClient) -> None:
        _register(client)
        storyboard_id = _create_storyboard(client)
        bundle = _generate_bundle(client, storyboard_id)

        response = client.get(
            f"/media/accessibility/bundles/{bundle['bundle_id']}/validate"
        )
        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["claims_consistent"] is True
        assert result["keyboard_operable"] is True
        assert result["playback_controllable"] is True


class TestPlaybackRoutes:
    def test_playback_pause_seek_and_reduced_motion(
        self, client: TestClient
    ) -> None:
        _register(client)
        storyboard_id = _create_storyboard(client)
        bundle = _generate_bundle(client, storyboard_id)
        playback_url = (
            f"/media/accessibility/bundles/{bundle['bundle_id']}/playback"
        )

        response = client.post(playback_url, json={"action": "start"})
        assert response.status_code == 200
        state = response.json()
        assert state["paused"] is False
        assert state["duration_seconds"] == 10.0

        response = client.post(playback_url, json={"action": "pause"})
        assert response.json()["paused"] is True

        response = client.post(
            playback_url, json={"action": "seek", "position_seconds": 7.0}
        )
        state = response.json()
        assert state["position_seconds"] == 7.0
        assert state["active_caption_text"]

        response = client.post(
            playback_url, json={"action": "set_reduced_motion", "enabled": True}
        )
        state = response.json()
        assert state["reduced_motion_enabled"] is True
        assert state["active_frame_description"]

    def test_playback_on_unknown_bundle_returns_404(
        self, client: TestClient
    ) -> None:
        _register(client)
        response = client.post(
            "/media/accessibility/bundles/missing/playback",
            json={"action": "start"},
        )
        assert response.status_code == 404
