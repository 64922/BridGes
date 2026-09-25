"""日常 LangGraph 父图（V2 Issue 02：可恢复的对话运行）。

编排合同（``.scratch/bridges-v2/issues/02`` + ``docs/v2/architecture.md``）：
日常父图按固定节点链推进——

    validate_turn → compile_context → select_explicit_module
      → invoke_subgraph_or_chat → verify_output → persist_result

- 本切片让**无模块普通聊天**走通整条链；模块子图自 Issue 11 起逐个接入
  ``invoke_subgraph_or_chat`` 的分派分支。
- ``select_explicit_module`` 只读随用户消息持久化、经服务端枚举校验的
  ``module_id`` 派发，模型不得从正文改写（本切片显式拒绝未接入模块，
  绝不悄悄降级为普通对话）。
- 节点进度只对应实际开始或完成的步骤：``node`` 事件在节点体执行前发
  started、成功返回后发 completed；失败节点只有 started 与随后携带
  节点位置的 error 事件。
- 用户停止在可取消节点边界生效（``stop_requested``/停止信号）；停止后
  不再进入后续节点，消息与运行按既有停止语义收敛。
- 每个超步（superstep）经 :class:`~bridges.chat.checkpoints.RepositoryCheckpointSaver`
  把图状态写入 bridges.db（thread_id=会话 ID，checkpoint_ns=运行 ID），
  租约恢复的尝试从会话线程上本运行的检查点谱系继续。
- 运行状态关联：graph_version、current_node（节点边界实时写入）、
  model_lock_id（persist_result 补写）；wait_reason 由澄清/逐题等待的
  后续切片写入，本切片恒为空。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from bridges.ai.adapters import StreamEvent
from bridges.chat.checkpoints import RepositoryCheckpointSaver
from bridges.chat.run_executor import chat_run_context
from bridges.chat.turn import (
    failed_thinking,
    finalize_message,
    initial_thinking,
    stopped_thinking,
)
from bridges.contracts.chat import (
    ChatMessageStatus,
    ChatMode,
    ChatModuleId,
    ChatStreamNodeData,
)

if TYPE_CHECKING:
    from bridges.chat.repository import GenerationRunRecord
    from bridges.chat.service import ChatService

#: 日常父图名称/版本（运行状态关联字段；节点集变化时递增）。
DAILY_GRAPH_VERSION = "daily-parent-v1"

NODE_VALIDATE_TURN = "validate_turn"
NODE_COMPILE_CONTEXT = "compile_context"
NODE_SELECT_EXPLICIT_MODULE = "select_explicit_module"
NODE_INVOKE_SUBGRAPH_OR_CHAT = "invoke_subgraph_or_chat"
NODE_VERIFY_OUTPUT = "verify_output"
NODE_PERSIST_RESULT = "persist_result"

#: 固定节点链（顺序即执行顺序；``node`` 事件与运行 current_node 使用此表）。
DAILY_GRAPH_NODES: tuple[str, ...] = (
    NODE_VALIDATE_TURN,
    NODE_COMPILE_CONTEXT,
    NODE_SELECT_EXPLICIT_MODULE,
    NODE_INVOKE_SUBGRAPH_OR_CHAT,
    NODE_VERIFY_OUTPUT,
    NODE_PERSIST_RESULT,
)

#: 节点的用户可读名称（失败信息标注位置用）。
NODE_LABELS: dict[str, str] = {
    NODE_VALIDATE_TURN: "校验回合",
    NODE_COMPILE_CONTEXT: "编译上下文",
    NODE_SELECT_EXPLICIT_MODULE: "选择模块",
    NODE_INVOKE_SUBGRAPH_OR_CHAT: "生成回答",
    NODE_VERIFY_OUTPUT: "核验输出",
    NODE_PERSIST_RESULT: "保存结果",
}


class DailyGraphStop(Exception):
    """用户停止：在可取消节点边界终止，不再进入后续节点。"""

    def __init__(self, node: str) -> None:
        super().__init__(f"run stopped at node {node}")
        self.node = node


class DailyTurnError(Exception):
    """图节点失败：失败位置（节点）、稳定错误码与是否可重试。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


