"""日常父图的results实现。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig

from bridges.career_plan.suggestion import detect_career_suggestion
from bridges.chat.graph_composite_result import (
    _persist_composite_result,
    _verify_composite_result,
)
from bridges.chat.graph_contracts import (
    NODE_PERSIST_RESULT,
    NODE_VERIFY_OUTPUT,
    DailyGraphStop,
    DailyGraphSuperseded,
    DailyTurnError,
    DailyTurnState,
    _GraphDeps,
)
from bridges.chat.terminal import (
    INTERNAL_ERROR_CODE,
)
from bridges.commute.suggestion import detect_commute_suggestion
from bridges.contracts.chat import (
    ChatMessageStatus,
)
from bridges.github.service import (
    GithubDelivery,
    GithubSupersededError,
)
from bridges.github.suggestion import detect_github_suggestion
from bridges.paper.suggestion import detect_paper_suggestion
from bridges.resources.suggestion import detect_resources_suggestion
from bridges.tieba.suggestion import detect_tieba_suggestion

if TYPE_CHECKING:
    pass

def _node_verify_output(
    state: DailyTurnState, config: RunnableConfig
) -> dict[str, Any]:
    """核验输出：本轮已收敛为明确终态（完成或诚实失败），绝不悬空。"""
    deps: _GraphDeps = config["configurable"]["deps"]
    run = deps.run
    if state.get("composite_outcome") is not None:
        _verify_composite_result(state)
        return {"composite_verified": True}
    if state.get("github_delivery") is not None:
        delivery = GithubDelivery.model_validate(state["github_delivery"])
        _verify_github_delivery(deps, delivery)
        return {"github_verified": True}
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
    if (
        message.tieba_research is not None
        and message.tieba_research.get("status") != "clarification"
    ):
        service = deps.service.tieba_research_service
        if service is None or not service.verify_message(
            deps.repo, account_id=run.account_id, run_id=run.run_id,
            conversation_id=run.conversation_id, assistant_message_id=run.assistant_message_id,
        ):
            raise DailyTurnError(
                NODE_VERIFY_OUTPUT, "tieba_delivery_unverified",
                "贴吧交付与可信核验产物不一致，本轮未通过核验。", retryable=False,
            )
    return {}
def _verify_github_delivery(deps: _GraphDeps, delivery: GithubDelivery) -> None:
    """读取真实核验产物并复验最终正文，拒绝无核验支持的成功交付。"""
    from bridges.github.contracts import GithubProjectStatus
    from bridges.github.presenting import (
        render_clarification_content,
        render_empty_content,
        render_result_content,
        render_stopped_content,
        safe_insight_choices,
    )
    from bridges.kernel.contracts import ArtifactTrust
    from bridges.kernel.repository import NodeKernelRepository

    projection = delivery.projection
    if projection.status is GithubProjectStatus.CLARIFICATION:
        valid = projection.pending is not None and not projection.recommendations
        expected_content = render_clarification_content(projection)
    elif delivery.message_status is ChatMessageStatus.ERROR:
        valid = (
            projection.status is GithubProjectStatus.ERROR
            and bool(projection.error_code) and not projection.recommendations
        )
        expected_content = render_empty_content(projection)
    elif delivery.message_status is ChatMessageStatus.STOPPED:
        valid = projection.status is GithubProjectStatus.STOPPED
        expected_content = render_stopped_content(projection)
    else:
        artifact = NodeKernelRepository(deps.repo.database).get_artifact(
            deps.run.account_id, delivery.verification_artifact_id or ""
        )
        valid = bool(
            artifact is not None and artifact.node == "github.verify"
            and artifact.conversation_id == deps.run.conversation_id
            and artifact.trust_state is ArtifactTrust.QUALIFIED
        )
        if valid:
            github_service = deps.service.github_projects_service
            if github_service is None:
                raise DailyTurnError(
                    NODE_VERIFY_OUTPUT, "github_service_unavailable",
                    "GitHub 服务当前不可用，无法核验交付。", retryable=True,
                )
            valid = github_service.verify_delivery_projection(
                deps.repo, delivery, account_id=deps.run.account_id, run_id=deps.run.run_id,
                conversation_id=deps.run.conversation_id,
            )
        expected_content = (
            render_result_content(projection) if projection.recommendations
            else render_empty_content(projection)
        )
    valid = valid and all(
        item.insight_zh is None or item.insight_zh in safe_insight_choices(item)
        for item in projection.recommendations
    )
    if not valid or delivery.content != expected_content:
        raise DailyTurnError(
            NODE_VERIFY_OUTPUT, "github_delivery_unverified",
            "GitHub 最终交付缺少有效核验依据，本轮未提交。", retryable=True,
        )
def _node_persist_result(
    state: DailyTurnState,
    config: RunnableConfig,
    *,
    paper_suggestion_detector: Callable[[str], dict[str, object] | None] = detect_paper_suggestion,
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
    if state.get("composite_outcome") is not None:
        _persist_composite_result(deps, state)
        return {}
    if state.get("github_delivery") is not None:
        _persist_github_delivery(deps, state)
    if state.get("module_dispatch") == "chat":
        user_message = deps.repo.get_message(run.account_id, run.user_message_id)
        if user_message is not None:
            # 一条消息只给一个建议：论文建议优先（其请求形态更明确），
            # 未命中时才依次考虑贴吧、GitHub、通勤与资料建议；五者都只
            # 建议，不后台执行。
            suggestion = (
                paper_suggestion_detector(user_message.content)
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
def _persist_github_delivery(deps: _GraphDeps, state: DailyTurnState) -> None:
    """父图在共享事务中统一提交 GitHub 结果与终态。"""
    from bridges.chat.turn import finalize_message
    from bridges.kernel.repository import NodeKernelRepository

    if not state.get("github_verified"):
        raise DailyTurnError(
            NODE_PERSIST_RESULT, "github_delivery_unverified",
            "GitHub 交付尚未通过父图核验，本轮未提交。", retryable=True,
        )
    delivery = GithubDelivery.model_validate(state["github_delivery"])
    run = deps.run
    with NodeKernelRepository(deps.repo.database).transaction():
        _verify_github_delivery(deps, delivery)
        message = deps.repo.get_message(run.account_id, run.assistant_message_id)
        # 提交后检查点尚未保存的恢复：相同交付只回放，不重复写入。
        if message is None or message.status == ChatMessageStatus.STREAMING:
            github_service = deps.service.github_projects_service
            if github_service is None:
                raise DailyTurnError(
                    NODE_PERSIST_RESULT, "github_service_unavailable",
                    "GitHub 服务当前不可用，无法提交交付。", retryable=True,
                )
            try:
                github_service.verify_delivery_scope(
                    deps.repo, delivery, account_id=run.account_id, run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    assistant_message_id=run.assistant_message_id, stop_event=deps.stop_event,
                    expected_lease_owner=run.lease_owner,
                )
            except GithubSupersededError as error:
                if str(error) == "run_stopped":
                    raise DailyGraphStop(NODE_PERSIST_RESULT) from error
                raise DailyGraphSuperseded(str(error)) from error
            finalize_message(
                deps.repo, run.account_id, run.assistant_message_id,
                status=delivery.message_status,
                error_code=delivery.projection.error_code,
                error_message=delivery.projection.error_message,
                duration_ms=None, model_id=None,
                run_lock_id=delivery.lock.lock_id if delivery.lock else None,
                lock=delivery.lock, started=deps.started, now=datetime.now(UTC),
                github_projects=delivery.projection.model_dump(mode="json"),
                final_content=delivery.content,
            )
        elif message.content != delivery.content or message.status != delivery.message_status:
            raise DailyGraphSuperseded("message_terminal")
    if delivery.message_status is ChatMessageStatus.ERROR:
        raise DailyTurnError(
            delivery.error_node or NODE_PERSIST_RESULT,
            delivery.projection.error_code or "github_failed",
            delivery.projection.error_message or "GitHub 项目推荐失败，请稍后重试。",
            retryable=delivery.projection.retryable,
        )
