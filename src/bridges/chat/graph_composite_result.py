"""日常父图的composite result实现。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bridges.contracts.chat import (
    ChatMessageStatus,
)
from bridges.state_copy import render_state_copy

if TYPE_CHECKING:
    pass

#: 日常父图名称/版本（运行状态关联字段；节点集变化时递增）。
#: v2：论文子图接入 invoke_subgraph_or_chat（首个显式模块，Issue 11）。
#: v2 不变：Issue 12（通勤）与 Issue 13（资料）都只放开显式模块白名单，
#: 复用同一派发节点，节点集没有变化——旧检查点不会出现这两个模块的派发
#: （当时它们在 select 处被拒），续跑语义一致。
from bridges.chat.graph_contracts import (
    NODE_PERSIST_RESULT,
    NODE_VERIFY_OUTPUT,
    DailyGraphStop,
    DailyGraphSuperseded,
    DailyTurnError,
    DailyTurnState,
    _GraphDeps,
)


def _verify_composite_result(state: DailyTurnState) -> None:
    """父图核验：最终门通过且成功分支都有内核产物引用。"""
    from bridges.orchestration.contracts import (  # noqa: PLC0415
        CompositeOutcome,
        SynthesisGateResult,
    )

    outcome = CompositeOutcome.model_validate(state["composite_outcome"])
    gate = SynthesisGateResult.model_validate(state["composite_gate"])
    if not gate.passed:
        raise DailyTurnError(
            NODE_VERIFY_OUTPUT, "composite_gate_failed",
            render_state_copy("error.composite_gate_failed"), retryable=False,
        )
    missing = [
        step.step_id
        for step in outcome.steps
        if step.state.value == "completed"
        and step.delivery is not None
        and not step.artifact_refs
    ]
    if missing:
        raise DailyTurnError(
            NODE_VERIFY_OUTPUT, "composite_delivery_unverified",
            render_state_copy("error.composite_delivery_unverified"), retryable=True,
        )
def _persist_composite_result(deps: _GraphDeps, state: DailyTurnState) -> None:
    """父图在守卫事务中一次提交全部投影、正文与终态。"""
    from bridges.chat.turn import finalize_message  # noqa: PLC0415
    from bridges.kernel.guard import RunCommitGuard  # noqa: PLC0415
    from bridges.kernel.repository import NodeKernelRepository  # noqa: PLC0415
    from bridges.orchestration.contracts import (  # noqa: PLC0415
        CompositeOutcome,
        CompositeStatus,
        SynthesisDraft,
        SynthesisGateResult,
    )
    from bridges.orchestration.production import (  # noqa: PLC0415
        first_failure,
        hydrate_career_projection,
        message_status_for,
        persist_synthesis_artifact,
        projection_updates,
        render_final_content,
        turn_result_for_outcome,
    )

    if not state.get("composite_verified"):
        raise DailyTurnError(
            NODE_PERSIST_RESULT, "composite_delivery_unverified",
            render_state_copy("error.composite_delivery_unverified"), retryable=True,
        )
    outcome = CompositeOutcome.model_validate(state["composite_outcome"])
    gate = SynthesisGateResult.model_validate(state["composite_gate"])
    if not gate.passed:
        raise DailyTurnError(
            NODE_PERSIST_RESULT, "composite_gate_failed",
            render_state_copy("error.composite_gate_failed"), retryable=False,
        )
    if outcome.status is CompositeStatus.REJECTED:
        raise DailyGraphSuperseded(outcome.rejection_code or "composite_rejected")
    if outcome.status is CompositeStatus.STOPPED:
        raise DailyGraphStop(NODE_PERSIST_RESULT)
    run = deps.run
    draft = SynthesisDraft.model_validate(state["composite_draft"])
    content = render_final_content(draft)
    outcome = hydrate_career_projection(
        NodeKernelRepository(deps.repo.database),
        account_id=run.account_id,
        outcome=outcome,
    )
    projections = projection_updates(outcome)
    status = message_status_for(outcome)
    failure = first_failure(outcome)
    lock = next(
        (
            step.delivery.lock
            for step in outcome.steps
            if step.delivery is not None and step.delivery.lock is not None
        ),
        None,
    )
    with NodeKernelRepository(deps.repo.database).transaction():
        # 核对执行开始时的快照，不能在提交时认领别人的新租约或新任务版本。
        current_run = deps.repo.get_generation_run(run.account_id, run.run_id)
        if current_run is None or current_run.lease_owner != state.get("composite_lease_owner"):
            raise DailyGraphSuperseded("lease_lost")
        from bridges.tasks.repository import TaskRepository  # noqa: PLC0415

        current_task = TaskRepository(deps.repo.database).current_task(
            run.account_id, run.conversation_id
        )
        current_ref = (
            [current_task.task_id, current_task.current_version]
            if current_task is not None else [None, None]
        )
        if current_ref != state.get("composite_task_ref"):
            raise DailyGraphSuperseded("task_version_changed")
        guard = RunCommitGuard(
            deps.repo,
            account_id=run.account_id,
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            assistant_message_id=run.assistant_message_id,
            stop_event=deps.stop_event,
        )
        guard.capture()
        decision = guard.verify()
        if not decision.ok:
            if decision.code == "run_stopped":
                raise DailyGraphStop(NODE_PERSIST_RESULT)
            raise DailyGraphSuperseded(decision.code)
        message = deps.repo.get_message(run.account_id, run.assistant_message_id)
        # 提交后检查点尚未保存的恢复：相同交付只回放，不重复写入。
        if message is None or message.status == ChatMessageStatus.STREAMING:
            task_ref = state.get("composite_task_ref") or [None, None]
            finalize_message(
                deps.repo, run.account_id, run.assistant_message_id,
                status=status,
                error_code=(
                    failure.code
                    if failure is not None and status is ChatMessageStatus.ERROR
                    else None
                ),
                error_message=(
                    failure.message
                    if failure is not None and status is ChatMessageStatus.ERROR
                    else None
                ),
                duration_ms=None, model_id=None,
                run_lock_id=lock.lock_id if lock is not None else None,
                lock=lock, started=deps.started, now=datetime.now(UTC),
                final_content=content,
                paper_search=projections.get("paper_search"),
                tieba_research=projections.get("tieba_research"),
                career_plan=projections.get("career_plan"),
                learning_resources=projections.get("learning_resources"),
                commute_route=projections.get("commute_route"),
                github_projects=projections.get("github_projects"),
                # 工单 38：精确回合结果（含阻塞结论与步骤可信状态）随
                # 投影、正文与终态在同一守卫事务内提交。
                turn_result=turn_result_for_outcome(
                    outcome,
                    task_id=task_ref[0],
                    task_version=task_ref[1],
                ),
            )
            # 同一守卫事务内保存脱敏综合产物：计划/步骤/证据引用、任务版本、
            # 条件快照与门裁决；不重复保存模块正文或私人原文。
            persist_synthesis_artifact(
                NodeKernelRepository(deps.repo.database),
                account_id=run.account_id,
                conversation_id=run.conversation_id,
                run_id=run.run_id,
                task_id=task_ref[0],
                task_version=task_ref[1],
                outcome=outcome,
                gate=gate,
                draft=draft,
                conditions=[
                    item
                    for item in state.get("composite_conditions") or []
                    if isinstance(item, dict)
                ],
                now=datetime.now(UTC),
            )
        elif message.content != content or message.status != status:
            raise DailyGraphSuperseded("message_terminal")
    if status is ChatMessageStatus.ERROR and failure is not None:
        raise DailyTurnError(
            failure.node or NODE_PERSIST_RESULT,
            failure.code,
            failure.message,
            retryable=failure.retryable,
        )
