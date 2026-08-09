"""用户 SKILL、插件与通用 MCP 退役的公共验收测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.mcp import McpError
from bridges.contracts.plugins import PluginError
from bridges.retirement import retire_user_extensions


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "retirement-test-key")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(app: Any) -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/plugins"),
        ("post", "/plugins/check"),
        ("post", "/plugins/install"),
        ("post", "/plugins/anything/enable"),
        ("delete", "/plugins/anything"),
        ("get", "/mcp"),
        ("post", "/mcp/check"),
        ("post", "/mcp/install"),
        ("post", "/mcp/anything/invoke"),
        ("post", "/mcp/anything/confirmations/c1/approve"),
        ("get", "/mcp/anything/calls"),
    ],
)
def test_legacy_extension_routes_are_safe_410(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(
        method.upper(),
        path,
        content=b"account=secret package=secret server=secret",
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": "999999999",
            "X-Bridges-Filename": "secret.zip",
        },
    )

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error"] == "user_extensions_retired"
    assert detail["service_version"] == "0.1.0"
    assert detail["traffic_class"] == "real"
    assert "secret" not in json.dumps(detail, ensure_ascii=False)


def test_compatibility_observations_are_aggregated_without_sensitive_fields(
    client: TestClient,
) -> None:
    client.get("/plugins", headers={"X-Bridges-Compatibility-Probe": "true"})
    response = client.get("/compatibility/observations")

    assert response.status_code == 200
    body = response.json()
    assert body["service_version"] == "0.1.0"
    assert body["routes"]["legacy.plugins.list"]["probe"] == 1
    assert "account" not in json.dumps(body, ensure_ascii=False)
    assert "request" not in json.dumps(body, ensure_ascii=False)


def test_builtin_skills_are_read_only_and_paper_search_remains_internal(
    app: Any, client: TestClient
) -> None:
    response = client.get("/skills")

    assert response.status_code == 200
    skills = response.json()
    assert [item["skill_id"] for item in skills] == [
        "bridges-pdf",
        "bridges-documents",
        "bridges-humanizer",
    ]
    assert all(item["read_only"] for item in skills)
    assert all("enabled" not in item for item in skills)
    assert app.state.arxiv_search_service is not None


def test_direct_extension_services_are_retired(app: Any) -> None:
    with pytest.raises(PluginError) as plugin_error:
        app.state.plugin_service.check_package("user-skill.zip", b"payload")
    with pytest.raises(McpError) as mcp_error:
        app.state.mcp_service.check("user-mcp.yaml", b"payload")

    assert plugin_error.value.status_code == 410
    assert mcp_error.value.status_code == 410


def test_retirement_is_idempotent_and_blocks_historical_runtime_state(app: Any) -> None:
    database = app.state.bridges_database
    now = "2026-08-09T00:00:00+00:00"
    database.connection.execute(
        "INSERT INTO accounts(account_id, email, created_at) VALUES (?, ?, ?)",
        ("account-a", "account-a@example.com", now),
    )
    database.connection.executescript(
        """
        INSERT INTO skill_packages(
            package_id, account_id, plugin_id, version, name, installed_at, updated_at
            ) VALUES ('pkg-1', 'account-a', 'user-skill', '1', '用户技能', datetime('now'), datetime('now'));
        INSERT INTO mcp_servers(
            mcp_id, account_id, name, version, source, integrity_sha256,
            command, permissions, status, enabled, installed_at, updated_at
        ) VALUES ('mcp-1', 'account-a', '用户 MCP', '1', 'upload', 'hash',
                      'python', '[]', 'healthy', 1, datetime('now'), datetime('now'));
        INSERT INTO conversations(
            conversation_id, account_id, title, mode, plugin_selection, created_at, updated_at
            ) VALUES ('conversation-1', 'account-a', '历史', 'companion', '[{"skill_id":"user-skill"}]', datetime('now'), datetime('now'));
        INSERT INTO messages(
            message_id, conversation_id, account_id, role, content, status,
            created_at, updated_at, mcp_call
            ) VALUES ('message-1', 'conversation-1', 'account-a', 'assistant', '', 'done', datetime('now'), datetime('now'),
                  '{"status":"running","mcp_id":"mcp-1"}');
        """,
    )
    database.connection.commit()

    first = retire_user_extensions(database)
    second = retire_user_extensions(database)

    assert first["skill_packages_disabled"] == 1
    assert first["mcp_servers_disabled"] == 1
    assert first["pending_calls_terminated"] == 1
    assert second["skill_packages_disabled"] == 0
    assert second["mcp_servers_disabled"] == 0
    assert second["pending_calls_terminated"] == 0

    skill = database.connection.execute(
        "SELECT status FROM skill_packages WHERE package_id = 'pkg-1'"
    ).fetchone()
    server = database.connection.execute(
        "SELECT status, enabled FROM mcp_servers WHERE mcp_id = 'mcp-1'"
    ).fetchone()
    conversation = database.connection.execute(
        "SELECT plugin_selection FROM conversations WHERE conversation_id = 'conversation-1'"
    ).fetchone()
    message = database.connection.execute(
        "SELECT mcp_call FROM messages WHERE message_id = 'message-1'"
    ).fetchone()

    assert skill["status"] == "disabled"
    assert server["status"] == "disabled"
    assert server["enabled"] == 0
    assert conversation["plugin_selection"] is None
    assert json.loads(message["mcp_call"])["error_code"] == "user_extensions_retired"
