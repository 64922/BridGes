"""Issue 08：无类别原子画像列表的公开读／改／删合同。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.chat import ChatMode


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """挂载 sqlite bridges.db 的应用：原子条目与四维记录都落盘（生产同形）。"""

    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue08-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


def _register(client: TestClient, username: str, email: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "qq_email": email,
            "password": "correct-horse-08",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())["account"]["id"]


def _service(client: TestClient) -> Any:
    return client.app.state.atomic_profile_service


def test_items_route_is_category_free_and_carries_no_internal_keys() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue08-atomic-list", "080001@qq.com")
    created = _service(client).remember(
        account_id, "我在准备雅思考试", source_message_id="message-1"
    )

    response = client.get("/profiles/items")

    assert response.status_code == 200, response.text
    items = response.json()
    assert [item["profile_item_id"] for item in items] == [created.profile_item_id]
    assert items[0]["text"] == "我在准备雅思考试"
    # 行内编辑与删除需要版本号；页面不暴露类别、主题提示或去重哈希。
    assert items[0]["version"] == 1
    for forbidden in (
        "dimension",
        "category",
        "topic_hint",
        "identity_key",
        "owner_account_id",
        "status",
    ):
        assert forbidden not in items[0]


def test_modify_route_saves_user_authoritative_text() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue08-atomic-modify", "080002@qq.com")
    created = _service(client).remember(account_id, "我喜欢跑步")
    version = created.version

    response = client.patch(
        f"/profiles/items/{created.profile_item_id}",
        json={"text": "我每周三和周六晚上跑步", "version": version},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "我每周三和周六晚上跑步"
    assert body["version"] == version + 1
    assert body["user_edited_at"] is not None
    assert body["write_origin"] == "user"


def test_modify_route_reports_version_conflict_and_duplicate_text() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue08-atomic-conflict", "080003@qq.com")
    service = _service(client)
    first = service.remember(account_id, "我养了一只叫团子的猫")
    second = service.remember(account_id, "我住在合肥")

    stale = client.patch(
        f"/profiles/items/{first.profile_item_id}",
        json={"text": "我养了两只猫", "version": first.version + 5},
    )
    duplicate = client.patch(
        f"/profiles/items/{second.profile_item_id}",
        json={"text": "我养了一只叫团子的猫", "version": second.version},
    )

    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["error"] == "atomic_profile_conflict"
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["detail"]["error"] == "atomic_profile_conflict"


def test_delete_route_writes_tombstone_and_hides_the_row() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue08-atomic-delete", "080004@qq.com")
    service = _service(client)
    item = service.remember(account_id, "我对花生过敏")

    response = client.request(
        "DELETE",
        f"/profiles/items/{item.profile_item_id}",
        json={"version": item.version},
    )
    remaining = client.get("/profiles/items")

    assert response.status_code == 204, response.text
    assert remaining.status_code == 200
    assert remaining.json() == []
    # 列表不展示墓碑，但墓碑本身留档：同键条目的自动抽取不会让它复活。
    assert service.list_items(account_id) == []
    assert [entry.status.value for entry in service._repository.list_items(
        account_id, include_withdrawn=True
    )] == ["withdrawn"]


def test_items_routes_are_account_scoped() -> None:
    client = TestClient(create_app())
    owner = _register(client, "issue08-atomic-owner", "080005@qq.com")
    # 第二个账户只为把会话切到「非属主」身份；它的 id 不参与断言。
    _register(client, "issue08-atomic-outsider", "080006@qq.com")
    item = _service(client).remember(owner, "我拿到了驾照")

    listed = client.get("/profiles/items")
    modified = client.patch(
        f"/profiles/items/{item.profile_item_id}",
        json={"text": "我拿到了 C1 驾照", "version": item.version},
    )
    deleted = client.request(
        "DELETE",
        f"/profiles/items/{item.profile_item_id}",
        json={"version": item.version},
    )

    # 当前请求主体是后注册的账户，看不到也改不动前一个账户的条目。
    assert listed.json() == []
    assert modified.status_code == 404, modified.text
    assert modified.json()["detail"]["error"] == "atomic_profile_not_found"
    assert deleted.status_code == 404, deleted.text
    assert client.get("/profiles/items").json() == []


def test_modify_route_rejects_blank_text() -> None:
    client = TestClient(create_app())
    account_id = _register(client, "issue08-atomic-blank", "080007@qq.com")
    item = _service(client).remember(account_id, "我习惯早起")

    response = client.patch(
        f"/profiles/items/{item.profile_item_id}",
        json={"text": "   ", "version": item.version},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"] == "atomic_profile_modify_failed"


def test_sqlite_composition_extracts_mirrors_and_deletes_in_one_pass(
    sqlite_app: Any,
) -> None:
    """落盘组合下：抽取镜像与删除墓碑都真实写入，且不因嵌套事务失败。"""

    client = TestClient(sqlite_app)
    account_id = _register(client, "issue08-sqlite", "080008@qq.com")
    conversation = sqlite_app.state.chat_service.create_conversation(
        account_id, mode=ChatMode.COMPANION
    )
    created = client.post(
        f"/chat/conversations/{conversation.conversation_id}/messages",
        json={"content": "我的目标是今年通过雅思考试"},
    )
    assert created.status_code == 200, created.text

    items = client.get("/profiles/items").json()
    assert [item["text"] for item in items] == ["今年通过雅思考试"]
    records = client.get("/profiles/four-dimensions").json()
    assert [record["content"] for record in records] == ["今年通过雅思考试"]

    removed = client.request(
        "DELETE",
        f"/profiles/items/{items[0]['profile_item_id']}",
        json={"version": items[0]["version"]},
    )

    assert removed.status_code == 204, removed.text
    assert client.get("/profiles/items").json() == []
    # 底层四维记录同步撤回：自动抽取不能把用户删掉的条目再写回来。
    assert client.get("/profiles/four-dimensions").json() == []
    status = client.get("/profiles/status").json()
    assert status["has_records"] is False
