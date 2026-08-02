"""Production runtime contract tests.

The seam under test: production artifacts (Dockerfiles, compose files) do not
require or detect Conda, and they use the same unified configuration schema and
CLI commands.
"""

from pathlib import Path

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
    assert "apps/api/Dockerfile" in text, "API service must build from the API Dockerfile"


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
    assert text.count("stop_signal: SIGTERM") >= 4, (
        "All services must declare SIGTERM for graceful shutdown"
    )


def test_compose_includes_background_executor_and_scheduler() -> None:
    """容器路径必须与源码路径一样启动后台执行器与提醒调度器（ADR-0013）。"""
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert 'command: ["BridGes", "worker"]' in text, (
        "Compose must run the background executor service"
    )
    assert 'command: ["BridGes", "scheduler"]' in text, (
        "Compose must run the reminder scheduler service"
    )


def test_compose_background_services_share_data_volume() -> None:
    """worker/scheduler 必须与 API 共享同一数据卷与数据库地址（AC3 卷语义一致）。"""
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert text.count("bridges-data:/var/lib/bridges") >= 3, (
        "api, worker and scheduler must mount the same bridges-data volume"
    )
    assert text.count("sqlite:////var/lib/bridges/bridges.db") >= 3, (
        "api, worker and scheduler must use the same database URL"
    )