class DailyTurnState(TypedDict, total=False):
    """父图状态（必须可序列化——检查点按超步持久化）。

    依赖对象（服务、事件回调、停止信号）经 ``config["configurable"]``
    传入节点，绝不放进状态。
    """

    account_id: str
    conversation_id: str
    run_id: str
    user_message_id: str
    assistant_message_id: str
    #: 随用户消息持久化的服务端校验值（validate_turn 从仓库读取后写回）。
    module_id: str | None
    mode: str
    use_knowledge_base: bool
    use_profile: bool
    #: select_explicit_module 的派发决定（本切片恒为 "chat"）。
    module_dispatch: str
    model_lock_id: str | None


class _GraphDeps:
    """节点依赖：服务、运行记录、事件回调与停止信号（不进检查点状态）。"""

    def __init__(
        self,
        service: ChatService,
        run: GenerationRunRecord,
        *,
        on_event: Callable[[StreamEvent], None],
        stop_event: threading.Event | None,
    ) -> None:
        self.service = service
        self.run = run
        self.on_event = on_event
        self.stop_event = stop_event
        self.repo = service._repo  # noqa: SLF001 - 图是服务编排的组成部分
        #: 最后透传事件的 kind（执行器补发终态事件的判断依据）。
        self.last_kind: str | None = None
        #: 最近进入的图节点（未知异常时的失败定位）。
        self.current_node: str | None = None

    # -- 事件 ------------------------------------------------------------

    def emit(self, event: StreamEvent) -> None:
        # 节点进度事件不参与"最后透传事件"判定：执行器补发终态事件时
        # 以最后一次内容/编排事件（started/delta/done/error/…）为准，
        # 否则 persist_result 节点的 completed 会遮蔽已透传的 done。
        if event.kind != "node":
            self.last_kind = event.kind
        self.on_event(event)

    def emit_node(
        self,
        message_id: str,
        node: str,
        status: str,
        *,
        duration_ms: int | None = None,
    ) -> None:
        self.emit(
            StreamEvent(
                kind="node",
                node=ChatStreamNodeData(
                    message_id=message_id,
                    node=node,
                    status=status,  # type: ignore[arg-type]
                    duration_ms=duration_ms,
                ),
            )
        )

    def emit_error(
        self, message_id: str, *, code: str, message: str, retryable: bool
    ) -> None:
        # error 事件与回合编排同形：code/message 走 StreamEvent 的
        # error_code/error_message 字段，执行器统一构造脱敏载荷。
        self.emit(
            StreamEvent(
                kind="error",
                error_code=code,
                error_message=message,
            )
        )

    # -- 停止与进度 ------------------------------------------------------

    def stop_requested(self) -> bool:
        """跨进程真相源（运行表）+ 同进程停止信号任一命中即视为请求停止。"""
        if self.stop_event is not None and self.stop_event.is_set():
            return True
        run = self.repo.get_generation_run(self.run.account_id, self.run.run_id)
        return run is not None and run.stop_requested

    def message_streaming(self) -> bool:
        message = self.repo.get_message(
            self.run.account_id, self.run.assistant_message_id
        )
        return message is not None and message.status == ChatMessageStatus.STREAMING

    def record_node(self, node: str) -> None:
        """节点边界实时写入 current_node（失败定位依据）。"""
        self.current_node = node
        self.repo.update_generation_progress(
            self.run.account_id, self.run.run_id, current_node=node
        )

    # -- 终态收敛（图提前终止时） ----------------------------------------

    def _conversation_mode(self) -> ChatMode:
        conversation = self.repo.get_conversation(
            self.run.account_id, self.run.conversation_id
        )
        return (
            ChatMode(conversation.mode) if conversation is not None else ChatMode.COMPANION
        )

    def converge_stopped(self) -> None:
        """把仍处 streaming 的消息收敛为 stopped（幂等；终态由守卫保证）。"""
        message = self.repo.get_message(
            self.run.account_id, self.run.assistant_message_id
        )
        if message is None or message.status != ChatMessageStatus.STREAMING:
            return
        finalize_message(
            self.repo,
            self.run.account_id,
            self.run.assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            started=time.monotonic(),
            now=datetime.now(UTC),
            thinking=stopped_thinking(initial_thinking(self._conversation_mode())),
        )

    def converge_error(self, error: DailyTurnError) -> None:
        """把仍处 streaming 的消息收敛为带节点位置与重试办法的错误。"""
        message = self.repo.get_message(
            self.run.account_id, self.run.assistant_message_id
        )
        if message is None or message.status != ChatMessageStatus.STREAMING:
            return
        label = NODE_LABELS.get(error.node, error.node)
        node_message = (
            f"在「{label}」步骤失败：{error.message}"
            + ("可点击重试。" if error.retryable else "请调整后重试。")
        )
        finalize_message(
            self.repo,
            self.run.account_id,
            self.run.assistant_message_id,
            status=ChatMessageStatus.ERROR,
            error_code=error.code,
            error_message=node_message,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            started=time.monotonic(),
            now=datetime.now(UTC),
            thinking=failed_thinking(initial_thinking(self._conversation_mode()), error.code),
        )
        self.emit_error(
            self.run.assistant_message_id,
            code=error.code,
            message=node_message,
            retryable=error.retryable,
        )


