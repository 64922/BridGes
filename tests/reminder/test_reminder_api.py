"""提醒 API 路由集成测试（Issue 33）。

使用临时 sqlite 数据库 + 进程内假 SMTP/IMAP 服务器构建应用：授权码
保存触发真实自发自收验证（经假服务器完成）；验证响应与审计不含授权码
正文；未验证禁止创建；解析→确认→创建→投递→查看记录→编辑/取消全流程；
跨账户一律 404；敏感操作（授权码保存/删除）要求近期密码确认。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fake_mail import FakeMailbox, FakeMailServers
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings

AUTH_CODE = "ABCDEFGHIJKLMNOP"
PASSWORD = "Passw0rd123!"


@pytest.fixture()
def servers() -> FakeMailServers:
    with FakeMailServers(FakeMailbox(auth_codes={AUTH_CODE})) as fake:
        yield fake


@pytest.fixture()
def reminder_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: FakeMailServers
) -> Any:
    """构建应用：sqlite 数据库 + 假邮件服务器端点（真实网关经假服务器）。"""
    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    monkeypatch.setenv("BRIDGES_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("BRIDGES_SMTP_PORT", str(servers.smtp_port))
    monkeypatch.setenv("BRIDGES_SMTP_PLAIN", "true")
    monkeypatch.setenv("BRIDGES_IMAP_HOST", "127.0.0.1")
    monkeypatch.setenv("BRIDGES_IMAP_PORT", str(servers.imap_port))
    monkeypatch.setenv("BRIDGES_IMAP_PLAIN", "true")
    get_settings.cache_clear()
    app = create_app()
    yield app
    get_settings.cache_clear()


@pytest.fixture()
def client(reminder_app: Any) -> TestClient:
    return TestClient(reminder_app)


def _register(client: TestClient, tag: str = "1") -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"reminder_user_{tag}",
            "qq_email": f"12345678{tag}@qq.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _reauth(client: TestClient) -> None:
    response = client.post("/auth/reauthenticate", json={"password": PASSWORD})
    assert response.status_code == 204, response.text


def _wait_verified(client: TestClient, timeout: float = 5.0) -> dict[str, Any]:
    """轮询 SMTP 状态直到不再 verifying（后台线程验证）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/reminders/smtp")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] != "verifying":
            return payload
        time.sleep(0.05)
    raise AssertionError("SMTP 验证超时未收敛")


def test_smtp_unconfigured_projection(client: TestClient) -> None:
    _register(client)
    response = client.get("/reminders/smtp")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unconfigured"
    assert payload["qq_email"] == "123456781@qq.com"  # 固定收发件人
    assert "authorization_code" not in payload


def test_save_smtp_code_requires_recent_auth(
    client: TestClient, reminder_app: Any
) -> None:
    """授权码保存属于敏感设置：会话超过近期认证窗口 → 403 reauth_required。"""
    from datetime import UTC, datetime, timedelta

    _register(client)
    identity = reminder_app.state.identity_service
    identity._now = lambda: datetime.now(UTC) + timedelta(minutes=6)
    response = client.put("/reminders/smtp", json={"authorization_code": AUTH_CODE})
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "reauth_required"


def test_reauth_retry_creates_single_verification_attempt(
    client: TestClient, reminder_app: Any
) -> None:
    """必红（issue 10）：reauth_required → 密码确认 → 自动重试保存。

    原页密码确认成功后自动重试原保存命令（授权码无需重输），断言只
    创建一个有效验证 attempt：403 拒绝发生在敏感门前（服务层未执行，
    不创建 attempt），确认后的重试保存恰好创建一个并最终 verified。
    """
    from datetime import UTC, datetime, timedelta

    _register(client)
    identity = reminder_app.state.identity_service
    identity._now = lambda: datetime.now(UTC) + timedelta(minutes=6)
    response = client.put("/reminders/smtp", json={"authorization_code": AUTH_CODE})
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "reauth_required"
    # 原页模态框的密码确认接口
    _reauth(client)
    response = client.put("/reminders/smtp", json={"authorization_code": AUTH_CODE})
    assert response.status_code == 200, response.text
    payload = _wait_verified(client)
    assert payload["status"] == "verified"
    database = reminder_app.state.reminder_service._database
    rows = database.connection.execute(
        "SELECT attempt_id, state, error_code FROM smtp_verification_attempts"
    ).fetchall()
    assert len(rows) == 1, f"应只创建一个验证 attempt，实际 {len(rows)}"
    assert rows[0]["state"] == "verified"
    assert rows[0]["error_code"] is None
    assert AUTH_CODE not in str(rows)


