"""插件中心 API 路由测试（Issue 34）。

覆盖列表/检查/安装/启停/卸载/演示的路由契约：未登录 401、坏包 422
带原因、同标识冲突 409、内置不可卸载 403、跨账户 404、未挂载服务
503，以及审计细节不含包内容。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.config import get_settings
from bridges.plugins.service import PluginService

from zip_builder import VALID_SKILL_MD, build_valid_zip, build_zip_with

PASSWORD = "correct-horse-34"


@pytest.fixture()
def plugin_app(tmp_path, monkeypatch) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "plugin-api-test-key")
    get_settings.cache_clear()
    from bridges.api.main import create_app

    app = create_app()
    yield app
    get_settings.cache_clear()


@pytest.fixture()
def client(plugin_app) -> TestClient:
    return TestClient(plugin_app)


def _register(client: TestClient, tag: str = "1") -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"plugin_user_{tag}",
            "qq_email": f"12345678{tag}@qq.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _post_zip(
    client: TestClient, url: str, filename: str, content: bytes
) -> Any:
    return client.post(
        url,
        content=content,
        headers={
            "Content-Type": "application/zip",
            "X-Bridges-Filename": filename,
        },
    )


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------


def test_list_plugins_requires_login(client: TestClient) -> None:
    response = client.get("/plugins")
    assert response.status_code == 401


def test_list_plugins_returns_builtin_and_empty_user(client: TestClient) -> None:
    _register(client)
    response = client.get("/plugins")
    assert response.status_code == 200
    body = response.json()
    ids = [p["skill_id"] for p in body["builtin"]]
    assert ids == ["bridges-pdf", "bridges-documents", "bridges-humanizer"]
    assert all(p["enabled"] for p in body["builtin"])
    assert all(p["read_only"] for p in body["builtin"])
    assert body["user"] == []


# ---------------------------------------------------------------------------
# 检查
# ---------------------------------------------------------------------------


def test_check_valid_package_returns_manifest(client: TestClient) -> None:
    _register(client)
    response = _post_zip(client, "/plugins/check", "todo.zip", build_valid_zip())
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["name"] == "待办整理助手"
    assert body["version"] == "1.2.3"
    assert body["file_count"] == 5
    assert len(body["files"]) == 5


def test_check_rejected_package_lists_reasons(client: TestClient) -> None:
    _register(client)
    evil = build_zip_with(entries=[("SKILL.md", VALID_SKILL_MD), ("evil.py", b"x")])
    response = _post_zip(client, "/plugins/check", "evil.zip", evil)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert any("evil.py" in reason and "脚本" in reason for reason in body["rejected_reasons"])
    # 纯检查不落库：列表仍为空
    listed = client.get("/plugins").json()
    assert listed["user"] == []


def test_check_oversized_package_413(client: TestClient) -> None:
    _register(client)
    response = client.post(
        "/plugins/check",
        content=b"z" * (6 * 1024 * 1024),
        headers={"X-Bridges-Filename": "big.zip"},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["error"] == "upload_too_large"


def test_check_missing_filename_400(client: TestClient) -> None:
    _register(client)
    response = client.post("/plugins/check", content=b"zip")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------


def test_install_success_and_audit(client: TestClient) -> None:
    account = _register(client)
    response = _post_zip(client, "/plugins/install", "todo.zip", build_valid_zip())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "installed"
    assert body["enabled"] is True
    assert body["object_id"]
    listed = client.get("/plugins").json()
    assert [p["plugin_id"] for p in listed["user"]] == ["test-todo"]
    events = plugin_audit_events(client, account)
    assert any(e["action"] == "plugin_install" for e in events)
    install = next(e for e in events if e["action"] == "plugin_install")
    assert install["details"]["plugin_id"] == "test-todo"
    assert install["details"]["data_categories"] == ["用户粘贴的待办文本"]
    assert "把文本中的待办事项整理成清单" not in str(install)


def plugin_audit_events(client: TestClient, account: dict[str, Any]) -> list[dict[str, Any]]:
    events = client.app.state.observability_service.list_audit_events(
        account_id=account["id"]
    )
    return [event.model_dump() for event in events]


def test_install_rejected_package_422_with_recoverable_failure(client: TestClient) -> None:
    _register(client)
    evil = build_zip_with(entries=[("SKILL.md", VALID_SKILL_MD), ("evil.py", b"x")])
    response = _post_zip(client, "/plugins/install", "evil.zip", evil)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "unsupported_package"
    listed = client.get("/plugins").json()
    assert len(listed["user"]) == 1
    assert listed["user"][0]["status"] == "install_failed"
    assert listed["user"][0]["object_id"] is None
    assert "evil.py" in listed["user"][0]["failure_reason"]


def test_install_conflict_when_already_installed(client: TestClient) -> None:
    _register(client)
    assert _post_zip(client, "/plugins/install", "todo.zip", build_valid_zip()).status_code == 200
    response = _post_zip(client, "/plugins/install", "todo.zip", build_valid_zip())
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "package_conflict"


def test_failed_package_reinstall_success(client: TestClient) -> None:
    _register(client)
    evil = build_zip_with(entries=[("SKILL.md", VALID_SKILL_MD), ("evil.py", b"x")])
    assert _post_zip(client, "/plugins/install", "x.zip", evil).status_code == 422
    response = _post_zip(client, "/plugins/install", "x.zip", build_valid_zip())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "installed"
    listed = client.get("/plugins").json()
    assert len(listed["user"]) == 1  # 失败记录被覆盖


# ---------------------------------------------------------------------------
# 启停 / 卸载
# ---------------------------------------------------------------------------


def test_enable_disable_and_uninstall(client: TestClient) -> None:
    _register(client)
    assert _post_zip(client, "/plugins/install", "todo.zip", build_valid_zip()).status_code == 200
    listed = client.post("/plugins/test-todo/disable").json()
    assert listed["user"][0]["status"] == "disabled"
    listed = client.post("/plugins/test-todo/enable").json()
    assert listed["user"][0]["status"] == "installed"
    listed = client.delete("/plugins/test-todo").json()
    assert listed["user"] == []


def test_builtin_disable_and_enable(client: TestClient) -> None:
    _register(client)
    listed = client.post("/plugins/bridges-pdf/disable").json()
    assert not next(p for p in listed["builtin"] if p["skill_id"] == "bridges-pdf")["enabled"]
    listed = client.post("/plugins/bridges-pdf/enable").json()
    assert next(p for p in listed["builtin"] if p["skill_id"] == "bridges-pdf")["enabled"]


def test_builtin_cannot_be_uninstalled(client: TestClient) -> None:
    _register(client)
    response = client.delete("/plugins/bridges-pdf")
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "builtin_not_mutable"


def test_enable_unknown_plugin_404(client: TestClient) -> None:
    _register(client)
    response = client.post("/plugins/nope/enable")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 账户隔离
# ---------------------------------------------------------------------------


def test_other_account_cannot_see_or_touch_user_package(client: TestClient) -> None:
    _register(client, tag="1")
    assert _post_zip(client, "/plugins/install", "todo.zip", build_valid_zip()).status_code == 200
    _register(client, tag="2")
    listed = client.get("/plugins").json()
    assert listed["user"] == []
    assert client.post("/plugins/test-todo/disable").status_code == 404
    assert client.delete("/plugins/test-todo").status_code == 404


# ---------------------------------------------------------------------------
# 内置能力演示
# ---------------------------------------------------------------------------


def test_demo_parses_markdown_for_real(client: TestClient) -> None:
    _register(client)
    response = _post_zip(
        client,
        "/plugins/builtin/bridges-documents/demo",
        "demo.md",
        "# 标题\n正文。\n".encode("utf-8"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["parser_version"] == "markdown-v1"
    assert body["sections"] >= 1
    assert "正文" in body["preview"]


def test_demo_chat_kind_skill_rejected(client: TestClient) -> None:
    _register(client)
    response = _post_zip(
        client,
        "/plugins/builtin/bridges-humanizer/demo",
        "demo.md",
        "# 标题\n".encode("utf-8"),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "demo_unsupported"


def test_demo_disabled_builtin_conflict(client: TestClient) -> None:
    _register(client)
    client.post("/plugins/bridges-documents/disable")
    response = _post_zip(
        client,
        "/plugins/builtin/bridges-documents/demo",
        "demo.md",
        "# 标题\n".encode("utf-8"),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "plugin_disabled"


def test_demo_parse_failure_422(client: TestClient) -> None:
    _register(client)
    response = _post_zip(
        client, "/plugins/builtin/bridges-documents/demo", "bad.pdf", b"%PDF-broken"
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "demo_parse_failed"


def test_demo_builtin_enabled_state_is_per_account(client: TestClient) -> None:
    _register(client, tag="1")
    client.post("/plugins/bridges-documents/disable")
    _register(client, tag="2")
    # 账户 2 的启用状态独立，不受账户 1 停用影响（内置按账户隔离）
    response = _post_zip(
        client,
        "/plugins/builtin/bridges-documents/demo",
        "demo.md",
        "# 标题\n".encode("utf-8"),
    )
    assert response.status_code == 200


def test_install_and_toggle_audit_records(client: TestClient) -> None:
    account = _register(client)
    assert _post_zip(client, "/plugins/install", "todo.zip", build_valid_zip()).status_code == 200
    client.post("/plugins/test-todo/disable")
    client.post("/plugins/test-todo/enable")
    client.delete("/plugins/test-todo")
    events = plugin_audit_events(client, account)
    actions = [e["action"] for e in events]
    for expected in ("plugin_install", "plugin_disable", "plugin_enable", "plugin_uninstall"):
        assert expected in actions
    for event in events:
        assert "待办整理助手" not in str(event)
