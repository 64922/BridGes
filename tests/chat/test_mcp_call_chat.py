"""聊天内 MCP 插件调用测试（Issue 36）。

覆盖：选中 MCP 后可经真实消息调用（send 携带 mcp_call → 真实 invoke
→ SSE mcp_call 事件 → 消息投影持久化）；未选中 MCP 拒绝调用（422/error
mcp_not_selected，绝不绕过选择器）；敏感操作挂起 → chat 域确认路由
approve/deny → 消息投影写回最终结果；刷新/恢复历史对话结果不丢失；
跨账户确认 404；调用失败原因可恢复（不伪造成功）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.mcp.fixtures import ECHO_YAML, NOTE_YAML

ECHO = "bridges-echo"
NOTE = "bridges-note"


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
            "username": f"mcp_chat_user_{tag}",
            "qq_email": f"12345677{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _install_mcp(client: TestClient, yaml_text: str, filename: str) -> None:
    response = client.post(
        "/mcp/install",
        content=yaml_text.encode("utf-8"),
        headers={"X-Bridges-Filename": filename},
    )
    assert response.status_code == 200, response.text


def _create_conversation(
    client: TestClient, selection: list[dict[str, str]] | None = None
) -> str:
    body: dict[str, Any] = {}
    if selection is not None:
        body["plugin_selection"] = selection
    response = client.post("/chat/conversations", json=body)
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line]
        event_name = None
        data: list[str] = []
        for line in lines:
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:"):].strip())
        if event_name and data:
            events.append((event_name, json.loads("\n".join(data))))
    return events


def _send_mcp_call(
    sqlite_app: Any,
    client: TestClient,
    conversation_id: str,
    mcp_id: str,
    tool: str,
    input_data: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Issue 02：发送 MCP 调用消息 → 驱动执行器 → 订阅运行事件。"""
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": f"调用 {mcp_id} 的 {tool} 工具",
            "mcp_call": {
                "mcp_id": mcp_id,
                "tool": tool,
                "input": input_data,
                "data_slice": {"text": "这是本次授权的内容", "attachments": []},
            },
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()
    sqlite_app.state.generation_executor.run_tick()

    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream(
        "GET",
        f"/chat/conversations/{conversation_id}/messages/"
        f"{created['assistant_message']['message_id']}/events",
        params={"cursor": 0},
    ) as stream:
        body = "\n".join(stream.iter_lines())
    events.extend(_parse_sse(body))
    return events


def test_selected_mcp_call_succeeds(sqlite_app: Any, client: TestClient) -> None:
    """选中 MCP 后真实调用成功：SSE mcp_call 事件 + 消息投影持久化。"""
    _register(client)
    _install_mcp(client, ECHO_YAML, "echo.yaml")
    conversation_id = _create_conversation(
        client, [{"kind": "mcp", "plugin_id": ECHO}]
    )
    events = _send_mcp_call(
        sqlite_app, client, conversation_id, ECHO, "echo", {"question": "你好"}
    )
    names = [name for name, _ in events]
    assert "started" in names
    assert "mcp_call" in names
    assert "done" in names
    call_event = next(data for name, data in events if name == "mcp_call")
    assert call_event["call"]["status"] == "succeeded"
    assert call_event["call"]["tool"] == "echo"
    assert "这是本次授权的内容" in call_event["call"]["result_summary"]
    done_event = next(data for name, data in events if name == "done")
    message = done_event["message"]
    assert message["mcp_call"]["status"] == "succeeded"
    assert message["mcp_call"]["mcp_name"] == "回显演示"
    # 刷新/恢复历史对话：结果不丢失
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    assistant = next(
        m for m in response.json()["messages"] if m["role"] == "assistant"
    )
    assert assistant["mcp_call"]["status"] == "succeeded"


def test_unselected_mcp_call_rejected(sqlite_app: Any, client: TestClient) -> None:
    """未选择的 MCP 调用被拒绝（mcp_not_selected），绝不绕过选择器。"""
    _register(client)
    _install_mcp(client, ECHO_YAML, "echo.yaml")
    conversation_id = _create_conversation(client)
    events = _send_mcp_call(
        sqlite_app, client, conversation_id, ECHO, "echo", {"question": "你好"}
    )
    error_event = next(
        (data for name, data in events if name == "error"), None
    )
    assert error_event is not None
    assert error_event["error"]["code"] == "mcp_not_selected"
    # 消息收敛为错误态，不伪造成功结果
    response = client.get(f"/chat/conversations/{conversation_id}")
    assistant = next(
        m for m in response.json()["messages"] if m["role"] == "assistant"
    )
    assert assistant["status"] == "error"
    assert assistant["mcp_call"] is None