def test_save_verify_and_projection_without_leak(client: TestClient) -> None:
    """保存授权码 → 自发自收验证（假服务器）→ verified；无泄漏。"""
    _register(client)
    _reauth(client)
    response = client.put("/reminders/smtp", json={"authorization_code": AUTH_CODE})
    assert response.status_code == 200, response.text
    payload = _wait_verified(client)
    assert payload["status"] == "verified"
    assert payload["verified_at"] is not None
    assert AUTH_CODE not in str(payload)
    assert "authorization_code" not in str(payload)


def test_save_rejects_password_shaped(client: TestClient) -> None:
    _register(client)
    _reauth(client)
    response = client.put("/reminders/smtp", json={"authorization_code": "P@ssw0rd"})
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_auth_code"


def test_verify_failure_reports_reason(
    client: TestClient, reminder_app: Any
) -> None:
    """授权码无效 → failed + 错误码 + 中文原因（提供重新验证路径）。"""
    _register(client)
    _reauth(client)
    response = client.put(
        "/reminders/smtp", json={"authorization_code": "WRONGCODE1234"}
    )
    assert response.status_code == 200
    payload = _wait_verified(client)
    assert payload["status"] == "failed"
    assert payload["error_code"] == "smtp_auth_failed"
    assert "授权码" in (payload["error_message"] or "")
    # 重新验证路径：POST /reminders/smtp/verify
    response = client.post("/reminders/smtp/verify")
    assert response.status_code == 200


def test_delete_smtp_code_requires_reauth_and_resets(
    client: TestClient, reminder_app: Any
) -> None:
    _register(client)
    _reauth(client)
    client.put("/reminders/smtp", json={"authorization_code": AUTH_CODE})
    _wait_verified(client)
    response = client.delete("/reminders/smtp")
    assert response.status_code == 200
    assert response.json()["status"] == "unconfigured"


def test_create_blocked_without_verified_smtp(client: TestClient) -> None:
    _register(client)
    parsed = _parse(client, "明天早上八点提醒我复习")
    response = client.post("/reminders", json=_confirmed(parsed))
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "smtp_not_verified"


def test_parse_preview_shape(client: TestClient) -> None:
    """自然语言解析预览：时区/首跑/重复规则/主题/正文/画像披露。"""
    _register(client)
    parsed = _parse(client, "每周一和周三下午3点半提醒我开会")
    assert parsed["schedule"]["timezone"] == "Asia/Shanghai"
    assert parsed["schedule"]["repeat"] == "weekly_days"
    assert parsed["schedule"]["repeat_weekdays"] == [1, 3]
    assert parsed["subject"] == "开会"
    assert "开会" in parsed["body_preview"]
    assert parsed["profile_usage"]["enabled"] is False


