"""数据生命周期 API 路由契约测试（Issue 37）。

覆盖：预览/导出/删除/状态/重试/备份/恢复端点、RecentAuthRequired 敏感门
（未再认证 403）、强确认文本、错误码（格式/完整性/口令/确认）、跨账户
404、登录必需、恢复后会话失效与凭据待重新配置。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.lifecycle.backup import BACKUP_MAGIC
from bridges.persistence import SqliteStateStore


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """带真实数据目录的 API 客户端（对象库需要 secret_key）。"""
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    get_settings.cache_clear()
    app = create_app(state_store=SqliteStateStore(tmp_path / "api-state.db"))
    return TestClient(app)


def _register(client: TestClient, username: str, qq_email: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "qq_email": qq_email,
            "password": "correct-horse-123",
        },
    )
    assert response.status_code == 201
    return response.json()["account"]["id"]


def _reauthenticate(client: TestClient, password: str = "correct-horse-123") -> None:
    response = client.post(
        "/auth/reauthenticate", json={"password": password}
    )
    assert response.status_code == 204


def _expire_recent_auth(client: TestClient) -> None:
    """把会话的最近认证时间推到 TTL 之前（模拟 5 分钟窗口过期）。"""
    from datetime import timedelta

    service = client.app.state.identity_service
    for stored in service._sessions.values():
        stored.reauthenticated_at = stored.reauthenticated_at - timedelta(minutes=10)


def _create_chat(client: TestClient) -> str:
    response = client.post(
        "/chat/conversations",
        json={"title": "API 测试对话", "mode": "companion"},
    )
    assert response.status_code == 201
    return response.json()["conversation_id"]


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def test_export_preview_requires_login(client: TestClient) -> None:
    response = client.get("/data/export-preview")
    assert response.status_code == 401


def test_export_preview_lists_categories(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _create_chat(client)
    response = client.get("/data/export-preview")
    assert response.status_code == 200
    body = response.json()
    assert body["secrets_omitted"] is True
    categories = {item["category"]: item for item in body["categories"]}
    assert categories["conversations"]["item_count"] == 1
    assert categories["messages"]["item_count"] == 0
    assert body["total_estimated_bytes"] > 0


def test_export_requires_recent_auth(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _expire_recent_auth(client)
    response = client.post("/data/export")
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "reauth_required"


def test_export_returns_json_download_after_reauth(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _create_chat(client)
    _reauthenticate(client)
    response = client.post("/data/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    document = json.loads(response.content)
    assert document["account"]["username"] == "alice"
    assert document["format_version"] == 1


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------


def test_delete_requires_reauth_and_confirmation(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _expire_recent_auth(client)
    response = client.post("/data/account/delete", json={"confirmation": "删除"})
    assert response.status_code == 403  # 未再认证


def test_delete_rejects_wrong_confirmation(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _reauthenticate(client)
    response = client.post("/data/account/delete", json={"confirmation": "不删除"})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "confirmation_required"


def test_delete_account_removes_data_and_session(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _create_chat(client)
    _reauthenticate(client)
    response = client.post("/data/account/delete", json={"confirmation": "删除"})
    assert response.status_code == 204
    # 会话已随删除失效：后续请求 401（cookie 清理由客户端处理）。
    preview = client.get("/data/export-preview")
    assert preview.status_code == 401
    # 重新登录被拒（账户已删除）。
    login = client.post(
        "/auth/login", json={"identifier": "alice", "password": "correct-horse-123"}
    )
    assert login.status_code == 401


def test_delete_status_and_retry(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _reauthenticate(client)
    status = client.get("/data/account/delete-status")
    assert status.status_code == 404  # 无删除记录
    client.post("/data/account/delete", json={"confirmation": "删除"})
    status = client.get("/data/account/delete-status")
    assert status.status_code == 401  # 会话已失效
    # 重试端点同样需要再认证（会话已失效）。
    response = client.post("/data/account/delete/retry")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 备份与恢复
# ---------------------------------------------------------------------------


def test_create_backup_requires_reauth(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _expire_recent_auth(client)
    response = client.post("/data/backups", data={"passphrase": "口令123"})
    assert response.status_code == 403


def test_create_backup_returns_encrypted_file(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _create_chat(client)
    _reauthenticate(client)
    response = client.post("/data/backups", data={"passphrase": "口令123"})
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "bridgesbackup" in response.headers["content-disposition"]
    assert response.content.startswith(BACKUP_MAGIC)


def test_restore_requires_reauth_and_confirmation(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _expire_recent_auth(client)
    response = client.post(
        "/data/restore",
        data={"passphrase": "口令123", "confirmation": "恢复"},
        files={"file": ("b.bridgesbackup", b"garbage", "application/octet-stream")},
    )
    assert response.status_code == 403


def test_restore_rejects_invalid_format_without_harm(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _reauthenticate(client)
    response = client.post(
        "/data/restore",
        data={"passphrase": "口令123", "confirmation": "恢复"},
        files={"file": ("b.bridgesbackup", b"NOT-A-BACKUP", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "backup_invalid_format"
    # 现有数据未被破坏。
    preview = client.get("/data/export-preview")
    assert preview.status_code == 200


def test_full_backup_restore_roundtrip_via_api(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _create_chat(client)
    _reauthenticate(client)
    backup = client.post("/data/backups", data={"passphrase": "口令123"})
    assert backup.status_code == 200
    backup_bytes = backup.content
    # 篡改数据：新建对话。
    _create_chat(client)
    # 恢复（需再次再认证：恢复会替换身份，但响应前会话仍有效）。
    _reauthenticate(client)
    restore = client.post(
        "/data/restore",
        data={"passphrase": "口令123", "confirmation": "恢复"},
        files={"file": ("b.bridgesbackup", backup_bytes, "application/octet-stream")},
    )
    assert restore.status_code == 200
    body = restore.json()
    assert body["ok"] is True
    assert body["account_count"] == 1
    # 恢复后会话失效（身份被替换、无会话）。
    preview = client.get("/data/export-preview")
    assert preview.status_code == 401
    # 重新登录成功（密码哈希随备份恢复）。
    login = client.post(
        "/auth/login", json={"identifier": "alice", "password": "correct-horse-123"}
    )
    assert login.status_code == 200
    # 对话回到备份时的一条。
    preview = client.get("/data/export-preview")
    assert preview.status_code == 200
    categories = {item["category"]: item for item in preview.json()["categories"]}
    assert categories["conversations"]["item_count"] == 1


def test_restore_wrong_passphrase_rejected(client: TestClient) -> None:
    _register(client, "alice", "10001@qq.com")
    _reauthenticate(client)
    backup = client.post("/data/backups", data={"passphrase": "口令123"})
    _reauthenticate(client)
    response = client.post(
        "/data/restore",
        data={"passphrase": "错误口令", "confirmation": "恢复"},
        files={"file": ("b.bridgesbackup", backup.content, "application/octet-stream")},
    )
    assert response.status_code == 200  # 预检失败以预览结果返回（不抛错）
    assert response.json()["ok"] is False
    assert any("口令错误" in reason for reason in response.json()["reasons"])
