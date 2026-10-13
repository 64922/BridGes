"""日常父图的nodes实现。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig

from bridges.chat.run_executor import chat_run_context
from bridges.commute.service import (
    COMMUTE_MODULE_ID,
)
from bridges.contracts.chat import (
    CHAT_MODULE_VALUES,
    ChatMessageStatus,
)
from bridges.contracts.tasks import TaskRelation, TaskStatus
from bridges.contracts.understanding import (
    understanding_from_snapshot,
)
from bridges.paper.service import (
    PAPER_MODULE_ID,
)
from bridges.routing.contracts import MODULE_CAPABILITIES, RouteStatus
from bridges.state_copy import render_state_copy
from bridges.tieba.service import (
    TIEBA_MODULE_ID,
)

if TYPE_CHECKING:
    pass

#: 日常父图名称/版本（运行状态关联字段；节点集变化时递增）。
#: v2：论文子图接入 invoke_subgraph_or_chat（首个显式模块，Issue 11）。
#: v2 不变：Issue 12（通勤）与 Issue 13（资料）都只放开显式模块白名单，
#: 复用同一派发节点，节点集没有变化——旧检查点不会出现这两个模块的派发
#: （当时它们在 select 处被拒），续跑语义一致。
from bridges.chat.graph_composite_plan import _invoke_composite_plan
from bridges.chat.graph_contracts import (
    AVAILABLE_MODULE_IDS,
    NODE_COMPILE_CONTEXT,
    NODE_INVOKE_SUBGRAPH_OR_CHAT,
    NODE_SELECT_EXPLICIT_MODULE,
    NODE_VALIDATE_TURN,
    RUN_CONFIG_MODULE_ID,
    DailyGraphStop,
    DailyTurnError,
    DailyTurnState,
    NodeBody,
    _GraphDeps,
)
from bridges.chat.graph_module_invokers import (
    _invoke_commute_module,
    _invoke_paper_module,
    _invoke_tieba_module,
)


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
    state: DailyTurnState,
    config: RunnableConfig,
    *,
    available_module_ids: frozenset[str] = AVAILABLE_MODULE_IDS,
) -> dict[str, Any]:
    """按已校验实际模块派发；旧消息保留原显式模块语义。"""
    deps: _GraphDeps = config["configurable"]["deps"]
    module_id = state.get("module_id")
    if module_id is not None and module_id not in available_module_ids:
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
    state: DailyTurnState,
    config: RunnableConfig,
    *,
    available_module_ids: frozenset[str] = AVAILABLE_MODULE_IDS,
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
    if dispatch not in {"chat", *available_module_ids}:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT, "module_not_available",
            "该模块已取消，无法继续执行。", retryable=False,
        )
    if understanding is not None and any(
        module not in available_module_ids for module in understanding.capability_list
    ):
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT, "module_not_available",
            "任务包含已取消的模块，请发送新的消息。", retryable=False,
        )
    if route.get("status") in {RouteStatus.CLARIFY, RouteStatus.REJECTED} or (
        understanding is not None
        and understanding.task_relation in {TaskRelation.PAUSE, TaskRelation.CANCEL}
    ):
        dispatch = "chat"
    if (
        dispatch == "chat"
        and understanding is not None
        and understanding.task_relation is TaskRelation.REVISE
        and not understanding.capability_list
    ):
        # 工单 37：修订轮只改条件（如“换成杭州”）时按上一轮综合恢复计划，
        # 重算受条件影响的步骤、复用未受影响的公共材料。
        from bridges.orchestration.planner import CompositePlanner  # noqa: PLC0415
        from bridges.orchestration.production import (  # noqa: PLC0415
            changed_condition_keys,
            load_prior_composite,
            previous_composite_modules,
        )

        prior_payload = load_prior_composite(
            deps.repo,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
        )
        modules = previous_composite_modules(prior_payload)
        conditions = [
            *understanding.hard_conditions,
            *understanding.effective_hard_conditions,
        ]
        changed = set(changed_condition_keys(prior_payload, conditions))
        if (
            modules
            and changed & {"city", "year_range"}
            and CompositePlanner().combination_for(modules) is not None
        ):
            return _invoke_composite_plan(
                deps,
                state,
                understanding.model_copy(update={"capability_list": modules}),
            )
    if dispatch in available_module_ids:
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
    if (
        dispatch == "chat"
        and understanding is not None
        and len(understanding.capability_list) > 1
    ):
        # 工单 37：已登记跨模块组合走复合计划（模块不自行结束助手消息）。
        return _invoke_composite_plan(deps, state, understanding)
    if dispatch == PAPER_MODULE_ID:
        return _invoke_paper_module(deps, state)
    if dispatch == TIEBA_MODULE_ID:
        return _invoke_tieba_module(deps, state)
    if dispatch == COMMUTE_MODULE_ID:
        return _invoke_commute_module(deps, state)
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
