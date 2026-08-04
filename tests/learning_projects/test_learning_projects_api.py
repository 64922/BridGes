"""Issue 19：学习项目 CRUD、对话归属与级联删除的 API 集成测试。"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lp_support import (
    create_conversation,
    create_project,
    insert_message,
    make_capability_ready,
    register_account,
    upload_project_file,
)

from bridges.contracts.chat import ChatMessageStatus


def test_project_crud_happy_path(client: TestClient) -> None:
    register_account(client, "1")
    created = create_project(client, "  高等数学  ", "考研复习")
    assert created["name"] == "高等数学"
    assert created["description"] == "考研复习"
    assert created["conversation_count"] == 0
    assert created["file_count"] == 0
    project_id = created["project_id"]
    assert created["created_at"] == created["updated_at"]

    listing = client.get("/learning-projects")
    assert listing.status_code == 200, listing.text
    assert [p["project_id"] for p in listing.json()["projects"]] == [project_id]

    detail = client.get(f"/learning-projects/{project_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == "高等数学"
    assert detail.json()["description"] == "考研复习"
    assert detail.json()["conversations"] == []

    renamed = client.patch(
        f"/learning-projects/{project_id}",
        json={"name": "线性代数", "description": "基础课"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "线性代数"
    assert renamed.json()["description"] == "基础课"
    assert renamed.json()["updated_at"] >= created["updated_at"]

    # 显式 null 清空描述；缺省字段保持不变
    cleared = client.patch(f"/learning-projects/{project_id}", json={"description": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["description"] == ""
    assert cleared.json()["name"] == "线性代数"

    deleted = client.delete(f"/learning-projects/{project_id}")
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"/learning-projects/{project_id}").status_code == 404
    assert client.get("/learning-projects").json()["projects"] == []


def test_project_validation_errors(client: TestClient) -> None:
    register_account(client, "1")
    assert client.post("/learning-projects", json={"name": "   "}).status_code == 422
    assert client.post("/learning-projects", json={}).status_code == 422
    assert client.post("/learning-projects", json={"name": "x" * 121}).status_code == 422

    project = create_project(client)
    project_id = project["project_id"]
    # 空更新体：至少提供一个字段
    assert client.patch(f"/learning-projects/{project_id}", json={}).status_code == 422
    assert (
        client.patch(f"/learning-projects/{project_id}", json={"name": "  "}).status_code
        == 422
    )
    # 显式 name: null 非法（名称永远不能为空）；缺省才表示保持不变
    assert (
        client.patch(f"/learning-projects/{project_id}", json={"name": None}).status_code
        == 422
    )
    assert (
        client.patch(
            f"/learning-projects/{project_id}",
            json={"name": None, "description": "新描述"},
        ).status_code
        == 422
    )
    # 被拒绝的请求不产生任何写入
    assert client.get(f"/learning-projects/{project_id}").json()["name"] == project["name"]


def test_project_list_orders_by_recent_update(client: TestClient) -> None:
    register_account(client, "1")
    first = create_project(client, "项目一")
    # Windows 时钟粒度约 15ms：隔开创建/更新，确保 updated_at 可比较
    time.sleep(0.05)
    second = create_project(client, "项目二")
    listing = client.get("/learning-projects").json()["projects"]
    assert [p["project_id"] for p in listing] == [
        second["project_id"],
        first["project_id"],
    ]
    # 更新项目一后排到最前
    time.sleep(0.05)
    client.patch(f"/learning-projects/{first['project_id']}", json={"name": "项目一改"})
    listing = client.get("/learning-projects").json()["projects"]
    assert [p["project_id"] for p in listing] == [
        first["project_id"],
        second["project_id"],
    ]


def test_summary_counts_conversations_and_files(client: TestClient) -> None:
    register_account(client, "1")
    project = create_project(client)
    project_id = project["project_id"]
    create_conversation(client, project_id=project_id)
    create_conversation(client, project_id=project_id)
    uploaded = upload_project_file(client, project_id, "笔记.txt", b"hello world")
    assert uploaded.status_code == 201, uploaded.text

    summary = client.get("/learning-projects").json()["projects"][0]
    assert summary["conversation_count"] == 2
    assert summary["file_count"] == 1


def test_create_conversation_with_project_id_uses_learning_projects(
    client: TestClient, sqlite_app: Any
) -> None:
    register_account(client, "1")
    project = create_project(client)
    created = client.post(
        "/chat/conversations",
        json={"project_id": project["project_id"], "mode": "study"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["project_id"] == project["project_id"]
    assert created.json()["mode"] == "study"

    missing = client.post("/chat/conversations", json={"project_id": "missing-project"})
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "project_not_found"
    assert missing.json()["detail"]["message"] == "学习项目不存在或没有访问权限。"

    bob_client = TestClient(sqlite_app)
    register_account(bob_client, "2")
    cross = bob_client.post(
        "/chat/conversations", json={"project_id": project["project_id"]}
    )
    assert cross.status_code == 404
    assert cross.json()["detail"]["error"] == "project_not_found"


def test_move_conversation_between_projects_and_detach(
    client: TestClient,
) -> None:
    register_account(client, "1")
    project_a = create_project(client, "项目甲")
    project_b = create_project(client, "项目乙")
    conversation_id = create_conversation(
        client, project_id=project_a["project_id"], title="讨论"
    )

    detail_a = client.get(f"/learning-projects/{project_a['project_id']}").json()
    assert [c["conversation_id"] for c in detail_a["conversations"]] == [conversation_id]
    assert detail_a["conversations"][0]["title"] == "讨论"

    moved = client.patch(
        f"/chat/conversations/{conversation_id}",
        json={"project_id": project_b["project_id"]},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["project_id"] == project_b["project_id"]
    assert (
        client.get(f"/learning-projects/{project_a['project_id']}").json()["conversations"]
        == []
    )
    detail_b = client.get(f"/learning-projects/{project_b['project_id']}").json()
    assert [c["conversation_id"] for c in detail_b["conversations"]] == [conversation_id]

    # 缺省 project_id 字段时归属不变（只改标题）
    renamed = client.patch(
        f"/chat/conversations/{conversation_id}", json={"title": "新标题"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["project_id"] == project_b["project_id"]

    # 显式 null 解除归属
    detached = client.patch(
        f"/chat/conversations/{conversation_id}", json={"project_id": None}
    )
    assert detached.status_code == 200, detached.text
    assert detached.json()["project_id"] is None
    assert (
        client.get(f"/learning-projects/{project_b['project_id']}").json()["conversations"]
        == []
    )

    # 移动目标必须存在
    missing = client.patch(
        f"/chat/conversations/{conversation_id}", json={"project_id": "missing-project"}
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "project_not_found"

    # 对话必须属于当前账户
    unknown = client.patch(
        "/chat/conversations/unknown-conversation",
        json={"project_id": project_a["project_id"]},
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["error"] == "conversation_not_found"


def test_move_preserves_messages_mode_events_and_attachments(
    client: TestClient, sqlite_app: Any
) -> None:
    account = register_account(client, "1")
    make_capability_ready(sqlite_app, account["id"])
    project_a = create_project(client, "项目甲")
    project_b = create_project(client, "项目乙")
    conversation_id = create_conversation(client, project_id=project_a["project_id"])

    switched = client.post(
        f"/chat/conversations/{conversation_id}/mode", json={"mode": "study"}
    )
    assert switched.status_code == 200, switched.text

    content = b"%PDF-1.7\nprivate study notes"
    uploaded = client.post(
        f"/chat/conversations/{conversation_id}/attachments",
        content=content,
        headers={
            "Content-Type": "application/pdf",
            "X-Bridges-Filename": "notes.pdf",
            "X-Bridges-Upload-Id": "lp-move-upload",
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    object_id = uploaded.json()["object_id"]
    sent = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "请阅读这个附件", "attachment_ids": [object_id]},
    )
    assert sent.status_code == 200, sent.text

    before = client.get(f"/chat/conversations/{conversation_id}").json()
    moved = client.patch(
        f"/chat/conversations/{conversation_id}",
        json={"project_id": project_b["project_id"]},
    )
    assert moved.status_code == 200, moved.text
    after = client.get(f"/chat/conversations/{conversation_id}").json()

    # 移动只改变归属：消息、模式事件与附件投影原样保留
    assert after["project_id"] == project_b["project_id"]
    assert after["messages"] == before["messages"]
    assert after["mode_events"] == before["mode_events"]
    user_message = next(m for m in after["messages"] if m["role"] == "user")
    assert user_message["attachments"][0]["object_id"] == object_id

    # 解除归属同样不触碰消息历史
    client.patch(f"/chat/conversations/{conversation_id}", json={"project_id": None})
    detached = client.get(f"/chat/conversations/{conversation_id}").json()
    assert detached["messages"] == before["messages"]
    assert detached["mode_events"] == before["mode_events"]


def test_delete_keep_detaches_conversations_and_removes_files(
    client: TestClient, sqlite_app: Any
) -> None:
    account = register_account(client, "1")
    project = create_project(client)
    project_id = project["project_id"]
    conversation_id = create_conversation(client, project_id=project_id)
    insert_message(
        sqlite_app,
        account["id"],
        conversation_id,
        status=ChatMessageStatus.DONE,
        content="保留我",
    )
    uploaded = upload_project_file(client, project_id, "笔记.txt", b"hello world")
    assert uploaded.status_code == 201, uploaded.text
    object_id = uploaded.json()["object_id"]

    deleted = client.delete(f"/learning-projects/{project_id}?contents=keep")
    assert deleted.status_code == 204, deleted.text

    assert client.get(f"/learning-projects/{project_id}").status_code == 404
    conversation = client.get(f"/chat/conversations/{conversation_id}")
    assert conversation.status_code == 200, conversation.text
    assert conversation.json()["project_id"] is None
    assert [m["content"] for m in conversation.json()["messages"]] == ["保留我"]
    # 项目文件随项目删除（不属于"保留的对话内容"）
    assert (
        client.get(
            f"/learning-projects/{project_id}/files/{object_id}/download"
        ).status_code
        == 404
    )


def test_delete_contents_delete_removes_everything(
    client: TestClient, sqlite_app: Any
) -> None:
    account = register_account(client, "1")
    make_capability_ready(sqlite_app, account["id"])
    project = create_project(client)
    project_id = project["project_id"]
    conversation_id = create_conversation(client, project_id=project_id)

    content = b"%PDF-1.7\nattachment body"
    uploaded = client.post(
        f"/chat/conversations/{conversation_id}/attachments",
        content=content,
        headers={
            "Content-Type": "application/pdf",
            "X-Bridges-Filename": "attach.pdf",
            "X-Bridges-Upload-Id": "lp-delete-upload",
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment_id = uploaded.json()["object_id"]
    sent = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "请阅读", "attachment_ids": [attachment_id]},
    )
    assert sent.status_code == 200, sent.text

    project_file = upload_project_file(client, project_id, "资料.txt", b"project file")
    assert project_file.status_code == 201, project_file.text
    file_object_id = project_file.json()["object_id"]

    deleted = client.delete(f"/learning-projects/{project_id}?contents=delete")
    assert deleted.status_code == 204, deleted.text

    assert client.get(f"/learning-projects/{project_id}").status_code == 404
    assert client.get(f"/chat/conversations/{conversation_id}").status_code == 404
    assert (
        client.get(
            f"/chat/conversations/{conversation_id}/attachments/{attachment_id}/download"
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/learning-projects/{project_id}/files/{file_object_id}/download"
        ).status_code
        == 404
    )
    assert client.get("/chat/conversations").json()["conversations"] == []


def test_delete_rolls_back_on_mid_delete_failure(
    client: TestClient, sqlite_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_account(client, "1")
    project = create_project(client)
    project_id = project["project_id"]
    conversation_id = create_conversation(client, project_id=project_id)
    uploaded = upload_project_file(client, project_id, "笔记.txt", b"hello world")
    assert uploaded.status_code == 201, uploaded.text
    object_id = uploaded.json()["object_id"]

    def _boom(account_id: str, document_id: str) -> None:
        raise RuntimeError("模拟级联中途失败")

    service = sqlite_app.state.learning_project_service
    monkeypatch.setattr(service._ingestion, "purge_document_rows", _boom)

    failing_client = TestClient(sqlite_app, raise_server_exceptions=False)
    failing_client.cookies.set("bridges_session", client.cookies.get("bridges_session"))
    response = failing_client.delete(f"/learning-projects/{project_id}?contents=delete")
    assert response.status_code == 500

    # 事务整体回滚：项目、对话归属与文件全部保持原状，无半删除状态
    monkeypatch.undo()
    assert client.get(f"/learning-projects/{project_id}").status_code == 200
    conversation = client.get(f"/chat/conversations/{conversation_id}")
    assert conversation.status_code == 200
    assert conversation.json()["project_id"] == project_id
    download = client.get(f"/learning-projects/{project_id}/files/{object_id}/download")
    assert download.status_code == 200
    assert download.content == b"hello world"


def test_delete_contents_delete_refuses_while_streaming(
    client: TestClient, sqlite_app: Any
) -> None:
    account = register_account(client, "1")
    project = create_project(client)
    project_id = project["project_id"]
    conversation_id = create_conversation(client, project_id=project_id)
    insert_message(
        sqlite_app,
        account["id"],
        conversation_id,
        status=ChatMessageStatus.STREAMING,
        content="生成中",
    )

    refused = client.delete(f"/learning-projects/{project_id}?contents=delete")
    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "generation_in_progress"
    # 项目与对话均未受影响
    assert client.get(f"/learning-projects/{project_id}").status_code == 200
    detail = client.get(f"/learning-projects/{project_id}").json()
    assert [c["conversation_id"] for c in detail["conversations"]] == [conversation_id]


def test_cross_account_access_is_uniform_404(
    client: TestClient, sqlite_app: Any
) -> None:
    register_account(client, "1")
    project = create_project(client)
    project_id = project["project_id"]
    conversation_id = create_conversation(client, project_id=project_id)
    uploaded = upload_project_file(client, project_id, "笔记.txt", b"hello world")
    assert uploaded.status_code == 201, uploaded.text
    object_id = uploaded.json()["object_id"]

    bob_client = TestClient(sqlite_app)
    register_account(bob_client, "2")
    bob_project = create_project(bob_client, "乙的项目")
    bob_conversation_id = create_conversation(bob_client)

    assert bob_client.get(f"/learning-projects/{project_id}").status_code == 404
    assert (
        bob_client.patch(
            f"/learning-projects/{project_id}", json={"name": "越权"}
        ).status_code
        == 404
    )
    assert bob_client.delete(f"/learning-projects/{project_id}").status_code == 404
    assert bob_client.get(f"/learning-projects/{project_id}/files").status_code == 404
    assert (
        upload_project_file(
            bob_client, project_id, "越权.txt", b"cross account"
        ).status_code
        == 404
    )
    assert (
        bob_client.get(
            f"/learning-projects/{project_id}/files/{object_id}/download"
        ).status_code
        == 404
    )
    assert (
        bob_client.delete(
            f"/learning-projects/{project_id}/files/{object_id}"
        ).status_code
        == 404
    )
    # 错误体统一为 404 而非 403，不泄漏资源是否存在
    detail = bob_client.get(f"/learning-projects/{project_id}").json()["detail"]
    assert detail["error"] == "project_not_found"

    # Bob 不能把 Alice 的对话挂到自己的项目（对话不属于 Bob）
    moved = bob_client.patch(
        f"/chat/conversations/{conversation_id}",
        json={"project_id": bob_project["project_id"]},
    )
    assert moved.status_code == 404
    assert moved.json()["detail"]["error"] == "conversation_not_found"
    # Bob 也不能把自己的对话挂到 Alice 的项目
    cross = bob_client.patch(
        f"/chat/conversations/{bob_conversation_id}",
        json={"project_id": project_id},
    )
    assert cross.status_code == 404
    assert cross.json()["detail"]["error"] == "project_not_found"

    # Alice 的项目、对话与文件均未受影响
    assert client.get(f"/learning-projects/{project_id}").status_code == 200
    assert client.get(f"/chat/conversations/{conversation_id}").json()[
        "project_id"
    ] == project_id
    assert (
        client.get(
            f"/learning-projects/{project_id}/files/{object_id}/download"
        ).status_code
        == 200
    )


def test_patch_title_and_project_applies_atomically(client: TestClient) -> None:
    register_account(client, "1")
    project = create_project(client, "项目甲")
    conversation_id = create_conversation(client, title="旧标题")

    response = client.patch(
        f"/chat/conversations/{conversation_id}",
        json={"title": "新标题", "pinned": True, "project_id": project["project_id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "新标题"
    assert body["pinned"] is True
    assert body["project_id"] == project["project_id"]

    detail = client.get(f"/learning-projects/{project['project_id']}").json()
    assert [c["conversation_id"] for c in detail["conversations"]] == [conversation_id]
    assert detail["conversations"][0]["title"] == "新标题"
    assert detail["conversations"][0]["pinned"] is True


def test_patch_invalid_title_leaves_project_membership_unchanged(
    client: TestClient,
) -> None:
    """组合 PATCH 校验失败时整体拒绝：不得留下"已移动但未改名"的半更新。"""
    register_account(client, "1")
    project = create_project(client, "项目甲")
    conversation_id = create_conversation(client, title="旧标题")

    response = client.patch(
        f"/chat/conversations/{conversation_id}",
        json={"title": "   ", "project_id": project["project_id"]},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "invalid_title"

    conversation = client.get(f"/chat/conversations/{conversation_id}").json()
    assert conversation["title"] == "旧标题"
    assert conversation["project_id"] is None
    assert (
        client.get(f"/learning-projects/{project['project_id']}").json()["conversations"]
        == []
    )


def test_patch_mid_write_failure_rolls_back_title_and_project(
    client: TestClient, sqlite_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写入中途失败时标题与项目归属都保持原状（单事务整体回滚）。"""
    register_account(client, "1")
    project = create_project(client, "项目甲")
    conversation_id = create_conversation(client, title="旧标题")

    from bridges.storage.database import ScopedConnection

    real_execute = ScopedConnection.execute

    def _boom(self: Any, sql: str, params: Any = None) -> Any:
        if sql.lstrip().upper().startswith("UPDATE CONVERSATIONS"):
            raise RuntimeError("模拟写入中途失败")
        return real_execute(self, sql, params)

    monkeypatch.setattr(ScopedConnection, "execute", _boom)

    failing_client = TestClient(sqlite_app, raise_server_exceptions=False)
    failing_client.cookies.set("bridges_session", client.cookies.get("bridges_session"))
    response = failing_client.patch(
        f"/chat/conversations/{conversation_id}",
        json={"title": "新标题", "project_id": project["project_id"]},
    )
    assert response.status_code == 500

    monkeypatch.undo()
    conversation = client.get(f"/chat/conversations/{conversation_id}").json()
    assert conversation["title"] == "旧标题"
    assert conversation["project_id"] is None
    assert (
        client.get(f"/learning-projects/{project['project_id']}").json()["conversations"]
        == []
    )
