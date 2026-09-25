"""V2 Issue 02 验收测试：可恢复的对话运行（日常 LangGraph 父图）。

覆盖验收标准（``.scratch/bridges-v2/issues/02-resumable-conversation-runs.md``）：
1. 普通对话通过可持久化运行图执行；node 进度只对应实际开始/完成的节点；
   失败明确标出位置与重试办法；
2. 同一请求重试复用稳定运行标识，不重复写用户/助手消息；同一会话同时
   只允许一个可写运行；
3. 用户停止后在可取消节点终止；刷新/断线后凭游标重放事件恢复终态；
4. 检查点、运行与事件按账户和会话隔离；跨账户恢复请求被拒绝，旧消息
   仍可阅读。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.config import get_settings
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _register,
)
from tests.chat.test_issue02_durable_generation import (
    _GatedSlowAdapter,
    sqlite_app,  # noqa: F401 - 复用应用夹具
    client,  # noqa: F401 - 复用客户端夹具
)

#: 日常父图固定节点链（编排合同顺序）。
EXPECTED_NODES = (
    "validate_turn",
    "compile_context",
    "select_explicit_module",
    "invoke_subgraph_or_chat",
    "verify_output",
    "persist_result",
)


def _node_events(
    collected: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, str]]:
    """从事件集中提取 (node, status) 序列。"""
    return [
        (payload["node"], payload["status"])
        for name, payload in collected
        if name == "node"
    ]


def test_normal_chat_walks_parent_graph_with_real_node_progress(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 1：普通对话走通父图整条链，进度只映射真实节点，运行状态关联图版本。"""
    account = _register(client)
    gates = [threading.Event(), threading.Event()]
    sqlite_app.state.chat_service._gateway = _gateway_with(_GatedSlowAdapter(gates))  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](client, conversation_id, content="走一遍父图")
    message_id = created["assistant_message"]["message_id"]

    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    try:
        # 运行进行中：运行视图暴露图版本/当前节点/游标（页面恢复来源）
        deadline = time.monotonic() + 10
        run_view = None
        while time.monotonic() < deadline:
            projection = client.get(f"/chat/conversations/{conversation_id}").json()
            assistant = [m for m in projection["messages"] if m["role"] == "assistant"][
                0
            ]
            run_view = assistant.get("active_run")
            if (
                run_view is not None
                and run_view["status"] == "running"
                and run_view["current_node"] in EXPECTED_NODES
            ):
                break
            time.sleep(0.05)
        assert (
            run_view is not None
            and run_view["status"] == "running"
            and run_view["current_node"] in EXPECTED_NODES
        ), "运行应进入 running 且节点边界实时写入 current_node"
        assert run_view["graph_version"] == "daily-parent-v2"
        assert run_view["current_node"] in EXPECTED_NODES
        assert run_view["wait_reason"] is None
        assert run_view["cursor"] > 0

        gates[0].set()
        gates[1].set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            run = sqlite_app.state.chat_service.generation_run(
                account["id"], created["run_id"]
            )
            if run is not None and run.status == "done":
                break
            time.sleep(0.05)
        assert run is not None and run.status == "done"
    finally:
        stop_exec.set()
        exec_thread.join(timeout=5)

    # 全量回放：六个节点依序 started → completed（真实步骤顺序）
    collected = generation_helpers["subscribe"](client, conversation_id, message_id)
    node_progress = _node_events(collected)
    assert node_progress == [
        (node, status)
        for node in EXPECTED_NODES
        for status in ("started", "completed")
    ], node_progress
    kinds = [name for name, _ in collected]
    assert kinds[0] == "started"
    assert [k for k in kinds if k != "node"][-1] == "done"
    assert "delta" in kinds, "普通对话仍应产出正文增量事件"

    # 运行状态关联：图版本、当前节点、模型锁、SSE 游标
    run = sqlite_app.state.chat_service.generation_run(account["id"], created["run_id"])
    assert run is not None
    assert run.graph_version == "daily-parent-v2"
    assert run.current_node == "persist_result"
    assert run.wait_reason is None

    # 检查点按超步持久化且与运行关联
    with sqlite_app.state.bridges_database.connection as conn:
        rows = conn.execute(
            "SELECT checkpoint_id FROM graph_checkpoints"
            " WHERE account_id = ? AND run_id = ?",
            (account["id"], created["run_id"]),
        ).fetchall()
    assert len(rows) >= len(EXPECTED_NODES), "每个节点超步都应有检查点"


