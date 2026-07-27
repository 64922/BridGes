"""Spaced-repetition and interleaved-practice scheduling service (T024).

ReviewSchedulingService turns confirmed knowledge states and learning records
into explainable review tasks. Tasks reference the evidence that justifies them,
can be postponed, adjusted or cancelled by the user, and produce new learning
evidence on completion without overwriting the previous knowledge state.
"""

from __future__ import annotations

import contextlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from science_companion.contracts.learning import (
    AnswerEvaluatedState,
    KnowledgeState,
    KnowledgeStateStatus,
    LearningRecord,
    LearningRecordSource,
    LearningRecordType,
    ReviewSchedule,
    ReviewTask,
    ReviewTaskAdjustRequest,
    ReviewTaskCancelRequest,
    ReviewTaskCompleteRequest,
    ReviewTaskPostponeRequest,
    ReviewTaskStatus,
    ReviewTaskType,
)
from science_companion.learning.adapters import LearningError
from science_companion.learning.ports import LearningRepository

if TYPE_CHECKING:
    from science_companion.contracts.workflows import RunProjection
    from science_companion.workflows.service import WorkflowService


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return secrets.token_urlsafe(16)


# Spaced repetition intervals in days. Each successful delayed retrieval doubles
# the interval up to a ceiling, mirroring a simple SM-2-like schedule.
_BASE_INTERVAL_DAYS = 1
_INTERVAL_CEILING_DAYS = 60


def _compute_interval_days(records: list[LearningRecord], state: KnowledgeState | None) -> int:
    """Compute the next review interval from learning evidence.

    Successful delayed retrievals or transfer tasks extend the interval;
    incorrect or partial results reset it. Emerging or unknown states keep the
    interval short so the learner gets interleaved practice.
    """
    if state is not None and state.status in {
        KnowledgeStateStatus.UNKNOWN,
        KnowledgeStateStatus.EMERGING,
    }:
        return _BASE_INTERVAL_DAYS

    successful_reviews = 0
    latest_eval: AnswerEvaluatedState | None = None
    for record in sorted(records, key=lambda r: r.created_at):
        if record.evaluated_state == AnswerEvaluatedState.CORRECT:
            successful_reviews += 1
            latest_eval = record.evaluated_state
        elif record.evaluated_state in {
            AnswerEvaluatedState.INCORRECT,
            AnswerEvaluatedState.PARTIAL,
        }:
            latest_eval = record.evaluated_state

    if latest_eval in {AnswerEvaluatedState.INCORRECT, AnswerEvaluatedState.PARTIAL}:
        return _BASE_INTERVAL_DAYS

    interval: int = _BASE_INTERVAL_DAYS * (2 ** max(0, successful_reviews - 1))
    return int(min(interval, _INTERVAL_CEILING_DAYS))


def _task_type_from_state(state: KnowledgeState | None) -> ReviewTaskType:
    if state is not None and state.status in {
        KnowledgeStateStatus.SUPPORTED,
        KnowledgeStateStatus.ROBUST,
    }:
        return ReviewTaskType.SPACED_REPETITION
    return ReviewTaskType.INTERLEAVED_PRACTICE


