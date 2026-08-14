"""Integration tests for the unified health seam.

The seam under test: a user (or the Web UI) can read the same health projection
from the API, and the projection carries live/ready/degraded semantics.

Issue 04/01：readiness 独立暴露 Tavily 健康快照（web_search 依赖 + 脱敏
extensions）；搜索故障只进入降级，不拖垮基础存活与就绪。
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridges import __version__
from bridges.api.main import create_app
from bridges.closeout.fixtures import CloseoutWebSearchClient
from bridges.config import get_settings
from bridges.web_search.contracts import WebSearchHealth, WebSearchHealthStatus
from bridges.web_search.service import WebSearchService
from bridges.web_search.tavily import (
    TAVILY_SEARCH_PROVIDER,
    TAVILY_SEARCH_PROVIDER_VERSION,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _failing_web_search_service() -> WebSearchService:
    class _FailingHealthClient(CloseoutWebSearchClient):
        def health_check(self) -> WebSearchHealth:
            return WebSearchHealth(
                provider=TAVILY_SEARCH_PROVIDER,
                provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
                status=WebSearchHealthStatus.CONNECT_ERROR,
                checked_at=datetime.now(UTC),
                error_code="web_search_connect",
            )

    return WebSearchService(
        client=_FailingHealthClient(),
        health_auto_refresh=False,
    )


def _replace_web_search(app: object, service: WebSearchService) -> None:
    app.state.web_search_service = service
    service.refresh_health()


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
    """Configuration failure (e.g. missing secret file) returns 503 fail, not 200.

    Issue 06：readiness 未就绪统一返回 503（响应体仍是同一份 projection），
    使 Playwright 等只认状态码的探针不会在依赖未就绪时开始测试。
    """
    env_key = "BRIDGES_SECRET_KEY_FILE"
    missing = tmp_path / "nonexistent-secret.key"
    os.environ[env_key] = str(missing)
    get_settings.cache_clear()
    try:
        failing_client = TestClient(create_app())
        response = failing_client.get("/health/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["ready"] == "fail"
        config_dep = [d for d in body["dependencies"] if d["name"] == "configuration"]
        assert len(config_dep) == 1
        assert config_dep[0]["status"] == "fail"
    finally:
        os.environ.pop(env_key, None)
        get_settings.cache_clear()


def test_health_ready_fails_without_global_key_in_development() -> None:
    """development 环境缺少全局百炼凭据：就绪检查 FAIL（GQ-01 纵深防御）。

    即使绕过 ``BridGes start`` 直接启动 API，也不会出现
    "ready=pass、模型不可用" 的半启动实例；消息不含任何 Key 正文。
    """
    env_key = "BRIDGES_ENVIRONMENT"
    previous = os.environ.get(env_key)
    os.environ[env_key] = "development"
    # 显式清空全局凭据（不依赖 conftest 覆盖），保证测试自含：任何环境
    # 下缺失 Key 的状态都成立。
    qwen_key_env = "BRIDGES_QWEN_API_KEY"
    qwen_key_file_env = "BRIDGES_QWEN_API_KEY_FILE"
    previous_key = os.environ.get(qwen_key_env)
    previous_key_file = os.environ.get(qwen_key_file_env)
    os.environ.pop(qwen_key_env, None)
    os.environ.pop(qwen_key_file_env, None)
    get_settings.cache_clear()
    try:
        response = TestClient(create_app()).get("/health/ready")
        # Issue 06：readiness 未就绪返回 503（而非 200）。
        assert response.status_code == 503
        body = response.json()
        assert body["ready"] == "fail"
        qwen_dep = [
            d for d in body["dependencies"] if d["name"] == "qwen_global_key"
        ]
        assert len(qwen_dep) == 1
        assert qwen_dep[0]["status"] == "fail"
        assert qwen_dep[0]["required"] is True
        assert "BRIDGES_QWEN_API_KEY" in qwen_dep[0]["message"]
    finally:
        if previous is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = previous
        if previous_key is None:
            os.environ.pop(qwen_key_env, None)
        else:
            os.environ[qwen_key_env] = previous_key
        if previous_key_file is None:
            os.environ.pop(qwen_key_file_env, None)
        else:
            os.environ[qwen_key_file_env] = previous_key_file
        get_settings.cache_clear()


def test_health_ready_reports_missing_production_persistence() -> None:
    env_key = "BRIDGES_ENVIRONMENT"
    database_key = "BRIDGES_DATABASE_URL"
    previous_environment = os.environ.get(env_key)
    previous_database = os.environ.get(database_key)
    os.environ[env_key] = "production"
    os.environ.pop(database_key, None)
    get_settings.cache_clear()
    try:
        response = TestClient(create_app()).get("/health/ready")
        # Issue 06：readiness 未就绪返回 503（而非 200）。
        assert response.status_code == 503
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


def test_health_readiness_exposes_web_search_snapshot_separately() -> None:
    """Issue 04/01：readiness 独立暴露 web_search 依赖，不把数据库健康等同搜索。"""
    app = create_app()
    _replace_web_search(app, WebSearchService(
        client=CloseoutWebSearchClient(),
        health_auto_refresh=False,
    ))
    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    web_search = [d for d in body["dependencies"] if d["name"] == "web_search"]
    assert len(web_search) == 1
    assert web_search[0]["status"] == "pass"
    assert web_search[0]["required"] is False
    assert body["ready"] == "pass"
    assert body["degraded"] == "pass"
    assert body["extensions"]["tavily_health_status"] == "ready"
    assert body["extensions"]["tavily_provider_version"] == "closeout-web-fixture-v1"
    assert 0 <= body["extensions"]["tavily_snapshot_age_ms"] <= 5000
    assert body["extensions"]["tavily_refresh_count"] == 1
    assert body["extensions"]["tavily_error_code"] is None
    assert body["extensions"]["tavily_stale_ready"] is False
    assert body["extensions"]["tavily_last_success_at"] is not None


def test_health_readiness_degrades_but_stays_alive_when_search_unreachable() -> None:
    """Issue 04/01：搜索明确非 READY 时 degraded=fail，但应用仍存活且就绪。"""
    app = create_app()
    _replace_web_search(app, _failing_web_search_service())
    test_client = TestClient(app)

    summary = test_client.get("/health").json()
    assert summary["ready"] == "pass"
    assert summary["degraded"] == "fail"
    web_search = [d for d in summary["dependencies"] if d["name"] == "web_search"]
    assert web_search[0]["status"] == "fail"
    assert web_search[0]["required"] is False
    assert summary["extensions"]["tavily_health_status"] == "connect_error"
    assert summary["extensions"]["tavily_error_code"] == "web_search_connect"

    ready = test_client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["ready"] == "pass"
    assert ready.json()["degraded"] == "fail"


def test_health_liveness_stays_fast_and_offline_when_search_down() -> None:
    """Issue 04/01：liveness 不访问公网、不带依赖；搜索故障不拖垮基础存活。"""
    app = create_app()
    _replace_web_search(app, _failing_web_search_service())
    test_client = TestClient(app)

    response = test_client.get("/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["live"] == "pass"
    assert body["ready"] == "unknown"
    assert body["degraded"] == "unknown"
    assert body["dependencies"] == []
    assert "web_search" not in body["extensions"]


def test_health_pending_probe_reports_unknown_without_network() -> None:
    """Issue 04/01：test 环境首次 readiness 返回 pending，不访问公网、不误报失败。"""
    response = TestClient(create_app()).get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    web_search = [d for d in body["dependencies"] if d["name"] == "web_search"]
    assert len(web_search) == 1
    assert web_search[0]["status"] == "unknown"
    assert web_search[0]["required"] is False
    assert body["degraded"] == "pass"
    assert body["extensions"]["tavily_health_status"] == "upstream_error"
    assert body["extensions"]["tavily_error_code"] == "web_search_health_pending"


def test_health_projection_is_sanitized_of_probe_and_credential_material() -> None:
    """Issue 04：健康投影不回显健康查询、响应正文、代理或凭据。"""
    app = create_app()
    _replace_web_search(app, WebSearchService(
        client=CloseoutWebSearchClient(),
        health_auto_refresh=False,
    ))
    serialized = TestClient(app).get("/health").text
    lowered = serialized.casefold()

    assert "bridges-provider-health-check" not in lowered
    assert "authorization" not in lowered
    assert "proxy" not in lowered
    assert "api_key" not in lowered
    assert "secret" not in lowered
    assert "cookie" not in lowered


def test_health_readiness_is_bounded_and_never_blocks_on_probe() -> None:
    """Issue 04：readiness 读取快照有延迟上限，不做同步公网访问。"""
    app = create_app()
    app.state.web_search_service = WebSearchService(
        client=CloseoutWebSearchClient(),
        health_auto_refresh=False,
    )
    test_client = TestClient(app)
    # 从未探测：readiness 必须立刻返回 pending（不等待探测）。
    started = datetime.now(UTC)
    response = test_client.get("/health/ready")
    elapsed_ms = (datetime.now(UTC) - started).total_seconds() * 1000

    assert response.status_code == 200
    # 若 readiness 同步等待探测，将耗时超过探针超时（默认 5s）；2s 内返回
    # 即证明只读取快照、不做公网访问。
    assert elapsed_ms < 2000
    assert response.json()["extensions"]["tavily_error_code"] == "web_search_health_pending"