def test_module_dispatch_failure_marks_location_and_retry(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 1：模块派发失败标出节点位置与重试办法，不悄悄降级为普通对话。

    V2 Issue 11 起论文模块已接入真实子图，V2 Issue 14 起贴吧模块也已接入，
    这条失败路径改用仍未接入的 ``resources`` 模块验证（同一
    ``select_explicit_module`` 拒绝逻辑）。
    """
    account = _register(client)
    adapter = _GatedSlowAdapter([])
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](
        client, conversation_id, content="搜一下资料", module_id="resources"
    )
    message_id = created["assistant_message"]["message_id"]

    generation_helpers["drive"](sqlite_app)

    collected = generation_helpers["subscribe"](client, conversation_id, message_id)
    node_progress = _node_events(collected)
    # select_explicit_module 之后不得再有任何节点完成
    assert ("select_explicit_module", "started") in node_progress
    reached_select = False
    for node, status in node_progress:
        if reached_select and status == "started":
            assert False, "停止/失败后不应进入后续节点"
        if node == "select_explicit_module" and status == "started":
            reached_select = True
    assert ("select_explicit_module", "completed") not in node_progress
    # 终态 error 事件：位置（节点中文名）与可操作说明
    errors = [payload for name, payload in collected if name == "error"]
    assert errors, "失败轮次必须有 error 事件"
    assert errors[-1]["error"]["code"] == "module_not_available"
    assert "选择模块" in errors[-1]["error"]["message"]
    # 运行与消息收敛为失败，失败节点留在 current_node
    run = sqlite_app.state.chat_service.generation_run(account["id"], created["run_id"])
    assert run is not None and run.status == "failed"
    assert run.error_code == "module_not_available"
    assert run.current_node == "select_explicit_module"
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [m for m in projection["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == "error"
    assert adapter.stream_calls == 0, "模块派发失败绝不调用模型"


def test_send_idempotency_replays_same_run_without_duplicates(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 2：同幂等键重试复用运行标识，不重复写消息（含进行中重放）。"""
    account = _register(client)
    gates = [threading.Event()]
    adapter = _GatedSlowAdapter(gates)
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](
        client,
        conversation_id,
        content="网络重试",
        idempotency_key="idem-send-0001",
    )

    # 运行仍在进行中：同键重放不得 409，也不得创建第二个运行
    replay = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "网络重试",
            "idempotency_key": "idem-send-0001",
        },
    )
    assert replay.status_code == 200, replay.text
    replayed = replay.json()
    assert replayed["idempotent_replay"] is True
    assert replayed["run_id"] == created["run_id"]
    assert (
        replayed["assistant_message"]["message_id"]
        == created["assistant_message"]["message_id"]
    )
    assert (
        replayed["user_message"]["message_id"] == created["user_message"]["message_id"]
    )

    gates[0].set()
    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            run = sqlite_app.state.chat_service.generation_run(
                account["id"], created["run_id"]
            )
            if run is not None and run.status == "done":
                break
            time.sleep(0.05)
        assert run is not None and run.status == "done"
    finally:
        stop_exec.set()
        exec_thread.join(timeout=5)

    # 完成后同键重放：仍复用同一运行，不新增任何数据、不再调用模型
    final_replay = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "网络重试", "idempotency_key": "idem-send-0001"},
    ).json()
    assert final_replay["idempotent_replay"] is True
    assert final_replay["run_id"] == created["run_id"]
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assert len([m for m in projection["messages"] if m["role"] == "user"]) == 1
    assert adapter.stream_calls == 1

    # 不同键视为新请求（正常创建新一轮）
    second = generation_helpers["send"](
        client, conversation_id, content="新的一轮", idempotency_key="idem-send-0002"
    )
    assert second["run_id"] != created["run_id"]


