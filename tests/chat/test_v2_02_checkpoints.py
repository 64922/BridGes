"""V2 Issue 02：LangGraph 检查点适配器（RepositoryCheckpointSaver）单元测试。

覆盖：
- 检查点经 bridges.db 持久化并可恢复（round-trip，重启保存器后仍可读）；
- thread_id 固定映射会话 ID、checkpoint_ns 映射运行 ID——同一会话两次
  运行的检查点互不串线；
- 账户隔离：跨账户保存器读不到检查点（SQL 层不可见，而非调用方约定）；
- 会话删除清理（delete_thread 只清本账户本会话）。
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from bridges.chat.checkpoints import RepositoryCheckpointSaver
from bridges.storage.database import BridgesDatabase


class _State(TypedDict):
    value: int
    path: list[str]


def _make_graph(saver: RepositoryCheckpointSaver) -> Any:
    """两节点线性小图：node_a 加一并记路径，node_b 只记路径。"""

    def node_a(state: _State) -> dict[str, Any]:
        return {"value": state["value"] + 1, "path": [*state["path"], "a"]}

    def node_b(state: _State) -> dict[str, Any]:
        return {"path": [*state["path"], "b"]}

    builder = StateGraph(_State)
    builder.add_node("node_a", node_a)
    builder.add_node("node_b", node_b)
    builder.add_edge(START, "node_a")
    builder.add_edge("node_a", "node_b")
    builder.add_edge("node_b", END)
    return builder.compile(checkpointer=saver)


@pytest.fixture
def database(tmp_path: Any) -> BridgesDatabase:
    db = BridgesDatabase(tmp_path / "bridges.db")
    db.initialize()
    return db


def _saver(
    database: BridgesDatabase,
    account_id: str = "acc-1",
    conversation_id: str = "conv-1",
    run_id: str = "run-1",
) -> RepositoryCheckpointSaver:
    return RepositoryCheckpointSaver(
        database,
        account_id=account_id,
        conversation_id=conversation_id,
        run_id=run_id,
    )


def test_checkpoint_round_trip_and_latest_read(database: BridgesDatabase) -> None:
    """检查点写入 bridges.db；同谱系读取返回最新检查点（含图状态）。"""
    graph = _make_graph(_saver(database))
    config = _saver(database).run_config()
    result = graph.invoke({"value": 1, "path": []}, config)

    assert result["value"] == 2
    assert result["path"] == ["a", "b"]

    # 新建保存器实例（模拟进程重启后恢复读取）
    fresh = _saver(database)
    latest = fresh.get_tuple(fresh.run_config())
    assert latest is not None
    recovered = latest.checkpoint["channel_values"]
    assert recovered["value"] == 2
    assert recovered["path"] == ["a", "b"]
    assert latest.config["configurable"]["checkpoint_id"]


def test_checkpoint_ns_isolates_runs_of_same_conversation(
    database: BridgesDatabase,
) -> None:
    """同一会话两次运行（不同 checkpoint_ns）的检查点谱系互不可见。"""
    first = graph = _make_graph(_saver(database, run_id="run-1"))
    first.invoke({"value": 1, "path": []}, _saver(database, run_id="run-1").run_config())
    del graph

    second = _make_graph(_saver(database, run_id="run-2"))
    second.invoke({"value": 10, "path": []}, _saver(database, run_id="run-2").run_config())

    latest_first = _saver(database, run_id="run-1").get_tuple(
        _saver(database, run_id="run-1").run_config()
    )
    latest_second = _saver(database, run_id="run-2").get_tuple(
        _saver(database, run_id="run-2").run_config()
    )
    assert latest_first is not None and latest_second is not None
    assert latest_first.checkpoint["channel_values"]["value"] == 2
    assert latest_second.checkpoint["channel_values"]["value"] == 11
    # 不同运行的检查点 ID 谱系独立
    assert (
        latest_first.config["configurable"]["checkpoint_id"]
        != latest_second.config["configurable"]["checkpoint_id"]
    )


def test_checkpoint_account_isolation(database: BridgesDatabase) -> None:
    """跨账户恢复请求被拒绝：其他账户保存器读不到任何检查点。"""
    graph = _make_graph(_saver(database, account_id="acc-1"))
    graph.invoke({"value": 1, "path": []}, _saver(database, account_id="acc-1").run_config())

    intruder = _saver(database, account_id="acc-other")
    assert intruder.get_tuple(intruder.run_config()) is None
    assert list(intruder.list(intruder.run_config())) == []


def test_delete_thread_cleans_only_own_conversation(
    database: BridgesDatabase,
) -> None:
    """会话删除清理检查点：只清本账户本会话，其他会话保留。"""
    graph = _make_graph(_saver(database, conversation_id="conv-1"))
    graph.invoke({"value": 1, "path": []}, _saver(database, conversation_id="conv-1").run_config())
    other = _make_graph(_saver(database, conversation_id="conv-2"))
    other.invoke(
        {"value": 1, "path": []}, _saver(database, conversation_id="conv-2").run_config()
    )

    _saver(database, conversation_id="conv-1").delete_thread("conv-1")
    assert (
        _saver(database, conversation_id="conv-1").get_tuple(
            _saver(database, conversation_id="conv-1").run_config()
        )
        is None
    )
    assert (
        _saver(database, conversation_id="conv-2").get_tuple(
            _saver(database, conversation_id="conv-2").run_config()
        )
        is not None
    )
