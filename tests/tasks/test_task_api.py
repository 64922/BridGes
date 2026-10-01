"""跨轮任务 API 集成测试（改进工单 08）。

使用临时 sqlite 数据库构建应用；覆盖：

- API 多轮重放：末尾条件、纠正、话题往返、等待错接、两个任务并存；
- 账户隔离：另一账户看不到、也改不了别人的任务；
- 乐观版本冲突返回 409；
- 断开进程（重建应用指向同一数据库）后任务投影仍可恢复。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.auth import SESSION_COOKIE_NAME
from bridges.api.main import create_app
from bridges.config import get_settings


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "task-api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return {"tmp_path": tmp_path, "app": create_app()}


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"task_user_{tag}",
            "qq_email": f"12345678{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _new_client(app: Any) -> TestClient:
    return TestClient(app)


def _create_conversation(client: TestClient) -> str:
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    subject = client.app.state.identity_service.resolve_session(token).subject
    conversation = client.app.state.chat_service.create_conversation(subject.account_id)
    return conversation.conversation_id


def _turn(client: TestClient, conversation_id: str, **body: Any) -> dict[str, Any]:
    response = client.post(
        "/tasks/turns",
        json={"conversation_id": conversation_id, **body},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_multi_turn_replay_end_to_end(app_env: dict[str, Any]) -> None:
    app = app_env["app"]
    client = _new_client(app)
    _register(client, "1")
    conversation_id = _create_conversation(client)

    # 轮 1：选购任务，长消息末尾预算。
    first = _turn(
        client, conversation_id,
        user_message_id="u1", relation="new", goal="推荐一台笔记本电脑",
        conditions=[
            {
                "kind": "budget", "text": "预算 5,000 元",
                "source_message_id": "u1", "source_span": "预算 5,000 元",
            },
            {
                "kind": "exclusion", "text": "不要二手",
                "scope": "conversation", "source_message_id": "u1",
            },
        ],
    )
    shopping_id = first["task"]["task"]["task_id"]
    assert first["created"] is True
    assert [c["text"] for c in first["task"]["effective_conditions"]] == ["预算 5,000 元"]

    # 轮 2：纠正为 3,000，本轮即生效。
    second = _turn(
        client, conversation_id,
        user_message_id="u2", relation="revise",
        conditions=[
            {
                "kind": "budget", "text": "预算 3,000 元",
                "source_message_id": "u2", "source_span": "改成 3,000",
            }
        ],
    )
    assert [c["text"] for c in second["task"]["effective_conditions"]] == ["预算 3,000 元"]

    # 轮 3：换话题到通勤（两个任务并存），旧任务暂停、预算不携带。
    third = _turn(
        client, conversation_id,
        user_message_id="u3", relation="new", goal="校园通勤路线", is_new_topic=True,
    )
    commute_id = third["task"]["task"]["task_id"]
    assert commute_id != shopping_id
    assert third["paused_task_ids"] == [shopping_id]
    assert third["task"]["effective_conditions"] == []
    assert [c["text"] for c in third["task"]["conversation_conditions"]] == ["不要二手"]

    # 轮 4：返回选购，恢复最新有效值 3,000，不复活 5,000。
    fourth = _turn(
        client, conversation_id,
        user_message_id="u4", relation="continue", explicit_task_id=shopping_id,
    )
    assert [c["text"] for c in fourth["task"]["effective_conditions"]] == ["预算 3,000 元"]
    assert fourth["task"]["task"]["status"] == "active"

    # 两个任务并存：会话内可见两条任务投影。
    listing = client.get(f"/tasks/conversations/{conversation_id}")
    assert listing.status_code == 200
    assert {item["task"]["task_id"] for item in listing.json()} == {
        shopping_id, commute_id
    }

    # 版本不可变：选购任务有两个版本。
    versions = client.get(f"/tasks/{shopping_id}/versions")
    assert [v["version"] for v in versions.json()] == [1, 2]

    # 条件审计：旧值只读保留为 superseded。
    conditions = client.get(f"/tasks/{shopping_id}/conditions").json()
    superseded = [c for c in conditions if c["status"] == "superseded"]
    assert [c["text"] for c in superseded] == ["预算 5,000 元"]


def test_plain_chat_does_not_create_task(app_env: dict[str, Any]) -> None:
    app = app_env["app"]
    client = _new_client(app)
    _register(client, "2")
    conversation_id = _create_conversation(client)
    result = _turn(
        client, conversation_id,
        user_message_id="u1", relation="new",
    )
    assert result["created"] is False
    assert client.get(f"/tasks/conversations/{conversation_id}").json() == []


def test_wait_mismatch_then_cancel_releases(app_env: dict[str, Any]) -> None:
    app = app_env["app"]
    client = _new_client(app)
    _register(client, "3")
    conversation_id = _create_conversation(client)
    created = _turn(
        client, conversation_id,
        user_message_id="u1", relation="new", goal="推荐笔记本",
    )
    task_id = created["task"]["task"]["task_id"]

    # 通过 API 登记等待（等待→答复→完成端到端贯通）。
    opened = client.post(
        "/tasks/waits",
        json={
            "conversation_id": conversation_id,
            "question": "你的预算大概是多少？",
            "missing_fields": ["budget"],
            "origin_message_id": "a1",
            "source_message_id": "u1",
        },
    )
    assert opened.status_code == 200, opened.text
    wait = opened.json()
    assert wait["status"] == "open"
    assert wait["expected_version"] == 1

    # 换话题后再说「好的」：不误填等待。
    mismatch = _turn(
        client, conversation_id,
        user_message_id="u2", relation="continue", explicit_task_id=task_id,
        answer_fields=[], answer_text="好的",
    )
    assert mismatch["wait_resolution"] in {"none", "rejected"}
    assert mismatch["resolved_wait"] is None

    # 取消任务：等待进入终态并释放资源。
    cancelled = _turn(
        client, conversation_id,
        user_message_id="u3", relation="cancel", explicit_task_id=task_id,
    )
    assert cancelled["task"]["task"]["status"] == "cancelled"
    waits = client.get(f"/tasks/{task_id}/waits").json()
    terminal = next(w for w in waits if w["wait_id"] == wait["wait_id"])
    assert terminal["status"] == "expired"
    assert terminal["released_at"] is not None

    # 已取消目标不能续接（409）。
    conflict = client.post(
        "/tasks/turns",
        json={
            "conversation_id": conversation_id,
            "user_message_id": "u4",
            "relation": "continue",
            "explicit_task_id": task_id,
        },
    )
    assert conflict.status_code == 409


def test_account_isolation_and_optimistic_conflict(app_env: dict[str, Any]) -> None:
    app = app_env["app"]
    owner = _new_client(app)
    _register(owner, "4")
    conversation_id = _create_conversation(owner)
    created = _turn(
        owner, conversation_id,
        user_message_id="u1", relation="new", goal="推荐笔记本",
    )
    task_id = created["task"]["task"]["task_id"]

    # 乐观版本冲突：期望版本 0，实际为 1。
    conflict = owner.post(
        "/tasks/turns",
        json={
            "conversation_id": conversation_id,
            "user_message_id": "u2",
            "relation": "revise",
            "expected_version": 0,
            "conditions": [
                {"kind": "budget", "text": "预算 3,000 元", "source_message_id": "u2"}
            ],
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "task_version_conflict"

    # 另一个账户：看不到、也改不了。
    other = _new_client(app)
    _register(other, "5")
    assert other.get(f"/tasks/{task_id}").status_code == 404
    assert other.get(f"/tasks/conversations/{conversation_id}").json() == []
    forbidden = other.post(
        "/tasks/turns",
        json={
            "conversation_id": conversation_id,
            "user_message_id": "u9",
            "relation": "continue",
            "explicit_task_id": task_id,
        },
    )
    assert forbidden.status_code == 404


def test_tasks_survive_process_restart(app_env: dict[str, Any]) -> None:
    app = app_env["app"]
    client = _new_client(app)
    _register(client, "6")
    conversation_id = _create_conversation(client)
    created = _turn(
        client, conversation_id,
        user_message_id="u1", relation="new", goal="推荐笔记本",
        conditions=[
            {"kind": "budget", "text": "预算 5,000 元", "source_message_id": "u1"}
        ],
    )
    task_id = created["task"]["task"]["task_id"]

    # 断开进程：用同一数据库重建应用与客户端（模拟重启）。
    restarted = create_app()
    client2 = TestClient(restarted)
    # 复用同一会话 Cookie（设备会话随数据库持久化）。
    client2.cookies.set(SESSION_COOKIE_NAME, client.cookies.get(SESSION_COOKIE_NAME))
    recovered = client2.get(f"/tasks/{task_id}")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["task"]["goal"] == "推荐笔记本"
    assert [c["text"] for c in recovered.json()["effective_conditions"]] == ["预算 5,000 元"]
