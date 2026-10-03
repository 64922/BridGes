"""日常 LangGraph 父图（V2 Issue 02：可恢复的对话运行）。

编排合同（``.scratch/bridges-v2/issues/02`` + ``docs/v2/architecture.md``）：
日常父图按固定节点链推进——

    validate_turn → compile_context → select_explicit_module
      → invoke_subgraph_or_chat → verify_output → persist_result

- 本切片让**无模块普通聊天**走通整条链；模块子图自 Issue 11 起逐个接入
  ``invoke_subgraph_or_chat`` 的分派分支。
- ``select_explicit_module`` 按版本化实际路由派发；历史消息仍读取原
  ``module_id``。请求模块提示保持原样，真正调用前再次核验硬条件和任务。
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
from bridges.career_plan.service import (
    CAREER_MODULE_ID,
    CAREER_NODE_LABELS,
    CareerModuleError,
)
from bridges.career_plan.suggestion import detect_career_suggestion
from bridges.chat.checkpoints import RepositoryCheckpointSaver
from bridges.chat.run_executor import chat_run_context
from bridges.chat.terminal import (
    INTERNAL_ERROR_CODE,
    TerminalOutcome,
    stopped_outcome,
)
from bridges.commute.service import (
    COMMUTE_MODULE_ID,
    COMMUTE_NODE_LABELS,
    CommuteModuleError,
    CommuteSupersededError,
)
from bridges.commute.suggestion import detect_commute_suggestion
from bridges.contracts.chat import (
    CHAT_MODULE_VALUES,
    ChatMessageStatus,
    ChatStreamNodeData,
)
from bridges.contracts.tasks import TaskRelation, TaskStatus
from bridges.contracts.understanding import (
    understanding_from_snapshot,
)
from bridges.github.service import (
    GITHUB_MODULE_ID,
    GITHUB_NODE_LABELS,
    GithubModuleError,
)
from bridges.github.suggestion import detect_github_suggestion
from bridges.paper.service import (
    PAPER_MODULE_ID,
    PAPER_NODE_LABELS,
    PaperModuleError,
    PaperSupersededError,
)
from bridges.paper.suggestion import detect_paper_suggestion
from bridges.resources.service import (
    RESOURCES_MODULE_ID,
    RESOURCES_NODE_LABELS,
    ResourcesModuleError,
)
from bridges.resources.suggestion import detect_resources_suggestion
from bridges.routing.contracts import MODULE_CAPABILITIES, RouteStatus
from bridges.state_copy import RecoveryAction, error_recovery, render_state_copy
from bridges.tieba.service import (
    TIEBA_MODULE_ID,
    TIEBA_NODE_LABELS,
    TiebaModuleError,
)
from bridges.tieba.suggestion import detect_tieba_suggestion

if TYPE_CHECKING:
    from bridges.chat.repository import GenerationRunRecord
    from bridges.chat.service import ChatService

#: 日常父图名称/版本（运行状态关联字段；节点集变化时递增）。
#: v2：论文子图接入 invoke_subgraph_or_chat（首个显式模块，Issue 11）。
#: v2 不变：Issue 12（通勤）与 Issue 13（资料）都只放开显式模块白名单，
#: 复用同一派发节点，节点集没有变化——旧检查点不会出现这两个模块的派发
#: （当时它们在 select 处被拒），续跑语义一致。
DAILY_GRAPH_VERSION = "daily-parent-v2"

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

#: 运行配置中的显式模块覆盖键（仅服务端在「点击建议启动」时写入）。
RUN_CONFIG_MODULE_ID = "module_id"

#: 已接入日常父图的显式模块（其余模块仍在开发：显式拒绝，绝不悄悄降级）。
AVAILABLE_MODULE_IDS: frozenset[str] = frozenset(
    {
        PAPER_MODULE_ID,
        COMMUTE_MODULE_ID,
        RESOURCES_MODULE_ID,
        TIEBA_MODULE_ID,
        CAREER_MODULE_ID,
        GITHUB_MODULE_ID,
    }
)

#: 节点的用户可读名称（失败信息标注位置用）。
NODE_LABELS: dict[str, str] = {
    NODE_VALIDATE_TURN: "校验回合",
    NODE_COMPILE_CONTEXT: "编译上下文",
    NODE_SELECT_EXPLICIT_MODULE: "选择模块",
    NODE_INVOKE_SUBGRAPH_OR_CHAT: "生成回答",
    NODE_VERIFY_OUTPUT: "核验输出",
    NODE_PERSIST_RESULT: "保存结果",
    # 子图节点：失败信息按真实失败的子图步骤标注位置（Issue 11 起）。
    **PAPER_NODE_LABELS,
    **TIEBA_NODE_LABELS,
    **GITHUB_NODE_LABELS,
    **RESOURCES_NODE_LABELS,
    **COMMUTE_NODE_LABELS,
    **CAREER_NODE_LABELS,
}


def node_label(node: str) -> str:
    """节点标识 → 用户可读名称；未知节点原样返回，不臆造文案。"""
    return NODE_LABELS.get(node, node)


#: 已登记错误码的恢复方式 → 注册表恢复提示路径；无恢复方式或未登记
#: 的错误不追加指引（失败原因本身必须已经说清楚）。
_RECOVERY_HINT_PATHS: dict[RecoveryAction, str] = {
    RecoveryAction.RETRY: "chat.progress.recovery_retry_plain",
    RecoveryAction.WAIT: "chat.progress.recovery_wait",
    RecoveryAction.ADJUST_REQUEST: "chat.progress.recovery_adjust",
    RecoveryAction.RECONFIGURE: "chat.progress.recovery_reconfigure",
}


def node_failure_message(
    *, node: str, message: str, retryable: bool, code: str | None = None
) -> str:
    """失败位置 + 真实原因 + 真实恢复方式（固定文案注册表渲染）。

    恢复提示以真实可重试性为准；不可重试时按错误码登记的恢复方式选择
    （重试/等待/调整/联系管理员），无恢复方式或未登记的错误不追加指引。
    """
    if retryable:
        recovery = render_state_copy("chat.progress.recovery_retry")
    else:
        action = error_recovery(code)
        path = None if action is None else _RECOVERY_HINT_PATHS.get(action)
        recovery = render_state_copy(path) if path is not None else ""
    return render_state_copy(
        "chat.progress.node_failure",
        label=node_label(node),
        message=message,
        recovery=recovery,
    )


class DailyGraphStop(Exception):
    """用户停止：在可取消节点边界终止，不再进入后续节点。"""

    def __init__(self, node: str) -> None:
        super().__init__(f"run stopped at node {node}")
        self.node = node


class DailyGraphSuperseded(Exception):
    """迟到结果：执行权已转移，本轮不写任何交付终态（交给当前执行者）。"""


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
    #: 持久化路由中的实际模块；旧消息读取历史提示。
    module_id: str | None
    mode: str
    use_knowledge_base: bool
    use_profile: bool
    #: select_explicit_module 的派发决定（本切片恒为 "chat"）。
    module_dispatch: str
    #: 本轮启动时锁定的主模型 ID（V2 Issue 09；None 表示沿用出厂矩阵）。
    run_model_id: str | None
    model_lock_id: str | None
    #: V2 Issue 03：compile_context 产出的模型就绪上下文（近期原文 +
    #: 较早摘要 + 补回原文；纯字符串/数字，检查点可序列化）。
    #: V2 Issue 05：None 表示照片轮跳过编译，生成回退多模态组装。
    compiled_messages: list[dict[str, str]] | None
    #: V2 Issue 08：同一次编译的预算记录（模型 ID、预算与已用估算），
    #: 供画像块按剩余输入预算裁剪；照片轮跳过编译时为空。
    context_budget: dict[str, object] | None


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
        #: 生成终态 module（Issue 01/02）：图边界提前终止时的消息、终态
        #: 事件与运行状态都经它提交，图不再自行拼装投影。
        self.terminal = service.terminal
        #: 本轮启动时刻（运行终态的耗时口径，与执行器墙钟同源）。
        self.started = time.monotonic()
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

    # -- 停止与进度 ------------------------------------------------------

    def stop_requested(self) -> bool:
        """跨进程真相源（运行表）+ 同进程停止信号任一命中即视为请求停止。"""
        if self.stop_event is not None and self.stop_event.is_set():
            return True
        run = self.repo.get_generation_run(self.run.account_id, self.run.run_id)
        return run is not None and run.stop_requested

    def record_node(self, node: str) -> None:
        """节点边界实时写入 current_node（失败定位依据）。"""
        self.current_node = node
        self.repo.update_generation_progress(
            self.run.account_id, self.run.run_id, current_node=node
        )

    # -- 终态收敛（图提前终止时，经生成终态 module 提交） ------------------

    def _run_duration_ms(self) -> int:
        """本轮墙钟耗时（图侧口径，随终态写入运行记录）。"""
        return max(1, int((time.monotonic() - self.started) * 1000))

    def converge_stopped(self) -> None:
        """把仍处 streaming 的消息收敛为 stopped（幂等；终态由守卫保证）。

        思考摘要、停止投影、终态事件与运行状态都由终态 module 派生与
        提交：图只声明「这一轮按用户停止结束」。已获准提交的终态不被
        迟到完成或重复停止改写（module 的持久守卫裁决）。
        """
        self.terminal.converge(
            self.run.account_id,
            self.run.run_id,
            self.run.assistant_message_id,
            fallback=stopped_outcome(),
            run_duration_ms=self._run_duration_ms(),
        )

    def converge_error(self, error: DailyTurnError) -> None:
        """把仍处 streaming 的消息收敛为带节点位置与重试办法的错误。

        节点位置的中文文案是本入口的输入；其余（思考摘要、终态事件、
        运行状态）由终态 module 派生与提交。消息已终态时（例如回合编排
        已先提交失败）收尾为幂等重放，不追加矛盾事件。
        """
        node_message = node_failure_message(
            node=error.node,
            message=error.message,
            retryable=error.retryable,
            code=error.code,
        )
        self.terminal.converge(
            self.run.account_id,
            self.run.run_id,
            self.run.assistant_message_id,
            fallback=TerminalOutcome(
                status=ChatMessageStatus.ERROR,
                error_code=error.code,
                error_message=node_message,
            ),
            run_duration_ms=self._run_duration_ms(),
        )


NodeBody = Callable[[DailyTurnState, RunnableConfig], dict[str, Any] | None]


def _node(node: str, body: NodeBody) -> Callable[[DailyTurnState, RunnableConfig], dict[str, Any]]:
    """节点包装器：停止检查 → 记录 current_node → started 事件 → 执行 → completed。"""

    def wrapped(state: DailyTurnState, config: RunnableConfig) -> dict[str, Any]:
        deps: _GraphDeps = config["configurable"]["deps"]
        # 可取消节点边界：停止请求一旦可见，不再进入后续节点、不再发出
        # 节点进度——终态事件必须保持为最后一个事件（订阅端重放以终态
        # 收尾）；消息终态本身由终态 module 的持久守卫裁决（先提交者胜，
        # 迟到停止不改写已获准的完成/失败）。
        if deps.stop_requested():
            raise DailyGraphStop(node)
        deps.record_node(node)
        deps.emit_node(state["assistant_message_id"], node, "started")
        started = time.monotonic()
        updates = body(state, config) or {}
        if deps.stop_requested():
            raise DailyGraphStop(node)
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
    route = assistant.route or {}
    module_id = user_message.module_id
    if route.get("understanding_version"):
        module_id = route.get("module_id")
        if module_id is None:
            module_id = next(
                (key for key, value in MODULE_CAPABILITIES.items()
                 if value.value == route.get("main_capability")),
                None,
            )
        if route.get("status") in {RouteStatus.CLARIFY, RouteStatus.REJECTED}:
            module_id = None
    elif module_id is None:
        # 用户点击建议启动（服务端在重试路径写入显式模块覆盖）：仍按枚举
        # 校验，且只在逐消息没有模块时生效——历史消息标识不被改写。
        override = (run.config or {}).get(RUN_CONFIG_MODULE_ID)
        module_id = str(override) if override is not None else None
    if module_id is not None and module_id not in CHAT_MODULE_VALUES:
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
    """编译本轮上下文：检索决策（幂等）+ 模型输入上下文编译（Issue 03）。

    编译以原始消息为权威源，在锁定模型已验证窗口内产出「当前请求 +
    近期原文 + 较早摘要 + 按需补回的原文」并落编译审计记录；产物进图
    状态（租约恢复时随检查点复用，不重复编译、不重复落审计）。
    """
    del state
    deps: _GraphDeps = config["configurable"]["deps"]
    deps.service.ensure_turn_context(deps.run)
    # 惰性导入：``bridges.chat.service`` 在模块级导入本模块，顶层导入会成环。
    from bridges.chat.service import ChatDomainError  # noqa: PLC0415

    try:
        compiled_messages, context_budget = deps.service.compile_turn_context(deps.run)
    except ChatDomainError as error:
        # 改进工单 03：运行额度快照不可验证时闭锁本轮——转成带节点位置的
        # 可重试失败，界面得到明确原因而不是笼统的内部错误。
        raise DailyTurnError(
            NODE_COMPILE_CONTEXT, error.code, error.message, retryable=True
        ) from error
    return {
        "compiled_messages": compiled_messages,
        "context_budget": context_budget,
    }


def _node_select_explicit_module(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    """按已校验实际模块派发；旧消息保留原显式模块语义。"""
    deps: _GraphDeps = config["configurable"]["deps"]
    module_id = state.get("module_id")
    if module_id is not None and module_id not in AVAILABLE_MODULE_IDS:
        # 六个日常模块现已全部接入；这道门守的是「请求契约已放行、但子图还没接入」
        # 的过渡状态（新增模块 ID 先上契约、子图随后接入），显式拒绝，绝不悄悄
        # 降级为普通对话（派发只读持久化值，模型无法从正文改写模块选择）。
        raise DailyTurnError(
            NODE_SELECT_EXPLICIT_MODULE,
            "module_not_available",
            render_state_copy("error.module_not_available"),
            retryable=False,
        )
    del deps
    return {"module_dispatch": module_id or "chat"}


def _node_invoke_subgraph_or_chat(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    """按显式派发调用子图；无模块时走普通对话（事件实时透传给订阅端）。"""
    deps: _GraphDeps = config["configurable"]["deps"]
    dispatch = state.get("module_dispatch")
    # 在真正调用前重读持久快照，租约从旧检查点恢复也不能绕过门禁。
    run = deps.run
    assistant = deps.repo.get_message(run.account_id, run.assistant_message_id)
    route = assistant.route if assistant is not None and assistant.route else {}
    understanding = understanding_from_snapshot((run.config or {}).get("understanding"))
    if route.get("understanding_version"):
        dispatch = route.get("module_id") or next(
            (key for key, value in MODULE_CAPABILITIES.items()
             if value.value == route.get("main_capability")),
            "chat",
        )
    if route.get("status") in {RouteStatus.CLARIFY, RouteStatus.REJECTED} or (
        understanding is not None
        and understanding.task_relation in {TaskRelation.PAUSE, TaskRelation.CANCEL}
    ):
        dispatch = "chat"
    if dispatch in AVAILABLE_MODULE_IDS:
        conversation = deps.repo.get_conversation(run.account_id, run.conversation_id)
        if conversation is None or conversation.mode != "companion":
            raise DailyTurnError(
                NODE_INVOKE_SUBGRAPH_OR_CHAT, "module_mode_conflict",
                render_state_copy("error.module_mode_conflict"), retryable=False,
            )
        if understanding is not None and understanding.blocks_network:
            raise DailyTurnError(
                NODE_INVOKE_SUBGRAPH_OR_CHAT, "network_not_allowed",
                render_state_copy("error.network_not_allowed"), retryable=False,
            )
        if understanding is not None and not understanding.allows_module(dispatch):
            raise DailyTurnError(
                NODE_INVOKE_SUBGRAPH_OR_CHAT, "source_not_allowed",
                render_state_copy("error.source_not_allowed"), retryable=False,
            )
        binding = (run.config or {}).get("task_binding")
        if isinstance(binding, dict):
            tasks = getattr(deps.service, "_tasks", None)
            projection = tasks.projection(run.account_id, binding.get("task_id")) if tasks else None
            if (
                projection is None
                or projection.task.conversation_id != run.conversation_id
                or projection.task.current_version != binding.get("version")
                or projection.task.status in {
                    TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.COMPLETED,
                }
            ):
                raise DailyTurnError(
                    NODE_INVOKE_SUBGRAPH_OR_CHAT, "task_state_conflict",
                    render_state_copy("error.task_state_conflict"), retryable=False,
                )
    if dispatch == PAPER_MODULE_ID:
        return _invoke_paper_module(deps, state)
    if dispatch == TIEBA_MODULE_ID:
        return _invoke_tieba_module(deps, state)
    if dispatch == GITHUB_MODULE_ID:
        return _invoke_github_module(deps, state)
    if dispatch == RESOURCES_MODULE_ID:
        return _invoke_resources_module(deps, state)
    if dispatch == COMMUTE_MODULE_ID:
        return _invoke_commute_module(deps, state)
    if dispatch == CAREER_MODULE_ID:
        return _invoke_career_module(deps, state)
    run = deps.run
    stream = deps.service.stream_generation(
        run.account_id,
        run.conversation_id,
        run.assistant_message_id,
        chat_run_context(run.account_id, run.conversation_id, run.run_id),
        until_user_message_id=run.user_message_id,
        use_knowledge_base=state.get("use_knowledge_base", True),
        use_profile=state.get("use_profile", True),
        compiled_messages=state.get("compiled_messages"),
        # 运行级模型锁定（V2 Issue 09）：换运行配置不中途切换本轮模型。
        model_id=state.get("run_model_id"),
        # V2 Issue 08：画像块与编译器共用同一份剩余输入预算。
        context_budget=state.get("context_budget"),
        # 改进工单 03：运行额度快照与编译器同源（从运行配置读取），
        # 网关据此记录实际模型与输入额度，旧运行不随新配置漂移。
        model_quota=(run.config or {}).get("model_quota"),
    )
    for event in stream:
        deps.emit(event)
    return {}


def _invoke_paper_module(deps: _GraphDeps, state: DailyTurnState) -> dict[str, Any]:
    """论文子图执行体：节点进度经同一 ``node`` 事件与 current_node 透传。

    子图内的失败按真实失败的子图步骤标注位置（``paper.search`` 等），
    并把等待原因写入运行表（持久化等待状态，跨轮次恢复的依据）。
    """
    run = deps.run
    service = getattr(deps.service, "paper_search_service", None)
    if service is None:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT,
            "module_not_available",
            "论文搜索模块当前不可用，请稍后重试。",
            retryable=True,
        )

    def emit_node(node: str, status: str, duration_ms: int | None) -> None:
        deps.emit_node(state["assistant_message_id"], node, status, duration_ms=duration_ms)

    try:
        outcome = service.run(
            repo=deps.repo,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            user_message_id=run.user_message_id,
            assistant_message_id=run.assistant_message_id,
            run_context=chat_run_context(run.account_id, run.conversation_id, run.run_id),
            run_model_id=state.get("run_model_id"),
            emit_node=emit_node,
            stop_event=deps.stop_event,
            module_context=deps.service.module_task_context(run, PAPER_MODULE_ID),
            model_quota=deps.service.run_model_quota(run),
            manifest_sink=lambda manifest: deps.service.audit_module_manifest(
                run, "paper.summarize", manifest
            ),
            assessment_manifest_sink=lambda manifest: deps.service.audit_module_manifest(
                run, "paper.assess", manifest
            ),
            writing_policy=(run.config or {}).get("global_writing_policy"),
        )
    except PaperSupersededError as error:
        # 迟到结果：本轮不再写交付终态，交给当前持有执行权的执行者收尾。
        raise DailyGraphSuperseded(str(error)) from error
    except PaperModuleError as error:
        raise DailyTurnError(
            error.node, error.code, error.message, retryable=error.retryable
        ) from error
    if outcome.wait_reason is not None:
        deps.repo.update_generation_progress(
            run.account_id, run.run_id, wait_reason=outcome.wait_reason
        )
    return {}


def _invoke_tieba_module(deps: _GraphDeps, state: DailyTurnState) -> dict[str, Any]:
    """贴吧子图执行体：节点进度经同一 ``node`` 事件与 current_node 透传。

    子图内的失败按真实失败的子图步骤标注位置（``tieba.search`` 等），
    并把等待原因写入运行表（持久化等待状态，跨轮次恢复的依据）。
    """
    run = deps.run
    service = getattr(deps.service, "tieba_research_service", None)
    if service is None:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT,
            "module_not_available",
            "贴吧信息搜集模块当前不可用，请稍后重试。",
            retryable=True,
        )

    def emit_node(node: str, status: str, duration_ms: int | None) -> None:
        deps.emit_node(state["assistant_message_id"], node, status, duration_ms=duration_ms)

    try:
        outcome = service.run(
            repo=deps.repo,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            user_message_id=run.user_message_id,
            assistant_message_id=run.assistant_message_id,
            emit_node=emit_node,
            stop_event=deps.stop_event,
        )
    except TiebaModuleError as error:
        raise DailyTurnError(
            error.node, error.code, error.message, retryable=error.retryable
        ) from error
    if outcome.wait_reason is not None:
        deps.repo.update_generation_progress(
            run.account_id, run.run_id, wait_reason=outcome.wait_reason
        )
    return {}


def _invoke_github_module(deps: _GraphDeps, state: DailyTurnState) -> dict[str, Any]:
    """GitHub 项目推荐子图执行体：节点进度经同一 ``node`` 事件与 current_node 透传。

    子图内的失败按真实失败的子图步骤标注位置（``github.search`` 等），
    并把等待原因写入运行表（持久化等待状态，跨轮次恢复的依据）。
    """
    run = deps.run
    service = getattr(deps.service, "github_projects_service", None)
    if service is None:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT,
            "module_not_available",
            "GitHub 项目推荐模块当前不可用，请稍后重试。",
            retryable=True,
        )

    def emit_node(node: str, status: str, duration_ms: int | None) -> None:
        deps.emit_node(state["assistant_message_id"], node, status, duration_ms=duration_ms)

    try:
        outcome = service.run(
            repo=deps.repo,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            user_message_id=run.user_message_id,
            assistant_message_id=run.assistant_message_id,
            run_context=chat_run_context(run.account_id, run.conversation_id, run.run_id),
            run_model_id=state.get("run_model_id"),
            emit_node=emit_node,
            stop_event=deps.stop_event,
            module_context=deps.service.module_task_context(run, GITHUB_MODULE_ID),
            model_quota=deps.service.run_model_quota(run),
            manifest_sink=lambda manifest: deps.service.audit_module_manifest(
                run, "github.insight", manifest
            ),
        )
    except GithubModuleError as error:
        raise DailyTurnError(
            error.node, error.code, error.message, retryable=error.retryable
        ) from error
    if outcome.wait_reason is not None:
        deps.repo.update_generation_progress(
            run.account_id, run.run_id, wait_reason=outcome.wait_reason
        )
    return {}


def _invoke_resources_module(deps: _GraphDeps, state: DailyTurnState) -> dict[str, Any]:
    """资料子图执行体：节点进度经同一 ``node`` 事件与 current_node 透传。

    与论文子图共用同一套事件、等待与失败合同；本模块的正文完全由真实证据
    渲染，不调用模型（因此不传运行上下文与模型 ID）。
    """
    run = deps.run
    service = getattr(deps.service, "learning_resources_service", None)
    if service is None:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT,
            "module_not_available",
            "学习资料推荐模块当前不可用，请稍后重试。",
            retryable=True,
        )

    def emit_node(node: str, status: str, duration_ms: int | None) -> None:
        deps.emit_node(state["assistant_message_id"], node, status, duration_ms=duration_ms)

    try:
        outcome = service.run(
            repo=deps.repo,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            user_message_id=run.user_message_id,
            assistant_message_id=run.assistant_message_id,
            emit_node=emit_node,
            stop_event=deps.stop_event,
            module_context=deps.service.module_task_context(
                run, RESOURCES_MODULE_ID
            ),
        )
    except ResourcesModuleError as error:
        raise DailyTurnError(
            error.node, error.code, error.message, retryable=error.retryable
        ) from error
    if outcome.wait_reason is not None:
        deps.repo.update_generation_progress(
            run.account_id, run.run_id, wait_reason=outcome.wait_reason
        )
    return {}


def _invoke_commute_module(deps: _GraphDeps, state: DailyTurnState) -> dict[str, Any]:
    """校园通勤子图执行体（V2 Issue 12）：与论文子图共用父图节点与事件流。

    子图内的失败按真实失败的子图步骤标注位置（``route.request`` 等），并把
    等待原因写入运行表（持久化等待状态，跨轮次恢复的依据）。
    """
    run = deps.run
    service = getattr(deps.service, "commute_service", None)
    if service is None:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT,
            "module_not_available",
            "校园通勤模块当前不可用，请稍后重试。",
            retryable=True,
        )

    def emit_node(node: str, status: str, duration_ms: int | None) -> None:
        deps.emit_node(state["assistant_message_id"], node, status, duration_ms=duration_ms)

    try:
        outcome = service.run(
            repo=deps.repo,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            user_message_id=run.user_message_id,
            assistant_message_id=run.assistant_message_id,
            run_context=chat_run_context(run.account_id, run.conversation_id, run.run_id),
            run_model_id=state.get("run_model_id"),
            emit_node=emit_node,
            stop_event=deps.stop_event,
            module_context=deps.service.module_task_context(run, COMMUTE_MODULE_ID),
        )
    except CommuteSupersededError as error:
        # 迟到结果：本轮不再写交付终态，交给当前持有执行权的执行者收尾。
        raise DailyGraphSuperseded(str(error)) from error
    except CommuteModuleError as error:
        raise DailyTurnError(
            error.node, error.code, error.message, retryable=error.retryable
        ) from error
    if outcome.wait_reason is not None:
        deps.repo.update_generation_progress(
            run.account_id, run.run_id, wait_reason=outcome.wait_reason
        )
    return {}


def _invoke_career_module(deps: _GraphDeps, state: DailyTurnState) -> dict[str, Any]:
    """职业规划子图执行体（V2 Issue 15）：与论文子图共用父图节点与事件流。

    子图内的失败按真实失败的子图步骤标注位置（``career.collect`` 等），并把
    等待原因写入运行表（持久化等待状态，跨轮次恢复的依据）。本模块的正文与
    分析完全由真实岗位证据渲染，不调用模型（因此不传模型 ID）。
    """
    run = deps.run
    understanding = understanding_from_snapshot((run.config or {}).get("understanding"))
    request_options = {}
    if understanding is not None and {"direction", "stage"} & set(understanding.answer_fields):
        # 主理解补齐的目标用于本次解析，历史用户正文保持原样。
        request_options["request_text"] = understanding.goal
    service = getattr(deps.service, "career_plan_service", None)
    if service is None:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT,
            "module_not_available",
            "职业规划模块当前不可用，请稍后重试。",
            retryable=True,
        )

    def emit_node(node: str, status: str, duration_ms: int | None) -> None:
        deps.emit_node(state["assistant_message_id"], node, status, duration_ms=duration_ms)

    try:
        outcome = service.run(
            repo=deps.repo,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            user_message_id=run.user_message_id,
            assistant_message_id=run.assistant_message_id,
            emit_node=emit_node,
            stop_event=deps.stop_event,
            **request_options,
        )
    except CareerModuleError as error:
        raise DailyTurnError(
            error.node, error.code, error.message, retryable=error.retryable
        ) from error
    if outcome.wait_reason is not None:
        deps.repo.update_generation_progress(
            run.account_id, run.run_id, wait_reason=outcome.wait_reason
        )
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
            message.error_code or INTERNAL_ERROR_CODE,
            message.error_message or "生成过程出现内部错误，请重试。",
            retryable=True,
        )
    return {}


def _node_persist_result(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    """结果收尾：运行状态关联模型运行锁，并落普通聊天的模块建议。

    V2 Issue 11：普通聊天（无模块）中明显的论文请求只**建议**一键启动
    论文模块，不在此处发起任何外部检索；建议随助手消息持久化，重开
    历史仍可见，点击后由服务端以原文显式派发。

    V2 Issue 13：论文建议优先（论文请求更具体），没有论文建议时才看
    资料请求。建议字段每轮只有一个，因此两个模块不会互相覆盖。

    V2 Issue 14：贴吧建议接在论文之后（吧内请求同样明确）。

    V2 Issue 16：GitHub 建议接在贴吧之后（论文／贴吧／GitHub 都属于
    「找外部资料」，先看更具体的论文与大吧，再看 GitHub），其后才是
    通勤与资料；五者共用同一建议字段，命中即止。
    """
    deps: _GraphDeps = config["configurable"]["deps"]
    run = deps.run
    if state.get("module_dispatch") == "chat":
        user_message = deps.repo.get_message(run.account_id, run.user_message_id)
        if user_message is not None:
            # 一条消息只给一个建议：论文建议优先（其请求形态更明确），
            # 未命中时才依次考虑贴吧、GitHub、通勤与资料建议；五者都只
            # 建议，不后台执行。
            suggestion = (
                detect_paper_suggestion(user_message.content)
                or detect_tieba_suggestion(user_message.content)
                or detect_github_suggestion(user_message.content)
                or detect_commute_suggestion(user_message.content)
                or detect_resources_suggestion(user_message.content)
                or detect_career_suggestion(user_message.content)
            )
            if suggestion is not None:
                deps.repo.update_message_module_suggestion(
                    run.account_id,
                    run.assistant_message_id,
                    suggestion,
                    datetime.now(UTC),
                )
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
