"""聊天 API 集成测试（Issue 11）。

使用临时 sqlite 数据库构建应用；模型调用由 test 环境的确定性适配器
驱动（Stub/可编程替身），不依赖账户凭据或探测状态（GQ-02：主对话
链路不再检查账户 Key/探测快照，新账户无需任何个人 Qwen 配置即可
发送）。覆盖：SSE 事件序列、停止、重试、账户隔离、错误分类与重启恢复。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterError, RateLimitError, StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.api.auth import SESSION_COOKIE_NAME
from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.ai import CapabilityKind, CapabilityRecord


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
    session_token = client.cookies.get(SESSION_COOKIE_NAME)
    assert session_token is not None
    subject = client.app.state.identity_service.resolve_session(session_token).subject
    conversation = client.app.state.chat_service.create_conversation(subject.account_id)
    return conversation.conversation_id


def _send(
    client: TestClient, conversation_id: str, content: str = "你好", **extra: Any
) -> dict[str, Any]:
    """Issue 02：发送消息 → 创建响应（消息已落库、运行已入队，无 SSE 流）。"""
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _subscribe_all(
    client: TestClient,
    conversation_id: str,
    message_id: str,
    cursor: int = 0,
    timeout: float = 15.0,
) -> list[tuple[str, dict[str, Any]]]:
    """订阅运行事件直到流结束（运行终态），返回事件序列（跳过心跳）。

    订阅端点在运行终态时回放完全部事件即结束；运行进行中则长轮询
    等待（心跳保活）。超时抛错，绝不悬挂。
    """
    events: list[tuple[str, dict[str, Any]]] = []
    deadline = time.monotonic() + timeout
    next_cursor = cursor
    while time.monotonic() < deadline:
        with client.stream(
            "GET",
            f"/chat/conversations/{conversation_id}/messages/{message_id}/events",
            params={"cursor": next_cursor},
        ) as response:
            assert response.status_code == 200, response.text
            body = "\n".join(response.iter_lines())
        for name, payload in _parse_sse(body):
            if name == "ping":
                continue
            events.append((name, payload))
        if body.strip():
            break
        time.sleep(0.05)
    if not body.strip():
        raise AssertionError("订阅流在超时前未结束（运行未终态）。")
    return events


def _drive_executor(sqlite_app: Any, *, timeout: float = 10.0) -> None:
    """同步驱动后台执行器直到没有待处理运行（test 环境无自动线程）。"""
    executor = sqlite_app.state.generation_executor
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        executor.run_tick()
        if "无待处理" in executor._last_summary:  # noqa: SLF001 - 测试读取摘要
            return
        time.sleep(0.02)
    raise AssertionError("执行器未在超时前完成运行。")


def _start_executor_thread(sqlite_app: Any) -> tuple[threading.Event, threading.Thread]:
    """启动后台执行器线程（真实反馈环测试：订阅与执行并发）。"""
    stop = threading.Event()
    thread = threading.Thread(
        target=sqlite_app.state.generation_executor.run_loop,
        kwargs={"stop": stop},
        daemon=True,
    )
    thread.start()
    return stop, thread


# ---------------------------------------------------------------------------
# Issue 05：首轮提交后锁定对话模式
# ---------------------------------------------------------------------------


def test_create_conversation_requires_first_turn_for_both_modes(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client)
    default = client.post("/chat/conversations", json={"title": "日常对话"})
    assert default.status_code == 409
    assert default.json()["detail"]["error"] == "first_turn_required"

    study = client.post("/chat/conversations", json={"title": "学习对话", "mode": "study"})
    assert study.status_code == 409
    assert study.json()["detail"]["error"] == "first_turn_required"

    listing = client.get("/chat/conversations").json()["conversations"]
    assert listing == []


def test_conversation_lifecycle_api_is_persistent_and_account_scoped(
    client: TestClient, sqlite_app: Any
) -> None:
    alice = _register(client, "31")
    conversation_id = _create_conversation(client)

    updated = client.patch(
        f"/chat/conversations/{conversation_id}",
        json={"title": "新的标题", "pinned": True},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "新的标题"
    assert updated.json()["pinned"] is True
    assert updated.json()["mode"] == "companion"

    listed = client.get("/chat/conversations")
    assert listed.status_code == 200
    assert listed.json()["conversations"][0]["conversation_id"] == conversation_id
    assert listed.json()["conversations"][0]["pinned"] is True

    bob_client = TestClient(sqlite_app)
    _register(bob_client, "32")
    assert bob_client.patch(
        f"/chat/conversations/{conversation_id}",
        json={"title": "越权改名"},
    ).status_code == 404
    assert bob_client.delete(f"/chat/conversations/{conversation_id}").status_code == 404

    deleted = client.delete(f"/chat/conversations/{conversation_id}")
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"/chat/conversations/{conversation_id}").status_code == 404
    assert client.get("/chat/conversations").json()["conversations"] == []
    assert alice["id"]


def test_first_turn_locks_mode_atomically_and_replays_same_idempotency_key(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client)
    conversation_id = _create_conversation(client)
    first = client.post(
        "/chat/first-turn",
        json={
            "conversation_id": conversation_id,
            "content": "首轮日常问题",
            "idempotency_key": "issue05-first-turn-1",
            "mode": "companion",
        },
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["conversation"]["mode"] == "companion"
    assert body["conversation"]["mode_locked"] is True
    assert body["conversation"]["mode_events"] == []
    assert len(body["conversation"]["messages"]) == 2

    replay = client.post(
        "/chat/first-turn",
        json={
            "conversation_id": conversation_id,
            "content": "首轮日常问题",
            "idempotency_key": "issue05-first-turn-1",
            "mode": "companion",
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["conversation"]["conversation_id"] == conversation_id
    assert len(replay.json()["conversation"]["messages"]) == 2

    conflict = client.post(
        "/chat/first-turn",
        json={
            "conversation_id": conversation_id,
            "content": "不能切换到学习",
            "idempotency_key": "issue05-first-turn-2",
            "mode": "study",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "conversation_mode_locked"

    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assert history["mode"] == "companion"
    assert history["mode_locked"] is True
    assert history["mode_events"] == []


def test_locked_mode_is_account_scoped(client: TestClient, sqlite_app: Any) -> None:
    _register(client, "51")
    conversation_id = _create_conversation(client)
    first = client.post(
        "/chat/first-turn",
        json={
            "conversation_id": conversation_id,
            "content": "账户隔离日常问题",
            "idempotency_key": "issue05-account-scope-1",
            "mode": "companion",
        },
    )
    assert first.status_code == 201

    bob = TestClient(sqlite_app)
    _register(bob, "52")
    assert bob.get(f"/chat/conversations/{conversation_id}").status_code == 404
    blocked = bob.post(
        "/chat/first-turn",
        json={
            "conversation_id": conversation_id,
            "content": "不应读取其他账户模式",
            "idempotency_key": "issue05-account-scope-2",
            "mode": "companion",
        },
    )
    assert blocked.status_code == 404
    assert blocked.json()["detail"]["error"] == "conversation_not_found"


def test_legacy_mode_switch_returns_410_and_records_privacy_safe_observation(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client)
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/mode", json={"mode": "study"}
    )
    assert response.status_code == 410
    assert response.json()["detail"] == {
        "error": "conversation_mode_switch_retired",
        "message": "模式已在首条消息提交时锁定；请新建另一个会话以使用其他模式。",
    }

    probe = client.post(
        f"/chat/conversations/{conversation_id}/mode",
        json={"mode": "companion"},
        headers={"X-Bridges-Compatibility-Probe": "1"},
    )
    assert probe.status_code == 410
    observations = sqlite_app.state.observability_service.compatibility_gate_snapshot()
    assert observations == [
        {
            "endpoint_id": "chat.conversation_mode_switch",
            "service_version": "0.1.0",
            "traffic_class": "real",
            "status_code": 410,
            "count": 1,
        },
        {
            "endpoint_id": "chat.conversation_mode_switch",
            "service_version": "0.1.0",
            "traffic_class": "probe",
            "status_code": 410,
            "count": 1,
        },
    ]


# ---------------------------------------------------------------------------
# GQ-02：新账户无需任何个人 Qwen 配置即可聊天
# ---------------------------------------------------------------------------


def test_new_account_without_any_key_can_send_and_receive_answer(
    client: TestClient, sqlite_app: Any
) -> None:
    """全新注册账户（无 Key、无密钥元数据、无探测记录）直接走通生成。"""
    _register(client)
    # GQ-07 后组合根不再持有账户 Qwen 凭据服务或探测服务（GQ-02 AC1：
    # 新账户无需任何个人 Qwen 配置即可聊天，账户级密钥机制已整体删除）
    assert not hasattr(sqlite_app.state, "credential_service")
    assert not hasattr(sqlite_app.state, "credential_store")
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "你好")
    assert created["run_id"]
    assert created["cursor"] >= 1
    assert created["user_message"]["status"] == "done"
    assert created["assistant_message"]["status"] == "streaming"
    # 后台执行器领取执行
    _drive_executor(sqlite_app)
    events = _subscribe_all(
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    assert [e[0] for e in events if e[0] not in ("stage", "node")] == ["started", "delta", "done"]
    done = [e for e in events if e[0] == "done"][-1][1]
    assert done["message"]["status"] == "done"
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assert [m["status"] for m in history["messages"]] == ["done", "done"]


def test_chat_requires_authentication(client: TestClient) -> None:
    anonymous = TestClient(client.app)
    response = anonymous.get("/chat/conversations")
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "unauthenticated"


# ---------------------------------------------------------------------------
# 生成主链路（持久化运行 + 事件订阅）
# ---------------------------------------------------------------------------


def test_send_streams_started_delta_done_and_persists_history(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client)
    conversation_id = _create_conversation(client)

    created = _send(client, conversation_id, "你好，介绍一下你自己")
    assert created["cursor"] == 1  # started 已持久化；无画像通知时不追加 profile
    assert created["assistant_message"]["attempt_number"] == 1
    message_id = created["assistant_message"]["message_id"]
    _drive_executor(sqlite_app)

    events = _subscribe_all(client, conversation_id, message_id)
    # Issue 06：阶段事件与 started/delta/done 同一事件流；断言只看
    # 内容事件（stage 为独立阶段埋点，不参与正文序列）。
    content_events = [e for e in events if e[0] not in ("stage", "node")]
    assert [e[0] for e in content_events] == ["started", "delta", "done"]
    started = content_events[0][1]
    assert started["conversation_id"] == conversation_id
    assert started["attempt_number"] == 1
    assert started["message_id"] == message_id
    assert content_events[1][1]["message_id"] == message_id
    assert content_events[1][1]["delta"]  # 替身回答有正文
    done = content_events[2][1]
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
    _register(client)
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
    created = _send(client, conversation_id, "你好")
    _drive_executor(sqlite_app)
    events = _subscribe_all(client, conversation_id, created["assistant_message"]["message_id"])
    content_kinds = [e[0] for e in events if e[0] not in ("stage", "node")]
    assert content_kinds == ["started", "delta", "delta", "delta", "done"]
    assert "".join(e[1]["delta"] for e in events if e[0] == "delta") == "第一第二第三"


def test_send_failure_streams_error_event_and_persists_actionable_message(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client)
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter(connect_error=RateLimitError("slow down"))
    )
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "你好")
    _drive_executor(sqlite_app)
    events = _subscribe_all(client, conversation_id, created["assistant_message"]["message_id"])
    content_events = [e for e in events if e[0] not in ("stage", "node")]
    assert [e[0] for e in content_events] == ["started", "error"]
    error = content_events[1][1]["error"]
    assert error["code"] == "rate_limit"
    assert "限流" in error["message"]
    assert error["retryable"] is True

    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = history["messages"][1]
    assert assistant["status"] == "error"
    assert assistant["error_code"] == "rate_limit"
    assert "限流" in (assistant["error_message"] or "")


def test_subscribe_after_run_terminal_replays_all_events_and_ends(
    client: TestClient, sqlite_app: Any
) -> None:
    """运行终态后订阅：回放全部持久化事件并立即结束，绝不悬挂。"""
    _register(client)
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "你好")
    message_id = created["assistant_message"]["message_id"]
    _drive_executor(sqlite_app)
    # 从任意游标订阅都能拿到剩余事件并结束（这里从 0 全量回放）
    events = _subscribe_all(client, conversation_id, message_id, cursor=0)
    assert [e[0] for e in events if e[0] not in ("stage", "node")] == ["started", "delta", "done"]
    # 从最后游标订阅：立即空流结束
    with client.stream(
        "GET",
        f"/chat/conversations/{conversation_id}/messages/{message_id}/events",
        params={"cursor": 999},
    ) as response:
        assert response.status_code == 200
        assert "\n".join(response.iter_lines()).strip() == ""


def test_stop_generation_via_api(client: TestClient, sqlite_app: Any) -> None:
    _register(client)
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter(
            [StreamChunk(kind="delta", delta=f"块{i}") for i in range(30)],
            slow=True,
        )
    )
    conversation_id = _create_conversation(client)

    # 订阅与停止控制使用独立的 TestClient：同一 httpx 客户端并发
    # 请求会互相干扰（Cookie 仓库等共享状态），导致订阅被误判为断开。
    control_client = TestClient(sqlite_app)
    control_client.cookies.set(
        "bridges_session", client.cookies.get("bridges_session")
    )

    stop_exec, exec_thread = _start_executor_thread(sqlite_app)
    try:
        created = _send(client, conversation_id, "开始生成")
        message_id = created["assistant_message"]["message_id"]
        collected: list[tuple[str, dict[str, Any]]] = []

        def consume() -> None:
            # 订阅使用独立 TestClient：同一 httpx 客户端并发请求会互相
            # 干扰（Cookie 仓库等共享状态），导致订阅被误判为断开。
            collected.extend(_subscribe_all(control_client, conversation_id, message_id))

        sub_thread = threading.Thread(target=consume)
        sub_thread.start()
        # 等待执行器领取并产出部分 delta（确认生成已真正开始）再停止；
        # 避免停止抢在执行器领取前（排队期停止不调用模型，无正文可保留）。
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            probe = control_client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["messages"][1]
            if probe["content"]:
                break
            time.sleep(0.05)
        stopped = control_client.post(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
        )
        assert stopped.status_code == 200
        # 只有显式停止才产生 stopped；停止在 2 秒内可见
        assert stopped.json()["message"]["status"] == "stopped"
        sub_thread.join(timeout=15)
        # 订阅流以 error(stopped) 终态结束，绝不会被迟到事件改写成 done
        kinds = [e[0] for e in collected]
        assert "error" in kinds, kinds
        assert any(
            name == "error" and payload["error"]["code"] == "stopped"
            for name, payload in collected
        )
        assert "done" not in kinds, kinds
        final = control_client.get(f"/chat/conversations/{conversation_id}").json()["messages"][1]
        assert final["status"] == "stopped"
        assert final["content"]  # 已接收正文保留
    finally:
        stop_exec.set()
        exec_thread.join(timeout=5)


def test_retry_via_api_creates_new_attempt(client: TestClient, sqlite_app: Any) -> None:
    _register(client)
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter(connect_error=RateLimitError("slow"))
    )
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "帮我分析")
    failed_id = created["assistant_message"]["message_id"]
    _drive_executor(sqlite_app)

    # 换成成功适配器后重试：新运行与新尝试（历史失败尝试原样保留）
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter([StreamChunk(kind="delta", delta="重试后的回答")])
    )
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{failed_id}/retry"
    )
    assert response.status_code == 200
    retried = response.json()
    assert retried["assistant_message"]["attempt_number"] == 2
    assert retried["run_id"] != created["run_id"]
    _drive_executor(sqlite_app)

    events = _subscribe_all(
        client, conversation_id, retried["assistant_message"]["message_id"]
    )
    assert [e[0] for e in events if e[0] not in ("stage", "node")] == ["started", "delta", "done"]

    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistants = [m for m in history["messages"] if m["role"] == "assistant"]
    assert [m["attempt_number"] for m in assistants] == [1, 2]
    assert assistants[0]["status"] == "error"  # 历史失败尝试原样保留
    assert assistants[1]["status"] == "done"
    assert assistants[1]["content"] == "重试后的回答"


def test_conversation_not_found_is_404(client: TestClient, sqlite_app: Any) -> None:
    _register(client)
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
    _register(client, "1")
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "爱丽丝的私密问题")
    assistant_id = created["assistant_message"]["message_id"]

    bob_client = TestClient(sqlite_app)
    _register(bob_client, "2")
    # 账户隔离与凭据无关（GQ-02）：Bob 同样无需任何 Qwen 配置，
    # 共享全局凭据不得放宽隔离判定
    assert bob_client.get("/chat/conversations").json()["conversations"] == []
    assert bob_client.get(f"/chat/conversations/{conversation_id}").status_code == 404
    assert (
        bob_client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "窥探"},
        ).status_code
        == 404
    )
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
    # Issue 02：跨账户无法订阅他人的运行事件（游标回放同样 404）
    assert (
        bob_client.get(
            f"/chat/conversations/{conversation_id}/messages/{assistant_id}/events"
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
    """GQ-02 + Issue 02 纵向回归：注册新账户 → 发送消息（生成中重启）→
    重启后消息保持 streaming 且携带可恢复运行视图 → 新进程执行器领取
    完成 → 恢复同一回答；不重复创建用户消息或模型调用。"""
    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app1 = create_app()
    client1 = TestClient(app1)
    _register(client1, "7")
    # 组合根不再持有账户 Qwen 凭据存储（GQ-07）：test 环境全局确定性
    # 适配器是唯一放行机制（GQ-02 AC7）
    assert not hasattr(app1.state, "credential_store")
    conversation_id = _create_conversation(client1)
    created = _send(client1, conversation_id, "重启前的问题")
    message_id = created["assistant_message"]["message_id"]
    # 不驱动执行器：运行停留在 queued，模拟生成中进程重启

    # 重启：同一环境重建应用（test 环境同样注册全局确定性适配器）
    get_settings.cache_clear()
    app2 = create_app()
    client2 = TestClient(app2)
    client2.cookies.set("bridges_session", client1.cookies.get("bridges_session"))

    history = client2.get(f"/chat/conversations/{conversation_id}")
    assert history.status_code == 200
    body = history.json()
    assert body["title"].startswith("重启前的问题")
    # 重启后消息仍是 streaming（活跃运行不被读取收敛），携带运行视图
    assistant = [m for m in body["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == "streaming"
    assert assistant["active_run"] is not None
    assert assistant["active_run"]["status"] == "queued"
    assert assistant["active_run"]["run_id"]
    # 新进程后台执行器领取并完成同一运行
    _drive_executor(app2)
    events = _subscribe_all(client2, conversation_id, message_id)
    assert [e for e in events if e[0] == "done"][-1][1]["message"]["status"] == "done"
    restored = client2.get(f"/chat/conversations/{conversation_id}").json()
    assert [(m["role"], m["status"]) for m in restored["messages"]] == [
        ("user", "done"),
        ("assistant", "done"),
    ]
    # 消息顺序与正文一致；用户消息只存在一条（未重复发送）
    assert restored["messages"][0]["content"] == "重启前的问题"
    assert restored["messages"][1]["content"]
    assert len([m for m in restored["messages"] if m["role"] == "user"]) == 1
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


# ---------------------------------------------------------------------------
# Issue 14：思考摘要（SSE 事件携带 + 终态保留）
# ---------------------------------------------------------------------------


def test_sse_carries_thinking_in_started_and_done(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client)
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "你好")
    # 创建响应本身即携带初始思考摘要（started 事件已持久化）
    assert created["assistant_message"]["thinking"] is not None
    assert created["assistant_message"]["thinking"]["steps"] == [
        "理解你的问题与当前语境",
        "组织并生成回答",
    ]
    _drive_executor(sqlite_app)
    events = _subscribe_all(client, conversation_id, created["assistant_message"]["message_id"])
    started = events[0][1]
    # started 事件同样携带初始思考摘要（前端据此自动展开）
    assert started["thinking"] is not None
    assert started["thinking"]["steps"] == [
        "理解你的问题与当前语境",
        "组织并生成回答",
    ]
    assert started["thinking"]["evidence"] == []
    assert started["thinking"]["tools"] == []
    done = [e for e in events if e[0] == "done"][-1]
    assert done[0] == "done"
    assert done[1]["message"]["thinking"]["steps"] == [
        "理解你的问题与当前语境",
        "组织并生成回答",
    ]
    assert done[1]["message"]["thinking"]["quality"] == ["回答已完整生成并保存"]
    # 耗时基于真实生成生命周期，非硬编码
    assert done[1]["message"]["duration_ms"] >= 1


def test_error_event_keeps_thinking_and_duration(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client)
    sqlite_app.state.chat_service._gateway = _gateway_with(
        _ProgrammableStreamAdapter(connect_error=RateLimitError("slow down"))
    )
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "你好")
    _drive_executor(sqlite_app)
    events = _subscribe_all(client, conversation_id, created["assistant_message"]["message_id"])
    error = [e for e in events if e[0] == "error"][-1]
    assert error[0] == "error"
    # 失败保留已完成摘要并显示中文状态
    assert error[1]["thinking"] is not None
    assert error[1]["thinking"]["steps"] == [
        "理解你的问题与当前语境",
        "组织并生成回答",
    ]
    assert error[1]["thinking"]["quality"] and "限流" in error[1]["thinking"]["quality"][0]
    assert error[1]["duration_ms"] is not None and error[1]["duration_ms"] >= 1


def test_second_account_cannot_read_thinking_summary(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client, "1")
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "爱丽丝的学习问题")
    assistant_id = created["assistant_message"]["message_id"]

    bob_client = TestClient(sqlite_app)
    _register(bob_client, "2")
    # Bob 读取 Alice 的对话整体 404：模式、事件与思考摘要都不泄漏
    assert bob_client.get(f"/chat/conversations/{conversation_id}").status_code == 404
    assert (
        bob_client.post(
            f"/chat/conversations/{conversation_id}/messages/{assistant_id}/retry"
        ).status_code
        == 404
    )


def test_issue06_chat_messages_populate_current_account_profile(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client, "61")
    messages = (
        "大三人工智能专业",
        "目标考211相关专业/考研",
        "想学习Transformer",
        "找Transformer论文",
    )
    for content in messages:
        conversation_id = _create_conversation(client)
        _send(client, conversation_id, content)

    profile = client.get("/profiles/four-dimensions")
    assert profile.status_code == 200, profile.text
    records = profile.json()
    assert {
        (record["dimension"], record["content"])
        for record in records
    } == {
        ("academic_status", "大三人工智能专业"),
        ("stage_goal", "考211相关专业/考研"),
        ("knowledge_interest", "Transformer"),
    }

    bob_client = TestClient(sqlite_app)
    _register(bob_client, "62")
    assert bob_client.get("/profiles/four-dimensions").json() == []