def test_send_idempotency_scoped_per_conversation(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 2/4：幂等键作用域限定在会话内，跨会话同键不命中。"""
    _register(client)
    gate = threading.Event()
    gate.set()
    sqlite_app.state.chat_service._gateway = _gateway_with(_GatedSlowAdapter([gate]))  # noqa: SLF001
    first = _create_conversation(client)
    second = _create_conversation(client)
    created = generation_helpers["send"](
        client, first, content="你好", idempotency_key="idem-scope-001"
    )
    other = generation_helpers["send"](
        client, second, content="你好", idempotency_key="idem-scope-001"
    )
    assert other["run_id"] != created["run_id"]
    assert other["idempotent_replay"] is False


def test_only_one_writable_run_per_conversation(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 2：同一会话同时只允许一个可写运行。"""
    _register(client)
    gates = [threading.Event()]
    sqlite_app.state.chat_service._gateway = _gateway_with(_GatedSlowAdapter(gates))  # noqa: SLF001
    conversation_id = _create_conversation(client)
    generation_helpers["send"](client, conversation_id, content="占用运行")
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "并发第二条"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error"] == "generation_in_progress"
    gates[0].set()


def test_stop_terminates_at_cancellable_node_and_replays_to_final_state(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 3：停止后在可取消节点终止；游标重放恢复最终状态。"""
    account = _register(client)
    gates = [threading.Event(), threading.Event()]
    adapter = _GatedSlowAdapter(gates)
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](client, conversation_id, content="生成中停止")
    message_id = created["assistant_message"]["message_id"]

    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    try:
        gates[0].set()
        time.sleep(0.4)
        stopped = client.post(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
        )
        assert stopped.status_code == 200, stopped.text
        gates[1].set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            run = sqlite_app.state.chat_service.generation_run(
                account["id"], created["run_id"]
            )
            if run is not None and run.status in {"stopped", "done", "failed"}:
                break
            time.sleep(0.05)
        assert run is not None and run.status == "stopped"
    finally:
        stop_exec.set()
        exec_thread.join(timeout=5)

    # 游标 0 全量重放：恢复最终状态（终态事件可读、停止后节点不再完成）
    collected = generation_helpers["subscribe"](client, conversation_id, message_id)
    kinds = [name for name, _ in collected]
    assert [k for k in kinds if k != "node"][-1] == "error", "停止的终态事件必须可回放"
    assert [e for e in collected if e[0] == "error"][-1][1]["error"]["code"] == "stopped"
    node_progress = _node_events(collected)
    assert ("persist_result", "completed") not in node_progress
    assert ("verify_output", "completed") not in node_progress
    # 部分游标续读：从中途游标重放仍收敛到同一终态
    partial = generation_helpers["subscribe"](
        client, conversation_id, message_id, cursor=3
    )
    assert partial[-1][0] == "error"
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [m for m in projection["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == "stopped"


def test_checkpoint_run_event_isolation_across_accounts(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 4：检查点/运行/事件按账户隔离；跨账户恢复被拒；旧消息可读。"""
    account = _register(client, tag="91")
    sqlite_app.state.chat_service._gateway = _gateway_with(_GatedSlowAdapter([]))  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](client, conversation_id, content="隔离运行")
    message_id = created["assistant_message"]["message_id"]
    generation_helpers["drive"](sqlite_app)

    # 检查点归属正确账户
    with sqlite_app.state.bridges_database.connection as conn:
        rows = conn.execute(
            "SELECT account_id, conversation_id, run_id FROM graph_checkpoints"
            " WHERE run_id = ?",
            (created["run_id"],),
        ).fetchall()
    assert rows, "运行应有持久化检查点"
    assert all(
        str(row["account_id"]) == account["id"]
        and str(row["conversation_id"]) == conversation_id
        for row in rows
    )

    # 另一账户：会话与订阅恢复请求一律不可见（404）
    intruder_client = TestClient(sqlite_app)
    _register(intruder_client, tag="92")
    assert (
        intruder_client.get(f"/chat/conversations/{conversation_id}").status_code == 404
    )
    events_response = intruder_client.get(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/events"
    )
    assert events_response.status_code == 404

    # 同账户重开（刷新）：旧消息仍可阅读，事件仍可全量重放
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assert [m for m in projection["messages"] if m["role"] == "user"]
    collected = generation_helpers["subscribe"](client, conversation_id, message_id)
    assert [e for e in collected if e[0] == "done"][-1][0] == "done"


def test_module_id_rejected_before_dispatch_when_unknown(
    sqlite_app: Any, client: TestClient
) -> None:
    """契约门：未知 module_id 在 API 校验层被拒（服务端校验、模型不改写）。"""
    _register(client)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "非法模块", "module_id": "not-a-module"},
    )
    assert response.status_code == 422, response.text


