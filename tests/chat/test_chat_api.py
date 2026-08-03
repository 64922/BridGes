"""聊天 API 集成测试（Issue 11）。

使用临时 sqlite 数据库构建应用；探测状态与凭据通过服务内部注入
（真实探测需要网络与真实 Key，协议级替身验证流式边界是自动测试的
正确形态）。覆盖：能力预检门、SSE 事件序列、停止、重试、账户隔离、
错误分类与重启恢复。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterError, RateLimitError
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.streaming import StreamChunk
from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.credentials import ProbeRecord, ProbeStatus
from bridges.credentials.store import InMemoryCredentialStore


def _chat_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_text_chat",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-completion-v1",
    )


class _ProgrammableStreamAdapter:
    """可编程流式适配器：多块增量、连接错误或慢速流。"""

    def __init__(
        self,
        chunks: list[StreamChunk] | None = None,
        connect_error: AdapterError | None = None,
        slow: bool = False,
    ) -> None:
        self._chunks = chunks or []
        self._connect_error = connect_error
        self._slow = slow

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> Any:
        from bridges.ai.adapters import AdapterResult

        return AdapterResult(
            actual_model_id=capability.model_id,
            output={
                "content": "".join(c.delta for c in self._chunks if c.kind == "delta")
            },
        )

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ):
        if self._connect_error is not None:
            raise self._connect_error
        for chunk in self._chunks:
            if self._slow:
                time.sleep(0.02)
            yield chunk


def _gateway_with(adapter: Any) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return gateway


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """构建挂载 sqlite bridges.db 的应用（聊天服务随数据库启用）。"""
    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _register(client: TestClient, tag: str = "1") -> dict[str, Any]:
    """注册一个测试账户；tag 决定用户名与 QQ 邮箱（QQ 号必须为纯数字）。"""
    response = client.post(
        "/auth/register",
        json={
            "username": f"chat_user_{tag}",
            "qq_email": f"12345678{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _make_capability_ready(
    sqlite_app: Any, account_id: str, status: ProbeStatus = ProbeStatus.AVAILABLE
) -> None:
    """注入"已配置 Key + 指定探测状态"（真实探测需要网络与真实 Key）。"""
    credential_service = sqlite_app.state.credential_service
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
            status=status,
            probed_at=datetime.now(UTC),
            error_message="测试原因。" if status == ProbeStatus.UNAVAILABLE else None,
        ),
    )


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


def _create_conversation(client: TestClient) -> str:
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


# ---------------------------------------------------------------------------
# 能力预检门
# ---------------------------------------------------------------------------


def test_send_without_key_returns_actionable_error_and_no_user_message(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "你好"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "no_api_key"
    assert "Qwen API Key" in detail["message"]
    # 未插入任何用户消息：失败不重复占位
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assert history["messages"] == []


def test_send_while_probing_returns_409(client: TestClient, sqlite_app: Any) -> None:
    account = _register(client)
    _make_capability_ready(sqlite_app, account["id"], ProbeStatus.PROBING)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "你好"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "capability_probing"
    assert "探测中" in response.json()["detail"]["message"]


def test_send_unavailable_capability_returns_reason(
    client: TestClient, sqlite_app: Any
) -> None:
    account = _register(client)
    _make_capability_ready(sqlite_app, account["id"], ProbeStatus.UNAVAILABLE)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "你好"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "capability_unavailable"
    assert "测试原因" in detail["message"]


def test_chat_requires_authentication(client: TestClient) -> None:
    anonymous = TestClient(client.app)
    response = anonymous.get("/chat/conversations")
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "unauthenticated"


# ---------------------------------------------------------------------------
# 流式主链路
# ---------------------------------------------------------------------------


def test_send_streams_started_delta_done_and_persists_history(
    client: TestClient, sqlite_app: Any
) -> None:
    account = _register(client)
    _make_capability_ready(sqlite_app, account["id"])
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "你好，介绍一下你自己"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert [e[0] for e in events] == ["started", "delta", "done"]
    started = events[0][1]
    assert started["conversation_id"] == conversation_id
    assert started["attempt_number"] == 1
    message_id = started["message_id"]
    assert events[1][1]["message_id"] == message_id
    assert events[1][1]["delta"]  # 替身回答有正文
    done = events[2][1]
    assert done["message"]["status"] == "done"
    assert done["message"]["run_lock_id"]
    assert done["message"]["model_id"] == "qwen3.7-plus-2026-05-26"

    # 标题由首条消息推导
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assert history["title"].startswith("你好，介绍一下你自己")
    roles = [(m["role"], m["status"]) for m in history["messages"]]
    assert roles == [("user", "done"), ("assistant", "done")]


def test_send_multi_delta_streaming_via_programmable_adapter(
    client: TestClient, sqlite_app: Any
) -> None:
    """真实流式适配器形态：多个 delta 增量逐步呈现。"""
    account = _register(client)
    _make_capability_ready(sqlite_app, account["id"])
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter(
            [
                StreamChunk(kind="delta", delta="第一"),
                StreamChunk(kind="delta", delta="第二"),
                StreamChunk(kind="delta", delta="第三"),
            ]
        )
    )
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "你好"},
    )
    events = _parse_sse(response.text)
    assert [e[0] for e in events] == ["started", "delta", "delta", "delta", "done"]
    assert "".join(e[1]["delta"] for e in events if e[0] == "delta") == "第一第二第三"


def test_send_failure_streams_error_event_and_persists_actionable_message(
    client: TestClient, sqlite_app: Any
) -> None:
    account = _register(client)
    _make_capability_ready(sqlite_app, account["id"])
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter(connect_error=RateLimitError("slow down"))
    )
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "你好"},
    )
    events = _parse_sse(response.text)
    assert [e[0] for e in events] == ["started", "error"]
    error = events[1][1]["error"]
    assert error["code"] == "rate_limit"
    assert "限流" in error["message"]
    assert error["retryable"] is True

    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = history["messages"][1]
    assert assistant["status"] == "error"
    assert assistant["error_code"] == "rate_limit"
    assert "限流" in (assistant["error_message"] or "")


def test_sse_always_terminates_when_message_finalized_before_stream(
    client: TestClient, sqlite_app: Any
) -> None:
    """消息在生成器启动前被停止：SSE 以诚实终态事件结束，绝不悬挂。"""
    account = _register(client)
    _make_capability_ready(sqlite_app, account["id"])
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter(
            [StreamChunk(kind="delta", delta=f"块{i}") for i in range(20)],
            slow=True,
        )
    )
    conversation_id = _create_conversation(client)
    control_client = TestClient(sqlite_app)
    control_client.cookies.set("bridges_session", client.cookies.get("bridges_session"))

    collected: list[str] = []

    def consume() -> None:
        with client.stream(
            "POST",
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "开始生成"},
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line:
                    collected.append(line)

    thread = threading.Thread(target=consume)
    thread.start()
    time.sleep(0.1)
    history = control_client.get(f"/chat/conversations/{conversation_id}").json()
    message_id = history["messages"][1]["message_id"]
    stopped = control_client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
    )
    assert stopped.status_code == 200
    thread.join(timeout=10)
    body = "\n".join(collected)
    assert "event: started" in body
    # 无论生成器是否来得及启动，SSE 都以 error（stopped）终态结束
    assert "event: error" in body
    assert '"code": "stopped"' in body


def test_stop_generation_via_api(client: TestClient, sqlite_app: Any) -> None:
    account = _register(client)
    _make_capability_ready(sqlite_app, account["id"])
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter(
            [StreamChunk(kind="delta", delta=f"块{i}") for i in range(30)],
            slow=True,
        )
    )
    conversation_id = _create_conversation(client)

    # 流式消费与停止控制必须使用独立的 TestClient：同一 httpx 客户端并发
    # 请求会互相干扰（Cookie 仓库等共享状态），导致流被误判为断流。
    control_client = TestClient(sqlite_app)
    control_client.cookies.set(
        "bridges_session", client.cookies.get("bridges_session")
    )

    collected: list[str] = []

    def consume() -> None:
        with client.stream(
            "POST",
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "开始生成"},
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line and line.startswith("event: delta"):
                    collected.append(line)

    thread = threading.Thread(target=consume)
    thread.start()
    time.sleep(0.15)
    history = control_client.get(f"/chat/conversations/{conversation_id}").json()
    message_id = history["messages"][1]["message_id"]
    stopped = control_client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
    )
    thread.join(timeout=10)
    assert stopped.status_code == 200
    assert stopped.json()["message"]["status"] == "stopped"
    assert len(collected) > 0  # 已收到部分增量
    final = control_client.get(f"/chat/conversations/{conversation_id}").json()["messages"][1]
    assert final["status"] == "stopped"
    assert final["content"]  # 已接收正文保留


def test_retry_via_api_creates_new_attempt(client: TestClient, sqlite_app: Any) -> None:
    account = _register(client)
    _make_capability_ready(sqlite_app, account["id"])
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter(connect_error=RateLimitError("slow"))
    )
    conversation_id = _create_conversation(client)
    client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "帮我分析"},
    )
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    failed_id = history["messages"][1]["message_id"]

    # 换成成功适配器后重试
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter([StreamChunk(kind="delta", delta="重试后的回答")])
    )
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{failed_id}/retry"
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[0][1]["attempt_number"] == 2
    assert [e[0] for e in events] == ["started", "delta", "done"]

    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistants = [m for m in history["messages"] if m["role"] == "assistant"]
    assert [m["attempt_number"] for m in assistants] == [1, 2]
    assert assistants[0]["status"] == "error"  # 历史失败尝试原样保留
    assert assistants[1]["status"] == "done"
    assert assistants[1]["content"] == "重试后的回答"


def test_conversation_not_found_is_404(client: TestClient, sqlite_app: Any) -> None:
    account = _register(client)
    _make_capability_ready(sqlite_app, account["id"])
    response = client.get("/chat/conversations/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "conversation_not_found"
    response = client.post(
        "/chat/conversations/does-not-exist/messages", json={"content": "你好"}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 账户隔离
# ---------------------------------------------------------------------------


def test_second_account_cannot_read_subscribe_retry_or_probe(
    client: TestClient, sqlite_app: Any
) -> None:
    alice = _register(client, "1")
    _make_capability_ready(sqlite_app, alice["id"])
    conversation_id = _create_conversation(client)
    client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "爱丽丝的私密问题"},
    )

    bob_client = TestClient(sqlite_app)
    bob = _register(bob_client, "2")
    # Bob 具备完整可用能力，隔离判定不受"未配置 Key"预检门干扰
    _make_capability_ready(sqlite_app, bob["id"])
    assert bob_client.get("/chat/conversations").json()["conversations"] == []
    assert bob_client.get(f"/chat/conversations/{conversation_id}").status_code == 404
    assert (
        bob_client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "窥探"},
        ).status_code
        == 404
    )
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant_id = history["messages"][1]["message_id"]
    assert (
        bob_client.post(
            f"/chat/conversations/{conversation_id}/messages/{assistant_id}/retry"
        ).status_code
        == 404
    )
    assert (
        bob_client.post(
            f"/chat/conversations/{conversation_id}/messages/{assistant_id}/stop"
        ).status_code
        == 404
    )
    # 列表也不泄漏标题
    bob_list = bob_client.get("/chat/conversations").json()
    assert all("爱丽丝" not in c["title"] for c in bob_list["conversations"])


# ---------------------------------------------------------------------------
# 重启恢复
# ---------------------------------------------------------------------------


def test_restart_runtime_restores_same_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完整运行时重启：同一数据库文件重新打开后历史一致。"""
    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app1 = create_app()
    client1 = TestClient(app1)
    account = _register(client1, "7")
    _make_capability_ready(app1, account["id"])
    conversation_id = _create_conversation(client1)
    client1.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "重启前的问题"},
    )

    # 重启：同一环境重建应用
    get_settings.cache_clear()
    app2 = create_app()
    client2 = TestClient(app2)
    client2.cookies.set("bridges_session", client1.cookies.get("bridges_session"))

    history = client2.get(f"/chat/conversations/{conversation_id}")
    assert history.status_code == 200
    body = history.json()
    assert body["title"].startswith("重启前的问题")
    assert [(m["role"], m["status"]) for m in body["messages"]] == [
        ("user", "done"),
        ("assistant", "done"),
    ]
    # 消息顺序与正文一致
    assert body["messages"][0]["content"] == "重启前的问题"
    assert body["messages"][1]["content"]
    # 对话列表同样恢复
    listing = client2.get("/chat/conversations").json()
    assert listing["conversations"][0]["conversation_id"] == conversation_id


# ---------------------------------------------------------------------------
# 未启用持久化（内存模式）
# ---------------------------------------------------------------------------


def test_chat_unavailable_in_memory_mode() -> None:
    """未配置数据库时聊天服务不挂载，返回明确的 503 而不静默降级。"""
    response = TestClient(create_app()).get("/chat/conversations")
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "chat_unavailable"
