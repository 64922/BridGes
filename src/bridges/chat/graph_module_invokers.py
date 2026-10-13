"""日常父图的module invokers实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bridges.career_plan.service import (
    CAREER_MODULE_ID,
    CareerModuleError,
    CareerSupersededError,
)
from bridges.chat.run_executor import chat_run_context
from bridges.commute.service import (
    COMMUTE_MODULE_ID,
    CommuteModuleError,
    CommuteSupersededError,
)
from bridges.contracts.understanding import (
    HardConditionKind,
    understanding_from_snapshot,
)
from bridges.github.service import (
    GITHUB_MODULE_ID,
    GithubModuleError,
    GithubSupersededError,
)
from bridges.paper.service import (
    PAPER_MODULE_ID,
    PaperModuleError,
    PaperSupersededError,
)
from bridges.resources.service import (
    RESOURCES_MODULE_ID,
    ResourcesModuleError,
    ResourcesSupersededError,
)
from bridges.tieba.service import (
    TIEBA_MODULE_ID,
    TiebaModuleError,
)

if TYPE_CHECKING:
    pass

#: 日常父图名称/版本（运行状态关联字段；节点集变化时递增）。
#: v2：论文子图接入 invoke_subgraph_or_chat（首个显式模块，Issue 11）。
#: v2 不变：Issue 12（通勤）与 Issue 13（资料）都只放开显式模块白名单，
#: 复用同一派发节点，节点集没有变化——旧检查点不会出现这两个模块的派发
#: （当时它们在 select 处被拒），续跑语义一致。
from bridges.chat.graph_contracts import (
    NODE_INVOKE_SUBGRAPH_OR_CHAT,
    DailyGraphSuperseded,
    DailyTurnError,
    DailyTurnState,
    _GraphDeps,
)


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
            run_context=chat_run_context(run.account_id, run.conversation_id, run.run_id),
            run_model_id=state.get("run_model_id"),
            model_quota=deps.service.run_model_quota(run),
            emit_node=emit_node,
            stop_event=deps.stop_event,
            module_context=deps.service.module_task_context(run, TIEBA_MODULE_ID),
            official_blocked_reason=_tieba_official_blocked_reason(run),
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
def _tieba_official_blocked_reason(run: Any) -> str | None:
    """用户硬条件只查贴吧时，官方核验路径必须被阻断（来源限制不被绕过）。"""
    from bridges.tieba.kernel import TIEBA_ONLY_OFFICIAL_BLOCKED

    understanding = understanding_from_snapshot((run.config or {}).get("understanding"))
    if understanding is None:
        return None
    for item in [
        *understanding.hard_conditions,
        *understanding.effective_hard_conditions,
    ]:
        if item.kind is not HardConditionKind.SOURCE_RESTRICTION:
            continue
        if any(word in item.text for word in ("贴吧", "吧里", "吧内")):
            return TIEBA_ONLY_OFFICIAL_BLOCKED
    return None
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
            requirement=_github_requirement(run),
            defer_finalization=True,
        )
    except GithubSupersededError as error:
        # 迟到结果：本轮不再写交付终态，交给当前持有执行权的执行者收尾。
        raise DailyGraphSuperseded(str(error)) from error
    except GithubModuleError as error:
        raise DailyTurnError(
            error.node, error.code, error.message, retryable=error.retryable
        ) from error
    if outcome.wait_reason is not None:
        deps.repo.update_generation_progress(
            run.account_id, run.run_id, wait_reason=outcome.wait_reason
        )
    if outcome.delivery is None:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT, "github_delivery_missing",
            "GitHub 模块缺少待核验交付，本轮未提交。", retryable=True,
        )
    return {"github_delivery": outcome.delivery.model_dump(mode="json"), "github_verified": False}
def _github_requirement(run: Any) -> Any:
    """运行配置里的类型化需求产物（工单 26 接缝；非法载荷按缺失处理）。

    生产端由工单 37 的跨模块依赖负责；本模块只读既定产物引用，不自行认领
    论文/岗位的身份。
    """
    raw = (run.config or {}).get("github_requirement")
    if not isinstance(raw, dict):
        return None
    from bridges.github.contracts import GithubRequirementInput

    try:
        return GithubRequirementInput.model_validate(raw)
    except ValueError:
        return None
def _invoke_resources_module(deps: _GraphDeps, state: DailyTurnState) -> dict[str, Any]:
    """资料子图执行体：节点进度经同一 ``node`` 事件与 current_node 透传。

    与通勤子图共用同一套内核执行、等待与失败合同；本模块的正文完全由真实
    证据渲染，不调用模型（``run_model_id`` 原样传入但被模块忽略）。
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
            run_context=chat_run_context(run.account_id, run.conversation_id, run.run_id),
            run_model_id=state.get("run_model_id"),
            emit_node=emit_node,
            stop_event=deps.stop_event,
            module_context=deps.service.module_task_context(
                run, RESOURCES_MODULE_ID
            ),
        )
    except ResourcesSupersededError as error:
        # 迟到结果：本轮不再写交付终态，交给当前持有执行权的执行者收尾。
        raise DailyGraphSuperseded(str(error)) from error
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
            run_context=chat_run_context(run.account_id, run.conversation_id, run.run_id),
            emit_node=emit_node,
            stop_event=deps.stop_event,
            module_context=deps.service.module_task_context(run, CAREER_MODULE_ID),
            **request_options,
        )
    except CareerSupersededError as error:
        # 迟到结果：本轮不再写交付终态，交给当前持有执行权的执行者收尾。
        raise DailyGraphSuperseded(str(error)) from error
    except CareerModuleError as error:
        raise DailyTurnError(
            error.node, error.code, error.message, retryable=error.retryable
        ) from error
    if outcome.wait_reason is not None:
        deps.repo.update_generation_progress(
            run.account_id, run.run_id, wait_reason=outcome.wait_reason
        )
    return {}
