"""Production runtime contract tests.

The seam under test: production artifacts (Dockerfiles, compose files) do not
require or detect Conda, and they use the same unified configuration schema and
CLI commands.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

PRODUCTION_ARTIFACTS = [
    REPO_ROOT / "apps" / "api" / "Dockerfile",
    REPO_ROOT / "apps" / "web" / "Dockerfile",
    REPO_ROOT / "infra" / "compose" / "docker-compose.yml",
]

COMPOSE_FILE = REPO_ROOT / "infra" / "compose" / "docker-compose.yml"


def test_dockerfiles_do_not_reference_conda() -> None:
    for path in PRODUCTION_ARTIFACTS:
        text = path.read_text(encoding="utf-8").lower()
        assert "conda" not in text, f"{path} must not reference Conda"
        assert "environment.yml" not in text, f"{path} must not reference environment.yml"


def test_compose_uses_unified_config_env_prefix() -> None:
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "BRIDGES_" in text, "Compose must inject BridGes unified config variables"


def test_compose_api_service_uses_bridges_api() -> None:
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "apps/api/Dockerfile" in text, "API service must build from the production API Dockerfile"


def test_compose_web_service_uses_standalone_node_runtime() -> None:
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "apps/web/Dockerfile" in compose_text, (
        "Web service must build from the production Web Dockerfile"
    )
    dockerfile = (REPO_ROOT / "apps" / "web" / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["node", "server.js"]' in dockerfile


def test_compose_api_healthcheck_uses_ready_probe() -> None:
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "/health/ready" in text, "API healthcheck must probe the ready endpoint"


def test_compose_services_have_graceful_stop_signal() -> None:
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert text.count("stop_signal: SIGTERM") >= 2, (
        "Both api and web services must declare SIGTERM for graceful shutdown"
    )
