"""Integration tests for the unified health seam.

The seam under test: a user (or the Web UI) can read the same health projection
from the API, and the projection carries live/ready/degraded semantics.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from science_companion import __version__
from science_companion.api.main import create_app
from science_companion.config import get_settings


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


def test_health_ready_reports_configuration_failure(tmp_path: Path) -> None:
    """Configuration failure (e.g. missing secret file) returns fail, not 500."""
    env_key = "SCIENCE_COMPANION_SECRET_KEY_FILE"
    missing = tmp_path / "nonexistent-secret.key"
    os.environ[env_key] = str(missing)
    get_settings.cache_clear()
    try:
        failing_client = TestClient(create_app())
        response = failing_client.get("/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] == "fail"
        config_dep = [d for d in body["dependencies"] if d["name"] == "configuration"]
        assert len(config_dep) == 1
        assert config_dep[0]["status"] == "fail"
    finally:
        os.environ.pop(env_key, None)
        get_settings.cache_clear()


def test_health_ready_reports_missing_production_persistence() -> None:
    env_key = "SCIENCE_COMPANION_ENVIRONMENT"
    database_key = "SCIENCE_COMPANION_DATABASE_URL"
    previous_environment = os.environ.get(env_key)
    previous_database = os.environ.get(database_key)
    os.environ[env_key] = "production"
    os.environ.pop(database_key, None)
    get_settings.cache_clear()
    try:
        response = TestClient(create_app()).get("/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] == "fail"
        persistence_dep = [
            dependency
            for dependency in body["dependencies"]
            if dependency["name"] == "persistence"
        ]
        assert len(persistence_dep) == 1
        assert persistence_dep[0]["status"] == "fail"
    finally:
        if previous_environment is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = previous_environment
        if previous_database is None:
            os.environ.pop(database_key, None)
        else:
            os.environ[database_key] = previous_database
        get_settings.cache_clear()
