"""MCP 插件管理 API 路由测试（Issue 35）。

覆盖列表/检查/安装/启停/卸载/撤权/调用/确认/记录的路由契约：未登录
401、坏描述 422 带原因、同标识冲突 409、跨账户 404、敏感挂起 202、
拒绝 403、未挂载服务 503，以及响应与审计不含秘密与正文。
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixtures import LATEST_VERSION_YAML, echo_yaml

from bridges.config import get_settings

PASSWORD = "correct-horse-35"
PYTHON = sys.executable


@pytest.fixture()
def mcp_app(tmp_path, monkeypatch) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "mcp-api-test-key")
    get_settings.cache_clear()
    from bridges.api.main import create_app

    app = create_app()
    yield app
    get_settings.cache_clear()


@pytest.fixture()
def client(mcp_app) -> TestClient:
    return TestClient(mcp_app)


def _register(client: TestClient, tag: str = "1") -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"mcp_user_{tag}",
            "qq_email": f"12345678{tag}@qq.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _post_yaml(client: TestClient, url: str, filename: str, content: bytes) -> Any:
    return client.post(
        url,
        content=content,
        headers={
            "Content-Type": "application/yaml",
            "X-Bridges-Filename": filename,
        },
    )


# ---------------------------------------------------------------------------
# 列表与登录
# ---------------------------------------------------------------------------


def test_list_requires_login(client: TestClient) -> None:
    response = client.get("/mcp")
    assert response.status_code == 401


def test_list_empty_after_register(client: TestClient) -> None:
    _register(client)
    response = client.get("/mcp")
    assert response.status_code == 200
    assert response.json()["servers"] == []


# ---------------------------------------------------------------------------
# 检查 / 安装
# ---------------------------------------------------------------------------


def test_check_valid_descriptor(client: TestClient) -> None:
    _register(client)
    response = _post_yaml(client, "/mcp/check", "echo.yaml", echo_yaml().encode("utf-8"))
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["mcp_id"] == "bridges-echo"
    assert body["permissions"]["data_categories"] == ["current_message_text"]
    assert body["integrity_sha256"] is not None
    # 纯检查不落库。
    assert client.get("/mcp").json()["servers"] == []


def test_check_rejected_descriptor_lists_reasons(client: TestClient) -> None:
    _register(client)
    response = _post_yaml(client, "/mcp/check", "bad.yaml", LATEST_VERSION_YAML.encode("utf-8"))
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert any("未锁定" in reason for reason in body["rejected_reasons"])


def test_check_non_yaml_rejected(client: TestClient) -> None:
    _register(client)
    response = _post_yaml(client, "/mcp/check", "echo.txt", echo_yaml().encode("utf-8"))
    assert response.status_code == 422
    assert "MCP 安装描述" in response.json()["detail"]["message"]


def test_install_valid_descriptor(client: TestClient) -> None:
    _register(client)
    response = _post_yaml(client, "/mcp/install", "echo.yaml", echo_yaml().encode("utf-8"))
    assert response.status_code == 200
    body = response.json()
    assert body["mcp_id"] == "bridges-echo"
    assert body["status"] == "healthy"
    assert body["call_count"] == 0
    assert client.get("/mcp").json()["servers"][0]["mcp_id"] == "bridges-echo"


def test_install_rejected_descriptor_422(client: TestClient) -> None:
    _register(client)
    response = _post_yaml(client, "/mcp/install", "bad.yaml", LATEST_VERSION_YAML.encode("utf-8"))
    assert response.status_code == 422
    assert any("未锁定" in reason for reason in response.json()["detail"]["message"].split("；"))


def test_install_conflict_409(client: TestClient) -> None:
    _register(client)
    _post_yaml(client, "/mcp/install", "echo.yaml", echo_yaml().encode("utf-8"))
    response = _post_yaml(client, "/mcp/install", "echo2.yaml", echo_yaml().encode("utf-8"))
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# 启停 / 卸载 / 撤权
# ---------------------------------------------------------------------------


def test_enable_disable_and_uninstall(client: TestClient) -> None:
    _register(client)
    _post_yaml(client, "/mcp/install", "echo.yaml", echo_yaml().encode("utf-8"))
    # 停用。
    response = client.post("/mcp/bridges-echo/disable")
    assert response.status_code == 200
    server = response.json()["servers"][0]
    assert server["status"] == "disabled"
    assert server["enabled"] is False
    # 停用后调用被拒。
    invoke = client.post(
        "/mcp/bridges-echo/invoke",
        json={"tool": "echo", "input": {}, "data_slice": {"text": "x"}},
    )
    assert invoke.status_code == 409
    assert "已停用" in invoke.json()["detail"]["message"]
    # 启用。
    response = client.post("/mcp/bridges-echo/enable")
    assert response.status_code == 200
    assert response.json()["servers"][0]["status"] == "healthy"
    # 卸载。
    response = client.delete("/mcp/bridges-echo")
    assert response.status_code == 200
    assert response.json()["servers"] == []
    # 重复卸载 404。
    assert client.delete("/mcp/bridges-echo").status_code == 404


def test_revoke_permissions_endpoint(client: TestClient) -> None:
    _register(client)
    _post_yaml(client, "/mcp/install", "echo.yaml", echo_yaml().encode("utf-8"))
    response = client.put(
        "/mcp/bridges-echo/permissions",
        json={
            "network_domains": [],
            "filesystem_read": [],
            "filesystem_write": [],
            "external_commands": [],
            "data_categories": [],
            "sensitive_operations": [],
        },
    )
    assert response.status_code == 200
    assert response.json()["permissions"]["data_categories"] == []


def test_revoke_permissions_invalid_422(client: TestClient) -> None:
    _register(client)
    _post_yaml(client, "/mcp/install", "echo.yaml", echo_yaml().encode("utf-8"))
    response = client.put(
        "/mcp/bridges-echo/permissions",
        json={
            "network_domains": ["https://evil.example.com"],
            "filesystem_read": ["relative/path"],
            "filesystem_write": [],
            "external_commands": [],
            "data_categories": ["full_chat_history"],
            "sensitive_operations": ["delete_everything"],
        },
    )
    # 非法敏感操作被请求体枚举校验拦截（422）；域名/路径经服务层校验。
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 调用与敏感确认
# ---------------------------------------------------------------------------


def test_invoke_success(client: TestClient) -> None:
    _register(client)
    _post_yaml(client, "/mcp/install", "echo.yaml", echo_yaml().encode("utf-8"))
    response = client.post(
        "/mcp/bridges-echo/invoke",
        json={"tool": "echo", "input": {}, "data_slice": {"text": "你好，MCP"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["result"]["echo"] == "你好，MCP"
    # 调用统计更新。
    server = client.get("/mcp").json()["servers"][0]
    assert server["call_count"] == 1
    assert server["last_call_status"] == "success"


def test_invoke_undeclared_slice_403(client: TestClient) -> None:
    _register(client)
    _post_yaml(client, "/mcp/install", "echo.yaml", echo_yaml().encode("utf-8"))
    response = client.post(
        "/mcp/bridges-echo/invoke",
        json={
            "tool": "echo",
            "input": {},
            "data_slice": {"text": "", "attachments": [{"filename": "a.txt"}]},
        },
    )
    assert response.status_code == 403
    assert "附件" in response.json()["detail"]["message"]


def test_sensitive_pending_then_approve(client: TestClient, tmp_path) -> None:
    _register(client)
    write_dir = tmp_path / "notes"
    write_dir.mkdir()
    note = (
        "---\nmcp_id: bridges-note\nname: 笔记助手\nversion: 1.0.0\nsource: local\n"
        "command:\n  - python\n  - -m\n  - bridges.mcp.servers.note\n"
        "network_domains: []\nfilesystem_read: []\nfilesystem_write:\n"
        f"  - {write_dir}\nexternal_commands: []\n"
        "data_categories:\n  - current_message_text\n"
        "sensitive_operations:\n  - write_file\n---\n"
    )
    _post_yaml(client, "/mcp/install", "note.yaml", note.encode("utf-8"))
    response = client.post(
        "/mcp/bridges-note/invoke",
        json={
            "tool": "note",
            "input": {"path": str(write_dir / "note.txt")},
            "data_slice": {"text": "待确认内容"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "sensitive_pending"
    confirmation_id = body["confirmation"]["confirmation_id"]
    assert "写入文件" in body["confirmation"]["target"]
    # 确认前文件不存在。
    assert not (write_dir / "note.txt").exists()
    # 确认 → 成功。
    approved = client.post(f"/mcp/bridges-note/confirmations/{confirmation_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "success"
    assert (write_dir / "note.txt").read_text(encoding="utf-8") == "待确认内容"


def test_sensitive_deny_403(client: TestClient, tmp_path) -> None:
    _register(client)
    write_dir = tmp_path / "notes"
    write_dir.mkdir()
    note = (
        "---\nmcp_id: bridges-note\nname: 笔记助手\nversion: 1.0.0\nsource: local\n"
        "command:\n  - python\n  - -m\n  - bridges.mcp.servers.note\n"
        "network_domains: []\nfilesystem_read: []\nfilesystem_write:\n"
        f"  - {write_dir}\nexternal_commands: []\n"
        "data_categories:\n  - current_message_text\n"
        "sensitive_operations:\n  - write_file\n---\n"
    )
    _post_yaml(client, "/mcp/install", "note.yaml", note.encode("utf-8"))
    response = client.post(
        "/mcp/bridges-note/invoke",
        json={
            "tool": "note",
            "input": {"path": str(write_dir / "note.txt")},
            "data_slice": {"text": "应被拒绝"},
        },
    )
    confirmation_id = response.json()["confirmation"]["confirmation_id"]
    denied = client.post(f"/mcp/bridges-note/confirmations/{confirmation_id}/deny")
    assert denied.status_code == 200
    body = denied.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "sensitive_denied"
    assert not (write_dir / "note.txt").exists()


def test_calls_endpoint(client: TestClient) -> None:
    _register(client)
    _post_yaml(client, "/mcp/install", "echo.yaml", echo_yaml().encode("utf-8"))
    client.post(
        "/mcp/bridges-echo/invoke",
        json={"tool": "echo", "input": {}, "data_slice": {"text": "调用记录"}},
    )
    response = client.get("/mcp/bridges-echo/calls")
    assert response.status_code == 200
    calls = response.json()
    assert len(calls) == 1
    assert calls[0]["tool"] == "echo"
    assert calls[0]["status"] == "success"
    # 记录不含输入正文。
    assert "调用记录" not in str(calls)


# ---------------------------------------------------------------------------
# 账户隔离与未挂载
# ---------------------------------------------------------------------------


def test_cross_account_404(client: TestClient) -> None:
    _register(client, "1")
    _post_yaml(client, "/mcp/install", "echo.yaml", echo_yaml().encode("utf-8"))
    # 登出并注册账户 B。
    client.post("/auth/logout")
    _register(client, "2")
    response = client.get("/mcp")
    assert response.status_code == 200
    assert response.json()["servers"] == []
    # 跨账户访问一律 404。
    assert client.post("/mcp/bridges-echo/disable").status_code == 404
    assert client.delete("/mcp/bridges-echo").status_code == 404
    assert (
        client.post(
            "/mcp/bridges-echo/invoke",
            json={"tool": "echo", "input": {}, "data_slice": {"text": "x"}},
        ).status_code
        == 404
    )
    assert client.get("/mcp/bridges-echo/calls").status_code == 404


def test_unmounted_service_503(tmp_path, monkeypatch) -> None:
    """未挂载 MCP 服务的实例拒绝操作（503）。"""
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "mcp-api-test-key")
    get_settings.cache_clear()
    from bridges.api.main import create_app

    app = create_app()
    # 移除挂载（模拟未启用场景）。
    app.state.mcp_service = None  # type: ignore[attr-defined]
    client = TestClient(app)
    response = client.get("/mcp")
    assert response.status_code == 503
    assert "未启用" in response.json()["detail"]["message"]
    get_settings.cache_clear()


def test_responses_never_leak_secrets(client: TestClient) -> None:
    """响应与调用记录不含秘密（Verification 4）。"""
    _register(client)
    _post_yaml(client, "/mcp/install", "echo.yaml", echo_yaml().encode("utf-8"))
    client.post(
        "/mcp/bridges-echo/invoke",
        json={"tool": "echo", "input": {}, "data_slice": {"text": "私人正文ABC"}},
    )
    blob = str(client.get("/mcp").json()) + str(client.get("/mcp/bridges-echo/calls").json())
    assert "sk-" not in blob
    assert "私人正文ABC" not in blob
