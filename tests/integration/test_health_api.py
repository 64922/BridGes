"""Integration tests for the unified health seam.

The seam under test: a user (or the Web UI) can read the same health projection
from the API, and the projection carries live/ready/degraded semantics.
"""

import pytest
from fastapi.testclient import TestClient

from science_companion import __version__
from science_companion.api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health_summary_returns_unified_projection(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()

    assert body["service"] == "api"
    assert body["version"] == __version__
    assert body["live"] == "pass"
    assert body["ready"] == "pass"
    assert body["degraded"] == "pass"
    assert isinstance(body["dependencies"], list)


def test_health_ready_includes_required_dependencies(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()

    assert body["live"] == "pass"
    assert body["ready"] == "pass"
    required = [d for d in body["dependencies"] if d["required"]]
    assert len(required) > 0
    for dep in required:
        assert dep["status"] == "pass"


def test_health_live_only_reports_liveness(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    body = response.json()

    assert body["live"] == "pass"
    assert body["ready"] == "unknown"
    assert body["degraded"] == "unknown"
    assert body["dependencies"] == []


def test_health_degraded_reports_optional_dependencies(client: TestClient) -> None:
    response = client.get("/health/degraded")
    assert response.status_code == 200
    body = response.json()

    assert body["live"] == "pass"
    assert body["degraded"] == "pass"