def test_retry_idempotency_reuses_run(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 2：重试请求同幂等键复用运行，不重复创建尝试。

    用仍未接入的 resources 模块保证终态确定（派发失败诚实收敛），
    与是否为论文/贴吧模块无关（两者已接入真实子图）。
    """
    account = _register(client)
    adapter = _GatedSlowAdapter([])
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](
        client, conversation_id, content="搜一下资料", module_id="resources"
    )
    message_id = created["assistant_message"]["message_id"]
    generation_helpers["drive"](sqlite_app)

    first_retry = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/retry",
        json={"idempotency_key": "idem-retry-001"},
    )
    assert first_retry.status_code == 200, first_retry.text
    first_body = first_retry.json()
    assert first_body["idempotent_replay"] is False
    # 新尝试运行执行到终态（同模块仍按派发失败诚实收敛）
    generation_helpers["drive"](sqlite_app)

    second_retry = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/retry",
        json={"idempotency_key": "idem-retry-001"},
    )
    assert second_retry.status_code == 200, second_retry.text
    second_body = second_retry.json()
    assert second_body["idempotent_replay"] is True
    assert second_body["run_id"] == first_body["run_id"]

    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assert len([m for m in projection["messages"] if m["role"] == "user"]) == 1
    run = sqlite_app.state.chat_service.generation_run(
        account["id"], first_body["run_id"]
    )
    assert run is not None and run.status == "failed"


def test_lease_recovery_resumes_from_checkpoint_lineage(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """租约恢复从检查点谱系续跑：已完成节点不重跑、不重复调模型。

    第一次尝试在 invoke 节点内被闸门卡住（模拟进程死亡：不收敛、消息
    留 streaming、无终态事件）；第二次尝试应从上次提交的节点边界续跑，
    只发出剩余节点（invoke/verify/persist）的进度事件。
    """
    service = sqlite_app.state.chat_service
    account = _register(client)
    # 第一次尝试：闸门不放行，运行卡在 invoke 节点内的模型流上
    blocked = threading.Event()
    service._gateway = _gateway_with(_GatedSlowAdapter([blocked]))  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](client, conversation_id, content="恢复续跑")
    message_id = created["assistant_message"]["message_id"]
    run_id = created["run_id"]

    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    try:
        deadline = time.monotonic() + 10
        run = None
        while time.monotonic() < deadline:
            run = service.generation_run(account["id"], run_id)
            if run is not None and run.current_node == "invoke_subgraph_or_chat":
                break
            time.sleep(0.05)
        assert run is not None and run.current_node == "invoke_subgraph_or_chat"
    finally:
        # 模拟进程死亡：放弃执行线程（闸门 15 秒超时后 daemon 自灭），
        # 消息保持 streaming，检查点谱系已提交到 select_explicit_module 完成。
        stop_exec.set()

    # 第二次尝试：换立即完成的模型适配器，直接驱动图执行面（不经执行器，
    # 避开"运行已被领取且租约未过期"的跳过守卫）。
    preset = threading.Event()
    preset.set()
    service._gateway = _gateway_with(_GatedSlowAdapter([preset]))  # noqa: SLF001
    run_record = service.generation_run(account["id"], run_id)
    assert run_record is not None
    emitted: list[Any] = []
    last_kind = service.run_graph_turn(
        run_record,
        on_event=emitted.append,
        stop_event=None,
    )
    assert last_kind == "done"
    node_progress = [
        (event.node.node, event.node.status)
        for event in emitted
        if event.node is not None
    ]
    # 续跑只执行剩余节点：不再有 validate/compile/select 的进度事件
    assert node_progress == [
        ("invoke_subgraph_or_chat", "started"),
        ("invoke_subgraph_or_chat", "completed"),
        ("verify_output", "started"),
        ("verify_output", "completed"),
        ("persist_result", "started"),
        ("persist_result", "completed"),
    ], node_progress
    assert [e.kind for e in emitted if e.kind == "delta"], "续跑应产出正文增量"

    message = service.message_projection(account["id"], message_id)
    assert message is not None and message.status == "done"
