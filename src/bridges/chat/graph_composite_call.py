"""日常父图的composite call实现。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from bridges.career_plan.service import (
    CAREER_MODULE_ID,
    CareerSupersededError,
)
from bridges.chat.graph_composite_plan import (
    _COMPOSITE_SERVICE_ATTRS,
    _composite_github_requirement,
)
from bridges.chat.run_executor import chat_run_context
from bridges.github.service import (
    GITHUB_MODULE_ID,
    GithubSupersededError,
)
from bridges.paper.service import (
    PAPER_MODULE_ID,
    PaperSupersededError,
)
from bridges.resources.service import (
    RESOURCES_MODULE_ID,
    ResourcesSupersededError,
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
    DailyTurnError,
    DailyTurnState,
    _GraphDeps,
)


def _composite_run_call(
    deps: _GraphDeps,
    state: DailyTurnState,
    *,
    module_id: str,
    goal: str,
    superseded: list[str],
) -> Callable[[Any, Any, Any], Any]:
    """构造一个模块的无终态调用（``defer_finalization=True``）。"""
    run = deps.run
    service = getattr(deps.service, _COMPOSITE_SERVICE_ATTRS[module_id], None)
    if service is None:
        raise DailyTurnError(
            NODE_INVOKE_SUBGRAPH_OR_CHAT, "module_not_available",
            "复合计划所需模块当前不可用，请稍后重试。", retryable=True,
        )

    def emit_node(node: str, status: str, duration_ms: int | None) -> None:
        deps.emit_node(
            state["assistant_message_id"], node, status, duration_ms=duration_ms
        )

    def call(step: Any, context: Any, resolved: Any) -> Any:
        del step
        common: dict[str, Any] = {
            "repo": deps.repo,
            "account_id": run.account_id,
            "conversation_id": run.conversation_id,
            "user_message_id": run.user_message_id,
            "assistant_message_id": run.assistant_message_id,
            "run_context": chat_run_context(
                run.account_id, run.conversation_id, run.run_id
            ),
            "emit_node": emit_node,
            "stop_event": deps.stop_event,
            "defer_finalization": True,
        }
        try:
            if module_id == PAPER_MODULE_ID:
                return service.run(
                    **common,
                    run_model_id=state.get("run_model_id"),
                    module_context=deps.service.module_task_context(
                        run, PAPER_MODULE_ID
                    ),
                    model_quota=deps.service.run_model_quota(run),
                    manifest_sink=lambda manifest: deps.service.audit_module_manifest(
                        run, "paper.summarize", manifest
                    ),
                    assessment_manifest_sink=lambda manifest: (
                        deps.service.audit_module_manifest(
                            run, "paper.assess", manifest
                        )
                    ),
                    writing_policy=(run.config or {}).get("global_writing_policy"),
                )
            if module_id == RESOURCES_MODULE_ID:
                return service.run(
                    **common,
                    run_model_id=state.get("run_model_id"),
                    request_text=resolved.get("topic"),
                    module_context=deps.service.module_task_context(
                        run, RESOURCES_MODULE_ID
                    ),
                )
            if module_id == CAREER_MODULE_ID:
                return service.run(
                    **common,
                    run_model_id=state.get("run_model_id"),
                    request_text=goal or None,
                    module_context=deps.service.module_task_context(
                        run, CAREER_MODULE_ID
                    ),
                )
            return service.run(
                **common,
                run_model_id=state.get("run_model_id"),
                module_context=deps.service.module_task_context(run, GITHUB_MODULE_ID),
                model_quota=deps.service.run_model_quota(run),
                manifest_sink=lambda manifest: deps.service.audit_module_manifest(
                    run, "github.insight", manifest
                ),
                requirement=_composite_github_requirement(context, goal),
            )
        except (
            PaperSupersededError,
            ResourcesSupersededError,
            CareerSupersededError,
            GithubSupersededError,
        ) as error:
            # 迟到结果：整轮由父图守卫统一拒绝，不写任何终态。
            superseded.append(str(error))
            raise

    return call