class ReviewSchedulingService:
    """Application service for spaced repetition and interleaved practice."""

    def __init__(self, repository: LearningRepository) -> None:
        self._repository = repository

    def schedule_reviews_for_mission(
        self, account_id: str, mission_id: str
    ) -> ReviewSchedule:
        """Compute and persist review tasks for a mission.

        Only concepts with at least one learning record receive a task; browsing
        and completion-only activities are never treated as mastery evidence.
        """
        mission = self._repository.get_mission(account_id, mission_id)
        records = self._repository.list_learning_records(account_id, mission_id)
        states = self._repository.list_knowledge_states_for_mission(account_id, mission_id)
        state_by_concept = {s.concept_id: s for s in states}

        # Cancel stale pending tasks before scheduling new ones.
        self._cancel_pending_tasks_for_mission(
            account_id, mission_id, reason="学习路径已重新编译，取消旧复习安排。"
        )

        task_ids: list[str] = []
        now = _now()
        for concept in mission.scope_concepts:
            concept_records = [r for r in records if r.concept_id == concept]
            if not concept_records:
                continue

            state = state_by_concept.get(concept)
            interval_days = _compute_interval_days(concept_records, state)
            task_type = _task_type_from_state(state)

            # Schedule from the most recent record, but never in the past.
            last_record = max(concept_records, key=lambda r: r.created_at)
            due_at = max(
                now,
                last_record.created_at + timedelta(days=interval_days),
            )

            # Simple forgetting evidence: elapsed time since the last record
            # relative to the interval.
            elapsed = now - last_record.created_at
            forgetting_note = ""
            if elapsed > timedelta(days=interval_days):
                forgetting_note = (
                    f"距上次学习已 {elapsed.days} 天，超过当前间隔 {interval_days} 天，"
                    "存在遗忘风险。"
                )
            else:
                forgetting_note = (
                    f"距上次学习 {elapsed.days} 天，按 {interval_days} 天间隔安排复习。"
                )

            task_type_label = (
                "间隔复习"
                if task_type == ReviewTaskType.SPACED_REPETITION
                else "交错练习"
            )
            reason = (
                f"学习使命“{mission.title}”要求掌握“{concept}”。"
                f"当前知识状态：{state.status.value if state else 'unknown'}。"
                f"基于 {len(concept_records)} 条学习记录，"
                f"安排{task_type_label}。"
                f"{forgetting_note}"
            )

            task = ReviewTask(
                task_id=_new_id(),
                mission_id=mission_id,
                owner_account_id=account_id,
                concept_id=concept,
                task_type=task_type,
                status=ReviewTaskStatus.SCHEDULED,
                due_at=due_at,
                reason=reason,
                source_record_ids=[r.record_id for r in concept_records],
                knowledge_state_id=state.state_id if state else None,
                interval_days=interval_days,
                postponed_to=None,
                cancellation_reason=None,
                run_id=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
            self._repository.save_review_task(task)
            task_ids.append(task.task_id)

        schedule = ReviewSchedule(
            schedule_id=_new_id(),
            mission_id=mission_id,
            owner_account_id=account_id,
            task_ids=task_ids,
            version=1,
            created_at=now,
            updated_at=now,
        )
        return self._repository.save_review_schedule(schedule)

    def get_review_schedule(
        self, account_id: str, mission_id: str
    ) -> ReviewSchedule:
        """Return the latest review schedule for a mission, computing one if absent."""
        self._repository.get_mission(account_id, mission_id)
        schedule = self._repository.get_review_schedule_for_mission(account_id, mission_id)
        if schedule is None:
            return self.schedule_reviews_for_mission(account_id, mission_id)
        return schedule

    def list_pending_tasks(
        self,
        account_id: str,
        mission_id: str,
        due_before: datetime | None = None,
    ) -> list[ReviewTask]:
        """List scheduled or postponed review tasks, optionally filtered by due date.

        Tasks are returned interleaved by concept and ordered by due date so that
        the learner practises related concepts in a mixed sequence.
        """
        self._repository.get_mission(account_id, mission_id)
        tasks = self._repository.list_review_tasks(account_id, mission_id)
        pending = [
            t
            for t in tasks
            if t.status in {ReviewTaskStatus.SCHEDULED, ReviewTaskStatus.POSTPONED}
            and (due_before is None or t.due_at <= due_before)
        ]
        # Interleave: group by concept, then round-robin.
        by_concept: dict[str, list[ReviewTask]] = {}
        for task in sorted(pending, key=lambda t: t.due_at):
            by_concept.setdefault(task.concept_id, []).append(task)

        interleaved: list[ReviewTask] = []
        concepts = sorted(by_concept.keys())
        index = 0
        while any(by_concept[c] for c in concepts):
            concept = concepts[index % len(concepts)]
            if by_concept[concept]:
                interleaved.append(by_concept[concept].pop(0))
            index += 1
        return interleaved

    def get_review_task(self, account_id: str, task_id: str) -> ReviewTask:
        """Return a review task if the account owns it."""
        return self._repository.get_review_task(account_id, task_id)

    def postpone_review_task(
        self,
        account_id: str,
        task_id: str,
        request: ReviewTaskPostponeRequest,
    ) -> ReviewTask:
        """Postpone a review task to a new due date."""
        task = self._repository.get_review_task(account_id, task_id)
        if task.status not in {ReviewTaskStatus.SCHEDULED, ReviewTaskStatus.POSTPONED}:
            raise LearningError("只能延后已安排或已延后的复习任务。")
        if request.new_due_at <= _now():
            raise LearningError("新的到期时间必须在未来。")

        task.status = ReviewTaskStatus.POSTPONED
        task.due_at = request.new_due_at
        task.postponed_to = request.new_due_at
        task.reason = f"{task.reason} [延后：{request.reason}]"
        task.version += 1
        task.updated_at = _now()
        return self._repository.save_review_task(task)

    def adjust_review_task(
        self,
        account_id: str,
        task_id: str,
        request: ReviewTaskAdjustRequest,
    ) -> ReviewTask:
        """Adjust the due date or interval of a review task."""
        task = self._repository.get_review_task(account_id, task_id)
        if task.status not in {ReviewTaskStatus.SCHEDULED, ReviewTaskStatus.POSTPONED}:
            raise LearningError("只能调整已安排或已延后的复习任务。")

        if request.new_due_at is not None:
            if request.new_due_at <= _now():
                raise LearningError("新的到期时间必须在未来。")
            task.due_at = request.new_due_at
            task.postponed_to = request.new_due_at
        if request.new_interval_days is not None:
            task.interval_days = request.new_interval_days

        task.status = ReviewTaskStatus.SCHEDULED
        task.reason = f"{task.reason} [调整：{request.reason}]"
        task.version += 1
        task.updated_at = _now()
        return self._repository.save_review_task(task)

    def cancel_review_task(
        self,
        account_id: str,
        task_id: str,
        request: ReviewTaskCancelRequest | None = None,
        *,
        workflow_service: WorkflowService | None = None,
    ) -> ReviewTask:
        """Cancel a pending review task and, if materialized, its workflow run."""
        task = self._repository.get_review_task(account_id, task_id)
        if task.status not in {ReviewTaskStatus.SCHEDULED, ReviewTaskStatus.POSTPONED}:
            raise LearningError("只能取消已安排或已延后的复习任务。")

        reason = request.reason if request else "用户取消。"
        task.status = ReviewTaskStatus.CANCELLED
        task.cancellation_reason = reason
        task.version += 1
        task.updated_at = _now()

        if task.run_id is not None and workflow_service is not None:
            from science_companion.workflows.service import WorkflowError

            with contextlib.suppress(WorkflowError):
                workflow_service.cancel_run(
                    account_id, task.run_id, f"review_task_cancelled:{reason}"
                )

        return self._repository.save_review_task(task)

    def complete_review_task(
        self,
        account_id: str,
        task_id: str,
        request: ReviewTaskCompleteRequest,
    ) -> tuple[ReviewTask, LearningRecord]:
        """Complete a review task and create a new learning record.

        The new record is evidence of a delayed retrieval; it is appended rather
        than overwriting the previous knowledge state.
        """
        task = self._repository.get_review_task(account_id, task_id)
        if task.status not in {ReviewTaskStatus.SCHEDULED, ReviewTaskStatus.POSTPONED}:
            raise LearningError("只能完成已安排或已延后的复习任务。")

        mission = self._repository.get_mission(account_id, task.mission_id)
        now = _now()

        record = LearningRecord(
            record_id=_new_id(),
            mission_id=mission.mission_id,
            owner_account_id=account_id,
            concept_id=task.concept_id,
            record_type=LearningRecordType.DELAYED_RETRIEVAL,
            source_type=LearningRecordSource.EXERCISE_ATTEMPT,
            source_id=task.task_id,
            response_text=request.response_text,
            evaluated_state=request.evaluated_state,
            misconception_corrected=None,
            evidence_refs=[],
            record_reason=request.record_reason,
            created_at=now,
        )
        saved_record = self._repository.save_learning_record(record)

        task.status = ReviewTaskStatus.COMPLETED
        task.version += 1
        task.updated_at = now
        saved_task = self._repository.save_review_task(task)
        return saved_task, saved_record

    def cancel_or_reschedule_on_path_change(
        self, account_id: str, mission_id: str
    ) -> ReviewSchedule:
        """Cancel or reschedule review tasks after a learning-path change.

        Tasks for concepts that are no longer on the path are cancelled. Tasks
        whose underlying knowledge state has been superseded are cancelled and
        replaced by a fresh schedule.
        """
        self._repository.get_mission(account_id, mission_id)
        path = self._repository.get_learning_path_for_mission(account_id, mission_id)
        states = self._repository.list_knowledge_states_for_mission(account_id, mission_id)
        state_by_concept = {s.concept_id: s for s in states}

        path_concepts = {n.concept_id for n in (path.nodes if path else [])}
        tasks = self._repository.list_review_tasks(account_id, mission_id)

        for task in tasks:
            if task.status not in {ReviewTaskStatus.SCHEDULED, ReviewTaskStatus.POSTPONED}:
                continue

            if task.concept_id not in path_concepts:
                task.status = ReviewTaskStatus.CANCELLED
                task.cancellation_reason = "该概念已不在当前学习路径中。"
                task.version += 1
                task.updated_at = _now()
                self._repository.save_review_task(task)
                continue

            current_state = state_by_concept.get(task.concept_id)
            if current_state is not None and task.knowledge_state_id != current_state.state_id:
                task.status = ReviewTaskStatus.CANCELLED
                task.cancellation_reason = "知识状态已更新，旧复习任务失效。"
                task.version += 1
                task.updated_at = _now()
                self._repository.save_review_task(task)

        return self.schedule_reviews_for_mission(account_id, mission_id)

    def submit_review_as_work_order(
        self,
        account_id: str,
        task_id: str,
        workflow_service: WorkflowService,
        *,
        project_id: str | None = None,
    ) -> RunProjection:
        """Materialize a review task as a WorkOrder on the task stage.

        This links the scheduled review to the timed-workflow seam introduced in
        T006, so the learner sees the review in the task stage and can cancel or
        advance it like any other run.
        """
        from science_companion.contracts.workflows import WorkOrder

        task = self._repository.get_review_task(account_id, task_id)
        if task.status not in {ReviewTaskStatus.SCHEDULED, ReviewTaskStatus.POSTPONED}:
            raise LearningError("只能将已安排或已延后的复习任务提交为工作流。")

        mission = self._repository.get_mission(account_id, task.mission_id)
        target_project_id = project_id or mission.project_id or task.mission_id

        order = WorkOrder(
            workflow_name="review_task",
            workflow_version="1",
            project_id=target_project_id,
            objective=f"复习：{task.concept_id}",
            success_criteria="正确回忆并解释该概念的核心内容。",
            risk_statement="本复习任务来自间隔复习调度，不改变事实锁或旧知识状态。",
            object_refs=[],
            memory_slice_refs=[],
            domain_pack_refs=[],
        )
        projection = workflow_service.submit_work_order(account_id, order)
        task.run_id = projection.run_id
        task.version += 1
        task.updated_at = _now()
        self._repository.save_review_task(task)
        return projection

    def _cancel_pending_tasks_for_mission(
        self, account_id: str, mission_id: str, reason: str
    ) -> None:
        tasks = self._repository.list_review_tasks(account_id, mission_id)
        for task in tasks:
            if task.status in {ReviewTaskStatus.SCHEDULED, ReviewTaskStatus.POSTPONED}:
                task.status = ReviewTaskStatus.CANCELLED
                task.cancellation_reason = reason
                task.version += 1
                task.updated_at = _now()
                self._repository.save_review_task(task)
