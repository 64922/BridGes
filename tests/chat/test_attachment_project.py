"""聊天附件项目归属测试（Issue 36「新附件归属」）。

覆盖：会话归属学习项目后，新上传聊天附件的摄取记录携带 project_id
（纳入项目检索范围）；检索项目层同时包含项目文件与归属聊天附件；
清除项目归属后新上传附件不再携带旧项目上下文；跨账户不泄漏归属。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _register(client: TestClient, tag: str = "1") -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"attachment_project_user_{tag}",
            "qq_email": f"12345676{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _create_project(client: TestClient, name: str = "项目甲") -> str:
    response = client.post(
        "/learning-projects", json={"name": name}
    )
    assert response.status_code == 201, response.text
    return response.json()["project_id"]


def _create_conversation(client: TestClient, project_id: str | None = None) -> str:
    body: dict[str, Any] = {}
    if project_id is not None:
        body["project_id"] = project_id
    response = client.post("/chat/conversations", json=body)
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def _upload_attachment(client: TestClient, conversation_id: str, content: str) -> str:
    response = client.post(
        f"/chat/conversations/{conversation_id}/attachments",
        content=content.encode("utf-8"),
        headers={"X-Bridges-Filename": quote("材料.txt")},
    )
    assert response.status_code == 201, response.text
    return response.json()["object_id"]


def _document_row(sqlite_app: Any, object_id: str, account_id: str) -> dict[str, Any]:
    database = sqlite_app.state.chat_attachment_service._database
    row = database.scoped(account_id).execute(
        "SELECT source, project_id FROM document_records"
        " WHERE object_id = ? AND account_id = ?",
        (object_id, account_id),
    ).fetchone()
    return {"source": str(row["source"]), "project_id": row["project_id"]}


def test_attachment_carries_conversation_project(
    sqlite_app: Any, client: TestClient
) -> None:
    """会话归属项目后，新上传聊天附件携带项目归属（纳入项目检索）。"""
    account = _register(client)
    project_id = _create_project(client)
    conversation_id = _create_conversation(client, project_id=project_id)
    object_id = _upload_attachment(client, conversation_id, "项目材料的正文内容")
    row = _document_row(sqlite_app, object_id, account["id"])
    assert row["source"] == "chat_attachment"
    assert row["project_id"] == project_id


def test_attachment_without_project_stays_unbound(
    sqlite_app: Any, client: TestClient
) -> None:
    """未归属项目的会话：新附件不携带任何项目上下文。"""
    account = _register(client)
    conversation_id = _create_conversation(client)
    object_id = _upload_attachment(client, conversation_id, "独立内容")
    row = _document_row(sqlite_app, object_id, account["id"])
    assert row["source"] == "chat_attachment"
    assert row["project_id"] is None


def test_cleared_project_attachment_not_carried(
    sqlite_app: Any, client: TestClient
) -> None:
    """清除项目归属后：新上传附件不再携带旧项目上下文。"""
    account = _register(client)
    project_id = _create_project(client)
    conversation_id = _create_conversation(client, project_id=project_id)
    # 清除归属（显式 null）
    response = client.patch(
        f"/chat/conversations/{conversation_id}",
        json={"project_id": None},
    )
    assert response.status_code == 200, response.text
    object_id = _upload_attachment(client, conversation_id, "清除后的内容")
    row = _document_row(sqlite_app, object_id, account["id"])
    assert row["project_id"] is None


def test_project_layer_includes_chat_attachments(
    sqlite_app: Any, client: TestClient
) -> None:
    """检索项目层同时包含项目文件与归属聊天附件（ready 文档集）。"""
    account = _register(client)
    project_id = _create_project(client)
    conversation_id = _create_conversation(client, project_id=project_id)
    object_id = _upload_attachment(client, conversation_id, "可检索的项目材料")
    # 项目文件（同项目另一来源）
    response = client.post(
        f"/learning-projects/{project_id}/files",
        content="项目专用文件内容".encode("utf-8"),
        headers={"X-Bridges-Filename": quote("项目文件.md")},
    )
    assert response.status_code == 201, response.text
    # 模拟后台摄取完成：把两份文档置为 ready（worker 行为不在本切片）。
    database = sqlite_app.state.chat_attachment_service._database
    with database.transaction():
        database.scoped(account["id"]).execute(
            "UPDATE document_records SET status = 'ready', updated_at = ?"
            " WHERE account_id = ? AND project_id = ?",
            ("2026-08-06T00:00:00+00:00", account["id"], project_id),
        )
    retrieval = sqlite_app.state.retrieval_service
    layers = retrieval._resolve_layers(
        account["id"],
        attachment_ids=[],
        project_id=project_id,
        use_knowledge_base=False,
    )
    ready = layers["project"]["ready_document_ids"]
    assert ready, "项目层应有已就绪文档"
    # 归属聊天附件与项目文件都应在项目层候选集合内（Issue 36）
    rows = database.scoped(account["id"]).execute(
        "SELECT document_id, source FROM document_records"
        " WHERE account_id = ? AND project_id = ?",
        (account["id"], project_id),
    ).fetchall()
    assert len(rows) >= 2, "项目层应有项目文件与归属聊天附件两份文档"
    assert all(
        str(row["document_id"]) in ready for row in rows
    ), "项目层候选应包含归属聊天附件与项目文件"


def test_cross_account_attachment_project_hidden(
    sqlite_app: Any, client: TestClient
) -> None:
    """跨账户上传到他人项目会话被拒绝（统一 404，不泄漏归属）。"""
    account_a = _register(client, tag="1")
    project_id = _create_project(client)
    conversation_id = _create_conversation(client, project_id=project_id)
    client.post("/auth/logout")
    account_b = _register(client, tag="2")
    response = client.post(
        f"/chat/conversations/{conversation_id}/attachments",
        content="越权内容".encode("utf-8"),
        headers={"X-Bridges-Filename": quote("越权.txt")},
    )
    assert response.status_code == 404
    assert account_a["id"] != account_b["id"]
