"""对话内教学序列的持久化与自适应推进（Issue 19）。"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.learning import AnswerEvaluatedState
from bridges.contracts.teaching import (
    TeachingArtifactStatus,
    TeachingLessonProjection,
    TeachingPlanProjection,
    TeachingPlanSession,
    TeachingQuiz,
    TeachingTurnProjection,
)
from bridges.contracts.teaching_progress import (
    LearningArtifactStatus,
    LearningAssessment,
    LearningAttempt,
    LearningLesson,
    LearningNextAction,
    LearningNextActionKind,
    LearningPlan,
    LearningPlanStatus,
    LearningQuiz,
    PlanAdjustment,
    PlanAdjustmentTrigger,
    TeachingProgressProjection,
)
from bridges.storage.database import BridgesDatabase


class TeachingProgressError(ValueError):
    """教学状态或证据合同不合法。"""


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(12)}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse_state(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TeachingProgressError("教学持久化快照格式不合法。")
    return parsed


@dataclass(frozen=True)
class ProgressPublication:
    """待与助手消息终态一起提交的教学写入。"""

    projection: TeachingTurnProjection
    commit: Callable[[], None]


class TeachingProgressService:
    """管理课时、测验、作答、评价和未来计划的单一持久化入口。"""

    _TRANSITIONS: dict[str, dict[str, frozenset[str]]] = {
        "plan": {
            LearningPlanStatus.ACTIVE.value: frozenset(
                {
                    LearningPlanStatus.WAITING_FOR_QUIZ.value,
                    LearningPlanStatus.BLOCKED.value,
                    LearningPlanStatus.COMPLETED.value,
                }
            ),
            LearningPlanStatus.WAITING_FOR_QUIZ.value: frozenset(
                {LearningPlanStatus.ACTIVE.value, LearningPlanStatus.BLOCKED.value}
            ),
            LearningPlanStatus.BLOCKED.value: frozenset({LearningPlanStatus.ACTIVE.value}),
            LearningPlanStatus.COMPLETED.value: frozenset(),
        },
        "artifact": {
            LearningArtifactStatus.DELIVERED.value: frozenset(
                {LearningArtifactStatus.INVALIDATED.value}
            ),
            LearningArtifactStatus.SUBMITTED.value: frozenset(
                {LearningArtifactStatus.ASSESSED.value, LearningArtifactStatus.INVALIDATED.value}
            ),
            LearningArtifactStatus.ASSESSED.value: frozenset(
                {LearningArtifactStatus.INVALIDATED.value}
            ),
            LearningArtifactStatus.INVALIDATED.value: frozenset(),
        },
    }

    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database

    @classmethod
    def assert_transition(cls, entity: str, current: str, target: str) -> None:
        """拒绝未登记的状态转换，避免迟到结果覆盖已提交状态。"""
        allowed = cls._TRANSITIONS.get(entity, {}).get(current)
        if allowed is None or target not in allowed:
            raise TeachingProgressError(
                f"不允许的教学状态转换：{entity} {current} -> {target}。"
            )

    @staticmethod
    def has_invalid_answer(projection: TeachingTurnProjection) -> bool:
        """判断本轮作答是否不足以创建评分和下一课。"""
        if not projection.evidence:
            return False
        answer = projection.evidence[-1]
        return (
            not answer.response_text.strip()
            or answer.evaluated_state == AnswerEvaluatedState.NEEDS_REVIEW
            or not answer.evidence_refs
        )

    def get_plan(self, account_id: str, conversation_id: str) -> LearningPlan | None:
        state = self._stored_plan_state(account_id, conversation_id)
        return (
            LearningPlan.model_validate(state["record"])
            if state is not None
            else None
        )

    def list_lessons(
        self, account_id: str, conversation_id: str
    ) -> list[LearningLesson]:
        rows = self._db.scoped(account_id).execute(
            "SELECT state_json FROM teaching_lessons "
            "WHERE account_id = ? AND conversation_id = ? "
            "ORDER BY lesson_number",
            (account_id, conversation_id),
        ).fetchall()
        return [
            LearningLesson.model_validate(_parse_state(str(row["state_json"]))["record"])
            for row in rows
        ]

    def list_quizzes(
        self, account_id: str, conversation_id: str
    ) -> list[LearningQuiz]:
        rows = self._db.scoped(account_id).execute(
            "SELECT state_json FROM teaching_quizzes "
            "WHERE account_id = ? AND conversation_id = ? "
            "ORDER BY created_at",
            (account_id, conversation_id),
        ).fetchall()
        return [
            LearningQuiz.model_validate(_parse_state(str(row["state_json"]))["record"])
            for row in rows
        ]

    def list_adjustments(
        self, account_id: str, conversation_id: str
    ) -> list[PlanAdjustment]:
        rows = self._db.scoped(account_id).execute(
            "SELECT state_json FROM teaching_plan_adjustments "
            "WHERE account_id = ? AND conversation_id = ? ORDER BY created_at",
            (account_id, conversation_id),
        ).fetchall()
        return [
            PlanAdjustment.model_validate(_parse_state(str(row["state_json"]))["record"])
            for row in rows
        ]

    def get_progress(
        self, account_id: str, conversation_id: str
    ) -> TeachingProgressProjection | None:
        plan = self.get_plan(account_id, conversation_id)
        if plan is None:
            return None
        lessons = self.list_lessons(account_id, conversation_id)
        latest = lessons[-1] if lessons else None
        quiz_id = plan.current_quiz_id
        quiz_status: LearningArtifactStatus | None = None
        if quiz_id is not None:
            row = self._db.scoped(account_id).execute(
                "SELECT state_json FROM teaching_quizzes "
                "WHERE quiz_id = ? AND account_id = ?",
                (quiz_id, account_id),
            ).fetchone()
            if row is not None:
                quiz_status = LearningQuiz.model_validate(
                    _parse_state(str(row["state_json"]))["record"]
                ).status
        action_row = self._db.scoped(account_id).execute(
            "SELECT state_json FROM teaching_next_actions "
            "WHERE account_id = ? AND conversation_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (account_id, conversation_id),
        ).fetchone()
        latest_action = (
            LearningNextAction.model_validate(
                _parse_state(str(action_row["state_json"]))["record"]
            )
            if action_row is not None
            else None
        )
        assessment_row = self._db.scoped(account_id).execute(
            "SELECT state_json FROM teaching_assessments "
            "WHERE account_id = ? AND conversation_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (account_id, conversation_id),
        ).fetchone()
        latest_assessment = (
            LearningAssessment.model_validate(
                _parse_state(str(assessment_row["state_json"]))["record"]
            )
            if assessment_row is not None
            else None
        )
        next_action = (
            latest_action.kind
            if latest_action is not None
            else (
                LearningNextActionKind.WAIT_FOR_QUIZ
                if plan.status == LearningPlanStatus.WAITING_FOR_QUIZ
                else LearningNextActionKind.CONTINUE
            )
        )
        next_action_reason = (
            latest_action.reason
            if latest_action is not None
            else (
                "请先完成当前测验，再生成下一课。"
                if plan.status == LearningPlanStatus.WAITING_FOR_QUIZ
                else "可以继续下一课。"
            )
        )
        return TeachingProgressProjection(
            plan_id=plan.plan_id,
            plan_version=plan.version,
            lesson_number=latest.lesson_number if latest else 1,
            delivered_lesson_count=plan.delivered_lesson_count,
            next_checkpoint=plan.next_checkpoint,
            quiz_id=quiz_id,
            quiz_status=quiz_status,
            assessment_state=(
                latest_assessment.evaluated_state
                if latest_assessment is not None
                else None
            ),
            next_action=next_action,
            next_action_reason=next_action_reason,
        )

    def prepare_publication(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
        projection: TeachingTurnProjection,
        content: str,
        *,
        formal_lesson: bool,
        fallback_plan: TeachingPlanProjection | None = None,
    ) -> ProgressPublication | None:
        """构造成功课时发布；提交由调用方放进消息终态事务。"""
        if not formal_lesson or not projection.can_answer_reliably or not content.strip():
            return None
        if self.has_invalid_answer(projection):
            return None
        self.migrate_legacy_conversation(account_id, conversation_id)
        existing = self._lesson_for_message(account_id, conversation_id, message_id)
        if existing is not None:
            return ProgressPublication(
                projection=self._projection_with_progress(projection, existing),
                commit=lambda: None,
            )

        stored_plan = self._stored_plan_state(account_id, conversation_id)
        plan_projection: TeachingPlanProjection | None
        new_plan = False
        if stored_plan is not None:
            stored_record = LearningPlan.model_validate(stored_plan["record"])
            stored_projection = TeachingPlanProjection.model_validate(
                stored_plan["projection"]
            )
            current_projection = projection.plan or fallback_plan
            if current_projection is not None and (
                current_projection.goal_snapshot != stored_projection.goal_snapshot
                or current_projection.target_concepts
                != stored_projection.target_concepts
            ):
                new_plan = True
                next_version = stored_record.version + 1
                plan_projection = current_projection.model_copy(
                    update={
                        "plan_id": self._versioned_plan_id(
                            current_projection.plan_id, next_version
                        ),
                        "version": next_version,
                        "owner_account_id": account_id,
                        "artifact_status": TeachingArtifactStatus.PUBLISHED,
                    }
                )
            else:
                plan_projection = stored_projection
        else:
            plan_projection = projection.plan or fallback_plan
        if plan_projection is None:
            raise TeachingProgressError("当前教学回合缺少可恢复的计划快照。")

        now = _now()
        if stored_plan is None:
            plan_version = plan_projection.version
            lesson_number = (
                projection.lesson.lesson_number if projection.lesson is not None else 1
            )
            delivered_count = lesson_number - 1
        elif new_plan:
            plan_version = plan_projection.version
            lesson_number = 1
            delivered_count = 0
        else:
            stored_record = LearningPlan.model_validate(stored_plan["record"])
            plan_version = stored_record.version
            lesson_number = stored_record.next_lesson_number
            delivered_count = stored_record.delivered_lesson_count

        lesson_projection = self._lesson_projection(
            projection,
            plan_projection,
            lesson_number,
            message_id,
            content,
        )
        plan_record = LearningPlan(
            plan_id=plan_projection.plan_id,
            account_id=account_id,
            conversation_id=conversation_id,
            version=plan_version,
            status=(
                LearningPlanStatus.WAITING_FOR_QUIZ
                if lesson_projection.understanding_check is not None
                else LearningPlanStatus.ACTIVE
            ),
            goal_snapshot=plan_projection.goal_snapshot,
            target_snapshot_id=plan_projection.target_snapshot_id,
            evidence_snapshot_id=plan_projection.evidence_snapshot_id,
            next_lesson_number=lesson_number + 1,
            delivered_lesson_count=delivered_count + 1,
            next_checkpoint=max(2, lesson_number + 2),
            current_quiz_id=(
                f"quiz-{lesson_projection.understanding_check.question_id}"
                if lesson_projection.understanding_check is not None
                else None
            ),
            profile_slice_id=plan_projection.profile_slice_id,
            created_at=(
                LearningPlan.model_validate(stored_plan["record"]).created_at
                if stored_plan is not None
                else now
            ),
            updated_at=now,
        )
        lesson_record = LearningLesson(
            lesson_id=lesson_projection.lesson_id,
            plan_id=plan_record.plan_id,
            account_id=account_id,
            conversation_id=conversation_id,
            plan_version=plan_record.version,
            lesson_number=lesson_number,
            message_id=message_id,
            title=lesson_projection.title,
            objective=lesson_projection.objective,
            content=content,
            evidence_snapshot_id=lesson_projection.evidence_snapshot_id,
            evidence_refs=list(lesson_projection.evidence_refs),
            created_at=now,
            delivered_at=now,
        )
        quiz_record = self._quiz_record(
            lesson_projection.understanding_check,
            lesson_record,
            plan_record,
            now,
        )
        assessment, attempt = self._assessment_records(
            account_id,
            conversation_id,
            projection,
            quiz_record,
            now,
        )
        action_kind, action_reason = self._next_action(
            assessment.evaluated_state if assessment is not None else None,
            quiz_record is not None,
        )
        action = LearningNextAction(
            next_action_id=_id("next"),
            plan_id=plan_record.plan_id,
            account_id=account_id,
            conversation_id=conversation_id,
            lesson_number=lesson_number,
            kind=action_kind,
            reason=action_reason,
            target_lesson_number=lesson_number + 1,
            created_at=now,
        )
        updated_projection = projection.model_copy(
            update={
                "plan": plan_projection.model_copy(
                    update={
                        "version": plan_record.version,
                        "owner_account_id": account_id,
                        "artifact_status": TeachingArtifactStatus.PUBLISHED,
                    }
                ),
                "lesson": lesson_projection.model_copy(
                    update={"artifact_status": TeachingArtifactStatus.PUBLISHED}
                ),
                "quiz": (
                    lesson_projection.understanding_check
                    if lesson_projection.understanding_check is not None
                    else projection.quiz
                ),
                "progress": TeachingProgressProjection(
                    plan_id=plan_record.plan_id,
                    plan_version=plan_record.version,
                    lesson_number=lesson_number,
                    delivered_lesson_count=plan_record.delivered_lesson_count,
                    next_checkpoint=plan_record.next_checkpoint,
                    quiz_id=quiz_record.quiz_id if quiz_record else None,
                    quiz_status=quiz_record.status if quiz_record else None,
                    assessment_state=(
                        assessment.evaluated_state if assessment is not None else None
                    ),
                    next_action=action_kind,
                    next_action_reason=action_reason,
                ),
                "next_prompt": action_reason,
            }
        )

        def commit() -> None:
            self._commit_publication(
                plan_record,
                plan_projection,
                lesson_record,
                lesson_projection,
                quiz_record,
                attempt,
                assessment,
                action,
            )

        return ProgressPublication(updated_projection, commit)

    def publish_successful_lesson(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
        projection: TeachingTurnProjection,
        content: str,
        *,
        fallback_plan: TeachingPlanProjection | None = None,
    ) -> TeachingTurnProjection:
        """独立调用 seam：原子写入一条成功课时及其测验/评价。"""
        publication = self.prepare_publication(
            account_id,
            conversation_id,
            message_id,
            projection,
            content,
            formal_lesson=True,
            fallback_plan=fallback_plan,
        )
        if publication is None:
            return projection
        with self._db.transaction():
            publication.commit()
        return publication.projection

    def apply_profile_event(
        self,
        account_id: str,
        trigger: PlanAdjustmentTrigger,
        trigger_key: str,
        reason: str,
    ) -> list[PlanAdjustment]:
        """把一次画像版本事件应用到该账户所有仍在进行的学习对话。"""
        rows = self._db.scoped(account_id).execute(
            "SELECT DISTINCT conversation_id FROM teaching_plans "
            "WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        adjustments: list[PlanAdjustment] = []
        for row in rows:
            conversation_id = str(row["conversation_id"])
            adjustment = self.apply_adjustment(
                account_id,
                conversation_id,
                trigger,
                f"{trigger_key}:{conversation_id}",
                reason,
            )
            if adjustment is not None:
                adjustments.append(adjustment)
        return adjustments

    def apply_feedback(
        self,
        account_id: str,
        conversation_id: str,
        trigger_key: str,
        reason: str,
    ) -> PlanAdjustment | None:
        """把节奏/难度反馈转成一条幂等的新计划版本。"""
        return self.apply_adjustment(
            account_id,
            conversation_id,
            PlanAdjustmentTrigger.USER_FEEDBACK,
            trigger_key,
            reason,
        )

    def apply_adjustment(
        self,
        account_id: str,
        conversation_id: str,
        trigger: PlanAdjustmentTrigger,
        trigger_key: str,
        reason: str,
    ) -> PlanAdjustment | None:
        plan = self.get_plan(account_id, conversation_id)
        if plan is None:
            return None
        existing = self._db.scoped(account_id).execute(
            "SELECT state_json FROM teaching_plan_adjustments "
            "WHERE account_id = ? AND trigger_key = ?",
            (account_id, trigger_key),
        ).fetchone()
        if existing is not None:
            return PlanAdjustment.model_validate(
                _parse_state(str(existing["state_json"]))["record"]
            )
        now = _now()
        affected = list(range(plan.next_lesson_number, plan.next_lesson_number + 3))
        adjustment = PlanAdjustment(
            adjustment_id=_id("adjustment"),
            plan_id=plan.plan_id,
            account_id=account_id,
            conversation_id=conversation_id,
            trigger=trigger,
            trigger_key=trigger_key,
            reason=reason,
            old_plan_version=plan.version,
            new_plan_version=plan.version + 1,
            affected_future_lesson_numbers=affected,
            created_at=now,
        )
        stored = self._stored_plan_state(account_id, conversation_id)
        if stored is None:
            raise TeachingProgressError("计划快照缺失，不能创建调整版本。")
        new_plan_id = f"{plan.plan_id}-v{adjustment.new_plan_version}"
        new_plan = plan.model_copy(
            update={
                "plan_id": new_plan_id,
                "version": adjustment.new_plan_version,
                "updated_at": now,
            }
        )
        new_projection = TeachingPlanProjection.model_validate(
            stored["projection"]
        ).model_copy(
            update={
                "plan_id": new_plan_id,
                "version": adjustment.new_plan_version,
                "owner_account_id": account_id,
                "artifact_status": TeachingArtifactStatus.PUBLISHED,
                "sessions": self._adjusted_sessions(
                    TeachingPlanProjection.model_validate(stored["projection"]),
                    affected,
                    reason,
                ),
            }
        )
        with self._db.transaction():
            self._insert_plan(new_plan, new_projection)
            self._db.scoped(account_id).execute(
                "INSERT INTO teaching_plan_adjustments "
                "(adjustment_id, plan_id, account_id, conversation_id, trigger_key, "
                "state_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    adjustment.adjustment_id,
                    adjustment.plan_id,
                    account_id,
                    conversation_id,
                    trigger_key,
                    _json({"record": adjustment.model_dump(mode="json")}),
                    adjustment.created_at.isoformat(),
                ),
            )
        return adjustment

    @staticmethod
    def _adjusted_sessions(
        projection: TeachingPlanProjection,
        affected_lesson_numbers: list[int],
        reason: str,
    ) -> list[TeachingPlanSession]:
        """把反馈编译进尚未交付课时的目标，不改写历史课时。"""
        if any(token in reason for token in ("太快", "太难")):
            note = "降低节奏，拆成更小的步骤并增加直观例子"
        elif any(token in reason for token in ("多练习", "多练")):
            note = "增加一次低负担练习和一个迁移例子"
        else:
            note = "根据最新反馈调整讲解路径"
        return [
            session.model_copy(
                update=(
                    {
                        "objective": f"{session.objective}（{note}）",
                        "checkpoint": f"{session.checkpoint}（{note}）",
                    }
                    if session.lesson_number in affected_lesson_numbers
                    else {}
                )
            )
            for session in projection.sessions
        ]

    def invalidate_evidence(
        self, account_id: str, conversation_id: str, evidence_snapshot_id: str
    ) -> int:
        """证据失效时标记相关课时和测验，阻止继续沿用旧题。"""
        changed = 0
        with self._db.transaction():
            rows = self._db.scoped(account_id).execute(
                "SELECT lesson_id, state_json FROM teaching_lessons "
                "WHERE account_id = ? AND conversation_id = ?",
                (account_id, conversation_id),
            ).fetchall()
            for row in rows:
                state = _parse_state(str(row["state_json"]))
                record = LearningLesson.model_validate(state["record"])
                if record.evidence_snapshot_id != evidence_snapshot_id:
                    continue
                invalidated = record.model_copy(
                    update={"status": LearningArtifactStatus.INVALIDATED}
                )
                self._db.scoped(account_id).execute(
                    "UPDATE teaching_lessons SET status = ?, state_json = ?, updated_at = ? "
                    "WHERE lesson_id = ? AND account_id = ?",
                    (
                        LearningArtifactStatus.INVALIDATED.value,
                        _json({"record": invalidated.model_dump(mode="json")}),
                        _now().isoformat(),
                        record.lesson_id,
                        account_id,
                    ),
                )
                changed += 1
            quizzes = self._db.scoped(account_id).execute(
                "SELECT quiz_id, state_json FROM teaching_quizzes "
                "WHERE account_id = ? AND conversation_id = ?",
                (account_id, conversation_id),
            ).fetchall()
            for row in quizzes:
                state = _parse_state(str(row["state_json"]))
                quiz_record = LearningQuiz.model_validate(state["record"])
                if evidence_snapshot_id not in quiz_record.evidence_refs:
                    lesson_row = self._db.scoped(account_id).execute(
                        "SELECT state_json FROM teaching_lessons "
                        "WHERE lesson_id = ? AND account_id = ?",
                        (quiz_record.lesson_id, account_id),
                    ).fetchone()
                    if lesson_row is None:
                        continue
                    lesson_state = LearningLesson.model_validate(
                        _parse_state(str(lesson_row["state_json"]))["record"]
                    )
                    if lesson_state.evidence_snapshot_id != evidence_snapshot_id:
                        continue
                invalidated_quiz = quiz_record.model_copy(
                    update={"status": LearningArtifactStatus.INVALIDATED}
                )
                self._db.scoped(account_id).execute(
                    "UPDATE teaching_quizzes SET status = ?, state_json = ?, updated_at = ? "
                    "WHERE quiz_id = ? AND account_id = ?",
                    (
                        LearningArtifactStatus.INVALIDATED.value,
                        _json({"record": invalidated_quiz.model_dump(mode="json")}),
                        _now().isoformat(),
                        quiz_record.quiz_id,
                        account_id,
                    ),
                )
                changed += 1
        return changed

    def migrate_legacy_conversation(self, account_id: str, conversation_id: str) -> None:
        """把 Issue 18 已交付的计划/第一课惰性导入新序列。"""
        if self.get_plan(account_id, conversation_id) is not None:
            return
        rows = self._db.scoped(account_id).execute(
            "SELECT message_id, content, teaching FROM messages "
            "WHERE account_id = ? AND conversation_id = ? AND role = 'assistant' "
            "AND status = 'done' AND teaching IS NOT NULL ORDER BY created_at",
            (account_id, conversation_id),
        ).fetchall()
        with self._db.transaction():
            for row in rows:
                try:
                    projection = TeachingTurnProjection.model_validate(
                        json.loads(str(row["teaching"]))
                    )
                except Exception:  # noqa: BLE001 - 历史坏投影不阻断新消息
                    continue
                if projection.plan is None or projection.lesson is None:
                    continue
                plan = projection.plan
                lesson = projection.lesson
                now = _now()
                record = LearningPlan(
                    plan_id=plan.plan_id,
                    account_id=account_id,
                    conversation_id=conversation_id,
                    version=plan.version,
                    status=(
                        LearningPlanStatus.WAITING_FOR_QUIZ
                        if lesson.understanding_check
                        else LearningPlanStatus.ACTIVE
                    ),
                    goal_snapshot=plan.goal_snapshot,
                    target_snapshot_id=plan.target_snapshot_id,
                    evidence_snapshot_id=plan.evidence_snapshot_id,
                    next_lesson_number=lesson.lesson_number + 1,
                    delivered_lesson_count=1,
                    next_checkpoint=lesson.lesson_number + 2,
                    current_quiz_id=(
                        f"quiz-{lesson.understanding_check.question_id}"
                        if lesson.understanding_check
                        else None
                    ),
                    profile_slice_id=plan.profile_slice_id,
                    created_at=now,
                    updated_at=now,
                )
                lesson_record = LearningLesson(
                    lesson_id=lesson.lesson_id,
                    plan_id=plan.plan_id,
                    account_id=account_id,
                    conversation_id=conversation_id,
                    plan_version=plan.version,
                    lesson_number=lesson.lesson_number,
                    message_id=str(row["message_id"]),
                    title=lesson.title,
                    objective=lesson.objective,
                    content=str(row["content"]),
                    evidence_snapshot_id=lesson.evidence_snapshot_id,
                    evidence_refs=list(lesson.evidence_refs),
                    created_at=now,
                    delivered_at=now,
                )
                self._insert_plan(record, plan)
                self._insert_lesson(lesson_record, lesson)
                quiz = self._quiz_record(lesson.understanding_check, lesson_record, record, now)
                if quiz:
                    self._insert_quiz(quiz)

    def _stored_plan_state(self, account_id: str, conversation_id: str) -> dict[str, Any] | None:
        row = self._db.scoped(account_id).execute(
            "SELECT state_json FROM teaching_plans WHERE account_id = ? AND conversation_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (account_id, conversation_id),
        ).fetchone()
        return _parse_state(str(row["state_json"])) if row is not None else None

    def _lesson_for_message(
        self, account_id: str, conversation_id: str, message_id: str
    ) -> LearningLesson | None:
        row = self._db.scoped(account_id).execute(
            "SELECT state_json FROM teaching_lessons WHERE account_id = ? "
            "AND conversation_id = ? AND message_id = ?",
            (account_id, conversation_id, message_id),
        ).fetchone()
        if row is None:
            return None
        return LearningLesson.model_validate(_parse_state(str(row["state_json"]))["record"])

    @staticmethod
    def _versioned_plan_id(plan_id: str, version: int) -> str:
        prefix, separator, suffix = plan_id.rpartition("-v")
        base = prefix if separator and suffix.isdigit() else plan_id
        return f"{base}-v{version}"

    @staticmethod
    def _lesson_projection(
        projection: TeachingTurnProjection,
        plan: TeachingPlanProjection,
        lesson_number: int,
        message_id: str,
        content: str,
    ) -> TeachingLessonProjection:
        source = projection.lesson
        if source is not None and source.lesson_number == lesson_number:
            return source.model_copy(
                update={
                    "explanation": content,
                    "lesson_number": lesson_number,
                    "plan_id": plan.plan_id,
                    "owner_account_id": plan.owner_account_id,
                }
            )
        concept = (
            (projection.mission.current_concept if projection.mission else None)
            or (plan.target_concepts[0] if plan.target_concepts else "当前概念")
        )
        session = next(
            (item for item in plan.sessions if item.lesson_number == lesson_number),
            None,
        )
        quiz = projection.quiz
        return TeachingLessonProjection(
            lesson_id=f"{plan.plan_id}-lesson-{lesson_number}-{message_id[:8]}",
            plan_id=plan.plan_id,
            owner_account_id=plan.owner_account_id,
            object_domain=plan.object_domain,
            artifact_status=TeachingArtifactStatus.STAGED,
            content_hash="",
            lesson_number=lesson_number,
            title=session.title if session else f"{concept}：第 {lesson_number} 课",
            objective=session.objective if session else f"继续理解“{concept}”并完成一次迁移。",
            explanation=content,
            examples=[],
            summary=[],
            understanding_check=quiz,
            evidence_refs=[
                source.source_id
                for source in [
                    *projection.evidence_gate.local_sources,
                    *projection.evidence_gate.external_sources,
                ]
            ],
            target_snapshot_id=plan.target_snapshot_id,
            evidence_snapshot_id=plan.evidence_snapshot_id,
            profile_usage=plan.profile_usage,
        )

    @staticmethod
    def _quiz_record(
        quiz: TeachingQuiz | None,
        lesson: LearningLesson,
        plan: LearningPlan,
        now: datetime,
    ) -> LearningQuiz | None:
        if quiz is None:
            return None
        return LearningQuiz(
            quiz_id=f"quiz-{quiz.question_id}",
            question_id=quiz.question_id,
            lesson_id=lesson.lesson_id,
            plan_id=plan.plan_id,
            account_id=plan.account_id,
            conversation_id=plan.conversation_id,
            plan_version=plan.version,
            lesson_number=lesson.lesson_number,
            concept=quiz.concept,
            question=quiz.question,
            expected_focus=list(quiz.expected_focus),
            evidence_refs=list(quiz.evidence_refs),
            created_at=now,
        )

    def _assessment_records(
        self,
        account_id: str,
        conversation_id: str,
        projection: TeachingTurnProjection,
        quiz: LearningQuiz | None,
        now: datetime,
    ) -> tuple[LearningAssessment | None, LearningAttempt | None]:
        if not projection.evidence:
            return None, None
        evidence = projection.evidence[-1]
        if not evidence.response_text.strip():
            return None, None
        answer_quiz = quiz
        if answer_quiz is None or evidence.question_id != answer_quiz.question_id:
            row = self._db.scoped(account_id).execute(
                "SELECT state_json FROM teaching_quizzes "
                "WHERE account_id = ? AND conversation_id = ? AND question_id = ?",
                (account_id, conversation_id, evidence.question_id),
            ).fetchone()
            if row is None:
                return None, None
            answer_quiz = LearningQuiz.model_validate(
                _parse_state(str(row["state_json"]))["record"]
            )
        attempt = LearningAttempt(
            attempt_id=evidence.answer_id,
            quiz_id=answer_quiz.quiz_id,
            account_id=account_id,
            conversation_id=conversation_id,
            source_message_id=evidence.source_message_id,
            response_text=evidence.response_text,
            created_at=now,
        )
        assessment = LearningAssessment(
            assessment_id=f"assessment-{attempt.attempt_id}",
            attempt_id=attempt.attempt_id,
            quiz_id=answer_quiz.quiz_id,
            account_id=account_id,
            conversation_id=conversation_id,
            evaluated_state=evidence.evaluated_state,
            evaluation_basis=evidence.evaluation_basis,
            evidence_refs=list(evidence.evidence_refs),
            created_at=now,
        )
        return assessment, attempt

    @staticmethod
    def _next_action(
        state: AnswerEvaluatedState | None, has_quiz: bool
    ) -> tuple[LearningNextActionKind, str]:
        if state == AnswerEvaluatedState.INCORRECT:
            return LearningNextActionKind.REMEDIATE, "先补救当前错误点，再进入下一课。"
        if state == AnswerEvaluatedState.PARTIAL:
            return LearningNextActionKind.REVIEW, "先复习尚未覆盖的关键点，再进行下一课。"
        if state == AnswerEvaluatedState.NEEDS_REVIEW:
            return LearningNextActionKind.REVIEW, "当前作答暂不形成掌握结论，可以补答或换一个例子。"
        if has_quiz:
            return LearningNextActionKind.WAIT_FOR_QUIZ, "请先完成当前测验，再生成下一课。"
        return LearningNextActionKind.CONTINUE, "可以继续下一课。"

    def _commit_publication(
        self,
        plan: LearningPlan,
        plan_projection: TeachingPlanProjection,
        lesson: LearningLesson,
        lesson_projection: TeachingLessonProjection,
        quiz: LearningQuiz | None,
        attempt: LearningAttempt | None,
        assessment: LearningAssessment | None,
        action: LearningNextAction,
    ) -> None:
        self._insert_plan(plan, plan_projection)
        self._insert_lesson(lesson, lesson_projection)
        if quiz is not None:
            self._insert_quiz(quiz)
        if attempt is not None:
            self._db.scoped(plan.account_id).execute(
                "INSERT OR IGNORE INTO teaching_attempts "
                "(attempt_id, quiz_id, account_id, conversation_id, source_message_id, "
                "status, state_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt.attempt_id,
                    attempt.quiz_id,
                    attempt.account_id,
                    attempt.conversation_id,
                    attempt.source_message_id,
                    attempt.status.value,
                    _json({"record": attempt.model_dump(mode="json")}),
                    attempt.created_at.isoformat(),
                    attempt.created_at.isoformat(),
                ),
            )
        if assessment is not None:
            self._db.scoped(plan.account_id).execute(
                "INSERT OR IGNORE INTO teaching_assessments "
                "(assessment_id, attempt_id, quiz_id, account_id, conversation_id, "
                "status, state_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    assessment.assessment_id,
                    assessment.attempt_id,
                    assessment.quiz_id,
                    assessment.account_id,
                    assessment.conversation_id,
                    assessment.status.value,
                    _json({"record": assessment.model_dump(mode="json")}),
                    assessment.created_at.isoformat(),
                    assessment.created_at.isoformat(),
                ),
            )
        self._db.scoped(plan.account_id).execute(
            "INSERT OR IGNORE INTO teaching_next_actions "
            "(next_action_id, plan_id, account_id, conversation_id, lesson_number, "
            "state_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action.next_action_id,
                action.plan_id,
                action.account_id,
                action.conversation_id,
                action.lesson_number,
                _json({"record": action.model_dump(mode="json")}),
                action.created_at.isoformat(),
                action.created_at.isoformat(),
            ),
        )

    def _insert_plan(self, record: LearningPlan, projection: TeachingPlanProjection) -> None:
        state = _json(
            {
                "record": record.model_dump(mode="json"),
                "projection": projection.model_copy(
                    update={"owner_account_id": record.account_id}
                ).model_dump(mode="json"),
            }
        )
        self._db.scoped(record.account_id).execute(
            "INSERT OR IGNORE INTO teaching_plans "
            "(plan_id, account_id, conversation_id, version, status, state_json, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.plan_id,
                record.account_id,
                record.conversation_id,
                record.version,
                record.status.value,
                state,
                record.created_at.isoformat(),
                record.updated_at.isoformat(),
            ),
        )
        self._db.scoped(record.account_id).execute(
            "UPDATE teaching_plans SET status = ?, state_json = ?, updated_at = ? "
            "WHERE plan_id = ? AND account_id = ? AND version = ?",
            (
                record.status.value,
                state,
                record.updated_at.isoformat(),
                record.plan_id,
                record.account_id,
                record.version,
            ),
        )

    def _insert_lesson(
        self, record: LearningLesson, projection: TeachingLessonProjection
    ) -> None:
        self._db.scoped(record.account_id).execute(
            "INSERT OR IGNORE INTO teaching_lessons "
            "(lesson_id, plan_id, account_id, conversation_id, lesson_number, "
            "message_id, status, state_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.lesson_id,
                record.plan_id,
                record.account_id,
                record.conversation_id,
                record.lesson_number,
                record.message_id,
                record.status.value,
                _json(
                    {
                        "record": record.model_dump(mode="json"),
                        "projection": projection.model_dump(mode="json"),
                    }
                ),
                record.created_at.isoformat(),
                record.delivered_at.isoformat(),
            ),
        )

    def _insert_quiz(self, record: LearningQuiz) -> None:
        self._db.scoped(record.account_id).execute(
            "INSERT OR IGNORE INTO teaching_quizzes "
            "(quiz_id, lesson_id, plan_id, account_id, conversation_id, question_id, "
            "status, state_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.quiz_id,
                record.lesson_id,
                record.plan_id,
                record.account_id,
                record.conversation_id,
                record.question_id,
                record.status.value,
                _json({"record": record.model_dump(mode="json")}),
                record.created_at.isoformat(),
                record.created_at.isoformat(),
            ),
        )

    def _projection_with_progress(
        self, projection: TeachingTurnProjection, lesson: LearningLesson
    ) -> TeachingTurnProjection:
        progress = self.get_progress(lesson.account_id, lesson.conversation_id)
        return projection.model_copy(update={"progress": progress})


__all__ = ["ProgressPublication", "TeachingProgressError", "TeachingProgressService"]
