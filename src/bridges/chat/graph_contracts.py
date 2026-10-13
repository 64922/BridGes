"""日常 LangGraph 父图的共享类型与依赖（V2 Issue 02：可恢复的对话运行）。

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
from typing import TYPE_CHECKING, Any, TypedDict

from langchain_core.runnables import RunnableConfig

from bridges.ai.adapters import StreamEvent
from bridges.chat.terminal import (
    TerminalOutcome,
    stopped_outcome,
)
from bridges.commute.service import (
    COMMUTE_MODULE_ID,
    COMMUTE_NODE_LABELS,
)
from bridges.contracts.chat import (
    ChatMessageStatus,
    ChatStreamNodeData,
)
from bridges.paper.service import (
    PAPER_MODULE_ID,
    PAPER_NODE_LABELS,
)
from bridges.state_copy import RecoveryAction, error_recovery, render_state_copy
from bridges.tieba.service import (
    TIEBA_MODULE_ID,
    TIEBA_NODE_LABELS,
)

if TYPE_CHECKING:
    from bridges.chat.repository import GenerationRunRecord
    from bridges.chat.service import ChatService

#: 日常父图名称/版本（运行状态关联字段；节点集变化时递增）。
#: v2：论文子图接入 invoke_subgraph_or_chat（首个显式模块，Issue 11）。
#: v3：收敛为论文、通勤和贴吧；旧检查点安全结束，避免恢复已退役模块。
DAILY_GRAPH_VERSION = "daily-parent-v3"

#: 旧日常图版本运行的安全结束码（R06）：旧检查点谱系不套当前节点集恢复，
#: 安全结束并保留历史；用户明确重试创建绑定当前版本的新运行。
DAILY_GRAPH_VERSION_CHANGED = "daily_graph_version_changed"

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

#: 当前日常模块白名单；退役模块不得派发或恢复执行。
AVAILABLE_MODULE_IDS: frozenset[str] = frozenset(
    {
        PAPER_MODULE_ID,
        COMMUTE_MODULE_ID,
        TIEBA_MODULE_ID,
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
    **COMMUTE_NODE_LABELS,
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


class DailyGraphStop(Exception):  # noqa: N818
    """用户停止：在可取消节点边界终止，不再进入后续节点。"""

    def __init__(self, node: str) -> None:
        super().__init__(f"run stopped at node {node}")
        self.node = node


class DailyGraphSuperseded(Exception):  # noqa: N818
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
    #: GitHub 子图交付先进入检查点，父图核验后再统一提交消息。
    github_delivery: dict[str, Any] | None
    github_verified: bool
    #: 工单 37 复合运行：计划/结果/综合/最终门先进入检查点，父图核验后
    #: 在守卫事务内一次性提交全部投影与正文。
    composite_outcome: dict[str, Any] | None
    composite_draft: dict[str, Any] | None
    composite_gate: dict[str, Any] | None
    composite_verified: bool
    composite_lease_owner: str | None
    composite_task_ref: list[Any]
    #: 工单 37：本轮硬条件快照（脱敏），供下一轮比较条件变化与跨轮复用。
    composite_conditions: list[dict[str, str]] | None


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
