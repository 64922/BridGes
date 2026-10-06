"""复合步骤通过质量门后的公开快照与游标事件原子发布。"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime

from bridges.chat.repository import ConversationRepository, GenerationRunRecord
from bridges.chat.turn_result import outcome_label, trust_label
from bridges.contracts.chat import (
    ResultTrust,
    TurnOutcome,
    TurnResultBlock,
    TurnResultProjection,
)
from bridges.kernel.guard import RunCommitGuard
from bridges.orchestration.contracts import StepResult, StepState
from bridges.state_copy.catalog import COMPOSITE_MODULE_LABELS
from bridges.tasks.repository import TaskRepository


class ProgressiveResultPublisher:
    """调度线程串行调用；拒绝停止、旧租约、旧任务版本与重复结果。"""

    def __init__(
        self, repo: ConversationRepository, run: GenerationRunRecord,
        task_ref: tuple[str | None, int | None], stop_event: threading.Event | None,
    ) -> None:
        self._repo = repo
        self._run = run
        self._task_ref = task_ref
        self._guard = RunCommitGuard(
            repo, account_id=run.account_id, run_id=run.run_id,
            conversation_id=run.conversation_id,
            assistant_message_id=run.assistant_message_id, stop_event=stop_event,
        )
        self._guard.capture()

    def publish(self, step: StepResult) -> None:
        if step.state is not StepState.COMPLETED or step.trust_state != "qualified":
            return
        run = self._run
        with self._repo.database.transaction():
            if not self._guard.verify().ok:
                return
            task = TaskRepository(self._repo.database).current_task(
                run.account_id, run.conversation_id,
            )
            task_ref = (task.task_id, task.current_version) if task else (None, None)
            if task_ref != self._task_ref:
                return
            message = self._repo.get_message(run.account_id, run.assistant_message_id)
            if message is None or message.status.value != "streaming":
                return
            # 快照为覆盖式协议；重放同一快照或租约恢复不会追加重复结果块。
            previous = message.turn_result or {}
            blocks = [TurnResultBlock.model_validate(item)
                      for item in previous.get("delivered", [])]
            if any(item.module_id == step.module_id for item in blocks):
                return
            blocks.append(TurnResultBlock(
                module_id=step.module_id,
                label=COMPOSITE_MODULE_LABELS[step.module_id],
                state="completed", trust=ResultTrust.QUALIFIED,
                # 仅取质量门重建的公开 Claim；summary/证据原文不作为发布输入。
                detail="\n".join(claim.text for claim in step.claims[:3]),
            ))
            route = message.route or {}
            result = TurnResultProjection(
                outcome=TurnOutcome.RUNNING, outcome_label=outcome_label(TurnOutcome.RUNNING),
                trust=ResultTrust.QUALIFIED, trust_label=trust_label(ResultTrust.QUALIFIED),
                delivered=blocks, requested_module_id=route.get("requested_module_id"),
                actual_module_id=route.get("module_id"),
                capability_list=route.get("capability_list") or [],
                route_source=route.get("route_source"),
                task_id=task_ref[0], task_version=task_ref[1],
            ).model_dump(mode="json")
            self._repo.database.scoped(run.account_id).execute(
                "UPDATE messages SET turn_result = ? WHERE message_id = ? AND account_id = ?",
                (json.dumps(result, ensure_ascii=False), run.assistant_message_id, run.account_id),
            )
            self._repo.append_generation_event_in_transaction(
                run.account_id, run.run_id, "result",
                {"kind": "result", "message_id": run.assistant_message_id, "result": result},
                datetime.now(UTC),
            )
