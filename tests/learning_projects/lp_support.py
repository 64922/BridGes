"""Issue 19：文件夹式学习项目测试共享辅助（唯一模块名，避免跨目录导入冲突）。"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi.testclient import TestClient
from pydantic import SecretStr

from bridges.chat.repository import MessageRecord
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.contracts.credentials import ProbeRecord, ProbeStatus
from bridges.credentials.store import InMemoryCredentialStore


def register_account(client: TestClient, tag: str = "1") -> dict[str, Any]:
    """注册一个测试账户；tag 决定用户名与 QQ 邮箱（QQ 号必须为纯数字）。"""
    response = client.post(
        "/auth/register",
        json={
            "username": f"lp_user_{tag}",
            "qq_email": f"24681357{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def make_capability_ready(app: Any, account_id: str) -> None:
    """注入"已配置 Key + 探测可用"（真实探测需要网络与真实 Key）。"""
    credential_service = app.state.credential_service
    credential_service._store = InMemoryCredentialStore()
    credential_service._store.save(account_id, SecretStr("sk-test-dummy"))
    credential_service._probes._put_record(
        account_id,
        ProbeRecord(
            probe_id=f"probe-{account_id}",
            capability_id="chat",
            model_id="qwen3.7-plus-2026-05-26",
            region="cn-beijing",
            parameters={},
            status=ProbeStatus.AVAILABLE,
            probed_at=datetime.now(UTC),
        ),
    )


def create_project(
    client: TestClient, name: str = "高等数学", description: str | None = None
) -> dict[str, Any]:
    """创建一个学习项目并断言 201，返回摘要投影。"""
    payload: dict[str, Any] = {"name": name}
    if description is not None:
        payload["description"] = description
    response = client.post("/learning-projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_conversation(
    client: TestClient, *, project_id: str | None = None, title: str | None = None
) -> str:
    """创建一个对话（可选归属学习项目）并返回对话标识。"""
    payload: dict[str, Any] = {}
    if project_id is not None:
        payload["project_id"] = project_id
    if title is not None:
        payload["title"] = title
    response = client.post("/chat/conversations", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def upload_project_file(
    client: TestClient,
    project_id: str,
    filename: str,
    content: bytes,
    *,
    content_type: str = "text/plain",
) -> Any:
    """按原始字节约定上传一份项目文件。"""
    return client.post(
        f"/learning-projects/{project_id}/files",
        content=content,
        headers={
            "Content-Type": content_type,
            "X-Bridges-Filename": quote(filename, safe=""),
        },
    )


def insert_message(
    app: Any,
    account_id: str,
    conversation_id: str,
    *,
    status: ChatMessageStatus,
    content: str = "消息内容",
    role: ChatMessageRole = ChatMessageRole.ASSISTANT,
) -> str:
    """直接向仓库写入一条消息（用于构造 streaming 等特定状态）。"""
    repo = app.state.chat_service._repo
    now = datetime.now(UTC)
    message_id = secrets.token_urlsafe(12)
    repo.insert_message(
        MessageRecord(
            message_id=message_id,
            conversation_id=conversation_id,
            account_id=account_id,
            role=role,
            attempt_number=1,
            status=status,
            content=content,
            thinking=None,
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            created_at=now,
            updated_at=now,
        )
    )
    return message_id
