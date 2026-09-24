"""Issue 03 反馈环测试：原子创建新会话首轮（真实 HTTP + SQLite + 闸门模型）。

覆盖验收标准：
- 首页发送普通消息后 500ms 内可见用户气泡与「排队/生成中」——首轮命令
  在同一事务内创建会话、用户消息、助手占位与 queued 运行，闸门未放行时
  投影也必须立即可见；
- 会话在首轮事务成功后立即出现在最近列表（不依赖助手输出完成）；
- 连续双击、网络响应重放、并发同键只创建一个会话、一条用户消息和一个 run；
- 首轮命令失败时不留任何不可见空草稿；
- 直接刷新新会话 URL 可以恢复相同消息和 run（服务端投影是事实源）；
- 技能意图（人味化）与附件经同一原子入口落库，可被后台执行器正常领取。

修复前（旧实现）：首页先建空会话、sessionStorage 暂存、跳转后再发送，
跨页面竞态导致空白主区、重复发送与最近列表延迟。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.config import get_settings
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _register,
)


class _GatedSlowAdapter:
    """可控慢模型：每块输出由测试闸门放行（真实流式调用计数）。

    started 事件在创建时已持久化，闸门只卡模型输出——首轮投影必须在
    闸门未放行（服务端尚未产出任何 token）时即可见。
    """

    def __init__(self, gates: list[threading.Event]) -> None:
        self._gates = gates
        self.stream_calls = 0
        self._lock = threading.Lock()

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ):
        with self._lock:
            self.stream_calls += 1
        for gate in self._gates:
            if not gate.wait(timeout=15):
                raise AssertionError("测试闸门未在超时前放行。")
            yield StreamChunk(kind="delta", delta="块")


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _first_turn(
    client: TestClient, body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    response = client.post("/chat/first-turn", json=body)
    return response.status_code, response.json()


def _upload(
    client: TestClient,
    conversation_id: str,
    *,
    filename: str = "课程资料.pdf",
    content: bytes | None = None,
    upload_id: str = "upload-1",
) -> dict[str, Any]:
    response = client.post(
        f"/chat/conversations/{conversation_id}/attachments",
        content=content or b"%PDF-1.7\nminimal test document",
        headers={
            "Content-Type": "application/pdf",
            "X-Bridges-Filename": quote(filename, safe=""),
            "X-Bridges-Upload-Id": upload_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_first_turn_creates_conversation_message_placeholder_run_atomically(
    sqlite_app: Any, client: TestClient
) -> None:
    """首轮命令：同一事务创建会话、用户消息、助手占位与 queued 运行。"""
    _register(client)
    gates = [threading.Event()]
    sqlite_app.state.chat_service._gateway = _gateway_with(_GatedSlowAdapter(gates))

    status, body = _first_turn(
        client, {"content": "你好，介绍一下自己", "idempotency_key": "first-turn-key-001"}
    )
    assert status == 201, body

    conversation = body["conversation"]
    assert conversation["conversation_id"]
    assert conversation["title"] == "你好，介绍一下自己"
    assert conversation["mode"] == "companion"
    assert body["user_message"]["role"] == "user"
    assert body["user_message"]["content"] == "你好，介绍一下自己"
    assert body["user_message"]["message_id"] == conversation["messages"][0]["message_id"]
    assistant = body["assistant_message"]
    assert assistant["role"] == "assistant"
    assert assistant["status"] == "streaming"
    assert assistant["content"] == ""
    assert body["run_id"]
    assert body["idempotent_replay"] is False

    # 闸门未放行（服务端尚未产出任何 token）时，投影必须立即可见：
    # 这正是「发送后立即看到自己的消息与生成中状态」的服务端事实。
    probe = client.get(f"/chat/conversations/{conversation['conversation_id']}").json()
    assert [m for m in probe["messages"] if m["role"] == "user"]
    assistant_probe = [m for m in probe["messages"] if m["role"] == "assistant"][0]
    assert assistant_probe["status"] == "streaming"
    assert assistant_probe["active_run"] is not None
    assert assistant_probe["active_run"]["status"] == "queued"


def test_first_turn_idempotent_replay_same_key_single_artifact(
    client: TestClient,
) -> None:
    """同幂等键重放：返回同一会话/消息/运行，不产生第二份数据。"""
    _register(client)
    first_status, first = _first_turn(
        client, {"content": "第一条消息", "idempotency_key": "replay-key-002"}
    )
    second_status, second = _first_turn(
        client, {"content": "第一条消息", "idempotency_key": "replay-key-002"}
    )
    assert first_status == 201
    assert second_status == 200, second
    assert second["idempotent_replay"] is True
    assert (
        second["conversation"]["conversation_id"]
        == first["conversation"]["conversation_id"]
    )
    assert second["run_id"] == first["run_id"]
    assert (
        second["user_message"]["message_id"] == first["user_message"]["message_id"]
    )
    # 内容不可变：重放不允许携带不同正文改变已有会话
    conversation_id = first["conversation"]["conversation_id"]
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    user_messages = [m for m in projection["messages"] if m["role"] == "user"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == "第一条消息"


def test_first_turn_concurrent_same_key_single_conversation(
    sqlite_app: Any, client: TestClient
) -> None:
    """并发双击/网络重放同键：只创建一个会话、一条消息和一个 run。"""
    _register(client)
    results: list[tuple[int, dict[str, Any]]] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def call() -> None:
        barrier.wait()
        try:
            status, body = _first_turn(
                client, {"content": "并发消息", "idempotency_key": "concurrent-key-003"}
            )
            results.append((status, body))
        except Exception as exc:  # noqa: BLE001 - 测试收集异常
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not errors, errors
    assert len(results) == 2
    conversation_ids = {body["conversation"]["conversation_id"] for _, body in results}
    assert len(conversation_ids) == 1, "并发同键必须收敛到同一会话"
    run_ids = {body["run_id"] for _, body in results}
    assert len(run_ids) == 1, "并发同键必须收敛到同一运行"

    projection = client.get(f"/chat/conversations/{conversation_ids.pop()}").json()
    assert len([m for m in projection["messages"] if m["role"] == "user"]) == 1
    assistants = [m for m in projection["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["active_run"] is not None
    # 两个请求返回同一 run：投影只有一份运行视图
    assert {body["run_id"] for _, body in results} == {
        assistants[0]["active_run"]["run_id"]
    }


def test_first_turn_listed_before_completion(
    sqlite_app: Any, client: TestClient
) -> None:
    """最近列表在首轮事务成功后立即可见，不等待助手输出完成。"""
    _register(client)
    gates = [threading.Event()]
    sqlite_app.state.chat_service._gateway = _gateway_with(_GatedSlowAdapter(gates))

    _, body = _first_turn(
        client, {"content": "学习 Transformer 结构", "idempotency_key": "list-key-004"}
    )
    conversation_id = body["conversation"]["conversation_id"]

    # 闸门未放行：列表必须已包含该会话（消息数 1、标题已推导）
    listing = client.get("/chat/conversations").json()
    matched = [
        item
        for item in listing["conversations"]
        if item["conversation_id"] == conversation_id
    ]
    assert len(matched) == 1, listing
    # message_count 含助手占位；关键是不依赖助手完成即可见
    assert matched[0]["message_count"] >= 1
    assert matched[0]["title"] == "学习 Transformer 结构"


def test_first_turn_failure_leaves_no_draft(client: TestClient) -> None:
    """首轮失败：不留下任何不可见空草稿，列表不变。"""
    _register(client)
    before = client.get("/chat/conversations").json()["conversations"]

    # 空正文 422
    status, body = _first_turn(client, {"content": "  ", "idempotency_key": "fail-key-005"})
    assert status == 422
    # 未注册 SKILL 标识 422
    status, body = _first_turn(
        client,
        {
            "content": "改写文章",
            "idempotency_key": "fail-key-006",
            "skill_id": "no-such-skill",
            "skill_input": {"skill_id": "no-such-skill"},
        },
    )
    assert status == 422, body

    after = client.get("/chat/conversations").json()["conversations"]
    assert after == before, "失败不得留下空草稿或部分会话"


def test_daily_first_turn_rejects_study_mode_and_unrecognized_modules(
    client: TestClient,
) -> None:
    """尚未验收的学习模式与未知模块不能绕过普通日常首轮入口。"""
    _register(client)
    before = client.get("/chat/conversations").json()["conversations"]

    study_status, study_body = _first_turn(
        client,
        {
            "content": "开始学习",
            "idempotency_key": "daily-gate-study-001",
            "mode": "study",
        },
    )
    assert study_status == 409
    assert study_body["detail"]["error"] == "study_mode_unavailable"

    module_status, _ = _first_turn(
        client,
        {
            "content": "找几篇论文",
            "idempotency_key": "daily-gate-module-002",
            "module_id": "unrecognized",
        },
    )
    assert module_status == 422
    assert client.get("/chat/conversations").json()["conversations"] == before

    create_study = client.post(
        "/chat/conversations", json={"title": "学习草稿", "mode": "study"}
    )
    assert create_study.status_code == 409
    create_module = client.post(
        "/chat/conversations", json={"title": "普通会话", "module_id": "unrecognized"}
    )
    assert create_module.status_code == 422
    assert client.get("/chat/conversations").json()["conversations"] == before


def test_message_request_rejects_mode_tampering_and_invalid_module(
    client: TestClient,
) -> None:
    """后续消息不能通过正文接口篡改固定模式或注入未知模块值。"""
    _register(client)
    status, body = _first_turn(
        client,
        {"content": "你好", "idempotency_key": "daily-lock-first-003"},
    )
    assert status == 201, body
    conversation_id = body["conversation"]["conversation_id"]
    before = client.get(f"/chat/conversations/{conversation_id}").json()
    assert before["mode"] == "companion"
    assert before["mode_locked"] is True

    tampered = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "切换到学习模式", "mode": "study"},
    )
    invalid_module = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "搜索论文", "module_id": "unrecognized"},
    )
    assert tampered.status_code == 422
    assert invalid_module.status_code == 422
    after = client.get(f"/chat/conversations/{conversation_id}").json()
    assert after["mode"] == before["mode"]
    user_messages_before = [
        (message["message_id"], message["content"])
        for message in before["messages"]
        if message["role"] == "user"
    ]
    user_messages_after = [
        (message["message_id"], message["content"])
        for message in after["messages"]
        if message["role"] == "user"
    ]
    assert user_messages_after == user_messages_before


def test_ordinary_text_persists_without_auto_selecting_legacy_modules(
    client: TestClient,
) -> None:
    """未选模块的普通文字只保存为普通日常消息，不按正文启动旧能力。"""
    _register(client)
    prompts = [
        "找几篇 Transformer 论文",
        "润色这段文章，让它更自然",
        "生成一张小猫图片",
        "帮我规划职业方向",
    ]
    for index, prompt in enumerate(prompts):
        status, body = _first_turn(
            client,
            {"content": prompt, "idempotency_key": f"ordinary-route-{index:03d}"},
        )
        assert status == 201, body
        user_message = body["user_message"]
        assert user_message["route"]["status"] == "ordinary"
        assert user_message["route"]["main_capability"] == "ordinary_chat"
        assert user_message["skill"] is None
        assert user_message["image"] is None
        assert body["assistant_message"]["arxiv_search"] is None


def test_first_turn_reuses_existing_empty_conversation(client: TestClient) -> None:
    """预建空会话（附件路径）被首轮复用；非空会话拒绝。"""
    _register(client)
    prebuilt = _create_conversation(client)

    status, body = _first_turn(
        client,
        {
            "content": "上传后的发送",
            "idempotency_key": "prebuilt-key-007",
            "conversation_id": prebuilt,
        },
    )
    assert status == 201, body
    assert body["conversation"]["conversation_id"] == prebuilt

    # 已有人消息的会话不能作为首轮目标：防止双发
    status, body = _first_turn(
        client,
        {
            "content": "再来一条",
            "idempotency_key": "prebuilt-key-008",
            "conversation_id": prebuilt,
        },
    )
    assert status == 409, body

    # 跨账户引用不泄漏存在性
    other = _register(client, tag="8")
    assert other["id"] != ""
    status, body = _first_turn(
        client,
        {
            "content": "试探他人会话",
            "idempotency_key": "prebuilt-key-009",
            "conversation_id": prebuilt,
        },
    )
    assert status == 404, body


def test_first_turn_idempotent_replay_with_prebuilt_conversation(
    client: TestClient,
) -> None:
    """预建会话（附件路径）同键重放：必须 200 返回既有数据，不被 409 短路。"""
    _register(client)
    prebuilt = _create_conversation(client)

    first_status, first = _first_turn(
        client,
        {
            "content": "附件路径首轮",
            "idempotency_key": "prebuilt-replay-key-014",
            "conversation_id": prebuilt,
        },
    )
    assert first_status == 201, first

    # 同键 + 同预建会话重放（模拟网络响应重放）：返回既有数据而非 409
    second_status, second = _first_turn(
        client,
        {
            "content": "附件路径首轮",
            "idempotency_key": "prebuilt-replay-key-014",
            "conversation_id": prebuilt,
        },
    )
    assert second_status == 200, second
    assert second["idempotent_replay"] is True
    assert second["run_id"] == first["run_id"]

    projection = client.get(f"/chat/conversations/{prebuilt}").json()
    assert len([m for m in projection["messages"] if m["role"] == "user"]) == 1


def test_first_turn_idempotent_replay_after_run_terminal(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """run 完成（终态）后同键重放：仍返回 200 与同一会话（契约：重放返回既有数据）。"""
    _register(client)
    release = threading.Event()
    release.set()
    adapter = _GatedSlowAdapter([release])
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)

    _, body = _first_turn(
        client, {"content": "完成后再重放", "idempotency_key": "terminal-replay-key-015"}
    )
    conversation_id = body["conversation"]["conversation_id"]

    # 驱动到 done（active_run 从投影消失，run 记录仍在运行表）
    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    generation_helpers["subscribe"](
        client, conversation_id, body["assistant_message"]["message_id"]
    )
    stop_exec.set()
    exec_thread.join(timeout=5)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [m for m in final["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == "done"
    assert assistant.get("active_run") is None

    replay_status, replay = _first_turn(
        client,
        {"content": "完成后再重放", "idempotency_key": "terminal-replay-key-015"},
    )
    assert replay_status == 200, replay
    assert replay["idempotent_replay"] is True
    assert replay["conversation"]["conversation_id"] == conversation_id
    assert replay["run_id"] == body["run_id"]


def test_first_turn_concurrent_different_keys_same_prebuilt(
    client: TestClient,
) -> None:
    """并发不同键首轮同一预建会话：一个 201、一个 409，只产生一条消息。"""
    _register(client)
    prebuilt = _create_conversation(client)
    results: list[tuple[int, dict[str, Any]]] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def call(key: str) -> None:
        barrier.wait()
        try:
            status, body = _first_turn(
                client,
                {
                    "content": "并发抢同一预建会话",
                    "idempotency_key": key,
                    "conversation_id": prebuilt,
                },
            )
            results.append((status, body))
        except Exception as exc:  # noqa: BLE001 - 测试收集异常
            errors.append(exc)

    threads = [
        threading.Thread(target=call, args=(key,))
        for key in ("prebuilt-concurrent-key-a", "prebuilt-concurrent-key-b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not errors, errors
    statuses = sorted(status for status, _ in results)
    assert statuses == [201, 409], results
    projection = client.get(f"/chat/conversations/{prebuilt}").json()
    assert len([m for m in projection["messages"] if m["role"] == "user"]) == 1


def test_first_turn_plain_turn_completes_via_executor(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """首轮创建的 run 可被后台执行器正常领取并完成（单一 done）。"""
    _register(client)
    release = threading.Event()
    release.set()
    adapter = _GatedSlowAdapter([release])
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)

    _, body = _first_turn(
        client, {"content": "普通首轮", "idempotency_key": "done-key-010"}
    )
    conversation_id = body["conversation"]["conversation_id"]
    message_id = body["assistant_message"]["message_id"]

    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, message_id, cursor=0
    )
    stop_exec.set()
    exec_thread.join(timeout=5)

    kinds = [name for name, _ in events]
    assert "done" in kinds, kinds
    assert adapter.stream_calls == 1, "模型只调用一次"
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [m for m in final["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == "done"


def test_first_turn_skill_payload_is_retired(
    sqlite_app: Any, client: TestClient
) -> None:
    """V2 issue 04：首轮携带人味化载荷 → 410，任何数据都不创建。"""
    account = _register(client)

    status, body = _first_turn(
        client,
        {
            "content": "改写下面的文章，让它更有人味",
            "idempotency_key": "skill-key-011",
            "skill_id": "bridges-humanizer",
            "skill_input": {
                "skill_id": "bridges-humanizer",
                "contract": {
                    "path": "rewrite",
                    "genre": "popular_science",
                    "source_text": "Transformer 是深度学习架构。",
                    "audience": "普通读者",
                    "channel": "公众号",
                    "length_target": "200 字",
                },
            },
        },
    )
    assert status == 410, body
    assert body["detail"]["error"] == "humanizer_capability_retired"
    # 入口在任何写入之前拒绝：不产生会话、消息或运行。
    listing = client.get("/chat/conversations").json()
    assert listing["conversations"] == []



def test_first_turn_attachment_bound_in_same_transaction(
    client: TestClient,
) -> None:
    """首轮携带附件 ID：附件与用户消息在同一事务绑定。"""
    _register(client)
    prebuilt = _create_conversation(client)
    attachment = _upload(client, prebuilt)
    object_id = attachment["object_id"]

    status, body = _first_turn(
        client,
        {
            "content": "分析这份课程资料",
            "idempotency_key": "attach-key-012",
            "conversation_id": prebuilt,
            "attachment_ids": [object_id],
        },
    )
    assert status == 201, body
    assert body["user_message"]["attachments"][0]["object_id"] == object_id

    projection = client.get(f"/chat/conversations/{prebuilt}").json()
    user_message = [m for m in projection["messages"] if m["role"] == "user"][0]
    assert user_message["attachments"][0]["object_id"] == object_id


def test_first_turn_refresh_restores_same_projection(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """刷新新会话 URL：从服务端投影恢复相同消息与 run，不重复发送。"""
    _register(client)
    gates = [threading.Event()]
    sqlite_app.state.chat_service._gateway = _gateway_with(_GatedSlowAdapter(gates))

    _, body = _first_turn(
        client, {"content": "刷新恢复", "idempotency_key": "refresh-key-013"}
    )
    conversation_id = body["conversation"]["conversation_id"]
    run_id = body["run_id"]

    # 模拟刷新：重新 GET 投影，消息/运行与首轮响应一致
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    user_messages = [m for m in projection["messages"] if m["role"] == "user"]
    assert len(user_messages) == 1
    assert user_messages[0]["message_id"] == body["user_message"]["message_id"]
    assistant = [m for m in projection["messages"] if m["role"] == "assistant"][0]
    assert assistant["active_run"]["run_id"] == run_id
    # started 事件已持久化：订阅线程从游标可收到，且不会产生第二条用户消息
    collected: list[tuple[str, dict[str, Any]]] = []

    def consume() -> None:
        collected.extend(
            generation_helpers["subscribe"](
                client, conversation_id, assistant["message_id"], cursor=0
            )
        )

    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    thread = threading.Thread(target=consume)
    thread.start()
    gates[0].set()
    thread.join(timeout=15)
    stop_exec.set()
    exec_thread.join(timeout=5)
    assert any(name == "done" for name, _ in collected), collected
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    assert len([m for m in final["messages"] if m["role"] == "user"]) == 1