def test_full_flow_create_deliver_inspect_edit_cancel(
    client: TestClient, servers: FakeMailServers
) -> None:
    """配置→验证→解析→确认创建→手动投递→查看记录→编辑→取消。"""
    _register(client)
    _reauth(client)
    client.put("/reminders/smtp", json={"authorization_code": AUTH_CODE})
    _wait_verified(client)

    parsed = _parse(client, "明天早上八点提醒我复习 transformer")
    response = client.post("/reminders", json=_confirmed(parsed))
    assert response.status_code == 201, response.text
    reminder = response.json()
    reminder_id = reminder["reminder_id"]
    assert reminder["qq_email"] == "123456781@qq.com"
    assert reminder["status"] == "enabled"
    assert reminder["subject"] == "复习 transformer"
    assert reminder["body"] == "复习 transformer\n\n—— 来自 BridGes 提醒"

    # 列表与详情
    response = client.get("/reminders")
    assert response.status_code == 200
    assert [r["reminder_id"] for r in response.json()] == [reminder_id]

    # 手动补发 → 投递记录（manual_retry/sent），邮件进入假服务器邮箱
    response = client.post(f"/reminders/{reminder_id}/send-now")
    assert response.status_code == 200, response.text
    delivery = response.json()
    assert delivery["kind"] == "manual_retry"
    assert delivery["outcome"] == "sent"
    assert len(servers.mailbox.messages) == 2  # 验证邮件 + 提醒邮件
    stored = servers.mailbox.messages[1]
    assert stored.recipients == ["123456781@qq.com"]
    assert "复习 transformer" in stored.subject

    response = client.get(f"/reminders/{reminder_id}/deliveries")
    assert response.status_code == 200
    deliveries = response.json()
    assert deliveries[0]["kind"] == "manual_retry"
    assert deliveries[0]["outcome"] == "sent"

    # 编辑
    parsed_edit = _parse(client, "明天晚上九点提醒我做总结")
    response = client.put(
        f"/reminders/{reminder_id}", json=_confirmed(parsed_edit)
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["subject"] == "做总结"
    assert updated["status"] == "enabled"

    # 暂停 → 恢复 → 取消
    response = client.post(f"/reminders/{reminder_id}/pause")
    assert response.json()["status"] == "paused"
    response = client.post(f"/reminders/{reminder_id}/resume")
    assert response.json()["status"] == "enabled"
    response = client.delete(f"/reminders/{reminder_id}")
    assert response.json()["status"] == "cancelled"
    # 投递记录保留
    assert len(client.get(f"/reminders/{reminder_id}/deliveries").json()) >= 1


def test_cross_account_isolated(client: TestClient) -> None:
    """跨账户访问提醒/投递记录一律 404。"""
    _register(client, "1")
    _reauth(client)
    client.put("/reminders/smtp", json={"authorization_code": AUTH_CODE})
    _wait_verified(client)
    parsed = _parse(client, "明天早上八点提醒我复习")
    response = client.post("/reminders", json=_confirmed(parsed))
    reminder_id = response.json()["reminder_id"]

    _register(client, "2")
    for path in (
        f"/reminders/{reminder_id}",
        f"/reminders/{reminder_id}/deliveries",
    ):
        response = client.get(path)
        assert response.status_code == 404
    response = client.post(f"/reminders/{reminder_id}/pause")
    assert response.status_code == 404
    assert client.get("/reminders").json() == []


def test_settings_timezone_roundtrip(client: TestClient) -> None:
    _register(client)
    response = client.put(
        "/reminders/settings", json={"timezone": "America/New_York"}
    )
    assert response.status_code == 200
    assert response.json()["timezone"] == "America/New_York"
    response = client.put("/reminders/settings", json={"timezone": "Mars/Olympus"})
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_timezone"


def test_parse_failure_clear_chinese_error(client: TestClient) -> None:
    _register(client)
    response = client.post(
        "/reminders/parse",
        json={"raw_text": "提醒我复习", "timezone": "Asia/Shanghai"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "parse_failed"
    assert "时间" in detail["message"]


def _parse(client: TestClient, raw_text: str) -> dict[str, Any]:
    response = client.post(
        "/reminders/parse",
        json={"raw_text": raw_text, "timezone": "Asia/Shanghai"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _confirmed(parsed: dict[str, Any]) -> dict[str, Any]:
    """把解析预览组装为确认创建载荷（保持确定性一致）。"""
    return {
        "raw_text": parsed["raw_text"],
        "schedule": parsed["schedule"],
        "subject": parsed["subject"],
    }
