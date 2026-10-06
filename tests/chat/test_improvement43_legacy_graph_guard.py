"""工单 43：旧日常图版本运行的安全结束与明确新运行（R06 集成接缝）。

工单 43 任务 3 要求不兼容配方的旧运行安全结束或迁移为明确新运行，不把
新图套旧谱系继续。学习图已有 ``study_graph_version_changed`` 守卫；本文件
覆盖本票补齐的日常图同语义守卫：

- 旧 ``graph_version`` 的运行不再进入当前节点集，直接以稳定错误码安全
  结束，保留历史消息与运行，且不产生任何模型调用或图检查点；
- 用户明确重试创建绑定当前 ``daily-parent-v2`` 的新运行并正常完成；
- 迁移 49 之前无图版本（``NULL``）的历史运行没有旧谱系，仍按首次执行
  进入当前图（兼容读取不被一刀切拒绝）。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _register,
)
from tests.chat.test_issue02_durable_generation import (
    client,  # noqa: F401 - 复用应用夹具
    sqlite_app,  # noqa: F401 - 复用应用夹具
)

#: 旧日常图版本（节点集变化前的历史版本，仅测试构造使用）。
_OLD_DAILY_GRAPH_VERSION = "daily-parent-v1"


class _CountingAdapter:
    """计数流式适配器：验证旧谱系结束路径零模型调用，重试路径真实调用。"""

    def __init__(self, content: str) -> None:
        self._content = content
        self.stream_calls = 0

    def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]):
        self.stream_calls += 1
        yield StreamChunk(kind="delta", delta=self._content)


def _send(
    client: TestClient,  # noqa: F811 - 形参与夹具导入同名
    conversation_id: str,
    content: str,
) -> dict[str, Any]:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _set_queued_run_graph_version(app: Any, account_id: str, version: str | None) -> None:
    database = app.state.chat_service._repo.database  # noqa: SLF001 - 测试注入旧谱系
    database.scoped(account_id).execute(
        "UPDATE generation_runs SET graph_version = ?"
        " WHERE account_id = ? AND status = 'queued'",
        (version, account_id),
    )


def test_old_daily_graph_run_ends_safely_and_retry_uses_current_graph(
    sqlite_app: Any,  # noqa: F811 - pytest 夹具
    client: TestClient,  # noqa: F811 - pytest 夹具
    generation_helpers: dict[str, Any],
) -> None:
    account = _register(client)
    adapter = _CountingAdapter("新版本回答")
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "介绍一下你自己")
    _set_queued_run_graph_version(
        sqlite_app, account["id"], _OLD_DAILY_GRAPH_VERSION
    )

    sqlite_app.state.generation_executor.run_tick()

    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = projection["messages"][-1]
    assert assistant["status"] == "error"
    assert assistant["error_code"] == "daily_graph_version_changed"
    assert "历史已保留" in assistant["error_message"]
    run = sqlite_app.state.chat_service.generation_run(account["id"], created["run_id"])
    assert run is not None and run.status == "failed"
    assert run.error_code == "daily_graph_version_changed"
    # 旧谱系不套新图：没有模型调用，也没有写入任何图检查点。
    assert adapter.stream_calls == 0
    with sqlite_app.state.bridges_database.connection as conn:
        rows = conn.execute(
            "SELECT checkpoint_id FROM graph_checkpoints"
            " WHERE account_id = ? AND run_id = ?",
            (account["id"], created["run_id"]),
        ).fetchall()
    assert rows == []

    retried = client.post(
        f"/chat/conversations/{conversation_id}/messages/"
        f"{assistant['message_id']}/retry"
    )
    assert retried.status_code == 200, retried.text
    sqlite_app.state.generation_executor.run_tick()
    resumed = client.get(f"/chat/conversations/{conversation_id}").json()
    final = resumed["messages"][-1]
    assert final["status"] == "done"
    assert final["content"] == "新版本回答"
    new_run = sqlite_app.state.chat_service.generation_run(
        account["id"], retried.json()["run_id"]
    )
    assert new_run is not None and new_run.graph_version == "daily-parent-v2"
    assert adapter.stream_calls == 1


def test_pre_graph_run_without_version_still_executes_current_graph(
    sqlite_app: Any,  # noqa: F811 - pytest 夹具
    client: TestClient,  # noqa: F811 - pytest 夹具
    generation_helpers: dict[str, Any],
) -> None:
    """迁移 49 前的历史运行没有图谱系，按首次执行进入当前图（兼容不拒绝）。"""

    account = _register(client)
    adapter = _CountingAdapter("兼容回答")
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "历史运行兼容")
    _set_queued_run_graph_version(sqlite_app, account["id"], None)

    sqlite_app.state.generation_executor.run_tick()

    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    final = projection["messages"][-1]
    assert final["status"] == "done"
    assert final["content"] == "兼容回答"
