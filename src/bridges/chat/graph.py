"""日常 LangGraph 父图的装配与公开入口。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from bridges.ai.adapters import StreamEvent
from bridges.chat.checkpoints import RepositoryCheckpointSaver
from bridges.chat.graph_composite_plan import (
    _composite_github_requirement,
)
from bridges.chat.graph_contracts import (
    AVAILABLE_MODULE_IDS,
    DAILY_GRAPH_NODES,
    DAILY_GRAPH_VERSION,
    DAILY_GRAPH_VERSION_CHANGED,
    NODE_COMPILE_CONTEXT,
    NODE_INVOKE_SUBGRAPH_OR_CHAT,
    NODE_LABELS,
    NODE_PERSIST_RESULT,
    NODE_SELECT_EXPLICIT_MODULE,
    NODE_VALIDATE_TURN,
    NODE_VERIFY_OUTPUT,
    DailyGraphStop,
    DailyGraphSuperseded,
    DailyTurnError,
    DailyTurnState,
    _GraphDeps,
    node_failure_message,
    node_label,
)
from bridges.chat.graph_nodes import (
    _node,
    _node_compile_context,
    _node_validate_turn,
)
from bridges.chat.graph_nodes import (
    _node_invoke_subgraph_or_chat as _run_node_invoke_subgraph_or_chat,
)
from bridges.chat.graph_nodes import (
    _node_select_explicit_module as _run_node_select_explicit_module,
)
from bridges.chat.graph_results import (
    _node_persist_result as _run_node_persist_result,
)
from bridges.chat.graph_results import (
    _node_verify_output,
)
from bridges.paper.suggestion import detect_paper_suggestion
from bridges.state_copy import render_state_copy

if TYPE_CHECKING:
    from bridges.chat.repository import GenerationRunRecord
    from bridges.chat.service import ChatService


def _node_select_explicit_module(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    return _run_node_select_explicit_module(
        state, config, available_module_ids=AVAILABLE_MODULE_IDS
    )


def _node_invoke_subgraph_or_chat(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    """调用分派节点，同时读取图入口暴露的模块白名单。"""
    return _run_node_invoke_subgraph_or_chat(
        state, config, available_module_ids=AVAILABLE_MODULE_IDS
    )


def _node_persist_result(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    return _run_node_persist_result(
        state, config, paper_suggestion_detector=detect_paper_suggestion
    )


def build_daily_graph(saver: RepositoryCheckpointSaver) -> Any:
    """按运行绑定检查点保存器编译日常父图（每次运行编译，租约恢复续跑）。"""
    builder: Any = StateGraph(DailyTurnState)
    builder.add_node(NODE_VALIDATE_TURN, _node(NODE_VALIDATE_TURN, _node_validate_turn))
    builder.add_node(
        NODE_COMPILE_CONTEXT, _node(NODE_COMPILE_CONTEXT, _node_compile_context)
    )
    builder.add_node(
        NODE_SELECT_EXPLICIT_MODULE,
        _node(NODE_SELECT_EXPLICIT_MODULE, _node_select_explicit_module),
    )
    builder.add_node(
        NODE_INVOKE_SUBGRAPH_OR_CHAT,
        _node(NODE_INVOKE_SUBGRAPH_OR_CHAT, _node_invoke_subgraph_or_chat),
    )
    builder.add_node(NODE_VERIFY_OUTPUT, _node(NODE_VERIFY_OUTPUT, _node_verify_output))
    builder.add_node(
        NODE_PERSIST_RESULT, _node(NODE_PERSIST_RESULT, _node_persist_result)
    )
    builder.add_edge(START, NODE_VALIDATE_TURN)
    builder.add_edge(NODE_VALIDATE_TURN, NODE_COMPILE_CONTEXT)
    builder.add_edge(NODE_COMPILE_CONTEXT, NODE_SELECT_EXPLICIT_MODULE)
    builder.add_edge(NODE_SELECT_EXPLICIT_MODULE, NODE_INVOKE_SUBGRAPH_OR_CHAT)
    builder.add_edge(NODE_INVOKE_SUBGRAPH_OR_CHAT, NODE_VERIFY_OUTPUT)
    builder.add_edge(NODE_VERIFY_OUTPUT, NODE_PERSIST_RESULT)
    builder.add_edge(NODE_PERSIST_RESULT, END)
    return builder.compile(checkpointer=saver)


def run_daily_turn(
    service: ChatService,
    run: GenerationRunRecord,
    *,
    on_event: Callable[[StreamEvent], None],
    stop_event: threading.Event | None = None,
) -> str | None:
    """同步执行一次日常父图运行，返回最后一个透传事件的 kind。

    - 事件（``node`` 进度与编排管线事件）实时经 ``on_event`` 透传，由执
      行器持久化为游标事件（SSE 订阅回放的唯一真相源不变）；
    - 用户停止（``DailyGraphStop``）在可取消节点边界终止：消息、终态
      事件与运行状态经生成终态 module 一次性收敛为 stopped（图边界是
      第一位收尾者，执行器尾段的重复收尾为幂等重放）；
    - 同一运行的检查点谱系已存在（租约恢复：上一尝试进程死亡）时以
      ``invoke(None)`` 从上次提交的节点边界续跑——已完成节点不重跑、
      不重复调模型、游标事件不重复；中断节点的重跑是 at-least-once，
      由消息事务与终态守卫保证收敛一致。
    - 节点失败（``DailyTurnError`` 或未知异常）把失败位置留在运行
      current_node，消息收敛为带节点位置与重试办法的可重试错误（同样
      经终态 module 提交；已获准提交的终态不被迟到异常改写）。
    """
    config = run.config or {}
    saver = RepositoryCheckpointSaver(
        service._repo.database,  # noqa: SLF001 - 图是服务编排的组成部分
        account_id=run.account_id,
        conversation_id=run.conversation_id,
        run_id=run.run_id,
    )
    deps = _GraphDeps(service, run, on_event=on_event, stop_event=stop_event)
    if run.graph_version != DAILY_GRAPH_VERSION:
        # 工单 43（R06）：旧版本图谱系可能已完成未原子提交的节点，不能套
        # 当前节点集继续；安全结束并保留历史，明确重试创建新版本的新运行。
        # NULL 仅表示没有版本记录，不能证明旧运行未执行；同样安全结束。
        deps.converge_error(
            DailyTurnError(
                NODE_VALIDATE_TURN,
                DAILY_GRAPH_VERSION_CHANGED,
                render_state_copy(f"error.{DAILY_GRAPH_VERSION_CHANGED}"),
                retryable=True,
            )
        )
        return deps.last_kind
    state: DailyTurnState = {
        "account_id": run.account_id,
        "conversation_id": run.conversation_id,
        "run_id": run.run_id,
        "user_message_id": run.user_message_id,
        "assistant_message_id": run.assistant_message_id,
        "module_id": None,
        "mode": "",
        "use_knowledge_base": bool(config.get("use_knowledge_base", True)),
        "use_profile": bool(config.get("use_profile", True)),
        "run_model_id": config.get("run_model_id"),
    }
    graph = build_daily_graph(saver)
    # 保存器按构造绑定 (thread_id=会话, checkpoint_ns=运行) 定位谱系，
    # LangGraph 组装的 config 只需携带 thread_id 满足图入口校验。
    graph_config: RunnableConfig = {
        "configurable": {
            "thread_id": run.conversation_id,
            "checkpoint_ns": run.run_id,
            "deps": deps,
        }
    }
    try:
        if saver.get_tuple(saver.run_config()) is not None:
            graph.invoke(None, graph_config)
        else:
            graph.invoke(state, graph_config)
    except DailyGraphStop:
        deps.converge_stopped()
    except DailyGraphSuperseded:
        # 执行权已转移：消息与运行终态由当前执行者收敛，本轮不再写终态。
        raise
    except DailyTurnError as error:
        deps.converge_error(error)
    except Exception as exc:  # noqa: BLE001 - 未知异常同样收敛为可重试失败
        deps.converge_error(
            DailyTurnError(
                deps.current_node or NODE_INVOKE_SUBGRAPH_OR_CHAT,
                "internal_error",
                f"生成过程出现内部错误（{exc.__class__.__name__}），请重试。",
            )
        )
    return deps.last_kind


__all__ = [
    "AVAILABLE_MODULE_IDS", "DAILY_GRAPH_NODES", "DAILY_GRAPH_VERSION",
    "DAILY_GRAPH_VERSION_CHANGED", "NODE_LABELS", "DailyGraphStop",
    "DailyGraphSuperseded", "DailyTurnError", "DailyTurnState",
    "_composite_github_requirement", "_node_invoke_subgraph_or_chat",
    "_node_persist_result", "_node_validate_turn", "_node_verify_output",
    "build_daily_graph", "detect_paper_suggestion", "node_failure_message",
    "node_label", "run_daily_turn",
]
