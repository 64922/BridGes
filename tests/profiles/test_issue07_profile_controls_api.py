"""Issue 07：画像记录/使用控制的公开 API 合同（版本化、账户隔离、幂等）。"""

from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from bridges.api.main import create_app


def _register(client: TestClient, username: str, email: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "qq_email": email,
            "password": "correct-horse-07",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())["account"]["id"]


def test_controls_defaults_are_both_enabled_and_versioned() -> None:
    client = TestClient(create_app())
    _register(client, "issue07-controls-default", "070101@qq.com")

    response = client.get("/profiles/controls")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recording_enabled"] is True
    assert body["usage_enabled"] is True
    assert body["controls_version"]
    assert body["usage_control_version"] == 0


def test_put_updates_each_switch_and_reads_back_consistently() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue07-controls-put", "070102@qq.com")
    service = client.app.state.automatic_profile_service

    stopped = client.put("/profiles/controls", json={"recording_enabled": False})
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["recording_enabled"] is False
    assert stopped.json()["usage_enabled"] is True

    # 聊天指令写的是同一份状态（同源读取，跨入口一致）。
    service.preprocess_message(
        account_id,
        conversation_id="conv-controls",
        message_id="m-1",
        content="不要记录",
        run_id="r-1",
        mode="daily",
    )
    assert client.get("/profiles/controls").json()["recording_enabled"] is False

    reenabled = client.put("/profiles/controls", json={"recording_enabled": True})
    assert reenabled.status_code == 200, reenabled.text
    assert reenabled.json()["recording_enabled"] is True

    usage_off = client.put("/profiles/controls", json={"usage_enabled": False})
    assert usage_off.status_code == 200, usage_off.text
    assert usage_off.json()["usage_enabled"] is False
    assert usage_off.json()["recording_enabled"] is True
    assert usage_off.json()["usage_control_version"] == 1


def test_put_is_idempotent_for_same_value() -> None:
    client = TestClient(create_app())
    _register(client, "issue07-controls-idempotent", "070103@qq.com")

    first = client.put("/profiles/controls", json={"usage_enabled": False})
    second = client.put("/profiles/controls", json={"usage_enabled": False})

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["usage_control_version"] == second.json()["usage_control_version"]
    assert first.json()["usage_updated_at"] == second.json()["usage_updated_at"]


def test_put_without_any_field_is_unprocessable() -> None:
    client = TestClient(create_app())
    _register(client, "issue07-controls-empty", "070104@qq.com")

    response = client.put("/profiles/controls", json={})

    assert response.status_code == 422, response.text


def test_unknown_fields_are_rejected() -> None:
    client = TestClient(create_app())
    _register(client, "issue07-controls-extra", "070105@qq.com")

    response = client.put(
        "/profiles/controls", json={"usage_enabled": True, "profile_enabled": True}
    )

    assert response.status_code == 422, response.text


def test_controls_require_a_session() -> None:
    client = TestClient(create_app())

    response = client.get("/profiles/controls")

    assert response.status_code == 401, response.text


def test_controls_are_account_scoped() -> None:
    client = TestClient(create_app())
    owner = _register(client, "issue07-controls-owner", "070106@qq.com")

    assert client.put("/profiles/controls", json={"usage_enabled": False}).status_code == 200

    # 切换到第二个账户的会话：读到的是它自己的默认状态。
    _register(client, "issue07-controls-outsider", "070107@qq.com")
    outsider = client.get("/profiles/controls").json()
    assert outsider["usage_enabled"] is True

    # 直接种读服务层复核隔离：owner 的状态不因 outsider 读取或写入变化。
    service = client.app.state.automatic_profile_service
    client.put("/profiles/controls", json={"usage_enabled": False})
    owner_controls = service.account_controls(owner)
    assert owner_controls.usage_enabled is False
    assert client.get("/profiles/controls").json()["usage_enabled"] is False
