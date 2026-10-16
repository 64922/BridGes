"""画像工单 15：整体概览的公开读取合同与失效联动。"""

from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from bridges.api.main import create_app

CAT_TEXT = "我养了一只猫叫团子"
HALF_HOUR_TEXT = "我每晚只能抽出半小时"


def _register(client: TestClient, username: str, email: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "qq_email": email,
            "password": "correct-horse-15",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())["account"]["id"]


def _seed(client: TestClient, account_id: str) -> dict[str, str]:
    service = client.app.state.atomic_profile_service
    cat = service.remember(account_id, CAT_TEXT, topics=["兴趣爱好"])
    half_hour = service.remember(account_id, HALF_HOUR_TEXT, topics=["现实约束"])
    return {
        "cat": cat.profile_item_id,
        "half_hour": half_hour.profile_item_id,
    }


def test_overview_route_returns_traceable_statements() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue15-list", "150001@qq.com")
    ids = _seed(client, account_id)

    response = client.get("/profiles/overview")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "current"
    assert body["usage_enabled"] is True
    assert body["notice"] is None
    assert body["generated_at"]
    segments = [
        segment
        for statement in body["statements"]
        for segment in statement["segments"]
    ]
    assert {segment["profile_item_id"] for segment in segments} == set(
        ids.values()
    )
    for statement in body["statements"]:
        assert statement["text"] == "；".join(
            segment["text"] for segment in statement["segments"]
        )
    cat_statement = next(
        statement
        for statement in body["statements"]
        if statement["topic"] == "兴趣与喜好"
    )
    assert cat_statement["text"] == CAT_TEXT


def test_overview_route_follows_edit_and_delete_without_stale_content() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue15-edit", "150002@qq.com")
    ids = _seed(client, account_id)
    assert client.get("/profiles/overview").status_code == 200

    service = client.app.state.atomic_profile_service
    cat = service.get_item(account_id, ids["cat"])
    edited = client.patch(
        f"/profiles/items/{ids['cat']}",
        json={"text": "我养了一只猫叫小白", "version": cat.version},
    )
    assert edited.status_code == 200, edited.text

    joined = "".join(
        statement["text"]
        for statement in client.get("/profiles/overview").json()["statements"]
    )
    assert "小白" in joined and CAT_TEXT not in joined

    cat = service.get_item(account_id, ids["cat"])
    deleted = client.request(
        "DELETE",
        f"/profiles/items/{ids['cat']}",
        json={"version": cat.version},
    )
    assert deleted.status_code == 204, deleted.text
    joined = "".join(
        statement["text"]
        for statement in client.get("/profiles/overview").json()["statements"]
    )
    assert "小白" not in joined
    assert HALF_HOUR_TEXT in joined


def test_overview_route_requires_login() -> None:
    client = TestClient(create_app())
    response = client.get("/profiles/overview")
    assert response.status_code == 401, response.text


def test_overview_route_is_account_scoped() -> None:
    client = TestClient(create_app())
    alice = _register(client, "issue15-alice", "150003@qq.com")
    _seed(client, alice)
    bob = _register(client, "issue15-bob", "150004@qq.com")
    bob_item = client.app.state.atomic_profile_service.remember(
        bob, "我叫小王", topics=["称呼"]
    )

    bob_body = client.get("/profiles/overview").json()
    relogin = client.post(
        "/auth/login",
        json={"identifier": "issue15-alice", "password": "correct-horse-15"},
    )
    assert relogin.status_code == 200, relogin.text
    alice_body = client.get("/profiles/overview").json()
    alice_ids = {
        segment["profile_item_id"]
        for statement in alice_body["statements"]
        for segment in statement["segments"]
    }
    bob_ids = {
        segment["profile_item_id"]
        for statement in bob_body["statements"]
        for segment in statement["segments"]
    }
    assert bob_item.profile_item_id in bob_ids
    assert bob_item.profile_item_id not in alice_ids
    assert not (alice_ids & bob_ids)


def test_controls_route_invalidates_overview_and_keeps_management() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue15-controls", "150005@qq.com")
    _seed(client, account_id)
    overview = client.app.state.profile_overview_service

    first = client.get("/profiles/overview").json()
    assert first["state"] == "current"
    stored_before, _ = overview.begin_overview_rebuild(account_id)
    assert stored_before is not None

    off = client.put("/profiles/controls", json={"usage_enabled": False})
    assert off.status_code == 200, off.text

    after = client.get("/profiles/overview").json()
    assert after["usage_enabled"] is False
    assert after["notice"]
    # 关闭使用仍可查看与管理（与原子记忆的使用控制边界一致）。
    assert after["statements"]
    stored_after, _ = overview.begin_overview_rebuild(account_id)
    assert stored_after is not None
    assert stored_after.epoch > stored_before.epoch