NodeBody = Callable[[DailyTurnState, RunnableConfig], dict[str, Any] | None]


def _node(node: str, body: NodeBody) -> Callable[[DailyTurnState, RunnableConfig], dict[str, Any]]:
    """节点包装器：停止检查 → 记录 current_node → started 事件 → 执行 → completed。"""

    def wrapped(state: DailyTurnState, config: RunnableConfig) -> dict[str, Any]:
        deps: _GraphDeps = config["configurable"]["deps"]
        # 可取消节点边界：用户停止后不再进入后续节点（停止只对未收敛的
        # 消息生效——已终态的消息不重复收敛）。
        if deps.stop_requested() and deps.message_streaming():
            raise DailyGraphStop(node)
        deps.record_node(node)
        deps.emit_node(state["assistant_message_id"], node, "started")
        started = time.monotonic()
        updates = body(state, config) or {}
        deps.emit_node(
            state["assistant_message_id"],
            node,
            "completed",
            duration_ms=max(1, int((time.monotonic() - started) * 1000)),
        )
        return updates

    return wrapped


def _node_validate_turn(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    """校验持久化回合：消息归属/状态、会话存在、逐消息 module_id 合法。"""
    del state
    deps: _GraphDeps = config["configurable"]["deps"]
    run = deps.run
    assistant = deps.repo.get_message(run.account_id, run.assistant_message_id)
    if assistant is None or assistant.conversation_id != run.conversation_id:
        raise DailyTurnError(
            NODE_VALIDATE_TURN,
            "message_not_found",
            "消息不存在或没有访问权限。",
            retryable=False,
        )
    if assistant.status != ChatMessageStatus.STREAMING:
        raise DailyTurnError(
            NODE_VALIDATE_TURN,
            "generation_not_active",
            "该回答已不在生成中。",
            retryable=False,
        )
    conversation = deps.repo.get_conversation(run.account_id, run.conversation_id)
    if conversation is None:
        raise DailyTurnError(
            NODE_VALIDATE_TURN,
            "conversation_not_found",
            "对话不存在或没有访问权限。",
            retryable=False,
        )
    user_message = deps.repo.get_message(run.account_id, run.user_message_id)
    if user_message is None:
        raise DailyTurnError(
            NODE_VALIDATE_TURN,
            "message_not_found",
            "消息不存在或没有访问权限。",
            retryable=False,
        )
    module_id = user_message.module_id
    if module_id is not None and module_id not in {item.value for item in ChatModuleId}:
        raise DailyTurnError(
            NODE_VALIDATE_TURN,
            "module_not_allowed",
            "模块选择不合法。",
            retryable=False,
        )
    return {"module_id": module_id, "mode": conversation.mode}


def _node_compile_context(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    """编译本轮上下文基础（检索决策；Issue 03 扩展为完整上下文编译器）。"""
    del state
    deps: _GraphDeps = config["configurable"]["deps"]
    deps.service.ensure_turn_context(deps.run)
    return {}


def _node_select_explicit_module(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    """显式模块派发：只读服务端校验并随消息持久化的 module_id。"""
    deps: _GraphDeps = config["configurable"]["deps"]
    module_id = state.get("module_id")
    if module_id is not None:
        # 模块子图自 Issue 11 起接入；此前显式拒绝，绝不悄悄降级为普通
        # 对话（派发只读持久化值，模型无法从正文改写模块选择）。
        raise DailyTurnError(
            NODE_SELECT_EXPLICIT_MODULE,
            "module_not_available",
            "该模块尚未开放，请使用普通对话。",
            retryable=False,
        )
    del deps
    return {"module_dispatch": "chat"}


def _node_invoke_subgraph_or_chat(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    """普通对话执行体：现有回合编排管线，事件实时透传给订阅端。"""
    deps: _GraphDeps = config["configurable"]["deps"]
    run = deps.run
    stream = deps.service.stream_generation(
        run.account_id,
        run.conversation_id,
        run.assistant_message_id,
        chat_run_context(run.account_id, run.conversation_id, run.run_id),
        until_user_message_id=run.user_message_id,
        use_knowledge_base=state.get("use_knowledge_base", True),
        use_profile=state.get("use_profile", True),
    )
    for event in stream:
        deps.emit(event)
    return {}


def _node_verify_output(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    """核验输出：本轮已收敛为明确终态（完成或诚实失败），绝不悬空。"""
    del state
    deps: _GraphDeps = config["configurable"]["deps"]
    run = deps.run
    message = deps.repo.get_message(run.account_id, run.assistant_message_id)
    if message is None:
        raise DailyTurnError(
            NODE_VERIFY_OUTPUT,
            "message_not_found",
            "消息不存在或没有访问权限。",
            retryable=False,
        )
    if message.status == ChatMessageStatus.STOPPED:
        raise DailyGraphStop(NODE_VERIFY_OUTPUT)
    if message.status != ChatMessageStatus.DONE:
        raise DailyTurnError(
            NODE_VERIFY_OUTPUT,
            message.error_code or "generation_failed",
            message.error_message or "生成过程出现内部错误，请重试。",
            retryable=True,
        )
    return {}


def _node_persist_result(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    """结果收尾：运行状态关联模型运行锁（消息正文/引用已由编排落库）。"""
    del state
    deps: _GraphDeps = config["configurable"]["deps"]
    run = deps.run
    message = deps.repo.get_message(run.account_id, run.assistant_message_id)
    if message is not None and message.run_lock_id:
        deps.repo.update_generation_progress(
            run.account_id, run.run_id, model_lock_id=message.run_lock_id
        )
    return {}


def build_daily_graph(saver: RepositoryCheckpointSaver) -> Any:
    """按运行绑定检查点保存器编译日常父图（每次运行编译，租约恢复续跑）。"""
    builder = StateGraph(DailyTurnState)
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
    - 用户停止（``DailyGraphStop``）在可取消节点边界终止：消息收敛为
      stopped，运行由执行器按消息终态收敛；
    - 节点失败（``DailyTurnError`` 或未知异常）把失败位置留在运行
      current_node，消息收敛为带节点位置与重试办法的可重试错误。
    """
    config = run.config or {}
    saver = RepositoryCheckpointSaver(
        service._repo.database,  # noqa: SLF001 - 图是服务编排的组成部分
        account_id=run.account_id,
        conversation_id=run.conversation_id,
        run_id=run.run_id,
    )
    deps = _GraphDeps(service, run, on_event=on_event, stop_event=stop_event)
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
    }
    graph = build_daily_graph(saver)
    try:
        graph.invoke(state, {"configurable": {"deps": deps}})
    except DailyGraphStop:
        deps.converge_stopped()
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