def test_sensitive_pending_approve_flow(
    sqlite_app: Any, client: TestClient, tmp_path: Path
) -> None:
    """敏感操作挂起 → chat 域确认 → 结果写回消息投影（刷新可恢复）。"""
    _register(client)
    write_dir = tmp_path / "note-out"
    write_dir.mkdir()
    _install_mcp(
        client,
        NOTE_YAML.format(write_dir=str(write_dir)),
        "note.yaml",
    )
    conversation_id = _create_conversation(
        client, [{"kind": "mcp", "plugin_id": NOTE}]
    )
    target_path = write_dir / "note.txt"
    events = _send_mcp_call(
        sqlite_app, client,
        conversation_id,
        NOTE,
        "note",
        {"path": str(target_path)},
    )
    call_event = next(data for name, data in events if name == "mcp_call")
    assert call_event["call"]["status"] == "sensitive_pending"
    confirmation = call_event["call"]["confirmation"]
    assert confirmation is not None
    assert confirmation["confirmation_id"]
    assert "写入文件" in confirmation["target"]
    # 消息投影携带确认载荷
    response = client.get(f"/chat/conversations/{conversation_id}")
    assistant = next(
        m for m in response.json()["messages"] if m["role"] == "assistant"
    )
    assert assistant["mcp_call"]["status"] == "sensitive_pending"
    assert assistant["mcp_call"]["confirmation"]["confirmation_id"] == (
        confirmation["confirmation_id"]
    )
    # approve：chat 域路由调真实确认并写回最终结果
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{assistant['message_id']}"
        f"/mcp/confirmations/{confirmation['confirmation_id']}/approve"
    )
    assert response.status_code == 200, response.text
    assert response.json()["mcp_call"]["status"] == "succeeded"
    assert "note.txt" in response.json()["mcp_call"]["result_summary"]
    assert (write_dir / "note.txt").exists()
    # 刷新后仍是最终结果（不丢失）
    response = client.get(f"/chat/conversations/{conversation_id}")
    assistant = next(
        m for m in response.json()["messages"] if m["role"] == "assistant"
    )
    assert assistant["mcp_call"]["status"] == "succeeded"


def test_sensitive_pending_deny_flow(
    sqlite_app: Any, client: TestClient, tmp_path: Path
) -> None:
    """拒绝敏感操作：调用安全终止并落库 denied 终态。"""
    _register(client)
    write_dir = tmp_path / "note-out-deny"
    write_dir.mkdir()
    _install_mcp(
        client,
        NOTE_YAML.format(write_dir=str(write_dir)),
        "note.yaml",
    )
    conversation_id = _create_conversation(
        client, [{"kind": "mcp", "plugin_id": NOTE}]
    )
    deny_path = write_dir / "deny.txt"
    events = _send_mcp_call(
        sqlite_app, client,
        conversation_id,
        NOTE,
        "note",
        {"path": str(deny_path)},
    )
    call_event = next(data for name, data in events if name == "mcp_call")
    confirmation = call_event["call"]["confirmation"]
    assert confirmation is not None
    response = client.get(f"/chat/conversations/{conversation_id}")
    assistant = next(
        m for m in response.json()["messages"] if m["role"] == "assistant"
    )
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{assistant['message_id']}"
        f"/mcp/confirmations/{confirmation['confirmation_id']}/deny"
    )
    assert response.status_code == 200, response.text
    assert response.json()["mcp_call"]["status"] == "denied"
    assert "已拒绝" in response.json()["mcp_call"]["error_message"]
    assert not (write_dir / "deny.txt").exists()


def test_mcp_call_conflicts_with_image_payload(
    sqlite_app: Any, client: TestClient
) -> None:
    """MCP 调用与图片载荷互斥：并发携带 422 拒绝，不静默丢弃。

    GQ-04 起图片载荷无账户凭据门禁，新账户直接可达互斥校验。
    """
    _register(client)
    _install_mcp(client, ECHO_YAML, "echo.yaml")
    conversation_id = _create_conversation(
        client, [{"kind": "mcp", "plugin_id": ECHO}]
    )
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "图片与 MCP 并发",
            "image": {"kind": "generate", "prompt": "一张图"},
            "mcp_call": {
                "mcp_id": ECHO,
                "tool": "echo",
                "input": {},
                "data_slice": {"text": "x", "attachments": []},
            },
        },
    )
    assert response.status_code == 422, response.text
    message = response.json()["detail"]["message"]
    assert "互斥" in message or "不能同时" in message


def test_cross_account_confirmation_404(
    sqlite_app: Any, client: TestClient, tmp_path: Path
) -> None:
    """跨账户确认他人消息的敏感操作统一 404（不泄漏存在性）。"""
    _register(client, tag="1")
    write_dir = tmp_path / "note-out-cross"
    write_dir.mkdir()
    _install_mcp(
        client,
        NOTE_YAML.format(write_dir=str(write_dir)),
        "note.yaml",
    )
    conversation_id = _create_conversation(
        client, [{"kind": "mcp", "plugin_id": NOTE}]
    )
    events = _send_mcp_call(
        sqlite_app, client,
        conversation_id,
        NOTE,
        "note",
        {"path": str(write_dir / "cross.txt")},
    )
    call_event = next(data for name, data in events if name == "mcp_call")
    confirmation = call_event["call"]["confirmation"]
    assert confirmation is not None
    response = client.get(f"/chat/conversations/{conversation_id}")
    assistant = next(
        m for m in response.json()["messages"] if m["role"] == "assistant"
    )
    message_id = assistant["message_id"]
    client.post("/auth/logout")
    _register(client, tag="2")
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}"
        f"/mcp/confirmations/{confirmation['confirmation_id']}/approve"
    )
    assert response.status_code == 404
